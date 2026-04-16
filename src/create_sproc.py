#!/usr/bin/env python3

# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Create Snowflake stored procedures for AI function operations.

This tool generates SQL for creating OPTIMIZE_AI_FUNCTION or EVALUATE_AI_FUNCTION or GENERATE_SYNTHETIC_DATA
stored procedures with the correct stage paths for IMPORTS and fully qualified procedure names.

Usage:
    # Print SQL to stdout
    PYTHONPATH=<SKILL_DIR>/src uv run python src/create_sproc.py optimize TEMP PUBLIC AI_FUNCTIONS

    # Write to file
    PYTHONPATH=<SKILL_DIR>/src uv run python src/create_sproc.py optimize TEMP PUBLIC AI_FUNCTIONS -o /tmp/sproc.sql

    # Execute directly
    PYTHONPATH=<SKILL_DIR>/src uv run python src/create_sproc.py optimize TEMP PUBLIC AI_FUNCTIONS --execute --connection MY_CONN
"""

import argparse
import os
import re
import sys
from pathlib import Path
from textwrap import dedent

import yaml
from jinja2 import Template

from custom_ai_function_utils import (
    COCO_SESSION_TAG_PREFIX,
    customai_query_tag_logging,
    create_session_from_connection,
)

SPROC_TEMPLATES = {
    "optimize": "optimize_sproc.sql.j2",
    "evaluate": "evaluate_sproc.sql.j2",
    "synthetic": "synthetic_data_sproc.sql.j2",
    "optimize_async": "optimize_async_sproc.sql.j2",
    "evaluate_async": "evaluate_async_sproc.sql.j2",
}


def validate_identifier(name: str, label: str) -> str:
    """Validate a Snowflake identifier.

    Args:
        name: The identifier to validate
        label: Description for error messages (e.g., "database", "schema", "stage_name")

    Returns:
        The validated identifier

    Raises:
        ValueError: If identifier format is invalid
    """
    pattern = r"^[A-Za-z_][A-Za-z0-9_$]*$"
    if not re.match(pattern, name):
        raise ValueError(
            f"Invalid {label}: '{name}'. "
            f"Must start with a letter or underscore and contain only alphanumeric characters, underscores, or $."
        )
    return name


def get_template_path(sproc_type: str) -> Path:
    """Get the path to the SQL template file."""
    template_file = SPROC_TEMPLATES.get(sproc_type)
    if not template_file:
        raise ValueError(
            f"Unknown SPROC type: '{sproc_type}'. Available: {list(SPROC_TEMPLATES.keys())}"
        )
    return Path(__file__).parent / template_file


SRC_DIR = Path(__file__).resolve().parent

_SPROC_CONFIG_CACHE: dict | None = None


def _load_sproc_config() -> dict:
    """Load sproc_config.yaml (cached)."""
    global _SPROC_CONFIG_CACHE
    if _SPROC_CONFIG_CACHE is None:
        config_path = SRC_DIR / "sproc_config.yaml"
        with open(config_path) as f:
            _SPROC_CONFIG_CACHE = yaml.safe_load(f)
    return _SPROC_CONFIG_CACHE


_INTER_FILE_IMPORT_RE = re.compile(
    r"^\s*from\s+(?:custom_ai_function_utils|metrics_core|snow_gepa_adapter"
    r"|snow_gepa_experiment|snow_gepa_optimize|snow_gepa_optimize_anything|snow_synthetic_data)\s+import\s+"
    r"(?:\([^)]*\)|[^\n]+)",
    re.MULTILINE,
)


def _build_inline_body(sproc_type: str) -> tuple[str, str]:
    """Read and concatenate Python sources for inline embedding.

    Returns:
        (python_body, handler_function_name)
    """
    config = _load_sproc_config()
    sproc_cfg = config.get(sproc_type)
    if sproc_cfg is None:
        raise ValueError(f"No inline config for SPROC type: {sproc_type}")

    parts: list[str] = []
    for filename in sproc_cfg["sources"]:
        source_path = SRC_DIR / filename
        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")
        content = source_path.read_text()
        content = _INTER_FILE_IMPORT_RE.sub("", content)
        parts.append(content)

    body = "\n".join(parts)
    # The inlined Python is wrapped in SQL AS $$ ... $$ delimiters.
    # custom_ai_function_utils.normalize_ddl_to_dollar_quoting() contains
    # literal "$$" in its code (e.g. `if "$$" in raw_ddl`), which would
    # prematurely close the SQL block.  Replacing $$ with \x24\x24 avoids
    # this — Python interprets \x24 as '$' at runtime, so the code
    # behaviour is unchanged.
    body = body.replace("$$", "\\x24\\x24")
    return body, sproc_cfg["handler"]


def render_sproc_sql(
    sproc_type: str,
    database: str,
    schema: str,
    stage_name: str = "",
    *,
    anonymous: bool = False,
    inline: bool = False,
) -> str:
    """Render the SPROC SQL with the correct stage path and fully qualified names.

    Args:
        sproc_type: Type of SPROC ('optimize', 'evaluate', or 'synthetic')
        database: Database name
        schema: Schema name
        stage_name: Stage name (required unless inline=True)
        anonymous: If True, output anonymous SPROC format
            (``WITH name AS PROCEDURE ... $$``).  The caller appends
            ``CALL name(args);`` with the actual arguments.
        inline: If True, embed Python source code directly in the SPROC
            body instead of using IMPORTS from a stage.

    Returns:
        Rendered SQL string
    """
    validate_identifier(database, "database")
    validate_identifier(schema, "schema")
    if not inline:
        validate_identifier(stage_name, "stage_name")

    template_path = get_template_path(sproc_type)
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    inline_body = ""
    inline_handler = ""
    if inline:
        inline_body, inline_handler = _build_inline_body(sproc_type)

    template_content = template_path.read_text()
    template = Template(template_content)

    rendered_sql = template.render(
        database=database,
        schema=schema,
        stage_name=stage_name,
        anonymous=anonymous,
        inline=inline,
        inline_body=inline_body,
        inline_handler=inline_handler,
    )

    return rendered_sql


def execute_sql(
    sql: str,
    connection_name: str,
    *,
    coco_session_id: str | None = None,
) -> None:
    """Execute SQL using Snowpark.

    When coco_session_id is provided, temporarily appends a query tag segment
    using the `customai_query_tag_logging` contextmanager.

    Args:
        sql: SQL to execute
        connection_name: Snowflake connection name
        coco_session_id: Cortex Code session id (from env CORTEX_SESSION_ID)
    """
    print(f"Connecting to Snowflake using connection: {connection_name}")
    session = create_session_from_connection(connection_name)

    try:
        if coco_session_id:
            with customai_query_tag_logging(
                session,
                coco_session_id,
                tag_prefix=COCO_SESSION_TAG_PREFIX,
            ):
                session.sql(sql).collect()
        else:
            session.sql(sql).collect()

        print("SPROC created successfully.")
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create Snowflake stored procedures for AI function operations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            Examples:
              # Print SQL to stdout
              %(prog)s optimize TEMP PUBLIC AI_FUNCTIONS

              # Write to file
              %(prog)s optimize TEMP PUBLIC AI_FUNCTIONS -o /tmp/sproc.sql

              # Execute directly
              %(prog)s optimize TEMP PUBLIC AI_FUNCTIONS --execute --connection MY_CONN
            """),
    )
    parser.add_argument(
        "sproc_type",
        choices=list(SPROC_TEMPLATES),
        help="Type of SPROC to create",
    )
    parser.add_argument(
        "database",
        help="Database name where the SPROC will be created",
    )
    parser.add_argument(
        "schema",
        help="Schema name where the SPROC will be created",
    )
    parser.add_argument(
        "stage_name",
        nargs="?",
        default="",
        help="Stage name for Python module imports (not required with --inline)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write SQL to file instead of stdout",
    )
    parser.add_argument(
        "--anonymous",
        action="store_true",
        help="Output anonymous SPROC definition (WITH...AS PROCEDURE) "
        "instead of CREATE PROCEDURE. The caller appends the CALL with arguments.",
    )
    parser.add_argument(
        "--inline",
        action="store_true",
        help="Embed Python source code directly in the SPROC body instead "
        "of using IMPORTS from a stage. No stage_name required.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the SQL directly in Snowflake",
    )
    parser.add_argument(
        "--connection",
        help="Snowflake connection name (required with --execute)",
    )

    args = parser.parse_args()

    if not args.inline and not args.stage_name:
        print("Error: stage_name is required unless --inline is used", file=sys.stderr)
        sys.exit(1)

    try:
        sql = render_sproc_sql(
            args.sproc_type,
            args.database,
            args.schema,
            args.stage_name,
            anonymous=args.anonymous,
            inline=args.inline,
        )
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.execute:
        if not args.connection:
            print(
                "Error: --execute requires --connection",
                file=sys.stderr,
            )
            sys.exit(1)

        coco_session_id = os.environ.get("CORTEX_SESSION_ID")
        execute_sql(
            sql,
            args.connection,
            coco_session_id=coco_session_id,
        )
    elif args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(sql)
        print(f"SQL written to: {args.output}")
    else:
        print(sql)
