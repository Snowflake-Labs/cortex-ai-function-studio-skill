# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

from contextlib import contextmanager
from functools import wraps
import json
import re
from typing import Any

from snowflake.snowpark import Session
from snowflake.snowpark.functions import (
    array_construct,
    call_function,
    col,
    lit,
    object_construct,
    parse_json,
)

CUSTOM_AI_FUNCTION_TAG_PREFIX = "__CUSTOM_AI_FUNCTION_LOG_"


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
        """

        if response_schema is not None:
            response_schema = patch_response_format_additional_properties(
                response_schema
            )

        # Setup messages argument using snowpark
        message_exprs = []
        if system_prompt is not None and system_prompt.strip():
            system_msg = object_construct(
                lit("role"), lit("system"), lit("content"), lit(system_prompt)
            )
            message_exprs.append(system_msg)
        message_exprs.append(
            object_construct(
                lit("role"), lit("user"), lit("content"), col("PROMPT_EXPR_COL")
            )
        )
        messages = array_construct(*message_exprs)

        # Model parameters using object construct
        model_parameters = object_construct(
            lit("temperature"),
            lit(temperature),
            lit("max_tokens"),
            lit(max_tokens),
        )

        # Response format if exists
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

        # create prompts df (include an explicit index so we can enforce ordering)
        if isinstance(user_prompts, str):
            prompts_for_df = [[0, user_prompts]]
        else:
            prompts_for_df = [[i, p] for i, p in enumerate(user_prompts)]
        df = session.create_dataframe(prompts_for_df, schema=["IDX", "PROMPT_EXPR_COL"])

        # AI_COMPLETE! (order by IDX to match input order)
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
    ) -> list[object] | None:
        # Calls ai complete with return error details
        # returns json if explicit json schema else just string
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
            cls._parse_json(fallback_result[0], allow_text_recovery)
            if fallback_result
            else None
        )
