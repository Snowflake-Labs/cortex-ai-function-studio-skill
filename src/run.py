#!/usr/bin/env python3

# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Unified runner for AI Function Studio anonymous stored procedures.

Supports three subcommands — evaluate, optimize, and synthetic — each rendering
an anonymous SPROC definition, appending the CALL with supplied arguments, and
executing the combined SQL via Snowpark.  Evaluate and optimize also support
asynchronous execution via Snowflake Tasks.

Usage:
    uv run --project <SKILL_DIR> python <SKILL_DIR>/src/run.py evaluate \
        --database DB --schema SCHEMA --stage AI_FUNCTIONS \
        --connection MY_CONN --function-name DB.SCHEMA.MY_FUNC ...

    uv run --project <SKILL_DIR> python <SKILL_DIR>/src/run.py optimize \
        --database DB --schema SCHEMA --stage AI_FUNCTIONS \
        --connection MY_CONN --function-name DB.SCHEMA.MY_FUNC ...

    uv run --project <SKILL_DIR> python <SKILL_DIR>/src/run.py synthetic \
        --database DB --schema SCHEMA --stage AI_FUNCTIONS \
        --connection MY_CONN --task-description "Classify tickets" ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from create_sproc import render_sproc_sql
from custom_ai_function_utils import (
    COCO_SESSION_TAG_PREFIX,
    customai_query_tag_logging,
    create_session_from_connection,
)


# ---------------------------------------------------------------------------
# Shared helpers — logging, nullable CLI types, SQL formatting
# ---------------------------------------------------------------------------

_NONE_SENTINELS = frozenset({"none", "null"})


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def nullable_str(value: str) -> str | None:
    """Argparse type: returns None for 'none'/'null', otherwise the string."""
    return None if value.lower() in _NONE_SENTINELS else value


def nullable_int(value: str) -> int | None:
    """Argparse type: returns None for 'none'/'null', otherwise int."""
    return None if value.lower() in _NONE_SENTINELS else int(value)


def bool_str(value: str) -> bool:
    """Argparse type: converts 'true'/'false' string to bool."""
    if value.lower() in ("true", "1", "yes"):
        return True
    if value.lower() in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"Expected true/false, got: {value}")


