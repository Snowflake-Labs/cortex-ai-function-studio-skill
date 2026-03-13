# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Generate SQL DDL for custom AI functions using Snowflake AI_COMPLETE.

This script accepts a JSON configuration and generates properly escaped SQL DDL
with structured output format. It is designed to be called by the create SKILL
workflow after gathering all required information interactively.

Example usage:
    # Via JSON file
    uv run python create_udf.py --config config.json

    # Via stdin
    echo '{"database": "MY_DB", ...}' | uv run python create_udf.py --config -

    # Via inline JSON argument
    uv run python create_udf.py --json '{"database": "MY_DB", ...}'

Expected JSON structure:
{
    "database": "MY_DB",
    "schema": "MY_SCHEMA",
    "function_name": "MY_FUNCTION",
    "function_intention": "One-line description of what the function should do",
    "model": "llama3.1-70b",
    "inputs": [
        {"name": "INPUT_COL", "sql_type": "VARCHAR"}
    ],
    "outputs": [
        {"name": "output_field", "json_type": "string", "description": "desc"}
    ],
    "system_prompt": "system prompt",
    "user_prompt_template": "user prompt"
}
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from textwrap import dedent
from typing import Any


@dataclass
class InputParam:
    """Represents a function input parameter."""

    name: str
    sql_type: str


@dataclass
class OutputField:
    """Represents an output field in the JSON schema."""

    name: str
    json_type: str
    description: str


@dataclass
class UDFSpec:
    """Complete specification for a UDF."""

    database: str
    schema: str
    function_name: str
    model: str
    function_intention: str = ""
    inputs: list[InputParam] = field(default_factory=list)
    outputs: list[OutputField] = field(default_factory=list)
    system_prompt: str = ""
    user_prompt_template: str = ""


JSON_TO_SQL_TYPE = {
    "string": "VARCHAR",
    "number": "FLOAT",
    "integer": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "VARIANT",
    "object": "VARIANT",
}


def escape_sql_string(s: str) -> str:
    """Escape a string for use in SQL single quotes.

    In Snowflake SQL, single quotes are escaped by doubling them.

    Args:
        s: The string to escape.

    Returns:
        The escaped string (without surrounding quotes).
    """
    return s.replace("'", "''")


def _sql_to_varchar(param_name: str, sql_type: str) -> str:
    """Convert a SQL parameter to VARCHAR for string concatenation.

    Args:
        param_name: The parameter name (column name).
        sql_type: The SQL type of the parameter.

    Returns:
        SQL expression that yields a VARCHAR.
    """
    sql_type_upper = sql_type.upper()
    if sql_type_upper in ("VARCHAR", "STRING", "TEXT", "CHAR"):
        return param_name
    if sql_type_upper == "ARRAY" or sql_type_upper.startswith("ARRAY"):
        return f"ARRAY_TO_STRING({param_name}, ', ')"
    return f"TO_VARCHAR({param_name})"


def build_user_prompt_sql(template: str, inputs: list[InputParam]) -> str:
    """Build the SQL expression for the user prompt with input concatenation.

    Args:
        template: The user prompt template with {PLACEHOLDER} syntax.
        inputs: List of input parameters.

    Returns:
        SQL expression that concatenates the prompt parts with inputs.
    """
    input_types = {inp.name.upper(): inp.sql_type for inp in inputs}

    import re

    placeholders = re.findall(r"\{(\w+)\}", template)

    if not placeholders:
        return f"'{escape_sql_string(template)}'"

    parts = []
    remaining = template

    for placeholder in placeholders:
        pattern = f"{{{placeholder}}}"
        if pattern in remaining:
            before, remaining = remaining.split(pattern, 1)
            if before:
                parts.append(f"'{escape_sql_string(before)}'")
            param_name = placeholder.upper()
            sql_type = input_types.get(param_name, "VARCHAR")
            parts.append(_sql_to_varchar(param_name, sql_type))

    if remaining:
        parts.append(f"'{escape_sql_string(remaining)}'")

    return " || ".join(parts)


def build_json_schema(outputs: list[OutputField]) -> dict[str, Any]:
    """Build the JSON schema for structured output.

    Args:
        outputs: List of output fields.

    Returns:
        JSON schema dictionary.
    """
    properties = {}
    required = []

    for out in outputs:
        prop: dict[str, Any] = {
            "type": out.json_type,
            "description": out.description,
        }
        if out.json_type == "array":
            prop["items"] = {"type": "string"}
        properties[out.name] = prop
        required.append(out.name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _normalize_comment(text: str, *, max_len: int = 1000) -> str:
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return ""
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3].rstrip() + "..."
    return cleaned


