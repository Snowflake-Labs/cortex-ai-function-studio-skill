# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Create custom AI functions in Snowflake.

Supports two modes:

1. **Flagged mode** (--database, --schema, etc.): Generates a standard AI_COMPLETE
   UDF from individual CLI flags specifying system prompt, user prompt template,
   inputs and outputs. Supports both text-only and multimodal (image/document)
   inputs. Uses response_format for structured JSON output when outputs are
   specified.

2. **Raw SQL mode** (--sql-body): Executes an agent-authored CREATE FUNCTION DDL
   directly. Supports arbitrary SQL UDF bodies (not limited to a single AI_COMPLETE
   call). The script handles execution, object tagging, and query tag logging.

Example usage:
    # Flagged mode — standard AI_COMPLETE UDF
    PYTHONPATH=<SKILL_DIR>/src uv run python create_udf.py \\
        --database MY_DB --schema MY_SCHEMA --function-name MY_FUNC \\
        --model claude-sonnet-4-5 --system-prompt 'Classify sentiment' \\
        --user-prompt-template '{TEXT}' \\
        --inputs '[{"name":"TEXT","sql_type":"VARCHAR"}]' \\
        --outputs '[{"name":"label","json_type":"string","description":"sentiment"}]' \\
        --execute --connection my_conn

    # Raw SQL mode — arbitrary UDF body
    PYTHONPATH=<SKILL_DIR>/src uv run python create_udf.py \\
        --sql-body 'CREATE FUNCTION ...' --execute --connection my_conn
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from textwrap import dedent
from typing import Any

from custom_ai_function_utils import (
    COCO_SESSION_TAG_PREFIX,
    apply_file_prompt_prefix_workaround,
    customai_query_tag_logging,
    create_session_from_connection,
)

CUSTOM_AI_FUNCTION_OBJECT_TAG = "CUSTOM_AI_FUNCTION_UDF_TAG"


def _resolve_warehouse(session, warehouse: str | None) -> str:
    if warehouse:
        return warehouse
    rows = session.sql("SELECT CURRENT_WAREHOUSE()").collect()
    wh = rows[0][0] if rows and rows[0] else None
    if not wh:
        raise ValueError(
            "No active warehouse (pass --warehouse or set one in your connection config)"
        )
    return wh


@dataclass
class InputParam:
    """Represents a function input parameter."""

    name: str
    sql_type: str
    is_file_path: bool = False


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
    stage_name: str | None = None

    @property
    def is_multimodal(self) -> bool:
        return any(inp.is_file_path for inp in self.inputs)


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


COMMENT_PREFIX = "[CORTEX AI FUNC STUDIO] "


def _normalize_comment(text: str, *, max_len: int = 1000) -> str:
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return ""
    prefix_len = len(COMMENT_PREFIX)
    effective_max = max_len - prefix_len
    if len(cleaned) > effective_max:
        cleaned = cleaned[: effective_max - 3].rstrip() + "..."
    return cleaned


def _build_multimodal_prompt_args(
    template: str,
    inputs: list[InputParam],
    stage_name: str,
) -> tuple[str, list[str]]:
    """Build the PROMPT() template string and argument list for multimodal calls.

    Translates {COLUMN_NAME} placeholders in the user template to {0}, {1}, ...
    positional placeholders expected by Snowflake's PROMPT() function, and builds
    the corresponding argument expressions (TO_FILE for file inputs, column name
    for text inputs).

    Returns:
        (prompt_template, prompt_args) where prompt_template has {0}/{1}/... and
        prompt_args are SQL expressions for each positional slot.
    """
    input_lookup = {inp.name.upper(): inp for inp in inputs}
    placeholders = re.findall(r"\{(\w+)\}", template)

    seen: dict[str, int] = {}
    prompt_args: list[str] = []
    translated_template = template

    for placeholder in placeholders:
        upper = placeholder.upper()
        if upper in seen:
            continue
        idx = len(prompt_args)
        seen[upper] = idx

        inp = input_lookup.get(upper)
        if inp and inp.is_file_path:
            prompt_args.append(
                f"TO_FILE('{escape_sql_string(stage_name)}', {inp.name})"
            )
        elif inp:
            prompt_args.append(_sql_to_varchar(inp.name, inp.sql_type))
        else:
            prompt_args.append(placeholder.upper())

    for placeholder in re.findall(r"\{(\w+)\}", template):
        upper = placeholder.upper()
        idx = seen[upper]
        translated_template = translated_template.replace(
            f"{{{placeholder}}}", f"{{{idx}}}", 1
        )

    return translated_template, prompt_args