def _sql_varchar(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def _sql_array(values: list[str]) -> str:
    items = ", ".join(_sql_varchar(v) for v in values)
    return f"ARRAY_CONSTRUCT({items})"


def _sql_int(value: int | None) -> str:
    return "NULL" if value is None else str(value)


def _sql_float(value: float | None) -> str:
    return "NULL" if value is None else str(value)


def _sql_bool(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def _sql_variant_json(value: str | None) -> str:
    """Format a raw JSON string as a VARIANT literal via PARSE_JSON."""
    if value is None:
        return "NULL"
    return "PARSE_JSON('" + value.replace("'", "''") + "')"


# ---------------------------------------------------------------------------
# Async task builder (shared by evaluate and optimize)
# ---------------------------------------------------------------------------


def _build_async_sql(
    args: argparse.Namespace,
    sproc_type: str,
    run_id_prefix: str,
    build_call_fn,
) -> tuple[str, str, str]:
    """Return (create_task_sql, execute_task_sql, run_id)."""
    func_short = args.function_name.rsplit(".", 1)[-1]
    run_id = args.run_id or f"{run_id_prefix}{func_short}_{int(time.time() * 1000)}"
    timeout_ms = (args.timeout_minutes or 240) * 60 * 1000

    anon_def = render_sproc_sql(
        sproc_type,
        args.database,
        args.schema,
        args.stage,
        anonymous=True,
        inline=args.inline,
    )

    call_args = argparse.Namespace(**vars(args))
    call_args.run_id = run_id
    call_stmt = build_call_fn(call_args)

    task_fqn = f"{args.database}.{args.schema}.{run_id}"
    create = (
        f"CREATE OR REPLACE TASK {task_fqn}\n"
        f"  WAREHOUSE = {args.warehouse}\n"
        f"  USER_TASK_TIMEOUT_MS = {timeout_ms}\n"
        f"AS\n"
        f"{anon_def}\n{call_stmt};"
    )
    execute = f"EXECUTE TASK {task_fqn};"
    return create, execute, run_id


# =========================================================================
# Evaluate
# =========================================================================


def _eval_build_call(args: argparse.Namespace) -> str:
    """Build ``CALL EVALUATE_AI_FUNCTION(...)`` with actual argument values."""
    params = [
        _sql_varchar(args.function_name),
        _sql_varchar(args.test_table),
        _sql_array(args.input_columns),
        _sql_varchar(args.label_column),
        _sql_varchar(args.metric_name),
        _sql_varchar(args.model_name),
        _sql_int(args.sample_size),
        _sql_varchar(args.results_table),
        _sql_variant_json(args.metric_options),
        _sql_int(args.max_length),
        _sql_varchar(args.custom_metric_udf),
        _sql_varchar(args.run_id),
    ]
    joined = ",\n    ".join(params)
    return f"CALL EVALUATE_AI_FUNCTION(\n    {joined}\n)"


def eval_build_sync_sql(args: argparse.Namespace) -> str:
    anon_def = render_sproc_sql(
        "evaluate",
        args.database,
        args.schema,
        args.stage,
        anonymous=True,
        inline=args.inline,
    )
    return f"{anon_def}\n{_eval_build_call(args)};"


def eval_build_async_sql(
    args: argparse.Namespace,
) -> tuple[str, str, str]:
    """Return (create_task_sql, execute_task_sql, run_id)."""
    return _build_async_sql(args, "evaluate", "ai_func_eval_", _eval_build_call)


def _add_evaluate_args(sub: argparse.ArgumentParser) -> None:
    sproc = sub.add_argument_group("sproc arguments")
    sproc.add_argument("--function-name", required=True)
    sproc.add_argument("--test-table", required=True)
    sproc.add_argument("--input-columns", nargs="+", required=True)
    sproc.add_argument("--label-column", required=True)
    sproc.add_argument("--metric-name", required=True)
    sproc.add_argument("--model-name", required=True)
    sproc.add_argument(
        "--sample-size",
        type=nullable_int,
        required=True,
        help="Number of rows to evaluate, or 'none' for all",
    )
    sproc.add_argument(
        "--results-table",
        type=nullable_str,
        required=True,
        help="Table for per-row results, or 'none' to skip",
    )
    sproc.add_argument(
        "--metric-options",
        type=nullable_str,
        required=True,
        help="JSON string for metric options, or 'none'",
    )
    sproc.add_argument("--max-length", type=int, default=500)
    sproc.add_argument(
        "--custom-metric-udf",
        type=nullable_str,
        required=True,
        help="Fully qualified UDF name, or 'none'",
    )
    sproc.add_argument(
        "--run-id",
        type=nullable_str,
        required=True,
        help="External tracking ID, or 'none' for auto-generated",
    )

    async_grp = sub.add_argument_group("async execution")
    async_grp.add_argument(
        "--async",
        dest="async_mode",
        action="store_true",
        help="Run via Snowflake Task instead of synchronously",
    )
    async_grp.add_argument("--warehouse", default=None, help="Required for --async")
    async_grp.add_argument("--timeout-minutes", type=int, default=240)


def _run_evaluate(args: argparse.Namespace, exec_fn) -> dict:
    if args.async_mode:
        create_sql, execute_sql_str, run_id = eval_build_async_sql(args)
        log(f"Creating and executing Task {run_id} ...")
        exec_fn(create_sql)
        exec_fn(execute_sql_str)
        return {
            "status": "submitted",
            "run_id": run_id,
            "task": f"{args.database}.{args.schema}.{run_id}",
        }

    sql = eval_build_sync_sql(args)
    log("Executing evaluation ...")
    rows = exec_fn(sql)

    score = rows[0][0] if rows else None
    result: dict = {
        "status": "success",
        "score": score,
        "metric": args.metric_name,
        "function": args.function_name,
    }
    if args.results_table:
        result["results_table"] = args.results_table
    if args.run_id:
        result["run_id"] = args.run_id
    return result


# =========================================================================
# Optimize
# =========================================================================


def _opt_build_call(args: argparse.Namespace) -> str:
    """Build ``CALL OPTIMIZE_AI_FUNCTION(...)`` with actual argument values."""
    params = [
        _sql_varchar(args.function_name),
        _sql_varchar(args.training_table),
        _sql_varchar(args.label_column),
        _sql_array(args.input_columns),
        _sql_varchar(args.metric_name),
        _sql_array(args.models),
        _sql_varchar(args.reflection_model),
        _sql_varchar(args.test_table),
        _sql_varchar(args.auto_budget),
        _sql_varchar(args.tracking_table),
        _sql_varchar(args.results_table),
        _sql_float(args.validation_fraction),
        _sql_float(args.temperature),
        _sql_int(args.max_tokens),
        _sql_variant_json(args.metric_options),
        _sql_varchar(args.custom_metric_udf),
        _sql_bool(args.enable_detailed_tracking),
        _sql_varchar(args.run_id),
        _sql_varchar(args.aggregation_metric),
    ]
    joined = ",\n    ".join(params)
    return f"CALL OPTIMIZE_AI_FUNCTION(\n    {joined}\n)"


def opt_build_sync_sql(args: argparse.Namespace) -> str:
    anon_def = render_sproc_sql(
        "optimize",
        args.database,
        args.schema,
        args.stage,
        anonymous=True,
        inline=args.inline,
    )
    return f"{anon_def}\n{_opt_build_call(args)};"


def opt_build_async_sql(
    args: argparse.Namespace,
) -> tuple[str, str, str]:
    """Return (create_task_sql, execute_task_sql, run_id)."""
    return _build_async_sql(args, "optimize", "ai_func_opt_", _opt_build_call)


def _add_optimize_args(sub: argparse.ArgumentParser) -> None:
    sproc = sub.add_argument_group("sproc arguments (required)")
    sproc.add_argument("--function-name", required=True)
    sproc.add_argument("--training-table", required=True)
    sproc.add_argument("--label-column", required=True)
    sproc.add_argument("--input-columns", nargs="+", required=True)
    sproc.add_argument("--metric-name", required=True)
    sproc.add_argument("--models", nargs="+", required=True)
    sproc.add_argument("--reflection-model", required=True)

    opt = sub.add_argument_group("sproc arguments (optional — use 'none' for NULL)")
    opt.add_argument(
        "--test-table",
        type=nullable_str,
        required=True,
        help="Held-out test table, or 'none' to use training table",
    )
    opt.add_argument("--auto-budget", required=True, help="light, medium, or heavy")
    opt.add_argument(
        "--tracking-table",
        type=nullable_str,
        required=True,
        help="Table to save candidate history, or 'none'",
    )
    opt.add_argument(
        "--results-table",
        type=nullable_str,
        required=True,
        help="Table to save final results, or 'none'",
    )
    opt.add_argument("--validation-fraction", type=float, required=True)
    opt.add_argument("--temperature", type=float, required=True)
    opt.add_argument("--max-tokens", type=int, required=True)
    opt.add_argument(
        "--metric-options",
        type=nullable_str,
        required=True,
        help="JSON string for metric options, or 'none'",
    )
    opt.add_argument(
        "--custom-metric-udf",
        type=nullable_str,
        required=True,
        help="Fully qualified UDF name, or 'none'",
    )
    opt.add_argument(
        "--enable-detailed-tracking",
        type=bool_str,
        required=True,
        help="'true' to enable verbose logging, or 'false'",
    )
    opt.add_argument(
        "--run-id",
        type=nullable_str,
        required=True,
        help="External tracking ID, or 'none' for auto-generated",
    )
    opt.add_argument(
        "--aggregation-metric",
        type=nullable_str,
        required=True,
        help="'accuracy' or 'f1-score', or 'none' to disable",
    )

    async_grp = sub.add_argument_group("async execution")
    async_grp.add_argument(
        "--async",
        dest="async_mode",
        action="store_true",
        help="Run via Snowflake Task instead of synchronously",
    )
    async_grp.add_argument("--warehouse", default=None, help="Required for --async")
    async_grp.add_argument("--timeout-minutes", type=int, default=240)


def _run_optimize(args: argparse.Namespace, exec_fn) -> dict:
    if args.async_mode:
        create_sql, execute_sql_str, run_id = opt_build_async_sql(args)
        log(f"Creating and executing Task {run_id} ...")
        exec_fn(create_sql)
        exec_fn(execute_sql_str)
        return {
            "status": "submitted",
            "run_id": run_id,
            "task": f"{args.database}.{args.schema}.{run_id}",
        }

    sql = opt_build_sync_sql(args)
    log("Executing optimization ...")
    rows = exec_fn(sql)

    raw = rows[0][0] if rows else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            pass

    result: dict = {
        "status": "success",
        "result": raw,
        "function": args.function_name,
    }
    if args.tracking_table:
        result["tracking_table"] = args.tracking_table
    if args.run_id:
        result["run_id"] = args.run_id
    return result


# =========================================================================
# Synthetic data
# =========================================================================


def _synth_build_call(args: argparse.Namespace) -> str:
    """Build ``CALL GENERATE_SYNTHETIC_DATA(...)`` with actual argument values."""
    params = [
        _sql_varchar(args.task_description),
        _sql_varchar(args.output_table),
        _sql_array(args.input_columns),
        _sql_varchar(args.model),
        _sql_int(args.num_examples),
        _sql_varchar(args.source_table),
        _sql_varchar(args.function_name),
        _sql_variant_json(args.output_schema),
        _sql_int(args.max_source_rows),
    ]
    joined = ",\n    ".join(params)
    return f"CALL GENERATE_SYNTHETIC_DATA(\n    {joined}\n)"


def synth_build_sync_sql(args: argparse.Namespace) -> str:
    anon_def = render_sproc_sql(
        "synthetic",
        args.database,
        args.schema,
        args.stage,
        anonymous=True,
        inline=args.inline,
    )
    return f"{anon_def}\n{_synth_build_call(args)};"


def _add_synthetic_args(sub: argparse.ArgumentParser) -> None:
    sproc = sub.add_argument_group("sproc arguments")
    sproc.add_argument(
        "--task-description", required=True, help="Description of the AI function task"
    )
    sproc.add_argument(
        "--output-table", required=True, help="Fully qualified output table name"
    )
    sproc.add_argument(
        "--input-columns",
        nargs="+",
        required=True,
        help="Input column names in argument order",
    )
    sproc.add_argument(
        "--model",
        type=nullable_str,
        required=True,
        help="Cortex model name, or 'none' to use default (claude-opus-4-6)",
    )
    sproc.add_argument(
        "--num-examples",
        type=int,
        required=True,
        help="Total number of examples to generate (default: 50)",
    )
    sproc.add_argument(
        "--source-table",
        type=nullable_str,
        required=True,
        help="Input-only table for pseudo-label mode, or 'none'",
    )
    sproc.add_argument(
        "--function-name",
        type=nullable_str,
        required=True,
        help="Existing function to infer output schema from, or 'none'",
    )
    sproc.add_argument(
        "--output-schema",
        type=nullable_str,
        required=True,
        help="JSON output schema string, or 'none'",
    )
    sproc.add_argument(
        "--max-source-rows",
        type=nullable_int,
        required=True,
        help="Row cap for pseudo-label preview, or 'none' for all rows",
    )


def _run_synthetic(args: argparse.Namespace, exec_fn) -> dict:
    sql = synth_build_sync_sql(args)
    log("Executing synthetic data generation ...")
    rows = exec_fn(sql)

    raw = rows[0][0] if rows else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            pass

    return {
        "status": "success",
        "result": raw,
        "output_table": args.output_table,
    }


# =========================================================================
# CLI
# =========================================================================

_SUBCOMMANDS = {
    "evaluate": (_add_evaluate_args, _run_evaluate),
    "optimize": (_add_optimize_args, _run_optimize),
    "synthetic": (_add_synthetic_args, _run_synthetic),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run AI Function Studio stored procedures.",
    )
    subs = p.add_subparsers(dest="command", required=True)

    for name, (add_args_fn, _) in _SUBCOMMANDS.items():
        sub = subs.add_parser(name)
        infra = sub.add_argument_group("infrastructure")
        infra.add_argument("--database", required=True)
        infra.add_argument("--schema", required=True)
        infra.add_argument("--stage", default="", help="Bare stage name (only needed without --inline)")
        infra.add_argument(
            "--inline", action="store_true", default=True,
            help="Embed Python source in SPROC body (default: True)",
        )
        infra.add_argument(
            "--connection", required=True, help="Snowflake connection name"
        )
        add_args_fn(sub)

    return p.parse_args()


# =========================================================================
# Main
# =========================================================================


def main() -> None:
    args = parse_args()

    _, run_fn = _SUBCOMMANDS[args.command]

    if getattr(args, "async_mode", False) and not args.warehouse:
        log("Error: --async requires --warehouse")
        sys.exit(1)

    coco_session_id = os.environ.get("CORTEX_SESSION_ID")
    session = create_session_from_connection(args.connection)

    try:
        log("Setting session context...")
        session.sql(f"USE DATABASE {args.database}").collect()
        session.sql(f"USE SCHEMA {args.database}.{args.schema}").collect()

        def _exec(sql: str) -> list:
            if coco_session_id:
                with customai_query_tag_logging(
                    session,
                    coco_session_id,
                    tag_prefix=COCO_SESSION_TAG_PREFIX,
                ):
                    return session.sql(sql).collect()
            return session.sql(sql).collect()

        result = run_fn(args, _exec)
        print(json.dumps(result, indent=2))

    finally:
        session.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"ERROR: {exc}")
        print(json.dumps({"status": "error", "message": str(exc)}, indent=2))
        sys.exit(1)
