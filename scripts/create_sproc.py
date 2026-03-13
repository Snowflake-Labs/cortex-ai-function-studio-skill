#!/usr/bin/env python3

# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Create Snowflake stored procedures for AI function operations.

This tool generates SQL for creating OPTIMIZE_AI_FUNCTION or EVALUATE_AI_FUNCTION or GENERATE_SYNTHETIC_DATA
stored procedures with the correct stage paths for IMPORTS and fully qualified procedure names.

Usage:
    # Print SQL to stdout
    uv run python scripts/create_sproc.py optimize TEMP PUBLIC AI_FUNCTIONS

    # Write to file
    uv run python scripts/create_sproc.py optimize TEMP PUBLIC AI_FUNCTIONS -o /tmp/sproc.sql

    # Execute directly
    uv run python scripts/create_sproc.py optimize TEMP PUBLIC AI_FUNCTIONS --execute --connection MY_CONN
"""

import argparse
import re
import sys
from pathlib import Path
from textwrap import dedent

from jinja2 import Template


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
    script_dir = Path(__file__).parent
    src_dir = script_dir.parent / "src"
    template_file = SPROC_TEMPLATES.get(sproc_type)
    if not template_file:
        raise ValueError(
            f"Unknown SPROC type: '{sproc_type}'. Available: {list(SPROC_TEMPLATES.keys())}"
        )
    return src_dir / template_file


def render_sproc_sql(
    sproc_type: str, database: str, schema: str, stage_name: str
) -> str:
    """Render the SPROC SQL with the correct stage path and fully qualified names.

    Args:
        sproc_type: Type of SPROC ('optimize', 'evaluate', or 'synthetic')
        database: Database name
        schema: Schema name
        stage_name: Stage name

    Returns:
        Rendered SQL string
    """
    validate_identifier(database, "database")
    validate_identifier(schema, "schema")
    validate_identifier(stage_name, "stage_name")

    template_path = get_template_path(sproc_type)
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    template_content = template_path.read_text()
    template = Template(template_content)

    rendered_sql = template.render(
        database=database,
        schema=schema,
        stage_name=stage_name,
    )

    return rendered_sql


def execute_sql(sql: str, connection_name: str) -> None:
    """Execute SQL using Snowpark.

    Args:
        sql: SQL to execute
        connection_name: Snowflake connection name
    """
    try:
        import snowflake.connector
    except ImportError:
        print(
            "Error: snowflake-connector-python is required for --execute",
            file=sys.stderr,
        )
        print("Install with: pip install snowflake-connector-python", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to Snowflake using connection: {connection_name}")
    conn = snowflake.connector.connect(connection_name=connection_name)

    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        print("SPROC created successfully.")
    finally:
        conn.close()


def main():
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
        help="Stage name for Python module imports",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write SQL to file instead of stdout",
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

    try:
        sql = render_sproc_sql(
            args.sproc_type, args.database, args.schema, args.stage_name
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
        execute_sql(sql, args.connection)
    elif args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(sql)
        print(f"SQL written to: {args.output}")
    else:
        print(sql)


if __name__ == "__main__":
    main()