def _resolve_output_schema(
    outputs: list[OutputField],
) -> tuple[str, str, str | None]:
    """Compute return type, result suffix, and response_format expression.

    Returns:
        (return_type, result_suffix, response_format_expr)
        - result_suffix is appended to AI_COMPLETE() to extract/cast the result
    """
    if not outputs:
        return "VARCHAR", "::VARCHAR", None

    json_schema = build_json_schema(outputs)
    response_format_json = json.dumps({"type": "json", "schema": json_schema}, indent=4)
    response_format_expr = f"PARSE_JSON('{escape_sql_string(response_format_json)}')"

    if len(outputs) == 1:
        return_type = JSON_TO_SQL_TYPE.get(outputs[0].json_type, "VARCHAR")
        result_suffix = f":{outputs[0].name}::{return_type}"
    else:
        return_type = "VARIANT"
        result_suffix = ""

    return return_type, result_suffix, response_format_expr


def _resolve_multimodal_prompt_template_and_args(
    spec: UDFSpec,
) -> tuple[str, list[str]]:
    """Resolve the PROMPT template and argument list for multimodal UDFs."""
    has_template_placeholders = bool(
        re.findall(r"\{(\w+)\}", spec.user_prompt_template)
    )
    if has_template_placeholders:
        return _build_multimodal_prompt_args(
            spec.user_prompt_template,
            spec.inputs,
            spec.stage_name,
        )

    prompt_args = []
    for inp in spec.inputs:
        if inp.is_file_path:
            prompt_args.append(
                f"TO_FILE('{escape_sql_string(spec.stage_name)}', {inp.name})"
            )
        else:
            prompt_args.append(_sql_to_varchar(inp.name, inp.sql_type))

    input_refs = " ".join(f"{{{i}}}" for i in range(len(prompt_args)))
    return f"{input_refs} {spec.user_prompt_template}", prompt_args


def _prompt_arg_is_to_file_expression(prompt_arg: str | None) -> bool:
    """True when the SQL expression passed to PROMPT() starts with TO_FILE(...)."""
    return bool(prompt_arg and prompt_arg.lstrip().upper().startswith("TO_FILE("))


def _build_create_function_ddl(
    fqn: str,
    input_params: str,
    return_type: str,
    escaped_comment: str,
    body_expr: str,
) -> str:
    """Build the CREATE OR REPLACE FUNCTION DDL wrapper."""
    return dedent(f"""\
        CREATE OR REPLACE FUNCTION {fqn}({input_params})
        RETURNS {return_type}
        LANGUAGE SQL
        COMMENT = '{COMMENT_PREFIX}{escaped_comment}'
        AS
        $$
            {body_expr}
        $$;""")


