# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Synthetic Data and pseudo-label generation for AI function workflows.

This file is designed to be embedded directly into Snowflake Python SPROCs.
It supports:
1) Fully synthetic data generation from task descriptions
2) Pseudo-labeling existing input-only tables
"""

import ast
import json
import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from textwrap import dedent
from typing import TypeVar

from custom_ai_function_utils import with_custom_ai_function_query_tag, RobustAIComplete
from snowflake.snowpark import Session
from snowflake.snowpark.functions import col, parse_json


_IDENTIFIER_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_MISSING = object()
PSEUDO_LABEL_MODEL = "claude-opus-4-6"
_T = TypeVar("_T")


def _sf_null_to_none(val: _T) -> _T | None:
    """Convert Snowflake sqlNullWrapper (and other null-like) values to Python None."""
    if val is None:
        return None
    if type(val).__name__ == "sqlNullWrapper":
        return None
    return val


def _normalize_identifier(name: str) -> str:
    """Normalize and validate a Snowflake identifier (unquoted).

    We keep this intentionally strict to prevent SQL injection via dynamic
    column names. Identifiers are uppercased and must match [A-Z_][A-Z0-9_]*.
    """
    if not isinstance(name, str):
        raise TypeError(f"Identifier must be a string, got {type(name).__name__}")
    normalized = name.strip().upper()
    if not normalized:
        raise ValueError("Identifier cannot be empty")
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(
            f"Invalid identifier: {name!r}. Use only letters, digits, and underscores; "
            "start with a letter or underscore."
        )
    return normalized


def _normalize_columns(columns: object | None, *, name: str) -> list[str]:
    """Normalize required column identifiers from ARRAY/list/CSV inputs."""
    if columns is None:
        raise ValueError(f"{name} is required and cannot be NULL")
    if isinstance(columns, (list, tuple)):
        cols = list(columns)
    elif isinstance(columns, str):
        # Allow passing a comma-separated string for convenience.
        cols = [c.strip() for c in columns.split(",") if c.strip()]
    else:
        raise TypeError(
            f"{name} must be an ARRAY/list or comma-separated string; got {type(columns).__name__}"
        )

    if not cols:
        raise ValueError(f"{name} is required and cannot be empty")

    normalized: list[str] = []
    seen: set[str] = set()
    for c in cols:
        col_name = _normalize_identifier(str(c))
        if col_name in seen:
            continue
        normalized.append(col_name)
        seen.add(col_name)

    if not normalized:
        raise ValueError(f"{name} is required and cannot be empty")
    return normalized


def _normalize_input_columns(input_columns: object | None) -> list[str]:
    """Normalize INPUT_COLUMNS argument into a validated list of identifiers."""
    return _normalize_columns(input_columns, name="INPUT_COLUMNS")


# Helper utilities for FUNCTION_NAME -> GET_DDL -> response_format.schema inference.
def _extract_balanced_parenthesized_content(text: str) -> str:
    """Helper for function-DDL schema inference.

    Extract the argument type list from SHOW FUNCTIONS output so
    `_extract_response_schema_from_function()` can call GET_DDL with the exact
    resolved function signature.
    """
    start = text.find("(")
    if start < 0:
        raise ValueError(f"Could not parse function signature: {text}")

    depth = 0
    content_start = -1
    for idx in range(start, len(text)):
        ch = text[idx]
        if ch == "(":
            depth += 1
            if depth == 1:
                content_start = idx + 1
        elif ch == ")":
            if depth == 0:
                raise ValueError(f"Could not parse function signature: {text}")
            depth -= 1
            if depth == 0 and content_start >= 0:
                return text[content_start:idx]

    raise ValueError(f"Could not parse function signature: {text}")


def _extract_balanced_object_literal(text: str, start_idx: int) -> str | None:
    """Helper for function-DDL schema inference.

    Extract the `response_format` object literal from function DDL while
    ignoring braces that appear inside quoted SQL/Python strings.
    """
    if start_idx < 0 or start_idx >= len(text) or text[start_idx] != "{":
        return None

    i = start_idx
    depth = 0
    in_single = False
    in_double = False
    escape = False

    while i < len(text):
        ch = text[i]

        if in_single:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == "'":
                # SQL-escaped apostrophe inside single-quoted string.
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 1
                else:
                    in_single = False
            i += 1
            continue

        if in_double:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_idx : i + 1]
            if depth < 0:
                return None

        i += 1

    return None


def _normalize_sql_single_quotes_for_python(text: str) -> str:
    """Helper for function-DDL schema inference.

    Convert SQL-style single-quote escaping from GET_DDL output into a form that
    `ast.literal_eval()` can safely parse when recovering `response_format`.
    """
    out: list[str] = []
    i = 0
    in_single = False
    in_double = False
    escape = False

    while i < len(text):
        ch = text[i]

        if in_single:
            if escape:
                out.append(ch)
                escape = False
                i += 1
                continue

            if ch == "\\":
                out.append(ch)
                escape = True
                i += 1
                continue

            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    out.append("\\'")
                    i += 2
                    continue
                out.append(ch)
                in_single = False
                i += 1
                continue

            out.append(ch)
            i += 1
            continue

        if in_double:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_double = False
            i += 1
            continue

        out.append(ch)
        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        i += 1

    return "".join(out)


def _extract_response_schema_from_function(
    session: Session, function_name: str
) -> dict[str, object]:
    """Infer output schema from an existing function signature/DDL.

    Purpose:
        Provide output-shape inference when users follow the function-first path
        (function already created, then data generation/pseudo-labeling).

    Notes:
        This is an optional convenience path. Data-first flows should still work
        with explicit OUTPUT_SCHEMA without FUNCTION_NAME.
    """
    fn = str(function_name).strip()
    if not fn:
        raise ValueError("FUNCTION_NAME is required for pseudo-label schema inference.")

    # Handle optional signature in function_name, e.g. DB.SCHEMA.FUNC(VARCHAR).
    base_name = fn
    provided_signature = None
    if "(" in fn:
        paren_idx = fn.index("(")
        base_name = fn[:paren_idx]
        provided_signature = fn[paren_idx:]

    parts = base_name.split(".")
    if len(parts) != 3:
        raise ValueError(
            "FUNCTION_NAME must be fully qualified (DB.SCHEMA.FUNC or DB.SCHEMA.FUNC(...))."
        )
    db, schema, func = (_normalize_identifier(p) for p in parts)

    rows = session.sql(
        f"SHOW FUNCTIONS LIKE '{func}' IN SCHEMA {db}.{schema}"
    ).collect()
    if not rows:
        raise ValueError(f"Function not found: {function_name}")

    # Resolve overload/signature.
    if provided_signature and len(rows) > 1:
        target_sig = f"{func}{provided_signature}"
        matching_row = None
        for row in rows:
            if row["arguments"].upper() == target_sig.upper():
                matching_row = row
                break
        if matching_row is None:
            raise ValueError(f"No overload matches FUNCTION_NAME: {function_name}")
        arguments = matching_row["arguments"]
    else:
        arguments = rows[0]["arguments"]

    # Use the helper above to recover the exact parameter list from SHOW
    # FUNCTIONS output before requesting the function DDL.
    param_types = _extract_balanced_parenthesized_content(str(arguments))
    full_signature = f"{db}.{schema}.{func}({param_types})"
    ddl_rows = session.sql(
        f"SELECT GET_DDL('FUNCTION', '{full_signature}') AS DDL"
    ).collect()
    if not ddl_rows:
        raise ValueError(f"Could not retrieve DDL for function: {full_signature}")

    ddl = str(ddl_rows[0]["DDL"])
    rf_match = re.search(r"""['"]response_format['"]\s*:\s*""", ddl)
    if not rf_match:
        raise ValueError(
            "Could not infer output schema: function DDL has no response_format. "
            "Recreate function with structured CORTEX.COMPLETE response_format."
        )

    start = rf_match.end()
    while start < len(ddl) and ddl[start].isspace():
        start += 1
    if start >= len(ddl) or ddl[start] != "{":
        raise ValueError(
            "Could not parse response_format object from function DDL. "
            "Recreate function with structured response_format."
        )

    # The helpers above exist specifically for this DDL inference path: GET_DDL
    # returns SQL-escaped text, so we first isolate the object literal and then
    # normalize quoting before parsing it back into Python objects.
    snippet = _extract_balanced_object_literal(ddl, start)
    if not snippet:
        raise ValueError(
            "Could not parse balanced response_format object in function DDL."
        )

    response_format = None
    try:
        response_format = ast.literal_eval(snippet)
    except (ValueError, SyntaxError):
        try:
            normalized_snippet = _normalize_sql_single_quotes_for_python(snippet)
            response_format = ast.literal_eval(normalized_snippet)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(
                f"Failed to parse response_format from function DDL: {exc}"
            ) from exc

    if not isinstance(response_format, dict):
        raise ValueError("response_format in function DDL is not an object.")

    schema_obj = response_format.get("schema")
    if not isinstance(schema_obj, dict):
        raise ValueError(
            "response_format.schema missing in function DDL. "
            "Recreate function with structured response_format.schema."
        )

    props = schema_obj.get("properties")
    if not isinstance(props, dict) or not props:
        raise ValueError(
            "response_format.schema.properties missing/empty in function DDL."
        )

    return schema_obj


