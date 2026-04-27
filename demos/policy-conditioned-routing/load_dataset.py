#!/usr/bin/env python3

# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Load the policy-conditioned routing v6 dataset into Snowflake.

Renders the Jinja2 SQL template with the target database/schema,
executes each statement, and verifies row counts.

Usage:
    PYTHONPATH=<SKILL_DIR>/src uv run --project <SKILL_DIR> python \
        <SKILL_DIR>/demos/policy-conditioned-routing/load_dataset.py \
        --connection MY_CONNECTION --database TEMP --schema PUBLIC
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import snowflake.connector
from jinja2 import Template

logger = logging.getLogger(__name__)

TEMPLATE_FILE = "create_support_ticket_v6_dataset.sql.j2"

EXPECTED_COUNTS = {
    "DEMO_COMPANY_ROUTING_POLICY_V6": 4,
    "DEMO_TICKETS_HARD_GOLD_V6_SMALL": 24,
    "DEMO_TICKETS_POLICY_TRAIN_V6_LARGE": 96,
}


def split_sql_statements(sql: str) -> list[str]:
    """Split rendered SQL into individual statements on semicolons.

    Handles semicolons inside single-quoted string literals correctly.
    """
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    i = 0
    while i < len(sql):
        c = sql[i]
        if in_string:
            current.append(c)
            if c == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                current.append(sql[i + 1])
                i += 2
                continue
            if c == "'":
                in_string = False
            i += 1
            continue
        if c == "'":
            in_string = True
            current.append(c)
            i += 1
            continue
        if c == ";":
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue
        current.append(c)
        i += 1

    # Trailing statement without semicolon
    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)

    return statements


def load_dataset(
    connection: str,
    database: str,
    schema: str,
) -> None:
    """Render the SQL template and execute all statements.

    Args:
        connection: Snowflake connection name.
        database: Target database.
        schema: Target schema.
    """
    template_path = Path(__file__).parent / TEMPLATE_FILE
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    logger.info("Rendering SQL template...")
    raw = template_path.read_text()
    rendered = Template(raw).render(database=database, schema=schema)

    statements = split_sql_statements(rendered)

    # Only execute CREATE statements; skip the verification SELECTs
    # that are appended to the template (we run our own verification).
    # Strip leading SQL comment lines (-- ...) before checking the keyword.
    def _is_create(stmt: str) -> bool:
        lines = stmt.split("\n")
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            return stripped.upper().startswith("CREATE")
        return False

    create_statements = [s for s in statements if _is_create(s)]

    effective_connection = os.getenv("SNOWFLAKE_CONNECTION_NAME") or connection
    logger.info(f"Connecting to Snowflake using connection '{effective_connection}'...")
    conn = snowflake.connector.connect(connection_name=effective_connection)

    try:
        cur = conn.cursor()
        for i, stmt in enumerate(create_statements, 1):
            # Extract table name for logging
            logger.info(f"Executing statement {i}/{len(create_statements)}...")
            cur.execute(stmt)
            result = cur.fetchone()
            if result:
                logger.info(f"  -> {result[0]}")

        # Verify row counts
        logger.info("Verifying row counts...")
        all_ok = True
        for table_name, expected in EXPECTED_COUNTS.items():
            fqn = f"{database}.{schema}.{table_name}"
            cur.execute(f"SELECT COUNT(*) FROM {fqn}")
            actual = cur.fetchone()[0]
            status = "OK" if actual == expected else "MISMATCH"
            if actual != expected:
                all_ok = False
            logger.info(
                f"  {table_name}: {actual} rows (expected {expected}) [{status}]"
            )

        # Verify zero subject overlap
        cur.execute(
            f"SELECT COUNT(*) FROM {database}.{schema}.DEMO_TICKETS_POLICY_TRAIN_V6_LARGE t "
            f"JOIN {database}.{schema}.DEMO_TICKETS_HARD_GOLD_V6_SMALL h ON t.SUBJECT = h.SUBJECT"
        )
        overlap = cur.fetchone()[0]
        logger.info(
            f"  Subject overlap between train and holdout: {overlap} (expected 0)"
        )
        if overlap != 0:
            all_ok = False

        if not all_ok:
            logger.error("Verification failed!")
            sys.exit(1)

        logger.info("Dataset loaded and verified successfully.")

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Load policy-conditioned routing v6 dataset into Snowflake.",
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
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    load_dataset(
        connection=args.connection,
        database=args.database,
        schema=args.schema,
    )