def generate_multimodal_sql(spec: UDFSpec) -> str:
    """Generate CREATE FUNCTION DDL for a multimodal AI_COMPLETE UDF.

    Uses messages array with PROMPT() + TO_FILE() for file inputs loaded from
    a Snowflake stage. System and user prompts are separated into distinct
    messages, matching the text-only path structure.

    Important: Snowflake's PROMPT() interprets every {…} in the template string
    as a positional placeholder. Text containing literal braces must be passed
    as a PROMPT argument value, NOT embedded in the template.
    """
    assert spec.stage_name, "stage_name is required for multimodal UDFs"

    fqn = f"{spec.database}.{spec.schema}.{spec.function_name}"
    input_params = ", ".join(f"{inp.name} {inp.sql_type}" for inp in spec.inputs)
    comment = _normalize_comment(spec.function_intention)
    escaped_comment = escape_sql_string(comment)
    return_type, result_suffix, response_format_expr = _resolve_output_schema(
        spec.outputs
    )
    translated_template, prompt_args = _resolve_multimodal_prompt_template_and_args(
        spec
    )

    args_str = ",\n                        ".join(prompt_args)
    response_format_line = (
        f",\n            response_format=>{response_format_expr}"
        if response_format_expr
        else ""
    )

    prompt_template = apply_file_prompt_prefix_workaround(
        translated_template,
        first_prompt_arg_is_file=_prompt_arg_is_to_file_expression(
            prompt_args[0] if prompt_args else None
        ),
    )
    ai_complete_call = dedent(f"""\
        AI_COMPLETE(
            model=>'{escape_sql_string(spec.model)}',
            messages=>ARRAY_CONSTRUCT(
                OBJECT_CONSTRUCT(
                    'role', 'system',
                    'content', '{escape_sql_string(spec.system_prompt)}'
                ),
                OBJECT_CONSTRUCT(
                    'role', 'user',
                    'content', PROMPT(
                        '{escape_sql_string(prompt_template)}',
                        {args_str}
                    )
                )
            ){response_format_line}
        )""").strip()

    body_expr = f"{ai_complete_call}{result_suffix}"
    return _build_create_function_ddl(
        fqn, input_params, return_type, escaped_comment, body_expr
    )


def generate_sql(spec: UDFSpec) -> str:
    """Generate the complete CREATE FUNCTION SQL DDL.

    Routes to multimodal (PROMPT + TO_FILE) when file inputs are present,
    otherwise uses text-only (messages array).
    """
    if spec.is_multimodal:
        return generate_multimodal_sql(spec)
    return _generate_text_sql(spec)


def _generate_text_sql(spec: UDFSpec) -> str:
    """Generate CREATE FUNCTION DDL for a text-only AI_COMPLETE UDF."""
    fqn = f"{spec.database}.{spec.schema}.{spec.function_name}"
    input_params = ", ".join(f"{inp.name} {inp.sql_type}" for inp in spec.inputs)
    comment = _normalize_comment(spec.function_intention)
    escaped_comment = escape_sql_string(comment)
    return_type, result_suffix, response_format_expr = _resolve_output_schema(
        spec.outputs
    )

    user_prompt_sql = build_user_prompt_sql(spec.user_prompt_template, spec.inputs)

    response_format_line = (
        f",\n            response_format=>{response_format_expr}"
        if response_format_expr
        else ""
    )

    ai_complete_call = dedent(f"""\
        AI_COMPLETE(
            model=>'{escape_sql_string(spec.model)}',
            messages=>ARRAY_CONSTRUCT(
                OBJECT_CONSTRUCT(
                    'role', 'system',
                    'content', '{escape_sql_string(spec.system_prompt)}'
                ),
                OBJECT_CONSTRUCT(
                    'role', 'user',
                    'content', {user_prompt_sql}
                )
            ){response_format_line}
        )""").strip()

    body_expr = f"{ai_complete_call}{result_suffix}"
    return _build_create_function_ddl(
        fqn, input_params, return_type, escaped_comment, body_expr
    )


