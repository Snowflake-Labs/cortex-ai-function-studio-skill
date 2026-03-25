#!/usr/bin/env python3

# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Deploy infrastructure for custom AI functions.

Provisions Snowflake infrastructure for AI function workflows:
1. Creates a stage if it doesn't exist
2. Uploads all Python modules to the stage
3. Creates or replaces all stored procedures (evaluate, optimize, synthetic data)

Outputs a JSON result summary to stdout; progress goes to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

from snowflake.snowpark import Session

from create_sproc import render_sproc_sql as render_sproc_template_sql

SCRIPT_DIR = Path(__file__).resolve().parent
SRC_DIR = SCRIPT_DIR.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from custom_ai_function_utils import customai_query_tag_logging  # noqa: E402

COCO_SESSION_TAG_PREFIX = "__CUSTOM_AI_FUNCTION_COCO_SESSION_ID_"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEBUG = False

SPROC_TYPES = {
    "EVALUATE_AI_FUNCTION": "evaluate",
    "EVALUATE_AI_FUNCTION_ASYNC": "evaluate_async",
    "OPTIMIZE_AI_FUNCTION": "optimize",
    "OPTIMIZE_AI_FUNCTION_ASYNC": "optimize_async",
    "GENERATE_SYNTHETIC_DATA": "synthetic",
}

STAGE_MODULES = [
    "src/custom_ai_function_utils.py",
    "src/metrics_core.py",
    "src/snow_gepa_adapter.py",
    "src/snow_gepa_optimize.py",
    "src/snow_synthetic_data.py",
]

SKILL_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers — logging and SQL execution
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def run_sql(session: Session, sql: str, *, step: str = "") -> list:
    try:
        return session.sql(sql).collect()
    except Exception as exc:
        prefix = f"[{step}] " if step else ""
        raise RuntimeError(f"{prefix}{exc}") from exc


# ---------------------------------------------------------------------------
# Helpers — Snowflake naming
# ---------------------------------------------------------------------------


def qualify_stage_name(stage_name: str, database: str, schema: str) -> str:
    """Turn a bare stage name into DB.SCHEMA.STAGE; pass-through if already qualified."""
    if "." in stage_name:
        if stage_name.count(".") != 2:
            raise ValueError(f"Stage must be NAME or DB.SCHEMA.NAME, got: {stage_name}")
        return stage_name
    return f"{database}.{schema}.{stage_name}"


def parse_stage_fqn(stage_fqn: str) -> tuple[str, str, str]:
    parts = stage_fqn.split(".")
    if len(parts) != 3:
        raise ValueError(f"Stage must resolve to DB.SCHEMA.STAGE, got: {stage_fqn}")
    return parts[0], parts[1], parts[2]


# ---------------------------------------------------------------------------
# Helpers — Snowflake operations
# ---------------------------------------------------------------------------


def resolve_warehouse(session: Session, warehouse: str | None) -> str:
    """Use the explicit --warehouse value, or fall back to the session default."""
    if warehouse:
        return warehouse
    rows = run_sql(session, "SELECT CURRENT_WAREHOUSE()", step="resolve warehouse")
    wh = rows[0][0] if rows and rows[0] else None
    if not wh:
        raise ValueError(
            "No active warehouse (pass --warehouse or set one in your connection config)"
        )
    return wh


def render_sproc_ddl(
    sproc_type: str, database: str, schema: str, stage_fqn: str
) -> str:
    stage_db, stage_schema, stage_name = parse_stage_fqn(stage_fqn)
    sql = render_sproc_template_sql(
        sproc_type=sproc_type,
        database=database,
        schema=schema,
        stage_name=stage_name,
    )
    # Rewrite stage prefix when the stage lives in a different db/schema.
    expected = f"@{database}.{schema}.{stage_name}/"
    actual = f"@{stage_db}.{stage_schema}.{stage_name}/"
    if expected != actual:
        sql = sql.replace(expected, actual)
    return sql


