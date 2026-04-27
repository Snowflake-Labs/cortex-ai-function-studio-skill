# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Generate sample data from the CUAD (Contract Understanding Atticus Dataset).

Downloads the CUAD master CSV directly via HTTP (no HuggingFace CLI or datasets
library required), extracts four key contract fields (parties, governing_law,
effective_date, expiration_date), and creates labeled tables in Snowflake.

The CSV is 3.8 MB. Each row contains clause excerpts for a commercial legal
contract along with expert-annotated answers for 41 clause categories.

CUAD is licensed under CC BY 4.0 by The Atticus Project (NeurIPS 2021).

Example usage:
    PYTHONPATH=<SKILL_DIR>/src uv run --project <SKILL_DIR> python <SKILL_DIR>/src/generate_cuad_data.py \
        --connection MY_CONNECTION --database TEMP --schema PUBLIC
    PYTHONPATH=<SKILL_DIR>/src uv run --project <SKILL_DIR> python <SKILL_DIR>/src/generate_cuad_data.py \
        --connection MY_CONNECTION --database TEMP --schema PUBLIC --train 25 --test 25
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd
from snowflake.snowpark import Session

from custom_ai_function_utils import create_session_from_connection

logger = logging.getLogger(__name__)

CUAD_CSV_URL = (
    "https://huggingface.co/datasets/theatticusproject/cuad"
    "/resolve/main/CUAD_v1/master_clauses.csv"
)

ANSWER_COLUMNS = {
    "Parties-Answer": "parties",
    "Governing Law-Answer": "governing_law",
    "Effective Date-Answer": "effective_date",
    "Expiration Date-Answer": "expiration_date",
}

CONTEXT_COLUMNS = [
    "Document Name",
    "Parties",
    "Agreement Date",
    "Effective Date",
    "Expiration Date",
    "Renewal Term",
    "Notice Period To Terminate Renewal",
    "Governing Law",
    "Most Favored Nation",
    "Non-Compete",
    "Exclusivity",
    "No-Solicit Of Customers",
    "No-Solicit Of Employees",
    "Non-Disparagement",
    "Termination For Convenience",
    "Rofr/Rofo/Rofn",
    "Change Of Control",
    "Anti-Assignment",
    "Revenue/Profit Sharing",
    "Price Restrictions",
    "Minimum Commitment",
    "Volume Restriction",
    "Ip Ownership Assignment",
    "Joint Ip Ownership",
    "License Grant",
    "Non-Transferable License",
    "Affiliate License-Licensor",
    "Affiliate License-Licensee",
    "Unlimited/All-You-Can-Eat-License",
    "Irrevocable Or Perpetual License",
    "Source Code Escrow",
    "Post-Termination Services",
    "Audit Rights",
    "Uncapped Liability",
    "Cap On Liability",
    "Liquidated Damages",
    "Warranty Duration",
    "Insurance",
    "Covenant Not To Sue",
    "Third Party Beneficiary",
    "Competitive Restriction Exception",
]


def download_cuad_csv(dest_dir: str) -> Path:
    """Download the CUAD master clauses CSV via HTTP."""
    dest_path = Path(dest_dir) / "master_clauses.csv"
    if dest_path.exists():
        logger.info(f"Using cached file: {dest_path}")
        return dest_path

    logger.info("Downloading CUAD master_clauses.csv (~3.8 MB)...")
    try:
        urllib.request.urlretrieve(CUAD_CSV_URL, dest_path)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise RuntimeError(
            f"Failed to download CUAD dataset from {CUAD_CSV_URL} — "
            "check your network connection and try again."
        ) from exc
    logger.info(f"Downloaded to {dest_path}")
    return dest_path


def _clean_answer(value: str) -> str:
    """Normalize a raw CUAD answer: strip whitespace, coerce NaN/[] to ''."""
    if pd.isna(value):
        return ""
    s = str(value).strip()
    if s in ("", "[]", "nan"):
        return ""
    return s


def _build_contract_text(row: pd.Series) -> str:
    """Join all non-empty clause excerpt columns into one contract text block."""
    sections = []
    for col in CONTEXT_COLUMNS:
        if col not in row.index:
            continue
        val = str(row[col]).strip()
        if val and val != "nan" and val != "[]":
            sections.append(val)
    return "\n\n".join(sections)


def build_contract_dataset(csv_path: Path) -> pd.DataFrame:
    """Build a per-contract DataFrame with 4 extracted fields from the CUAD CSV.

    Filters to contracts with >= 3 of 4 target fields populated and >= 200
    chars of clause text.
    """
    logger.info("Reading CUAD master CSV...")
    df = pd.read_csv(csv_path)
    logger.info(f"Loaded {len(df)} contracts")

    records = []
    for _, row in df.iterrows():
        fields = {}
        for csv_col, field_name in ANSWER_COLUMNS.items():
            fields[field_name] = _clean_answer(row.get(csv_col, ""))

        populated = sum(1 for v in fields.values() if v)
        if populated < 3:
            continue

        contract_text = _build_contract_text(row)
        if len(contract_text) < 200:
            continue

        records.append(
            {
                "CONTRACT_TEXT": contract_text,
                "EXPECTED_GOV_LAW": fields["governing_law"],
                "EXPECTED_OUTPUT": json.dumps(fields),
            }
        )

    result = pd.DataFrame(records)
    logger.info(f"Contracts with >= 3 target fields: {len(result)}")

    field_stats = result["EXPECTED_OUTPUT"].apply(
        lambda x: sum(1 for v in json.loads(x).values() if v)
    )
    logger.info(
        f"  All 4 fields: {(field_stats == 4).sum()}, "
        f"3 fields: {(field_stats == 3).sum()}"
    )

    return result