def generate_object_tag_alter(spec: UDFSpec, tag_value: str) -> str:
    fqn = f"{spec.database}.{spec.schema}.{spec.function_name}"
    input_params = ", ".join(inp.sql_type for inp in spec.inputs)

    sql = dedent(f"""
        ALTER FUNCTION {fqn}({input_params}) set TAG {CUSTOM_AI_FUNCTION_OBJECT_TAG}='{tag_value}'
    """)

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
        raw_sql_type = inp.get("sql_type", "VARCHAR").upper()
        # Accept sql_type "STAGE_FILE_PATH" as the canonical way to declare
        # a file-path input.  Normalise to VARCHAR (the actual SQL param type)
        # and set is_file_path automatically.  The legacy is_file_path boolean
        # is still honoured for backward compatibility.
        is_file_path = raw_sql_type == "STAGE_FILE_PATH" or inp.get(
            "is_file_path", False
        )
        sql_type = "VARCHAR" if raw_sql_type == "STAGE_FILE_PATH" else raw_sql_type
        inputs.append(
            InputParam(
                name=inp["name"].upper(),
                sql_type=sql_type,
                is_file_path=is_file_path,
            )
        )

    if not inputs:
        raise ValueError("At least one input parameter is required")

    has_file_inputs = any(inp.is_file_path for inp in inputs)
    stage_name = config.get("stage_name")
    if has_file_inputs and not stage_name:
        raise ValueError(
            "stage_name is required when any input has "
            "sql_type: STAGE_FILE_PATH (or is_file_path: true)"
        )

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
        model=config.get("model", "claude-sonnet-4-5"),
        inputs=inputs,
        outputs=outputs,
        system_prompt=config["system_prompt"],
        user_prompt_template=config["user_prompt_template"],
        stage_name=stage_name,
    )


def run_create_udf(spec, connection, warehouse):
    session = create_session_from_connection(connection)
    try:
        warehouse = _resolve_warehouse(session, warehouse)
        session.use_database(spec.database)
        session.use_schema(spec.schema)
        session.use_warehouse(warehouse)

        coco_session_id = os.environ.get("CORTEX_SESSION_ID")
        sql = generate_sql(spec)

        session.sql(
            f"CREATE TAG IF NOT EXISTS {CUSTOM_AI_FUNCTION_OBJECT_TAG}"
        ).collect()

        if coco_session_id:
            with customai_query_tag_logging(
                session,
                coco_session_id,
                tag_prefix=COCO_SESSION_TAG_PREFIX,
            ):
                session.sql(sql).collect()
                session.sql(generate_object_tag_alter(spec, coco_session_id)).collect()
        else:
            session.sql(sql).collect()
            session.sql(generate_object_tag_alter(spec, "NO_ID")).collect()
    finally:
        session.close()


# Regex pieces are split for readability/reviewability:
# - quoted identifier: "My Name" (double quotes escaped as "")
# - unquoted identifier: DB / SCHEMA / FUNC style word chars
# - identifier: either quoted or unquoted
# - fully qualified name: up to DB.SCHEMA.FUNCTION
_QUOTED_IDENTIFIER_RE = r'"(?:[^"]|"")+"'
_UNQUOTED_IDENTIFIER_RE = r"\w+"
_IDENTIFIER_RE = rf"(?:{_QUOTED_IDENTIFIER_RE}|{_UNQUOTED_IDENTIFIER_RE})"
_FQN_RE = rf"{_IDENTIFIER_RE}(?:\.{_IDENTIFIER_RE}){{0,2}}"
_CREATE_FUNCTION_RE = re.compile(
    rf"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+" rf"({_FQN_RE})\s*\(",
    re.IGNORECASE,
)
_PARAM_DECL_RE = re.compile(rf"\s*{_IDENTIFIER_RE}\s+(.+?)\s*$", re.DOTALL)