def _resolve_output_spec(
    *,
    output_schema: object | None,
    session: Session,
    function_name: str | None,
) -> tuple[list[str], dict[str, dict[str, object]]]:
    """Resolve the canonical output shape for synthetic/pseudo-label generation.

    Accepted sources (in precedence order):
    1) Explicit `OUTPUT_SCHEMA`
    2) Inferred schema from `FUNCTION_NAME` response_format

    Returns:
        A tuple of:
        - output column names in normalized identifier form
        - per-column property objects (empty dict when no typed schema is available)

    This function centralizes validation so downstream generation logic can assume
    a consistent, non-empty output contract regardless of which input path users took.
    """
    schema_obj = output_schema
    if isinstance(output_schema, str):
        try:
            schema_obj = RobustAIComplete.parse_ai_complete_payload(
                output_schema, allow_text_recovery=False
            )
        except json.JSONDecodeError as exc:
            raise ValueError(f"OUTPUT_SCHEMA is not valid JSON: {exc}") from exc
    if schema_obj is not None and not isinstance(schema_obj, dict):
        raise ValueError(
            f"OUTPUT_SCHEMA must parse to an object, got {type(schema_obj).__name__}"
        )
    if schema_obj is None and function_name:
        schema_obj = _extract_response_schema_from_function(session, function_name)

    if schema_obj is None:
        raise ValueError(
            "Output schema is required. Provide OUTPUT_SCHEMA or "
            "FUNCTION_NAME with a structured response_format."
        )

    props = schema_obj.get("properties")
    if not isinstance(props, dict) or not props:
        raise ValueError("Output schema must include non-empty properties.")
    raw_props: dict[str, object] = {
        _normalize_identifier(str(k)): v for k, v in props.items() if str(k).strip()
    }

    output_cols = list(raw_props.keys())
    if not output_cols:
        raise ValueError(
            "Output schema must contain at least one property. Provide OUTPUT_SCHEMA "
            "or FUNCTION_NAME with a structured response_format."
        )

    output_properties: dict[str, dict[str, object]] = {}
    for col_name in output_cols:
        prop = raw_props[col_name]
        if isinstance(prop, Mapping):
            output_properties[col_name] = dict(prop)
        elif isinstance(prop, str) and prop.strip():
            output_properties[col_name] = {"type": prop.strip()}
        else:
            output_properties[col_name] = {}

    return output_cols, output_properties


