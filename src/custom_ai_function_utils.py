# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

from contextlib import contextmanager
from functools import wraps
import json
import re
from typing import Any, Callable, ParamSpec, TypeVar, cast

from snowflake.snowpark import Session
from snowflake.snowpark.functions import (
    array_construct,
    call_function,
    col,
    lit,
    object_construct,
    parse_json,
)


P = ParamSpec("P")
R = TypeVar("R")


CUSTOM_AI_FUNCTION_TAG_PREFIX = "__CUSTOM_AI_FUNCTION_LOG_"
COCO_SESSION_TAG_PREFIX = "__CUSTOM_AI_FUNCTION_COCO_SESSION_ID_"
TEMP_AI_FUNCTION_MAX_ATTEMPTS = 3


def _get_ai_sql_error_handling_value(session: Session) -> str | None:
    rows = session.sql(
        "SHOW PARAMETERS LIKE 'AI_SQL_ERROR_HANDLING_USE_FAIL_ON_ERROR' IN SESSION"
    ).collect()

    row_dict = rows[0].asDict()
    val = row_dict.get("value")
    return str(val) if val is not None else None


@contextmanager
def ai_sql_error_handling_use_fail_on_error_disabled_for_sproc(session: Session):
    """Set AI_SQL_ERROR_HANDLING_USE_FAIL_ON_ERROR=FALSE for the duration of a SPROC handler."""
    orig = _get_ai_sql_error_handling_value(session)

    session.sql(
        "ALTER SESSION SET AI_SQL_ERROR_HANDLING_USE_FAIL_ON_ERROR = FALSE"
    ).collect()

    try:
        yield session
    finally:
        # unset if none
        if orig is None or str(orig).strip() == "":
            session.sql(
                "ALTER SESSION UNSET AI_SQL_ERROR_HANDLING_USE_FAIL_ON_ERROR"
            ).collect()
        else:
            session.sql(
                f"ALTER SESSION SET AI_SQL_ERROR_HANDLING_USE_FAIL_ON_ERROR = {str(orig).strip().upper()}"
            ).collect()