def _split_top_level_csv(text: str) -> list[str]:
    """Split by commas, ignoring commas inside parens or quoted identifiers."""
    parts: list[str] = []
    start = 0
    depth = 0
    in_quotes = False

    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            if in_quotes and i + 1 < len(text) and text[i + 1] == '"':
                i += 1
            else:
                in_quotes = not in_quotes
        elif not in_quotes:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                parts.append(text[start:i].strip())
                start = i + 1
        i += 1

    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_fqn_from_ddl(ddl: str) -> tuple[str, str]:
    """Extract the fully-qualified function name and param type signature from DDL.

    Returns:
        (fqn, param_types) where param_types is e.g. "VARCHAR, NUMBER".
    """
    m = _CREATE_FUNCTION_RE.search(ddl)
    if not m:
        raise ValueError(
            "Could not parse function name from DDL. "
            "Expected 'CREATE [OR REPLACE] FUNCTION <name>(...)'."
        )
    fqn = m.group(1)
    params_start = m.end() - 1  # points to opening "("
    depth = 0
    in_quotes = False
    params_end = -1

    i = params_start
    while i < len(ddl):
        ch = ddl[i]
        if ch == '"':
            if in_quotes and i + 1 < len(ddl) and ddl[i + 1] == '"':
                i += 1
            else:
                in_quotes = not in_quotes
        elif not in_quotes:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    params_end = i
                    break
        i += 1

    if params_end == -1:
        raise ValueError(
            "Could not parse function signature from DDL. "
            "Expected balanced parentheses in CREATE FUNCTION parameters."
        )

    raw_params = ddl[params_start + 1 : params_end].strip()
    if not raw_params:
        return fqn, ""

    param_types = []
    for decl in _split_top_level_csv(raw_params):
        type_match = _PARAM_DECL_RE.match(decl)
        if type_match:
            param_types.append(type_match.group(1).strip())
        else:
            param_types.append(decl.strip())

    param_types = ", ".join(param_types)
    return fqn, param_types


def run_raw_sql(ddl: str, connection: str, warehouse: str | None) -> None:
    """Execute an agent-authored CREATE FUNCTION DDL with tagging and logging."""
    fqn, param_types = _parse_fqn_from_ddl(ddl)

    parts = fqn.rsplit(".", 2)
    if len(parts) < 3:
        raise ValueError(
            f"Function name '{fqn}' must be fully qualified (DB.SCHEMA.FUNC)."
        )
    database, schema = parts[0], parts[1]

    session = create_session_from_connection(connection)
    try:
        warehouse = _resolve_warehouse(session, warehouse)
        session.use_database(database)
        session.use_schema(schema)
        session.use_warehouse(warehouse)

        coco_session_id = os.environ.get("CORTEX_SESSION_ID")

        session.sql(
            f"CREATE TAG IF NOT EXISTS {CUSTOM_AI_FUNCTION_OBJECT_TAG}"
        ).collect()

        tag_sql = (
            f"ALTER FUNCTION {fqn}({param_types}) "
            f"SET TAG {CUSTOM_AI_FUNCTION_OBJECT_TAG}"
            f"='{coco_session_id or 'NO_ID'}'"
        )

        if coco_session_id:
            with customai_query_tag_logging(
                session,
                coco_session_id,
                tag_prefix=COCO_SESSION_TAG_PREFIX,
            ):
                session.sql(ddl).collect()
                session.sql(tag_sql).collect()
        else:
            session.sql(ddl).collect()
            session.sql(tag_sql).collect()
    finally:
        session.close()