def generate_sql(spec: UDFSpec) -> str:
    """Generate the complete CREATE FUNCTION SQL DDL.

    Args:
        spec: The UDF specification.

    Returns:
        Complete SQL DDL string.
    """
    fqn = f"{spec.database}.{spec.schema}.{spec.function_name}"
    user_input_params = ", ".join(f"{inp.name} {inp.sql_type}" for inp in spec.inputs)
    # Add MODEL_NAME and SYSTEM_PROMPT as final parameters with defaults (allows runtime override)
    input_params = (
        f"{user_input_params}, "
        f"MODEL_NAME VARCHAR DEFAULT '{spec.model}', "
        f"SYSTEM_PROMPT VARCHAR DEFAULT NULL"
    )
    is_single_output = len(spec.outputs) == 1
    if is_single_output:
        return_type = JSON_TO_SQL_TYPE.get(spec.outputs[0].json_type, "VARCHAR")
        output_accessor = f":{spec.outputs[0].name}::{return_type}"
    else:
        return_type = "VARIANT"
        output_accessor = ""

    json_schema = build_json_schema(spec.outputs)
    response_format_json = json.dumps({"type": "json", "schema": json_schema}, indent=4)

    user_prompt_sql = build_user_prompt_sql(spec.user_prompt_template, spec.inputs)
    comment = _normalize_comment(spec.function_intention)
    escaped_comment = escape_sql_string(comment)

    response_format_expr = f"PARSE_JSON('{escape_sql_string(response_format_json)}')"

    ai_complete_call = dedent(f"""\
        AI_COMPLETE(
            model=>MODEL_NAME,
            messages=>ARRAY_CONSTRUCT(
                OBJECT_CONSTRUCT(
                    'role', 'system',
                    'content', COALESCE(SYSTEM_PROMPT, '{escape_sql_string(spec.system_prompt)}')
                ),
                OBJECT_CONSTRUCT(
                    'role', 'user',
                    'content', {user_prompt_sql}
                )
            ),
            response_format=>{response_format_expr}
        )""").strip()

    sql = dedent(f"""\
        CREATE OR REPLACE FUNCTION {fqn}({input_params})
        RETURNS {return_type}
        LANGUAGE SQL
        COMMENT = '{escaped_comment}'
        AS
        $$
            {ai_complete_call}{output_accessor}
        $$;""")

    return sql


def parse_config(config: dict[str, Any]) -> UDFSpec:
    """Parse JSON configuration into a UDFSpec.

    Args:
        config: Dictionary with UDF configuration.

    Returns:
        UDFSpec object.

    Raises:
        ValueError: If required fields are missing or invalid.
    """
    required = [
        "database",
        "schema",
        "function_name",
        "inputs",
        "outputs",
        "system_prompt",
        "user_prompt_template",
    ]
    missing = [f for f in required if f not in config]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    inputs = []
    for inp in config["inputs"]:
        if "name" not in inp:
            raise ValueError("Each input must have a 'name' field")
        inputs.append(
            InputParam(
                name=inp["name"].upper(),
                sql_type=inp.get("sql_type", "VARCHAR").upper(),
            )
        )

    if not inputs:
        raise ValueError("At least one input parameter is required")

    outputs = []
    for out in config["outputs"]:
        if "name" not in out:
            raise ValueError("Each output must have a 'name' field")
        outputs.append(
            OutputField(
                name=out["name"],
                json_type=out.get("json_type", "string").lower(),
                description=out.get("description", ""),
            )
        )

    if not outputs:
        raise ValueError("At least one output field is required")

    function_intention = config.get("function_intention", "") or ""

    return UDFSpec(
        database=config["database"].upper(),
        schema=config["schema"].upper(),
        function_name=config["function_name"].upper(),
        function_intention=str(function_intention),
        model=config.get("model", "llama3.1-70b"),
        inputs=inputs,
        outputs=outputs,
        system_prompt=config["system_prompt"],
        user_prompt_template=config["user_prompt_template"],
    )


def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Generate SQL DDL for custom AI functions (non-interactive).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            Examples:
                # From JSON file
                uv run python create_udf.py --config config.json

                # From stdin
                cat config.json | uv run python create_udf.py --config -

                # Inline JSON
                uv run python create_udf.py --json '{"database": "DB", "schema": "S", ...}'

            The script outputs the complete CREATE FUNCTION SQL statement to stdout.
            """),
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to JSON config file, or '-' to read from stdin",
    )
    parser.add_argument(
        "--json",
        type=str,
        dest="json_str",
        help="Inline JSON configuration string",
    )

    args = parser.parse_args()

    config = None

    if args.json_str:
        try:
            config = json.loads(args.json_str)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in --json argument: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.config:
        try:
            if args.config == "-":
                config = json.load(sys.stdin)
            else:
                with open(args.config) as f:
                    config = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in config file: {e}", file=sys.stderr)
            sys.exit(1)
        except FileNotFoundError:
            print(f"Error: Config file not found: {args.config}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()
        print("\nError: Either --config or --json is required", file=sys.stderr)
        sys.exit(1)

    try:
        spec = parse_config(config)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    sql = generate_sql(spec)

    print(sql)


if __name__ == "__main__":
    main()