def create_table(
    session: Session,
    database: str,
    schema: str,
    table_name: str,
) -> None:
    """CREATE the contract extraction demo table."""
    fqn = f"{database}.{schema}.{table_name}"
    sql = f"""
        CREATE TABLE {fqn} (
            CONTRACT_TEXT VARCHAR,
            EXPECTED_GOV_LAW VARCHAR,
            EXPECTED_OUTPUT VARCHAR
        )
    """
    logger.info(f"Creating table {fqn}...")
    session.sql(sql).collect()


def insert_data(
    session: Session,
    database: str,
    schema: str,
    table_name: str,
    df: pd.DataFrame,
) -> None:
    """Write a DataFrame into the given demo table."""
    fqn = f"{database}.{schema}.{table_name}"

    logger.info(f"Inserting {len(df)} rows into {fqn}...")
    upload_df = pd.DataFrame(
        {
            "CONTRACT_TEXT": df["CONTRACT_TEXT"].values,
            "EXPECTED_GOV_LAW": df["EXPECTED_GOV_LAW"].values,
            "EXPECTED_OUTPUT": df["EXPECTED_OUTPUT"].values,
        }
    )
    from snowflake.snowpark.types import StructType, StructField, StringType

    sp_schema = StructType([StructField(c, StringType()) for c in upload_df.columns])
    rows = list(upload_df.itertuples(index=False, name=None))
    session.create_dataframe(rows, schema=sp_schema).write.mode("append").save_as_table(
        fqn
    )


def main(
    connection: str,
    database: str,
    schema: str,
    train: int = 25,
    test: int = 25,
    seed: int = 42,
    train_table: str = "DEMO_CONTRACT_TRAIN",
    test_table: str = "DEMO_CONTRACT_TEST",
) -> None:
    """Download CUAD, extract fields, and create train/test tables in Snowflake."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        csv_path = download_cuad_csv(tmp_dir)
        contracts_df = build_contract_dataset(csv_path)

    total_needed = train + test
    available = len(contracts_df)

    if available < total_needed:
        logger.warning(
            f"Requested {total_needed} rows but only {available} contracts "
            f"have >= 3 fields populated. Adjusting split proportionally."
        )
        train_frac = train / total_needed
        train = int(available * train_frac)
        test = available - train

    logger.info(f"Sampling {train} training + {test} test rows (seed={seed})...")
    sampled = contracts_df.sample(n=train + test, random_state=seed)
    train_df = sampled.head(train)
    test_df = sampled.tail(test)

    conn_name = os.getenv("SNOWFLAKE_CONNECTION_NAME") or connection
    logger.info(f"Connecting to Snowflake using connection '{conn_name}'...")
    session = create_session_from_connection(conn_name)

    try:
        create_table(session, database, schema, train_table)
        insert_data(session, database, schema, train_table, train_df)

        if test > 0:
            create_table(session, database, schema, test_table)
            insert_data(session, database, schema, test_table, test_df)

        logger.info("Done!")
        logger.info(
            f"  Training table: {database}.{schema}.{train_table} "
            f"({len(train_df)} rows)"
        )
        if test > 0:
            logger.info(
                f"  Test table: {database}.{schema}.{test_table} "
                f"({len(test_df)} rows)"
            )
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate legal contract extraction data from the CUAD dataset."
    )
    parser.add_argument(
        "--connection",
        type=str,
        required=True,
        help="Snowflake connection name",
    )
    parser.add_argument(
        "--database",
        type=str,
        required=True,
        help="Target Snowflake database name",
    )
    parser.add_argument(
        "--schema",
        type=str,
        required=True,
        help="Target Snowflake schema name",
    )
    parser.add_argument(
        "--train",
        type=int,
        default=25,
        help="Number of training rows (default: 25)",
    )
    parser.add_argument(
        "--test",
        type=int,
        default=25,
        help="Number of test rows (default: 25)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--train-table",
        type=str,
        default="DEMO_CONTRACT_TRAIN",
        help="Name for the training table (default: DEMO_CONTRACT_TRAIN)",
    )
    parser.add_argument(
        "--test-table",
        type=str,
        default="DEMO_CONTRACT_TEST",
        help="Name for the test table (default: DEMO_CONTRACT_TEST)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    main(
        connection=args.connection,
        database=args.database,
        schema=args.schema,
        train=args.train,
        test=args.test,
        seed=args.seed,
        train_table=args.train_table,
        test_table=args.test_table,
    )