def upload_stage_modules(session: Session, stage_fqn: str) -> None:
    for rel in STAGE_MODULES:
        path = SKILL_DIR / rel
        if not path.exists():
            raise FileNotFoundError(f"Missing module: {path}")
        log(f"  {Path(rel).name}")
        session.file.put(
            f"file://{path}",
            f"@{stage_fqn}",
            auto_compress=False,
            overwrite=True,
        )


def create_stored_procedures(
    session: Session, database: str, schema: str, stage_fqn: str
) -> None:
    coco_session_id = os.environ.get("CORTEX_SESSION_ID")

    for proc_name, sproc_type in SPROC_TYPES.items():
        log(f"  {proc_name}")
        ddl = render_sproc_ddl(sproc_type, database, schema, stage_fqn)

        if coco_session_id:
            with customai_query_tag_logging(
                session,
                coco_session_id,
                tag_prefix=COCO_SESSION_TAG_PREFIX,
            ):
                run_sql(session, ddl, step=f"create procedure {proc_name}")
        else:
            run_sql(session, ddl, step=f"create procedure {proc_name}")


def provision_infrastructure(
    session: Session, database: str, schema: str, stage_fqn: str, warehouse: str
) -> dict:
    """Set session context, create stage, upload modules, and create SPROCs."""
    log("Setting session context...")
    run_sql(session, f"USE DATABASE {database}", step="USE DATABASE")
    run_sql(session, f"USE SCHEMA {schema}", step="USE SCHEMA")
    run_sql(session, f"USE WAREHOUSE {warehouse}", step="USE WAREHOUSE")

    log("Ensuring stage...")
    run_sql(
        session,
        f"CREATE STAGE IF NOT EXISTS {stage_fqn} " f"DIRECTORY = (ENABLE = TRUE)",
        step="create stage",
    )

    log("Uploading modules...")
    upload_stage_modules(session, stage_fqn)

    log("Creating stored procedures...")
    create_stored_procedures(session, database, schema, stage_fqn)

    return {
        "status": "success",
        "stage": stage_fqn,
        "modules": [Path(m).name for m in STAGE_MODULES],
        "procedures": list(SPROC_TYPES.keys()),
        "database": database,
        "schema": schema,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy infrastructure (stage, modules, stored procedures) "
        "for custom AI function workflows.",
    )
    parser.add_argument("--database", required=True, help="Target database")
    parser.add_argument("--schema", required=True, help="Target schema")
    parser.add_argument("--connection", required=True, help="Snowflake connection name")
    parser.add_argument("--warehouse", help="Warehouse for session context")
    parser.add_argument(
        "--stage",
        default="AI_FUNCTIONS",
        help="Stage name or DB.SCHEMA.STAGE (default: AI_FUNCTIONS)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan without executing",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()

    database = args.database
    schema = args.schema
    stage_fqn = qualify_stage_name(args.stage, database, schema)

    if args.dry_run:
        plan = {
            "status": "dry_run",
            "stage": stage_fqn,
            "modules": [Path(m).name for m in STAGE_MODULES],
            "procedures": list(SPROC_TYPES.keys()),
            "connection": args.connection,
        }
        print(json.dumps(plan, indent=2))
        return

    session = Session.builder.config("connection_name", args.connection).create()
    try:
        warehouse = resolve_warehouse(session, args.warehouse)
        result = provision_infrastructure(
            session, database, schema, stage_fqn, warehouse
        )
        print(json.dumps(result, indent=2))
    finally:
        session.close()


def _write_debug_log(e: Exception) -> str | None:
    """Write full error details to a temp file when DEBUG is enabled."""
    if not DEBUG:
        return None
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = Path(tempfile.gettempdir()) / f"deploy_ai_function_{ts}.log"
        path.write_text(
            f"timestamp: {ts}\n"
            f"error: {e}\n\n"
            f"traceback:\n{traceback.format_exc()}\n"
        )
        return str(path)
    except Exception:
        return None


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"ERROR: {exc}")
        debug_log = _write_debug_log(exc)
        if debug_log:
            log(f"Debug log written to: {debug_log}")
        error = {"status": "error", "message": str(exc)}
        if debug_log:
            error["debug_log"] = debug_log
        print(json.dumps(error, indent=2))
        sys.exit(1)