def _coerce_int(name: str, value: object, *, minimum: int | None = None) -> int:
    """Coerce numeric SPROC inputs once with consistent validation errors."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if minimum is not None and parsed < minimum:
        if minimum == 1:
            raise ValueError(f"{name} must be > 0.")
        raise ValueError(f"{name} must be >= {minimum}.")
    return parsed


def _coerce_optional_int(
    name: str, value: object | None, *, minimum: int | None = None
) -> int | None:
    if value is None:
        return None
    return _coerce_int(name, value, minimum=minimum)


def _normalize_optional_text(value: object | None) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _resolve_model_name(model: object | None, *, pseudo_mode: bool) -> str:
    model_name = _normalize_optional_text(model)
    if model_name:
        return model_name
    if pseudo_mode:
        return PSEUDO_LABEL_MODEL
    raise ValueError("MODEL is required for synthetic generation mode.")


def _prepare_generation_request(
    *,
    session: Session,
    input_columns: object,
    model: object | None,
    source_table: object | None,
    function_name: object | None,
    output_schema: object | None,
    num_examples: object,
    easy_pct: object,
    medium_pct: object,
    batch_size: object,
    max_source_rows: object | None,
) -> dict[str, object]:
    """Normalize and validate all request preconditions in one place."""
    model = _sf_null_to_none(model)
    source_table = _sf_null_to_none(source_table)
    function_name = _sf_null_to_none(function_name)
    output_schema = _sf_null_to_none(output_schema)
    max_source_rows = _sf_null_to_none(max_source_rows)

    source_table_str = _normalize_optional_text(source_table)
    is_pseudo_mode = bool(source_table_str)
    function_name_str = _normalize_optional_text(function_name)
    resolved_model = _resolve_model_name(model, pseudo_mode=is_pseudo_mode)

    batch_size_int = _coerce_int("BATCH_SIZE", batch_size, minimum=1)
    num_examples_int = _coerce_int("NUM_EXAMPLES", num_examples)
    easy_pct_int = _coerce_int("EASY_PCT", easy_pct)
    medium_pct_int = _coerce_int("MEDIUM_PCT", medium_pct)
    max_source_rows_int = _coerce_optional_int(
        "MAX_SOURCE_ROWS", max_source_rows, minimum=1
    )

    for pct_name, pct_value in (
        ("EASY_PCT", easy_pct_int),
        ("MEDIUM_PCT", medium_pct_int),
    ):
        if pct_value < 0 or pct_value > 100:
            raise ValueError(f"{pct_name} must be between 0 and 100.")

    input_cols = _normalize_input_columns(input_columns)
    reserved = {"ID", "EXPECTED", "DIFFICULTY"}
    collisions = sorted([c for c in input_cols if c in reserved])
    if collisions:
        raise ValueError(
            f"INPUT_COLUMNS contains reserved column name(s): {', '.join(collisions)}"
        )

    output_cols, output_properties = _resolve_output_spec(
        output_schema=output_schema,
        session=session,
        function_name=function_name_str or None,
    )

    request: dict[str, object] = {
        "mode": "pseudo_label" if is_pseudo_mode else "synthetic",
        "model": resolved_model,
        "input_cols": input_cols,
        "output_cols": output_cols,
        "output_properties": output_properties,
        "batch_size": batch_size_int,
        "num_examples": num_examples_int,
        "easy_pct": easy_pct_int,
        "medium_pct": medium_pct_int,
        "source_table": source_table_str,
        "max_source_rows": max_source_rows_int,
    }

    if is_pseudo_mode:
        return request

    if num_examples_int <= 0:
        raise ValueError("NUM_EXAMPLES must be > 0.")
    hard_pct = 100 - easy_pct_int - medium_pct_int
    if hard_pct < 0:
        raise ValueError("easy_pct + medium_pct cannot exceed 100")
    request["hard_pct"] = hard_pct
    return request


class ExampleNormalizer:
    """Validate and normalize LLM examples into strict input/output shapes."""

    def __init__(self, input_cols: list[str], output_cols: list[str]) -> None:
        self.input_cols = input_cols
        self.output_cols = output_cols

    def _coerce_input_value(self, val: object) -> str:
        if val is None:
            return ""
        if isinstance(val, (dict, list, tuple)):
            return json.dumps(val, ensure_ascii=False)
        return str(val)

    def _get_case_insensitive(self, raw_dict: dict, key: str) -> object:
        if key in raw_dict:
            return raw_dict[key]
        for k, v in raw_dict.items():
            if isinstance(k, str) and k.strip().upper() == key:
                return v
        return _MISSING

    def _normalize_inputs(self, ex: dict) -> tuple[dict[str, str] | None, str | None]:
        raw_inputs = ex.get("inputs")
        if raw_inputs is None:
            return None, "inputs value missing"
        if not isinstance(raw_inputs, dict):
            return None, "inputs must be a JSON object"

        out: dict[str, str] = {}
        missing: list[str] = []
        for col_name in self.input_cols:
            val = self._get_case_insensitive(raw_inputs, col_name)
            if val is _MISSING:
                missing.append(col_name)
                continue
            out[col_name] = self._coerce_input_value(val)
        if missing:
            return None, f"missing input keys: {', '.join(missing)}"
        return out, None

    def _normalize_outputs(
        self, ex: dict
    ) -> tuple[dict[str, object] | None, str | None]:
        """Normalize outputs nested under the "outputs" key."""
        raw_outputs = ex.get("outputs")
        if raw_outputs is None:
            return None, "outputs value missing"
        if not isinstance(raw_outputs, dict):
            return None, "outputs must be a JSON object"

        out: dict[str, object] = {}
        missing: list[str] = []
        for col_name in self.output_cols:
            val = self._get_case_insensitive(raw_outputs, col_name)
            if val is _MISSING:
                missing.append(col_name)
                continue
            if isinstance(val, tuple):
                out[col_name] = list(val)
            elif isinstance(val, Mapping):
                out[col_name] = dict(val)
            else:
                out[col_name] = val
        if missing:
            return None, f"missing output keys: {', '.join(missing)}"
        return out, None

    def normalize_examples(self, parsed: list) -> list[dict]:
        examples: list[dict] = []
        invalid_counts = {"non_dict": 0, "invalid_inputs": 0, "invalid_outputs": 0}
        first_error = None

        for ex in parsed:
            if not isinstance(ex, dict):
                invalid_counts["non_dict"] += 1
                if first_error is None:
                    first_error = "example is not an object"
                continue

            inputs, input_err = self._normalize_inputs(ex)
            if input_err:
                invalid_counts["invalid_inputs"] += 1
                if first_error is None:
                    first_error = f"inputs: {input_err}"
                continue

            outputs, outputs_err = self._normalize_outputs(ex)
            if outputs_err:
                invalid_counts["invalid_outputs"] += 1
                if first_error is None:
                    first_error = f"outputs: {outputs_err}"
                continue

            examples.append(
                {
                    "inputs": inputs,
                    "outputs": outputs,
                    "category": ex.get("category")
                    if isinstance(ex.get("category"), str)
                    else "",
                }
            )

        if not examples:
            details = ", ".join(
                f"{key}={count}" for key, count in invalid_counts.items() if count
            )
            detail_msg = details or "no valid examples"
            if first_error:
                detail_msg = f"{detail_msg}. First error: {first_error}"
            raise ValueError(
                f"Model returned {len(parsed)} items but 0 were valid ({detail_msg}). "
                f"Check input columns {self.input_cols} and output columns {self.output_cols}."
            )

        return examples


def _generate_batch(
    session: Session,
    task_description: str,
    batch_size: int,
    batch_idx: int,
    model: str,
    difficulty: str = "medium",
    *,
    input_columns: list[str],
    output_keys: list[str],
    output_properties: dict[str, dict[str, object]] | None = None,
) -> list[dict]:
    """Generate a single batch of synthetic examples at a specific difficulty.

    Args:
        session: Snowpark session
        task_description: Description of the AI function task
        batch_size: Number of examples to generate in this batch
        batch_idx: Current batch index (for diversity hints)
        model: Cortex model name to use for generation
        difficulty: Target difficulty level ("easy", "medium", or "hard")

    Returns:
        List of example dicts
    """
    input_cols = input_columns
    output_cols = output_keys
    output_props = output_properties or {col_name: {} for col_name in output_cols}

    difficulty_guidance = {
        "easy": "Generate straightforward, common cases with clear inputs and obvious expected outputs. These should be simple examples that any basic implementation should handle correctly.",
        "medium": "Generate moderately complex cases that require some reasoning or handling of unusual and creative scenarios. Include cases with multiple valid interpretations or edge conditions.",
        "hard": "Generate challenging cases with ambiguous inputs, edge cases, special characters, multi-step reasoning, or scenarios that might trip up naive implementations.",
    }

    diversity_hints = [
        "Focus on realistic production-like examples.",
        "Include varied formatting and structure.",
        "Include domain-specific terminology.",
        "Include examples with varying input lengths.",
    ]
    hint = diversity_hints[batch_idx % len(diversity_hints)]

    diff_guidance = difficulty_guidance.get(difficulty, difficulty_guidance["medium"])

    col_list = ", ".join(input_cols)
    col_pairs = ", ".join([f'"{c}": "..."' for c in input_cols])
    input_properties: dict[str, object] = {c: {"type": "string"} for c in input_cols}
    output_properties_schema: dict[str, object] = {
        c: (
            dict(output_props.get(c, {}))
            if isinstance(output_props.get(c, {}), Mapping)
            else {}
        )
        for c in output_cols
    }
    output_shape = ", ".join(output_cols)
    output_instructions = (
        '- "outputs": a JSON object with exactly these keys: ' f"{output_shape}"
    )
    out_pairs = ", ".join([f'"{c}": "..."' for c in output_cols])
    example_payload = f'{{"inputs": {{{col_pairs}}}, "outputs": {{{out_pairs}}}}}'

    input_instructions = dedent(f"""\
        Each example must include:
        - "inputs": a JSON object with exactly these keys: {col_list}
          (each value is a string; keep each under 200 chars)
        {output_instructions}
        - Input values must be nested under the "inputs" key (do NOT use top-level keys or "input").
        - Output values must be nested under the "outputs" key (do NOT use "expected").

        Keys are case-sensitive; use the keys exactly as shown above.
        """).strip()

    json_mode_instructions = dedent(f"""\
        Return a JSON object with key "examples", where "examples" is a JSON array.
        Return ONLY valid JSON, no markdown.

        Example format:
        {{"examples": [{example_payload}]}}
        """).strip()

    base_prompt = dedent(f"""\
        Generate exactly {batch_size} UNIQUE test examples for this AI function.
        ALL examples must be {difficulty.upper()} difficulty.

        Function intention: {task_description}

        Difficulty guidance ({difficulty}): {diff_guidance}

        Additional guidance: {hint}

        {input_instructions}

        Make examples diverse and different from typical examples.
        """).strip()

    def _build_prompt(*, include_json_instructions: bool) -> str:
        if not include_json_instructions:
            return base_prompt
        return f"{base_prompt}\n\n{json_mode_instructions}"

    # JSON schema for strict JSON mode output validation.
    # Keep this schema minimal to maximize yield; validate exact columns in Python.
    # Include additionalProperties=false to improve compatibility with strict
    # OpenAI-style schema validation in some backends.
    response_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "examples": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "inputs": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": input_properties,
                            "required": input_cols,
                        },
                        "outputs": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": output_properties_schema,
                            "required": output_cols,
                        },
                    },
                    "required": ["inputs", "outputs"],
                },
            }
        },
        "required": ["examples"],
    }
    max_tokens = 8192
    temperature = 0.8

    parsed = RobustAIComplete.run_ai_complete_with_json_fallback(
        session=session,
        model=model,
        primary_prompt=_build_prompt(include_json_instructions=False),
        fallback_prompt=_build_prompt(include_json_instructions=True),
        response_schema=response_schema,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if parsed is None:
        return []

    if isinstance(parsed, dict) and "examples" in parsed:
        parsed = parsed.get("examples")

    if not isinstance(parsed, list):
        raise ValueError(
            "Expected JSON object with 'examples' array, got "
            f"{type(parsed).__name__}"
        )

    normalizer = ExampleNormalizer(input_cols, output_cols)
    return normalizer.normalize_examples(parsed)


def _generate_examples_for_difficulty(
    session: Session,
    task_description: str,
    num_examples: int,
    difficulty: str,
    model: str,
    batch_size: int,
    *,
    input_columns: list[str],
    output_keys: list[str],
    output_properties: dict[str, dict[str, object]] | None = None,
) -> tuple[list[dict], list[str]]:
    """Generate examples for a specified difficulty level.

    Args:
        session: Snowpark session
        task_description: Description of the AI function task
        num_examples: Number of examples to generate
        difficulty: Target difficulty level
        model: Cortex model name to use for generation
        batch_size: Maximum examples per batch

    Returns:
        Tuple of (examples list, errors list)
    """
    examples: list[dict] = []
    errors: list[str] = []

    # Generate in batches, allowing multiple retries across batches.
    # We cap total model calls at ~3x the number of batches required.
    required_batches = max(1, (num_examples + batch_size - 1) // batch_size)
    max_calls = max(3, required_batches * 3)

    call_idx = 0
    while len(examples) < num_examples and call_idx < max_calls:
        remaining = num_examples - len(examples)
        current_batch_size = min(batch_size, remaining)

        try:
            batch_examples = _generate_batch(
                session=session,
                task_description=task_description,
                batch_size=current_batch_size,
                batch_idx=call_idx,
                model=model,
                difficulty=difficulty,
                input_columns=input_columns,
                output_keys=output_keys,
                output_properties=output_properties,
            )
        except Exception as e:
            errors.append(f"{difficulty} call {call_idx + 1} error: {str(e)}")
            call_idx += 1
            continue

        if not batch_examples:
            errors.append(f"{difficulty} call {call_idx + 1} returned 0 examples")
            call_idx += 1
            continue

        for ex in batch_examples:
            ex["difficulty"] = difficulty
        examples.extend(batch_examples)
        call_idx += 1

    if len(examples) < num_examples:
        raise RuntimeError(
            f"Failed to generate {num_examples} {difficulty} examples after {call_idx} call(s) "
            f"(generated {len(examples)}). Errors: {errors}"
        )

    return examples[:num_examples], errors


def _create_output_table(
    session: Session, output_table: str, input_cols: list[str]
) -> None:
    """Create or replace output table with the canonical labeled schema."""
    input_col_ddl = ",\n            ".join([f'"{c}" VARCHAR' for c in input_cols])
    session.sql(f"""
        CREATE OR REPLACE TABLE {output_table} (
            ID INT AUTOINCREMENT,
            {input_col_ddl},
            EXPECTED VARIANT,
            DIFFICULTY VARCHAR
        )
    """).collect()


def _insert_examples(
    session: Session,
    output_table: str,
    input_cols: list[str],
    output_cols: list[str],
    examples: list[dict],
) -> dict[str, int]:
    """Insert normalized examples in batches and return difficulty counts."""
    if not examples:
        return {}

    difficulty_counts: dict[str, int] = {}
    insert_rows: list[list[object]] = []
    for example in examples:
        raw_inputs = example.get("inputs")
        inputs: dict[str, object] = raw_inputs if isinstance(raw_inputs, dict) else {}
        raw_outputs = example.get("outputs")
        output_vals: dict[str, object] = (
            raw_outputs if isinstance(raw_outputs, dict) else {}
        )
        difficulty = str(example.get("difficulty", "medium"))

        row_values: list[object] = [str(inputs.get(col_name, "")) for col_name in input_cols]
        expected_obj = {col_name: output_vals.get(col_name) for col_name in output_cols}
        expected_json = json.dumps(expected_obj, ensure_ascii=False)
        row_values.append(expected_json)
        row_values.append(difficulty)
        insert_rows.append(row_values)

        diff_key = difficulty.lower()
        difficulty_counts[diff_key] = difficulty_counts.get(diff_key, 0) + 1

    source_schema = [*input_cols, "EXPECTED_JSON", "DIFFICULTY"]
    batch_size = 1000
    for start_idx in range(0, len(insert_rows), batch_size):
        chunk_rows = insert_rows[start_idx : start_idx + batch_size]
        chunk_df = session.create_dataframe(chunk_rows, schema=source_schema)
        payload_df = chunk_df.select(
            *[col(name) for name in input_cols],
            parse_json(col("EXPECTED_JSON")).alias("EXPECTED"),
            col("DIFFICULTY"),
        )
        payload_df.write.mode("append").save_as_table(output_table, column_order="name")

    return difficulty_counts


def _build_pseudo_label_prompt(
    task_description: str,
    inputs: dict[str, str],
    output_schema: dict[str, object],
) -> str:
    """Build a deterministic prompt for labeling one input row."""
    input_json = json.dumps(inputs, ensure_ascii=False)
    output_schema_json = json.dumps(output_schema, ensure_ascii=False, sort_keys=True)
    return dedent(f"""\
        You are generating expected labels for supervised evaluation.

        Task description:
        {task_description}

        Input row (JSON):
        {input_json}

        The JSON object MUST satisfy this output schema:
        {output_schema_json}
        """).strip()


def _pseudo_label_batch(
    session: Session,
    *,
    task_description: str,
    inputs_batch: list[dict[str, str]],
    model: str,
    output_cols: list[str],
    output_properties: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    """Pseudo-label one batch of input rows."""
    if not inputs_batch:
        return []

    response_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            col_name: (
                dict(output_properties.get(col_name, {}))
                if isinstance(output_properties.get(col_name, {}), Mapping)
                else {}
            )
            for col_name in output_cols
        },
        "required": output_cols,
    }
    normalizer = ExampleNormalizer(input_cols=[], output_cols=output_cols)
    outputs: list[dict[str, object]] = []

    for idx_val, inputs in enumerate(inputs_batch):
        prompt = _build_pseudo_label_prompt(task_description, inputs, response_schema)
        fallback_prompt = (
            f"{prompt}\n\n"
            "Return ONLY a valid JSON object. "
            "Do not include markdown, code fences, or extra text."
        )
        parsed = RobustAIComplete.run_ai_complete_with_json_fallback(
            session=session,
            model=model,
            primary_prompt=prompt,
            fallback_prompt=fallback_prompt,
            response_schema=response_schema,
            temperature=0.0,
            max_tokens=8192,
        )

        if parsed is None:
            raise ValueError(f"Model returned empty response for row index {idx_val}")
        if isinstance(parsed, dict) and isinstance(parsed.get("outputs"), dict):
            parsed = parsed["outputs"]
        if not isinstance(parsed, dict):
            raise ValueError(
                f"Expected JSON object output for row index {idx_val}, got {type(parsed).__name__}"
            )

        normalized = normalizer.normalize_examples([{"inputs": {}, "outputs": parsed}])[
            0
        ]["outputs"]
        outputs.append(normalized)

    return outputs


def _load_source_inputs(
    session: Session,
    *,
    source_table: str,
    input_cols: list[str],
    max_source_rows: int | None,
) -> list[dict[str, str]]:
    """Load input rows from source table for pseudo-labeling."""
    col_expr = ", ".join([f'"{col_name}"' for col_name in input_cols])
    query = f"SELECT {col_expr} FROM {source_table}"
    if max_source_rows is not None:
        query += f" LIMIT {int(max_source_rows)}"

    rows = session.sql(query).collect()
    inputs_list: list[dict[str, str]] = []
    for row in rows:
        if not hasattr(row, "asDict"):
            raise TypeError("Snowpark row object does not support asDict().")
        row_dict = row.asDict()
        normalized_row = {
            str(key).strip().upper(): value
            for key, value in row_dict.items()
            if isinstance(key, str) and str(key).strip()
        }
        item: dict[str, str] = {}
        for col_name in input_cols:
            val = normalized_row.get(col_name, _MISSING)
            if val is _MISSING:
                raise ValueError(
                    f"Source table {source_table} is missing required input column: {col_name}"
                )
            if val is None:
                item[col_name] = ""
            elif isinstance(val, (dict, list, tuple)):
                item[col_name] = json.dumps(val, ensure_ascii=False)
            else:
                item[col_name] = str(val)
        inputs_list.append(item)
    return inputs_list


def _generate_pseudo_labeled_examples(
    session: Session,
    *,
    task_description: str,
    source_table: str,
    input_cols: list[str],
    output_cols: list[str],
    output_properties: dict[str, dict[str, object]],
    batch_size: int,
    max_source_rows: int | None,
    model: str,
) -> list[dict]:
    """Generate pseudo labels for existing source rows."""
    source_inputs = _load_source_inputs(
        session,
        source_table=source_table,
        input_cols=input_cols,
        max_source_rows=max_source_rows,
    )
    if not source_inputs:
        return []

    all_examples: list[dict] = []
    effective_batch_size = max(1, min(batch_size, len(source_inputs)))

    for start in range(0, len(source_inputs), effective_batch_size):
        batch = source_inputs[start : start + effective_batch_size]
        batch_num = start // effective_batch_size + 1
        outputs = _pseudo_label_batch(
            session,
            task_description=task_description,
            inputs_batch=batch,
            model=model,
            output_cols=output_cols,
            output_properties=output_properties,
        )
        if len(outputs) != len(batch):
            raise RuntimeError(
                f"Output count mismatch in batch {batch_num}: "
                f"expected {len(batch)}, got {len(outputs)}"
            )
        for inputs, outputs_obj in zip(batch, outputs):
            all_examples.append(
                {"inputs": inputs, "outputs": outputs_obj, "difficulty": "pseudo"}
            )

    return all_examples


@with_custom_ai_function_query_tag("GEN_SYNTH_DATA")
def generate_synthetic_data(
    session: Session,
    task_description: str,
    output_table: str,
    input_columns: object,
    model: str | None = None,
    num_examples: int = 50,
    easy_pct: int = 50,
    medium_pct: int = 30,
    batch_size: int = 100,
    source_table: str | None = None,
    function_name: str | None = None,
    output_schema: object | None = None,
    max_source_rows: int | None = None,
) -> dict:
    """Generate synthetic data or pseudo labels and store in a Snowflake table.

    This is the main SPROC handler function.

    Args:
        session: Snowpark session
        task_description: Description of the AI function task
        output_table: Fully qualified table name for output
        input_columns: Input columns to generate/map
        model: Cortex model name (required for synthetic mode; optional in pseudo-label mode)
        num_examples: Total number of synthetic examples to generate
        easy_pct: Percentage of easy examples (0-100)
        medium_pct: Percentage of medium examples (0-100)
        batch_size: Maximum records per LLM call
        source_table: Optional source table for pseudo-label mode
        function_name: Optional function name used for output schema inference
        output_schema: Optional explicit JSON schema for outputs
        max_source_rows: Optional cap for pseudo-label rows (preview mode)

    Returns:
        Dict with generation statistics and mode metadata.
    """
    try:
        request = _prepare_generation_request(
            session=session,
            input_columns=input_columns,
            model=model,
            source_table=source_table,
            function_name=function_name,
            output_schema=output_schema,
            num_examples=num_examples,
            easy_pct=easy_pct,
            medium_pct=medium_pct,
            batch_size=batch_size,
            max_source_rows=max_source_rows,
        )

        input_cols = request["input_cols"]
        output_cols = request["output_cols"]
        output_properties = request["output_properties"]
        resolved_model = str(request["model"])
        batch_size = int(request["batch_size"])

        if request["mode"] == "pseudo_label":
            source_table_str = str(request["source_table"])
            max_source_rows_val = request["max_source_rows"]
            max_source_rows_int = (
                int(max_source_rows_val) if max_source_rows_val is not None else None
            )
            all_examples = _generate_pseudo_labeled_examples(
                session,
                task_description=task_description,
                source_table=source_table_str,
                input_cols=input_cols,
                output_cols=output_cols,
                output_properties=output_properties,
                batch_size=batch_size,
                max_source_rows=max_source_rows_int,
                model=resolved_model,
            )
            _create_output_table(session, output_table, input_cols)
            difficulty_counts = _insert_examples(
                session,
                output_table=output_table,
                input_cols=input_cols,
                output_cols=output_cols,
                examples=all_examples,
            )
            difficulty_counts.setdefault("pseudo", 0)
            return {
                "success": True,
                "mode": "pseudo_label",
                "model_used": resolved_model,
                "source_table": source_table_str,
                "output_table": output_table,
                "total_generated": len(all_examples),
                "input_columns": input_cols,
                "expected_keys": output_cols,
                "difficulty_distribution": difficulty_counts,
                "is_preview": max_source_rows_val is not None,
                "batch_errors": None,
            }

        # Synthetic generation mode.
        num_examples = int(request["num_examples"])
        easy_pct = int(request["easy_pct"])
        medium_pct = int(request["medium_pct"])

        target_easy = int(num_examples * easy_pct / 100)
        target_medium = int(num_examples * medium_pct / 100)
        target_hard = num_examples - target_easy - target_medium

        all_examples = []
        batch_errors = []
        effective_batch_size = min(batch_size, num_examples)

        difficulty_targets = [
            ("easy", target_easy),
            ("medium", target_medium),
            ("hard", target_hard),
        ]
        active_targets = [
            (difficulty, target_num_examples)
            for difficulty, target_num_examples in difficulty_targets
            if target_num_examples > 0
        ]

        if active_targets:
            # Difficulty levels are independent, so generate them concurrently.
            results_by_difficulty: dict[str, tuple[list[dict], list[str]]] = {}
            generation_errors: list[str] = []

            with ThreadPoolExecutor(
                max_workers=min(3, len(active_targets))
            ) as executor:
                future_to_difficulty = {
                    executor.submit(
                        _generate_examples_for_difficulty,
                        session=session,
                        task_description=task_description,
                        num_examples=target_num_examples,
                        difficulty=difficulty,
                        model=resolved_model,
                        batch_size=effective_batch_size,
                        input_columns=input_cols,
                        output_keys=output_cols,
                        output_properties=output_properties,
                    ): difficulty
                    for difficulty, target_num_examples in active_targets
                }

                for future in as_completed(future_to_difficulty):
                    difficulty = future_to_difficulty[future]
                    try:
                        examples, errors = future.result()
                        results_by_difficulty[difficulty] = (examples, errors)
                    except Exception as exc:
                        generation_errors.append(
                            f"{difficulty} generation failed: {exc}"
                        )

            if generation_errors:
                raise RuntimeError("; ".join(generation_errors))

            # Preserve stable difficulty ordering in downstream inserts/statistics.
            for difficulty, _ in difficulty_targets:
                result = results_by_difficulty.get(difficulty)
                if not result:
                    continue
                examples, errors = result
                all_examples.extend(examples)
                batch_errors.extend(errors)

        if not all_examples:
            return {
                "success": False,
                "error": "Failed to generate any examples",
                "batch_errors": batch_errors,
            }

        _create_output_table(session, output_table, input_cols)
        difficulty_counts = _insert_examples(
            session,
            output_table=output_table,
            input_cols=input_cols,
            output_cols=output_cols,
            examples=all_examples,
        )
        for key in ("easy", "medium", "hard"):
            difficulty_counts.setdefault(key, 0)

        return {
            "success": True,
            "mode": "synthetic",
            "model_used": resolved_model,
            "output_table": output_table,
            "total_generated": len(all_examples),
            "input_columns": input_cols,
            "expected_keys": output_cols,  # Keys within the EXPECTED VARIANT column
            "difficulty_distribution": difficulty_counts,
            "batch_errors": batch_errors if batch_errors else None,
        }
    except (TypeError, ValueError, RuntimeError) as exc:
        return {"success": False, "error": str(exc)}