def with_ai_sql_error_handling_use_fail_on_error_disabled_for_sproc() -> (
    Callable[[Callable[P, R]], Callable[P, R]]
):
    """
    Decorator: wrap a Snowpark-SPROC handler in the session param context.
    With this, temporary functions will return {value: T, error: string} (when used with TempAIFunction class)
    With permanent functions, this will make failures return null instead of failing entire query
    it is to note that with BCR, this will soon become default behaviour and decorator will soon be not needed
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(session: Session, *args: P.args, **kwargs: P.kwargs) -> R:
            with ai_sql_error_handling_use_fail_on_error_disabled_for_sproc(session):
                return func(session, *args, **kwargs)

        return cast(Callable[P, R], wrapper)

    return decorator


def create_session_from_connection(connection: str) -> Session:
    return Session.builder.config("connection_name", connection).create()


def build_temp_function_name(function_name: str, prefix: str) -> str:
    """Build a thread-safe temporary function name from a fully qualified function name.

    Args:
        function_name: Fully qualified function name (DB.SCHEMA.FUNC or
            DB.SCHEMA.FUNC(VARCHAR, ...)).
        prefix: Prefix for the temp function (e.g., ``"__OPT_TEMP"``
            or ``"__OPT_TEST"``).

    Returns:
        Fully qualified temp function name like ``DB.SCHEMA.__OPT_TEMP_FUNC_<tid>``.
    """
    import threading

    base_name = function_name.split("(")[0] if "(" in function_name else function_name
    parts = base_name.split(".")
    db, schema, func = parts[0], parts[1], parts[2]
    tid = threading.current_thread().ident or 0
    return f"{db}.{schema}.{prefix}_{func}_{tid}"


@contextmanager
def customai_query_tag_logging(
    session: Session,
    tag_suffix: str,
    *,
    tag_prefix: str = CUSTOM_AI_FUNCTION_TAG_PREFIX,
):
    original_tag = session.query_tag

    def _compose_next_tag() -> str:
        if not original_tag:
            return json.dumps({tag_prefix: tag_suffix}, separators=(",", ":"))

        string_tag = f"{original_tag}|{tag_prefix}{tag_suffix}"

        try:
            parsed = json.loads(original_tag)

            if isinstance(parsed, dict):
                parsed[tag_prefix] = tag_suffix
            elif isinstance(parsed, list):
                parsed.append(string_tag)
            else:
                return string_tag

            return json.dumps(parsed, separators=(",", ":"))
        except Exception:
            return string_tag

    try:
        session.query_tag = _compose_next_tag()
        yield session
    finally:
        session.query_tag = original_tag


def with_custom_ai_function_query_tag(tag_suffix: str):
    def decorator(func):
        @wraps(func)
        def wrapper(session, *args, **kwargs):
            with customai_query_tag_logging(session, tag_suffix):
                return func(session, *args, **kwargs)

        return wrapper

    return decorator


def patch_response_format_additional_properties(
    schema: object,
) -> object:
    """Recursively ensure `additionalProperties: false` is set on all object schemas."""

    def _patch(node: object) -> object:
        if isinstance(node, list):
            return [_patch(v) for v in node]
        if not isinstance(node, dict):
            return node

        patched: dict[str, Any] = {k: _patch(v) for k, v in node.items()}

        node_type = patched.get("type")
        is_object_type = node_type == "object" or (
            isinstance(node_type, list) and "object" in node_type
        )
        looks_like_object_schema = any(
            k in patched for k in ("properties", "patternProperties", "required")
        )

        if is_object_type or looks_like_object_schema:
            patched["additionalProperties"] = False

        return patched

    return _patch(schema)


def validate_stage_file_access(
    session: Session,
    stage_name: str | None,
    file_columns: list[str] | None = None,
    *,
    sample_file_paths: list[str] | None = None,
    table_name: str | None = None,
    dataset: list[dict] | None = None,
) -> None:
    """Validate stage accessibility and file availability for multimodal functions.

    Call before evaluate/optimize to catch configuration issues early.

    Supply file samples via exactly one of:
        - ``sample_file_paths``: pre-extracted list of paths
        - ``table_name``: queries up to 3 sample paths from the table
        - ``dataset``: extracts paths from loaded data dicts
        (all optional — if none given, only stage accessibility is checked)

    Raises:
        ValueError: With an actionable message if any check fails.
    """
    if not file_columns:
        return

    if not stage_name:
        raise ValueError(
            "stage_name is required when the function uses file inputs "
            f"(detected file columns: {file_columns}). "
            "Provide stage_name in metric_options, e.g. "
            "metric_options={'stage_name': '@DB.SCHEMA.AI_FUNCTIONS', ...}"
        )

    try:
        session.sql(
            f"SELECT 1 FROM DIRECTORY({stage_name}) LIMIT 1"
        ).collect()
    except Exception as e:
        raise ValueError(
            f"Cannot access stage {stage_name}. "
            "Verify the stage exists and your role has USAGE privilege. "
            f"Error: {e}"
        ) from e

    paths = _resolve_sample_paths(
        session, file_columns[0], sample_file_paths, table_name, dataset
    )
    if not paths:
        return

    paths_to_check = paths[:3]
    conditions = " OR ".join(
        f"RELATIVE_PATH = '{p}'" for p in paths_to_check
    )
    try:
        rows = session.sql(
            f"SELECT RELATIVE_PATH FROM DIRECTORY({stage_name}) "
            f"WHERE {conditions} LIMIT 1"
        ).collect()
    except Exception as e:
        raise ValueError(
            f"Cannot query files in stage {stage_name}. Error: {e}"
        ) from e

    if not rows:
        sample_display = ", ".join(f"'{p}'" for p in paths_to_check)
        raise ValueError(
            f"No matching files found in stage {stage_name}. "
            f"Checked paths: {sample_display}. "
            "Verify that stage_name points to the correct stage "
            "and that the file paths in your data are stage-relative paths."
        )


def _resolve_sample_paths(
    session: Session,
    file_column: str,
    sample_file_paths: list[str] | None,
    table_name: str | None,
    dataset: list[dict] | None,
) -> list[str]:
    """Extract sample file paths from whichever source is provided."""
    if sample_file_paths:
        return [p for p in sample_file_paths if p]

    if table_name:
        quoted = (
            f'"{file_column}"'
            if not file_column.startswith('"')
            else file_column
        )
        rows = session.sql(
            f"SELECT {quoted} AS FP FROM {table_name} "
            f"WHERE {quoted} IS NOT NULL LIMIT 3"
        ).collect()
        return [str(r["FP"]) for r in rows]

    if dataset:
        return [
            str(item["inputs"][file_column])
            for item in dataset[:3]
            if item.get("inputs", {}).get(file_column)
        ]

    return []


class RobustAIComplete:
    """Utilities for parsing and recovering JSON-like model outputs."""

    _error_mode_init_attempted = False
    _can_use_error_details_mode = False

    JSON_CODE_BLOCK_RE = re.compile(
        r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE
    )
    JSON_MODE_ERROR_MARKERS = (
        "json mode output validation error",
        "unmarshalling the model output",
        "invalid json",
        "unexpected end of json input",
        # Common parser failures surfaced by vLLM/OpenAI-compatible servers.
        "eof while parsing",
        "while parsing a string",
        "unterminated string",
        "expecting value",
        "expecting ',' delimiter",
    )
    RETURN_DETAILS_BLOCK_MARKERS = (
        "return details is not allowed",
        "ai_sql_error_handling_use_fail_on_error",
    )

    @classmethod
    def _initialize_error_mode_once(cls, session: Session) -> None:
        """Try once to enable return_error_details compatibility in the session."""
        if cls._error_mode_init_attempted:
            return
        # Cache the capability check once per process to avoid repeated ALTER SESSION calls.
        cls._error_mode_init_attempted = True
        try:
            session.sql(
                "ALTER SESSION SET AI_SQL_ERROR_HANDLING_USE_FAIL_ON_ERROR = FALSE"
            ).collect()
            cls._can_use_error_details_mode = True
        except Exception:
            cls._can_use_error_details_mode = False

    @staticmethod
    def _extract_balanced_substring(
        text: str, open_ch: str, close_ch: str
    ) -> str | None:
        start: int | None = None
        depth = 0
        # Track string/escape state so braces/brackets inside quoted text are ignored.
        in_string = False
        escape = False
        for idx, ch in enumerate(text):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue
            if ch == open_ch:
                if depth == 0:
                    start = idx
                depth += 1
            elif ch == close_ch and depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    return text[start : idx + 1]
        return None

    @classmethod
    def _parse_json(
        cls, value: object, *, allow_text_recovery: bool = False
    ) -> object | None:
        """Parse JSON from value; optionally recover from wrapped/non-JSON text."""
        if not isinstance(value, str):
            return value

        text = value.strip()
        if not text:
            return None

        # Soft text-recovery is intentionally disabled for now.
        allow_text_recovery = False
        if not allow_text_recovery:
            return json.loads(text)

        # Unwrap common Markdown code fences like ```...``` or ```json...```.
        fenced_match = cls.JSON_CODE_BLOCK_RE.search(text)
        if fenced_match:
            text = fenced_match.group(1).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            candidate = cls._extract_balanced_substring(text, open_ch, close_ch)
            if candidate is None:
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None

    @classmethod
    def parse_ai_complete_payload(
        cls, raw_response: object, *, allow_text_recovery: bool = False
    ) -> object | None:
        """Parse AI_COMPLETE response payload into JSON-like Python objects."""
        parsed = cls._parse_json(raw_response, allow_text_recovery=allow_text_recovery)
        if parsed is None:
            return None

        # Detail-enabled calls can wrap payload in {'value', 'error'}.
        if isinstance(parsed, dict) and ("value" in parsed or "error" in parsed):
            error_text = parsed.get("error")
            if error_text is not None and str(error_text).strip():
                raise RuntimeError(str(error_text))
            parsed = parsed.get("value")

        return cls._parse_json(parsed, allow_text_recovery=allow_text_recovery)

    @classmethod
    def is_json_mode_validation_error(cls, message: str) -> bool:
        """True when error text indicates strict JSON mode/schema validation failure."""
        lowered = message.lower()
        return any(marker in lowered for marker in cls.JSON_MODE_ERROR_MARKERS)

    @classmethod
    def _is_return_details_not_allowed_error(cls, exc: Exception) -> bool:
        lowered = str(exc).lower()
        return all(marker in lowered for marker in cls.RETURN_DETAILS_BLOCK_MARKERS)

    @classmethod
    def _execute_ai_complete(
        cls,
        session: Session,
        model: str,
        user_prompts: list[str] | str,
        temperature: float,
        max_tokens: int,
        response_schema: dict[str, Any] | None,
        include_error_details: bool,
        system_prompt: str | None = None,
        file_paths: list[str] | None = None,
        stage_name: str | list[str] | None = None,
    ) -> list[object]:
        """Execute Snowflake AI_COMPLETE() and return the raw payload.

        AI_COMPLETE v9 signature:
            AI_COMPLETE(
              model string,
              messages array,
              model_parameters object default {},
              response_format variant default null,
              show_details boolean default false,
              provisioned_throughput_id string default null,
              return_error_details boolean default false
            )

        When ``file_paths`` and ``stage_name`` are provided, each prompt
        is wrapped in ``PROMPT('{0} {1}', text, TO_FILE(stage, path))``
        so the model receives the file alongside the text.  Pass a
        ``str`` for a single stage or ``list[str]`` for per-row stages.
        """

        if response_schema is not None:
            response_schema = patch_response_format_additional_properties(
                response_schema
            )

        per_row_stages = isinstance(stage_name, list)
        multimodal = bool(file_paths and stage_name)

        if multimodal:
            stage_expr = col("STAGE_COL") if per_row_stages else lit(stage_name)
            content_expr = call_function(
                "PROMPT",
                lit("{0} {1}"),
                col("PROMPT_EXPR_COL"),
                call_function("TO_FILE", stage_expr, col("FILE_PATH_COL")),
            )
        else:
            content_expr = col("PROMPT_EXPR_COL")

        message_exprs = []
        if system_prompt is not None and system_prompt.strip():
            system_msg = object_construct(
                lit("role"), lit("system"), lit("content"), lit(system_prompt)
            )
            message_exprs.append(system_msg)
        message_exprs.append(
            object_construct(
                lit("role"), lit("user"), lit("content"), content_expr
            )
        )
        messages = array_construct(*message_exprs)

        model_parameters = object_construct(
            lit("temperature"),
            lit(temperature),
            lit("max_tokens"),
            lit(max_tokens),
        )

        response_format = (
            object_construct(
                lit("type"),
                lit("json"),
                lit("schema"),
                parse_json(lit(json.dumps(response_schema))),
            )
            if response_schema is not None
            else lit(None)
        )

        arguments = [
            lit(model),
            messages,
            model_parameters,
            response_format,
            lit(False),  # show_details
            lit(""),  # provisioned_throughput_id
            lit(include_error_details),  # return_error_details
        ]

        # Build DataFrame with prompts (and optional image paths)
        if isinstance(user_prompts, str):
            user_prompts = [user_prompts]

        if multimodal:
            if per_row_stages:
                prompts_for_df = [
                    [i, p, fp, sn]
                    for i, (p, fp, sn) in enumerate(
                        zip(user_prompts, file_paths, stage_name)
                    )
                ]
                schema = ["IDX", "PROMPT_EXPR_COL", "FILE_PATH_COL", "STAGE_COL"]
            else:
                prompts_for_df = [
                    [i, p, fp]
                    for i, (p, fp) in enumerate(zip(user_prompts, file_paths))
                ]
                schema = ["IDX", "PROMPT_EXPR_COL", "FILE_PATH_COL"]
        else:
            prompts_for_df = [[i, p] for i, p in enumerate(user_prompts)]
            schema = ["IDX", "PROMPT_EXPR_COL"]

        df = session.create_dataframe(prompts_for_df, schema=schema)

        df_result = df.select(
            col("IDX"),
            col("PROMPT_EXPR_COL"),
            call_function("AI_COMPLETE", *arguments).alias("RESPONSE"),
        ).order_by(col("IDX"))
        rows = df_result.collect()
        return [row["RESPONSE"] for row in rows]

    @classmethod
    def call_ai_complete(
        cls,
        session: Session,
        model: str,
        user_prompts: list[str],
        temperature: float,
        max_tokens: int,
        response_schema: dict[str, Any] | None,
        system_prompt: str | None = None,
        file_paths: list[str] | None = None,
        stage_name: str | list[str] | None = None,
    ) -> list[object] | None:
        cls._initialize_error_mode_once(session)

        def dispatch(include_error_details: bool) -> list[object]:
            raw_variant = cls._execute_ai_complete(
                session,
                model=model,
                user_prompts=user_prompts,
                temperature=temperature,
                max_tokens=max_tokens,
                response_schema=response_schema,
                include_error_details=include_error_details,
                system_prompt=system_prompt,
                file_paths=file_paths,
                stage_name=stage_name,
            )
            return [json.loads(row) for row in raw_variant]

        # Use cached session capability to decide whether return_error_details is safe.
        if cls._can_use_error_details_mode:
            try:
                raw_responses = dispatch(True)
                raw_responses = [resp["value"] for resp in raw_responses]
            except Exception as exc:
                if not cls._is_return_details_not_allowed_error(exc):
                    raise
                cls._can_use_error_details_mode = False
                raw_responses = dispatch(False)
        else:
            raw_responses = dispatch(False)

        return raw_responses if raw_responses else None

    @classmethod
    def run_ai_complete_with_json_fallback(
        cls,
        session: Session,
        model: str,
        primary_prompt: str,
        fallback_prompt: str,
        response_schema: dict[str, Any],
        temperature: float,
        max_tokens: int,
        allow_text_recovery: bool = False,
    ) -> object:
        """Run AI_COMPLETE with strict schema first, then prompt-only fallback."""
        strict_result: object | None = None
        try:
            strict_result = cls.call_ai_complete(
                session,
                model=model,
                user_prompts=[primary_prompt],
                temperature=temperature,
                max_tokens=max_tokens,
                response_schema=response_schema,
            )
        except Exception as exc:
            if not (
                isinstance(exc, json.JSONDecodeError)
                or cls.is_json_mode_validation_error(str(exc))
            ):
                raise

        # A strict call can return null/empty payloads in some backends.
        # Treat that as non-success and attempt prompt-only fallback.
        if strict_result is not None:
            return strict_result[0]

        fallback_result = cls.call_ai_complete(
            session,
            model=model,
            user_prompts=[fallback_prompt],
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=None,
        )
        return (
            cls._parse_json(fallback_result[0], allow_text_recovery=allow_text_recovery)
            if fallback_result
            else None
        )


STAGE_KEY_PREFIX = "__STAGE_"


def stage_key(col_name: str) -> str:
    """Return the inputs-dict key that holds the per-row stage for *col_name*."""
    return f"{STAGE_KEY_PREFIX}{col_name}"


def parse_file_value(val: object) -> tuple[str, str] | None:
    """Parse a Snowflake FILE variant collected into Python.

    When a FILE-typed column is collected via Snowpark, the value arrives as
    a Python ``dict`` (interactive sessions) **or** a JSON string (inside
    stored procedures)::

        {"STAGE": "@DB.SCHEMA.STAGE", "RELATIVE_PATH": "images/cat.jpg", ...}

    Returns ``(stage_name, relative_path)`` if *val* is a FILE variant,
    or ``None`` otherwise.
    """
    parsed = val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(parsed, dict) and "RELATIVE_PATH" in parsed and "STAGE" in parsed:
        return (str(parsed["STAGE"]), str(parsed["RELATIVE_PATH"]))
    return None


def normalize_ddl_to_dollar_quoting(raw_ddl: str) -> str:
    """Convert function DDL body to use ``$$`` delimiters.

    ``GET_DDL`` may return the body wrapped in ``'...'`` with internal
    single-quotes doubled (``''``).  Converting to ``$$`` delimiters
    removes the escaping so the body can be manipulated with simple
    regex patterns.

    If the DDL already uses ``$$`` delimiters it is returned unchanged.
    """
    if "$$" in raw_ddl:
        return raw_ddl
    match = re.search(r"(\bAS\s+)'(.*)'(\s*;?\s*)$", raw_ddl, re.DOTALL | re.IGNORECASE)
    if not match:
        return raw_ddl

    prefix = raw_ddl[: match.start(0)] + match.group(1)
    body = match.group(2).replace("''", "'")
    suffix = match.group(3)

    return f"{prefix}$$\n{body}\n$${suffix}"


_TO_FILE_RE = re.compile(
    r"TO_FILE\(\s*'([^']+)'\s*,\s*(\w+)\s*\)", re.IGNORECASE
)

_FILE_PARAM_RE = re.compile(
    r'"?(\w+)"?\s+FILE\b', re.IGNORECASE
)

_ARRAY_PARAM_RE = re.compile(
    r'"?(\w+)"?\s+ARRAY\b', re.IGNORECASE
)


def extract_to_file_refs(ddl: str) -> tuple[str, list[str]] | None:
    """Extract stage name and file-path columns from ``TO_FILE()`` in DDL.

    Returns ``(stage_name, [column_names])`` or ``None`` if no
    ``TO_FILE()`` calls are found.  Used to auto-detect multimodal
    functions so the LLM judge can receive images.
    """
    ddl = normalize_ddl_to_dollar_quoting(ddl)
    matches = _TO_FILE_RE.findall(ddl)
    if not matches:
        return None
    stage = matches[0][0]
    columns = list(dict.fromkeys(m[1] for m in matches))
    return stage, columns


def extract_file_type_params(ddl: str) -> list[str] | None:
    """Extract parameter names declared with ``FILE`` data type from DDL.

    Parses the function signature (between the first ``(`` and its matching
    ``)``) and returns parameter names whose type is ``FILE``.  Returns
    ``None`` if no FILE parameters are found.

    This complements :func:`extract_to_file_refs` which detects VARCHAR-path
    functions with ``TO_FILE()`` in the body.  Together they cover both
    multimodal patterns.
    """
    ddl = normalize_ddl_to_dollar_quoting(ddl)

    sig_match = re.search(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+\S+\s*\(([^)]*)\)",
        ddl,
        re.IGNORECASE,
    )
    if not sig_match:
        return None

    param_list = sig_match.group(1)
    matches = _FILE_PARAM_RE.findall(param_list)
    if not matches:
        return None
    return list(dict.fromkeys(matches))


def _extract_array_params(ddl: str) -> set[str]:
    """Extract parameter names declared with ``ARRAY`` data type from DDL.

    Returns a set of upper-cased parameter names whose type is ``ARRAY``.
    Used by :meth:`TempAIFunction.call_rows` to cast columns back to ARRAY
    after ``session.create_dataframe`` converts Python lists to VARCHAR.
    """
    ddl = normalize_ddl_to_dollar_quoting(ddl)
    sig_match = re.search(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+)?FUNCTION\s+\S+\s*\(([^)]*)\)",
        ddl,
        re.IGNORECASE,
    )
    if not sig_match:
        return set()
    param_list = sig_match.group(1)
    matches = _ARRAY_PARAM_RE.findall(param_list)
    return {m.upper() for m in matches}


class TempAIFunction:
    """Utility class for creating and invoking TempAIFunction with error handling retries"""

    def __init__(
        self,
        session: Session,
        original_ddl: str,
        temp_function_name: str,
        candidate_model: str,
        candidate_prompt: str,
        file_type_params: set[str] | None = None,
        stage_name: str | None = None,
    ) -> None:
        self.session = session
        self.original_ddl = original_ddl
        self.temp_function_name = temp_function_name
        self.candidate_model = candidate_model
        self.candidate_prompt = candidate_prompt
        self._file_type_params = {p.upper() for p in file_type_params} if file_type_params else set()
        self._stage_name = stage_name
        self._array_type_params = _extract_array_params(original_ddl)

        self.accessor_field = self._extract_output_accessor(original_ddl)

        self.ddl = self._build_ddl(
            original_ddl=original_ddl,
            temp_function_name=temp_function_name,
            candidate_model=candidate_model,
            candidate_prompt=candidate_prompt,
        )
        self.session.sql(self.ddl).collect()

    @staticmethod
    def _extract_return_type(ddl: str) -> str | None:
        """Utility function to extract return type from DDL"""
        match = re.search(r"\bRETURNS\s+(.+?)\s+LANGUAGE\b", ddl, re.IGNORECASE)
        return match.group(1).strip()

    @staticmethod
    def _extract_output_accessor(raw_ddl: str) -> str | None:
        """Utility function to extract accessors from DDL (:field::TYPE or similar that we add)"""
        match = re.search(
            r"(:\s*\w+\s*::\s*[A-Z0-9_\(\), ]+)",
            raw_ddl,
            re.IGNORECASE,
        )

        matched_accessor = match.group(1) if match else None

        # Check for accessor field
        accessor_field = None
        if matched_accessor:
            m = re.search(r":\s*(\w+)\s*::", matched_accessor)
            accessor_field = m.group(1)
            accessor_field = accessor_field.upper()

        return accessor_field

    @staticmethod
    def _find_ai_complete_call(ddl: str) -> tuple[str, int, int] | None:
        """Find AI_COMPLETE(...) using recursive descent style parsing.

        Returns (inner_content, start_of_match, end_of_match) or None.
        Handles nested calls like ARRAY_CONSTRUCT(OBJECT_CONSTRUCT(...))
        and SQL string literals containing parens.
        """
        m = re.search(r"AI_COMPLETE\s*\(", ddl, re.IGNORECASE)
        if not m:
            return None

        start = m.end()  # position after opening (

        def _skip_string(pos: int) -> int:
            """Advance past a SQL single-quoted string, handling '' escapes."""
            pos += 1  # skip opening '
            while pos < len(ddl):
                if ddl[pos] != "'":
                    pos += 1
                elif pos + 1 < len(ddl) and ddl[pos + 1] == "'":
                    pos += 2  # skip escaped ''
                else:
                    return pos + 1  # skip closing '
            return pos

        def _skip_parens(pos: int) -> int:
            """Advance past a balanced (...) group, recursing for nested parens/strings."""
            pos += 1  # skip opening (
            while pos < len(ddl):
                ch = ddl[pos]
                if ch == "'":
                    pos = _skip_string(pos)
                elif ch == "(":
                    pos = _skip_parens(pos)
                elif ch == ")":
                    return pos + 1  # skip closing )
                else:
                    pos += 1
            return pos  # unterminated

        i = start
        while i < len(ddl):
            ch = ddl[i]
            if ch == "'":
                i = _skip_string(i)
            elif ch == "(":
                i = _skip_parens(i)
            elif ch == ")":
                inner = ddl[start:i]
                return inner, m.start(), i + 1
            else:
                i += 1

        return None

    @classmethod
    def _rewrite_ai_complete_for_error_details(
        cls, ddl: str, *, value_type: str
    ) -> str:
        """Utility function for rewriting AI_COMPLETE call to ensure that
        return_error_details parameter is present"""

        # Inject return_error_details=>TRUE if missing
        found = cls._find_ai_complete_call(ddl)
        if found:
            inner, match_start, match_end = found
            if not re.search(
                r"\breturn_error_details\s*=>\s*(true|false)\b",
                inner,
                re.IGNORECASE,
            ):
                inner = inner + ", return_error_details=>TRUE"
            ddl = ddl[:match_start] + f"AI_COMPLETE({inner})" + ddl[match_end:]

        # Remove output accessor like :field::TYPE
        ddl = re.sub(
            r":\s*\w+\s*::\s*[A-Z0-9_\(\), ]+",
            "",
            ddl,
            count=1,
            flags=re.IGNORECASE,
        )

        # Cast AI_COMPLETE to typed OBJECT(value VARIANT, error STRING)
        # Note this is always variant
        found = cls._find_ai_complete_call(ddl)
        if found:
            inner, match_start, match_end = found
            replacement = f"(AI_COMPLETE({inner}))::OBJECT(value VARIANT, error STRING)"
            ddl = ddl[:match_start] + replacement + ddl[match_end:]

        return ddl

    @classmethod
    def _build_ddl(
        cls,
        *,
        original_ddl: str,
        temp_function_name: str,
        candidate_model: str,
        candidate_prompt: str,
    ) -> str:
        """BUILD DDL for create temp function"""
        ddl = normalize_ddl_to_dollar_quoting(original_ddl)
        return_type = cls._extract_return_type(ddl)
        value_type = return_type or "VARIANT"

        # Update RETURNS clause to OBJECT(value VARIANT, error STRING)
        ddl = re.sub(
            r"(\bRETURNS\s+).+?(\s+LANGUAGE\b)",
            r"\g<1>OBJECT(value VARIANT, error STRING)\2",
            ddl,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )

        # Replace function name and make it TEMPORARY
        ddl = re.sub(
            r"(CREATE\s+OR\s+REPLACE\s+)(?:TEMPORARY\s+)?FUNCTION\s+\S+(\s*\()",
            rf"\g<1>TEMPORARY FUNCTION {temp_function_name}\2",
            ddl,
            count=1,
            flags=re.IGNORECASE,
        )

        # Replace model
        escaped_model = candidate_model.replace("'", "''")
        ddl = re.sub(
            r"(model\s*=>\s*')[^']*(')",
            rf"\g<1>{escaped_model}\2",
            ddl,
            count=1,
            flags=re.IGNORECASE,
        )

        # Replace prompt
        escaped_prompt = candidate_prompt.replace("'", "''")
        ddl = re.sub(
            r"('role'\s*,\s*'system'\s*,\s*'content'\s*,\s*')(?:[^']|'')*(')",
            rf"\g<1>{escaped_prompt}\2",
            ddl,
            count=1,
            flags=re.DOTALL,
        )

        ddl = cls._rewrite_ai_complete_for_error_details(ddl, value_type=value_type)
        return ddl

    def call_rows(self, rows: list[dict[str, object]]) -> list[object]:
        """Builds AI_COMPLETE call with columns specified in rows dictionary
        Retries failed rows up to 3 attemps and surfaces error into value on last attempt"""

        if not rows:
            return []

        # Stable row id to preserve input order.
        # Pre-serialize ARRAY-typed values to JSON so that create_dataframe
        # stores a valid JSON string that parse_json() can restore to ARRAY.
        indexed_rows = []
        for idx, row in enumerate(rows):
            r = {"__ROW_ID": idx}
            for k, v in (row or {}).items():
                if self._array_type_params and k.upper() in self._array_type_params and isinstance(v, (list, tuple)):
                    r[k] = json.dumps(v)
                else:
                    r[k] = v
            indexed_rows.append(r)

        # Build column names
        all_cols: list[str] = ["__ROW_ID"]
        seen = {"__ROW_ID"}
        for row in rows:
            for k in (row or {}).keys():
                if k not in seen:
                    seen.add(k)
                    all_cols.append(k)
        arg_cols = []
        for c in all_cols:
            if c == "__ROW_ID" or c.startswith(STAGE_KEY_PREFIX):
                continue
            per_row_stage_col = f"{STAGE_KEY_PREFIX}{c}"
            if per_row_stage_col in seen:
                arg_cols.append(call_function("TO_FILE", col(per_row_stage_col), col(c)))
            elif self._file_type_params and c.upper() in self._file_type_params and self._stage_name:
                arg_cols.append(call_function("TO_FILE", lit(self._stage_name), col(c)))
            elif self._array_type_params and c.upper() in self._array_type_params:
                arg_cols.append(parse_json(col(c)))
            else:
                arg_cols.append(col(c))

        # Create dataframe with rows and select on columns
        df = self.session.create_dataframe(indexed_rows)
        df = df.select(*[col(c) for c in all_cols])

        attempt = 0
        remaining_ids = set(range(len(rows)))
        values_by_id: dict[int, object] = {}
        errors_by_id: dict[int, str] = {}

        while remaining_ids and attempt < TEMP_AI_FUNCTION_MAX_ATTEMPTS:
            attempt += 1

            # Filter and call AI_COMPLETE for remaining IDS
            df_attempt = df.filter(col("__ROW_ID").isin(list(remaining_ids)))
            result_obj = call_function(self.temp_function_name, *arg_cols).alias(
                "RESULT"
            )

            # Collect results
            res_df = df_attempt.select(col("__ROW_ID").alias("ROW_ID"), result_obj)

            # Extract value/error fields.
            value_col = col("RESULT")["value"].alias("VALUE")
            error_col = col("RESULT")["error"].alias("ERROR")
            res_df = res_df.select(col("ROW_ID"), value_col, error_col)

            # Collect and process, adding rows with errors to next_remaining
            collected = res_df.collect()
            next_remaining: set[int] = set()
            for r in collected:
                rid = int(r["ROW_ID"])
                err = r["ERROR"]
                if err is None or str(err).strip() == "":
                    values_by_id[rid] = r["VALUE"]
                    errors_by_id.pop(rid, None)
                else:
                    errors_by_id[rid] = str(err)
                    next_remaining.add(rid)

            remaining_ids = next_remaining

        # Populate final output
        out: list[object] = [None] * len(rows)
        for i in range(len(rows)):
            # Error occurred, surface error into value
            if i in errors_by_id:
                err = errors_by_id.get(i, "Unknown error")
                out[i] = f"INFERENCE_ERROR: {err}"
                continue

            v = values_by_id[i]
            # Parse JSON string to dict if needed (Snowflake may return VARIANT as string)
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    pass
            # Apply accessor if exists
            if self.accessor_field and isinstance(v, dict):
                # Normalize capatilization
                key_map = {str(k).upper(): k for k in v.keys()}
                key = key_map.get(self.accessor_field)
                # apply accessor
                v = v.get(key)
            out[i] = v
        return out