def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Create custom AI functions in Snowflake.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            Examples:
                # Flagged mode — generate and execute
                PYTHONPATH=<SKILL_DIR>/src uv run python create_udf.py \\
                    --database DB --schema SCH --function-name MY_FUNC \\
                    --system-prompt 'Classify sentiment' --user-prompt-template '{TEXT}' \\
                    --inputs '[{"name":"TEXT","sql_type":"VARCHAR"}]' \\
                    --outputs '[{"name":"label","json_type":"string","description":"sentiment"}]' \\
                    --execute --connection my_conn

                # Raw SQL — execute agent-authored DDL directly
                PYTHONPATH=<SKILL_DIR>/src uv run python create_udf.py \\
                    --sql-body 'CREATE FUNCTION ...' --execute --connection my_conn
            """),
    )
    parser.add_argument(
        "--sql-body",
        type=str,
        dest="sql_body",
        help="Complete CREATE FUNCTION DDL to execute directly (requires --execute)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the CREATE FUNCTION statement in Snowflake",
    )
    parser.add_argument(
        "--connection",
        help="Snowflake connection name (required with --execute)",
    )
    parser.add_argument(
        "--warehouse",
        help="Warehouse for session context (defaults to connection's current warehouse)",
    )

    # --- Flagged arguments ---
    flag_group = parser.add_argument_group(
        "flagged config",
        "Individual arguments for specifying UDF configuration. "
        "Provide --database to activate this mode.",
    )
    flag_group.add_argument("--database", type=str, help="Target Snowflake database")
    flag_group.add_argument("--schema", type=str, help="Target Snowflake schema")
    flag_group.add_argument(
        "--function-name", type=str, dest="function_name", help="Name for the UDF"
    )
    flag_group.add_argument(
        "--function-intention",
        type=str,
        dest="function_intention",
        default="",
        help="One-line description of the function's purpose",
    )
    flag_group.add_argument(
        "--model",
        type=str,
        dest="flag_model",
        default="claude-sonnet-4-5",
        help="Cortex model name (default: claude-sonnet-4-5)",
    )
    flag_group.add_argument(
        "--system-prompt", type=str, dest="system_prompt", help="System prompt text"
    )
    flag_group.add_argument(
        "--user-prompt-template",
        type=str,
        dest="user_prompt_template",
        help="User prompt template with {PLACEHOLDER} syntax",
    )
    flag_group.add_argument(
        "--inputs",
        type=str,
        dest="inputs_json",
        help='JSON array of input specs, e.g. \'[{"name":"TEXT","sql_type":"VARCHAR"}]\'',
    )
    flag_group.add_argument(
        "--outputs",
        type=str,
        dest="outputs_json",
        help='JSON array of output specs, e.g. \'[{"name":"label","json_type":"string","description":"..."}]\'',
    )
    flag_group.add_argument(
        "--stage-name",
        type=str,
        dest="stage_name",
        help="Snowflake stage name (required for multimodal/file inputs)",
    )

    args = parser.parse_args()

    if args.sql_body:
        if not args.execute:
            print("Error: --sql-body requires --execute", file=sys.stderr)
            sys.exit(1)
        if not args.connection:
            print("Error: --execute requires --connection", file=sys.stderr)
            sys.exit(1)
        try:
            run_raw_sql(args.sql_body, args.connection, args.warehouse)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    config = None

    if args.database is not None:
        # Build config dict from individual flags
        missing = []
        for field_name, attr in [
            ("--schema", "schema"),
            ("--function-name", "function_name"),
            ("--system-prompt", "system_prompt"),
            ("--user-prompt-template", "user_prompt_template"),
            ("--inputs", "inputs_json"),
            ("--outputs", "outputs_json"),
        ]:
            if getattr(args, attr) is None:
                missing.append(field_name)
        if missing:
            print(
                f"Error: flagged mode requires: {', '.join(missing)}",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            inputs = json.loads(args.inputs_json)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in --inputs: {e}", file=sys.stderr)
            sys.exit(1)

        try:
            outputs = json.loads(args.outputs_json)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in --outputs: {e}", file=sys.stderr)
            sys.exit(1)

        config = {
            "database": args.database,
            "schema": args.schema,
            "function_name": args.function_name,
            "function_intention": args.function_intention,
            "model": args.flag_model,
            "system_prompt": args.system_prompt,
            "user_prompt_template": args.user_prompt_template,
            "inputs": inputs,
            "outputs": outputs,
        }
        if args.stage_name:
            config["stage_name"] = args.stage_name

    else:
        parser.print_help()
        print(
            "\nError: --database (flagged mode) or --sql-body is required",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        spec = parse_config(config)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.execute:
        if not args.connection:
            print("Error: --execute requires --connection", file=sys.stderr)
            sys.exit(1)
        run_create_udf(spec, args.connection, args.warehouse)
    else:
        print(generate_sql(spec))


if __name__ == "__main__":
    main()
