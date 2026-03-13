# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Generate sample data from FredZhang7/toxi-text-3M dataset.

This script loads the toxi-text-3M dataset, samples balanced rows for training
and test sets, and creates tables directly in Snowflake.

Each row contains a text sample and a binary toxicity label ("toxic" or
"not_toxic"). The dataset spans 55 languages, enabling multilingual content
moderation evaluation.

Example usage:
    python generate_toxicity_data.py --connection MY_CONNECTION --database TEMP --schema PUBLIC
    python generate_toxicity_data.py --connection MY_CONNECTION --database TEMP --schema PUBLIC --train 300 --test 200
"""

from __future__ import annotations

import argparse
import logging
import os

import datasets
import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas

logger = logging.getLogger(__name__)


def is_toxic_to_label(value: int) -> str:
    """Convert integer toxicity flag to string label.

    Args:
        value: 0 (not toxic) or 1 (toxic).

    Returns:
        "toxic" or "not_toxic".
    """
    return "toxic" if value == 1 else "not_toxic"


def create_table(
    conn: snowflake.connector.SnowflakeConnection,
    database: str,
    schema: str,
    table_name: str,
) -> None:
    """Create a table for storing toxicity detection demo data.

    Args:
        conn: Active Snowflake connection.
        database: The database name.
        schema: The schema name.
        table_name: The table name.
    """
    fqn = f"{database}.{schema}.{table_name}"
    sql = f"""
        CREATE OR REPLACE TABLE {fqn} (
            TEXT VARCHAR,
            EXPECTED_OUTPUT VARCHAR
        )
    """
    logger.info(f"Creating table {fqn}...")
    conn.cursor().execute(sql)


def insert_data(
    conn: snowflake.connector.SnowflakeConnection,
    database: str,
    schema: str,
    table_name: str,
    df: pd.DataFrame,
) -> None:
    """Insert data into a toxicity detection demo table.

    Args:
        conn: Active Snowflake connection.
        database: The database name.
        schema: The schema name.
        table_name: The table name.
        df: DataFrame with TEXT and EXPECTED_OUTPUT columns.
    """
    fqn = f"{database}.{schema}.{table_name}"

    logger.info(f"Inserting {len(df)} rows into {fqn}...")
    upload_df = pd.DataFrame(
        {
            "TEXT": df["TEXT"].values,
            "EXPECTED_OUTPUT": df["EXPECTED_OUTPUT"].values,
        }
    )
    write_pandas(conn, upload_df, table_name, database=database, schema=schema)


def main(
    connection: str,
    database: str,
    schema: str,
    train: int = 300,
    test: int = 200,
    seed: int = 42,
) -> None:
    """Load dataset, sample balanced data, and create Snowflake tables.

    Samples an equal number of toxic and not_toxic rows so the dataset is
    balanced (50/50), regardless of the original class distribution (~14%
    toxic in the full dataset).

    Args:
        connection: Snowflake connection name.
        database: Target Snowflake database name.
        schema: Target Snowflake schema name.
        train: Number of training rows.
        test: Number of test rows.
        seed: Random seed for reproducibility.
    """
    logger.info("Loading FredZhang7/toxi-text-3M dataset...")
    dataset = datasets.load_dataset("FredZhang7/toxi-text-3M")
    df = dataset["train"].to_pandas()

    logger.info(f"Full dataset size: {len(df)} rows")

    # Map integer labels to string labels
    df["label"] = df["is_toxic"].apply(is_toxic_to_label)

    toxic_df = df[df["is_toxic"] == 1]
    nontoxic_df = df[df["is_toxic"] == 0]

    logger.info(f"Toxic rows: {len(toxic_df)}, Non-toxic rows: {len(nontoxic_df)}")

    # Sample balanced 50/50 split for both train and test
    total_needed = train + test
    per_class = total_needed // 2

    if per_class > len(toxic_df):
        logger.warning(
            f"Requested {per_class} toxic rows but only {len(toxic_df)} "
            f"available. Reducing sample size."
        )
        per_class = len(toxic_df)

    toxic_sample = toxic_df.sample(n=per_class, random_state=seed)
    nontoxic_sample = nontoxic_df.sample(n=per_class, random_state=seed)

    combined = pd.concat([toxic_sample, nontoxic_sample]).sample(
        frac=1, random_state=seed
    )

    # Split into train and test
    train_rows = min(train, len(combined))
    test_rows = min(test, len(combined) - train_rows)

    logger.info(
        f"Sampling {train_rows} training + {test_rows} test rows "
        f"(balanced 50/50, seed={seed})..."
    )

    train_slice = combined.head(train_rows)
    test_slice = combined.tail(test_rows)

    train_upload = pd.DataFrame(
        {
            "TEXT": train_slice["text"].values,
            "EXPECTED_OUTPUT": train_slice["label"].values,
        }
    )
    test_upload = pd.DataFrame(
        {
            "TEXT": test_slice["text"].values,
            "EXPECTED_OUTPUT": test_slice["label"].values,
        }
    )

    logger.info(f"Connecting to Snowflake using connection '{connection}'...")
    conn = snowflake.connector.connect(
        connection_name=os.getenv("SNOWFLAKE_CONNECTION_NAME") or connection
    )

    try:
        create_table(conn, database, schema, "DEMO_TOXICITY_TRAIN")
        insert_data(conn, database, schema, "DEMO_TOXICITY_TRAIN", train_upload)

        create_table(conn, database, schema, "DEMO_TOXICITY_TEST")
        insert_data(conn, database, schema, "DEMO_TOXICITY_TEST", test_upload)

        logger.info("Done!")
        logger.info(
            f"  Training table: {database}.{schema}.DEMO_TOXICITY_TRAIN ({len(train_upload)} rows)"
        )
        logger.info(
            f"  Test table: {database}.{schema}.DEMO_TOXICITY_TEST ({len(test_upload)} rows)"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate sample toxicity detection data from toxi-text-3M dataset."
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
        default=300,
        help="Number of training rows (default: 300)",
    )
    parser.add_argument(
        "--test",
        type=int,
        default=200,
        help="Number of test rows (default: 200)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
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
    )
