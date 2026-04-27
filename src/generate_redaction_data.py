# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Generate sample data from ai4privacy/pii-masking-300k dataset.

This script loads the ai4privacy PII masking dataset, samples rows for training
and test sets, and creates tables directly in Snowflake.

Example usage:
    pip install datasets pandas numpy snowflake-connector-python
    python generate_redaction_data.py --connection MY_CONNECTION --database TEMP --schema PUBLIC
    python generate_redaction_data.py --connection MY_CONNECTION --database TEMP --schema PUBLIC --train 100 --test 200
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any

import datasets
import numpy as np
import pandas as pd
from snowflake.snowpark import Session

from custom_ai_function_utils import create_session_from_connection

logger = logging.getLogger(__name__)


def convert_to_serializable(obj: Any) -> Any:
    """Convert numpy arrays and nested structures to JSON-serializable types.

    Args:
        obj: Any object that may contain numpy arrays, lists, tuples, or dicts.

    Returns:
        The same structure with numpy arrays converted to Python lists.
    """
    if isinstance(obj, np.ndarray):
        return [convert_to_serializable(item) for item in obj.tolist()]
    if isinstance(obj, (list, tuple)):
        return [convert_to_serializable(item) for item in obj]
    if isinstance(obj, dict):
        return {k: convert_to_serializable(v) for k, v in obj.items()}
    return obj


def create_table(
    session: Session,
    database: str,
    schema: str,
    table_name: str,
) -> None:
    """Create a table for storing redaction demo data.

    Args:
        session: Active Snowpark session.
        database: The database name.
        schema: The schema name.
        table_name: The table name.
    """
    fqn = f"{database}.{schema}.{table_name}"
    sql = f"""
        CREATE TABLE {fqn} (
            INPUT_TEXT VARCHAR,
            EXPECTED_OUTPUT VARCHAR,
            PRIVACY_MASK VARIANT
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
    """Insert data into a redaction demo table.

    Args:
        session: Active Snowpark session.
        database: The database name.
        schema: The schema name.
        table_name: The table name.
        df: DataFrame with source_text, target_text, and privacy_mask columns.
    """
    fqn = f"{database}.{schema}.{table_name}"

    logger.info(f"Inserting {len(df)} rows into {fqn}...")
    upload_df = pd.DataFrame(
        {
            "INPUT_TEXT": df["source_text"],
            "EXPECTED_OUTPUT": df["target_text"],
            "PRIVACY_MASK": df["privacy_mask"].apply(
                lambda x: json.dumps(convert_to_serializable(x))
            ),
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
    train: int = 50,
    test: int = 100,
    seed: int = 42,
    language: str | None = None,
) -> None:
    """Load dataset, sample data, and create Snowflake tables.

    Args:
        connection: Snowflake connection name.
        database: Target Snowflake database name.
        schema: Target Snowflake schema name.
        train: Number of training rows.
        test: Number of test rows.
        seed: Random seed for reproducibility.
        language: Optional language filter (e.g., "French"). Case-insensitive.
    """
    logger.info("Loading ai4privacy/pii-masking-300k dataset...")
    dataset = datasets.load_dataset("ai4privacy/pii-masking-300k")
    df = dataset["train"].to_pandas()

    logger.info(f"Dataset size: {len(df)} rows")

    if language:
        df = df[df["language"].str.lower() == language.lower()]
        logger.info(f"Filtered to language '{language}': {len(df)} rows")
        if len(df) < train + test:
            raise ValueError(
                f"Not enough rows for language '{language}': "
                f"need {train + test}, found {len(df)}"
            )

    logger.info(f"Sampling {train} training + {test} test rows (seed={seed})...")

    sampled = df.sample(n=train + test, random_state=seed)
    train_df = sampled.head(train)
    test_df = sampled.tail(test)

    conn_name = os.getenv("SNOWFLAKE_CONNECTION_NAME") or connection
    logger.info(f"Connecting to Snowflake using connection '{conn_name}'...")
    session = create_session_from_connection(conn_name)

    try:
        create_table(session, database, schema, "DEMO_REDACTION_TRAIN")
        insert_data(session, database, schema, "DEMO_REDACTION_TRAIN", train_df)

        create_table(session, database, schema, "DEMO_REDACTION_TEST")
        insert_data(session, database, schema, "DEMO_REDACTION_TEST", test_df)

        logger.info("Done!")
        logger.info(
            f"  Training table: {database}.{schema}.DEMO_REDACTION_TRAIN ({len(train_df)} rows)"
        )
        logger.info(
            f"  Test table: {database}.{schema}.DEMO_REDACTION_TEST ({len(test_df)} rows)"
        )
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate sample PII redaction data from ai4privacy dataset."
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
        default=10,
        help="Number of training rows (default: 10)",
    )
    parser.add_argument(
        "--test",
        type=int,
        default=20,
        help="Number of test rows (default: 20)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional language filter, e.g. 'French', 'English', 'German' (case-insensitive)",
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
        language=args.language,
    )
