# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Core metric functions for AI function evaluation.

This file is designed to be embedded directly into Snowflake Python SPROCs.
No external dependencies except stdlib.
"""

from collections.abc import Callable
from collections import Counter
import re
from textwrap import dedent
import time
from typing import Protocol, runtime_checkable

import pandas as pd
from snowflake.snowpark import Session
import json

from custom_ai_function_utils import (
    validate_stage_file_access,
    with_ai_sql_error_handling_use_fail_on_error_disabled_for_sproc,
    with_custom_ai_function_query_tag,
    RobustAIComplete,
)

LLM_JUDGE_DEFAULT_MODEL = "claude-sonnet-4-5"
LLM_JUDGE_DEFAULT_TEMP = 0.0
LLM_JUDGE_DEFAULT_MAX_TOKENS = 8192
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


@runtime_checkable
class CustomMetric(Protocol):
    """Protocol for custom evaluation metrics.

    Every custom metric file must define a class named ``CustomMetric`` that
    satisfies this protocol. The class must be callable with the signature
    below -- that is the only requirement.

    The file name (without ``.py``) becomes the metric name used in
    ``EVALUATE_AI_FUNCTION``. The class name is always ``CustomMetric``.
    Different metrics are distinguished by file name, not class name.

    Evaluation metrics produce a numeric score **and** text feedback explaining
    why the prediction is correct or incorrect. The feedback is used during the
    optimization step to refine the prompt your function uses, so it should be
    specific and actionable (e.g., "Found 3 of 5 keywords; missing: X, Y").
    """

    def __call__(
        self,
        expected: str,
        predicted: str,
        session: Session | None = None,
        **kwargs,
    ) -> tuple[float, str]:
        """Evaluate a single (expected, predicted) pair.

        Args:
            expected: Ground truth value.
            predicted: Model output.
            session: Snowpark session (needed for LLM-based metrics).
            **kwargs: Metric-specific options.

        Returns:
            (score, feedback) where score is 0.0-1.0 and feedback explains
            the score in a way that is useful for optimization.
        """
        ...


def _call_custom_metric_udf(
    metric_udf: str,
    expected: str,
    predicted: str,
    session: Session,
) -> tuple[float, str]:
    """Call a custom metric implemented as a Python UDF.

    The UDF must accept (EXPECTED VARCHAR, PREDICTED VARCHAR) and return
    VARIANT with keys ``score`` (float 0.0-1.0) and ``feedback`` (string).

    Args:
        metric_udf: Fully qualified UDF name (e.g., ``DB.SCHEMA.MY_METRIC``).
        expected: Ground truth value.
        predicted: Model output.
        session: Snowpark session.

    Returns:
        (score, feedback) tuple.
    """
    safe_name = _validate_and_quote_udf_name(metric_udf)

    result = session.sql(
        f"SELECT {safe_name}(?, ?) AS RESULT",
        params=[str(expected), str(predicted)],
    ).collect()

    if not result:
        return 0.0, "Custom metric UDF returned no result"

    return _parse_metric_result(result[0]["RESULT"])


def _call_custom_metric_udf_batch(
    metric_udf: str,
    items: list[tuple[str, str]],
    session: Session,
) -> list[tuple[float, str]]:
    """Batched evaluation using a custom metric UDF.

    Evaluates all (expected, predicted) pairs in a single SQL query using a
    VALUES clause, similar to ``llm_judge_batch``.

    Args:
        metric_udf: Fully qualified UDF name.
        items: List of (expected, predicted) tuples.
        session: Snowpark session.

    Returns:
        List of (score, feedback) tuples in same order as input.
    """
    if not items:
        return []

    safe_name = _validate_and_quote_udf_name(metric_udf)

    value_qmarks = ", ".join(f"({idx}, ?, ?)" for idx in range(len(items)))
    bind_params: list[object] = []
    for expected, predicted in items:
        bind_params.extend([str(expected), str(predicted)])

    results = session.sql(
        f"""
        SELECT idx, {safe_name}(expected_val, predicted_val) AS RESULT
        FROM VALUES {value_qmarks} AS t(idx, expected_val, predicted_val)
        ORDER BY idx
    """,
        params=bind_params,
    ).collect()

    outputs = []
    for row in results:
        outputs.append(_parse_metric_result(row["RESULT"]))

    return outputs


def quote_identifier(name: str) -> str:
    """Quote a Snowflake identifier for dynamic SQL usage."""
    return '"' + str(name).replace('"', '""') + '"'


def _validate_and_quote_udf_name(name: str) -> str:
    """Validate a dotted Snowflake UDF name and quote each part.

    Accepts 1-3 part names (``FUNC``, ``SCHEMA.FUNC``, ``DB.SCHEMA.FUNC``).
    Each part must be a bare identifier (alphanumeric/underscore) or
    double-quoted.  Returns a safely-quoted fully qualified name.
    """
    raw = str(name).strip()
    if not raw:
        raise ValueError("Custom metric UDF name cannot be empty")

    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    for ch in raw:
        if ch == '"':
            in_quotes = not in_quotes
            current.append(ch)
        elif ch == "." and not in_quotes:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))

    if not 1 <= len(parts) <= 3:
        raise ValueError(
            f"Custom metric UDF name must be a 1-3 part identifier "
            f"(e.g., DB.SCHEMA.FUNC), got {len(parts)} parts: {raw!r}"
        )

    quoted: list[str] = []
    for part in parts:
        stripped = part.strip()
        if not stripped:
            raise ValueError(f"Empty identifier part in UDF name: {raw!r}")
        if stripped.startswith('"') and stripped.endswith('"') and len(stripped) > 1:
            inner = stripped[1:-1]
        elif _IDENTIFIER_RE.match(stripped):
            inner = stripped
        else:
            raise ValueError(
                f"Invalid identifier in UDF name: {stripped!r} (from {raw!r}). "
                f"Each part must be alphanumeric/underscore or double-quoted."
            )
        quoted.append(quote_identifier(inner))

    return ".".join(quoted)


def _parse_metric_result(raw: object) -> tuple[float, str]:
    """Parse the VARIANT result from a custom metric UDF call.

    Returns ``(score, feedback)``.  Falls back to ``(0.0, error_message)``
    on any parse failure so a single bad row never crashes the run.
    """
    if raw is None:
        return 0.0, "Custom metric UDF returned NULL"

    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return 0.0, f"Custom metric UDF returned non-JSON string: {raw[:200]}"

    if not isinstance(parsed, dict):
        return 0.0, (
            f"Custom metric UDF returned {type(parsed).__name__}, "
            f"expected dict with 'score' and 'feedback'"
        )

    if "score" not in parsed:
        return 0.0, (
            f"Custom metric UDF result missing 'score' key. "
            f"Keys: {sorted(parsed.keys())}"
        )

    try:
        score = float(parsed["score"])
    except (TypeError, ValueError):
        return 0.0, (f"Custom metric UDF 'score' is not numeric: {parsed['score']!r}")

    feedback = str(parsed.get("feedback", ""))
    return score, feedback


def to_text(value: object) -> str:
    """Convert values (including VARIANT dict/list) to stable text."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def get_table_column_names(session: Session, table_name: str) -> set[str]:
    """Best-effort table column introspection for safer dynamic SQL."""
    try:
        rows = session.sql(f"DESCRIBE TABLE {table_name}").collect()
    except Exception:
        return set()

    names: set[str] = set()
    for row in rows:
        row_name = None
        if hasattr(row, "asDict"):
            row_dict = row.asDict()
            for key, value in row_dict.items():
                if str(key).upper() == "NAME":
                    row_name = value
                    break
        if row_name is None:
            try:
                row_name = row["NAME"]
            except Exception:
                try:
                    row_name = row["name"]
                except Exception:
                    row_name = None
        if row_name is not None:
            names.add(str(row_name).strip().upper())
    return names


def validate_input_columns(
    table_columns: set[str], input_columns: list[str], table_name: str
) -> None:
    """Validate that all input columns exist in the table.

    Args:
        table_columns: Set of uppercase column names from the table.
            If empty (introspection failed), validation is skipped.
        input_columns: Column names to validate.
        table_name: Table name for error messages.

    Raises:
        ValueError: If any input column is not found in the table.
    """
    if not table_columns:
        return
    missing = [
        col
        for col in input_columns
        if col.strip('"').strip("'").upper() not in table_columns
    ]
    if missing:
        raise ValueError(
            f"Input column(s) {missing} not found in table {table_name}. "
            f"Available columns: {sorted(table_columns)}."
        )


def resolve_expected_column(table_columns: set[str], expected_column: str) -> str:
    """Normalize the expected/label column name by stripping surrounding quotes.

    Callers are responsible for validating that the returned name exists in the
    target table.  No implicit fallback is performed — if the column is wrong,
    the caller should surface a clear error.

    Args:
        table_columns: Unused (kept for call-site compatibility).
        expected_column: Raw column name (may have quotes).

    Returns:
        Quote-stripped column name.
    """
    return str(expected_column).strip('"').strip("'")


def resolve_multi_output_columns(
    table_columns: set[str], expected_columns: list[str]
) -> list[tuple[str, str]]:
    """Resolve multi-output column names against table columns.

    Args:
        table_columns: Set of uppercase column names.  If empty, columns
            are returned as-is.
        expected_columns: List of output column names to resolve.

    Returns:
        List of ``(output_key, resolved_table_col)`` pairs.  Columns not
        found in the table are silently dropped.
    """
    if not table_columns:
        return [(str(c), str(c)) for c in expected_columns]
    pairs: list[tuple[str, str]] = []
    for col in expected_columns:
        upper = str(col).upper()
        if upper in table_columns:
            pairs.append((str(col), upper))
    return pairs


def build_object_construct_expr(
    resolved_pairs: list[tuple[str, str]], alias: str
) -> str:
    """Build a Snowflake ``OBJECT_CONSTRUCT(...)`` SQL expression.

    Args:
        resolved_pairs: List of ``(output_key, table_col)`` pairs.
        alias: Column alias for the expression (e.g. ``"EXPECTED"``).

    Returns:
        SQL expression like ``OBJECT_CONSTRUCT('k1', "COL1", ...) AS ALIAS``.
    """
    parts: list[str] = []
    for output_key, table_col in resolved_pairs:
        safe_key = str(output_key).replace("'", "''")
        parts.append(f"'{safe_key}'")
        parts.append(quote_identifier(table_col))
    return f"OBJECT_CONSTRUCT({', '.join(parts)}) AS {alias}"


def parse_metric_options(
    metric_options: dict | None,
) -> tuple[dict, str | None, list[str]]:
    """Parse metric options, extracting ``output_field`` and ``expected_columns``.

    Args:
        metric_options: Raw metric options dict (may be ``None``).

    Returns:
        Tuple of ``(cleaned_opts, output_field, expected_columns)`` where
        *cleaned_opts* has ``output_field`` and ``expected_columns`` removed.
    """
    if not isinstance(metric_options, dict):
        return {}, None, []
    opts = dict(metric_options)
    output_field = opts.pop("output_field", None)
    expected_columns_raw = opts.pop("expected_columns", None)
    expected_columns: list[str] = []
    if isinstance(expected_columns_raw, (list, tuple)):
        expected_columns = [
            str(c).strip() for c in expected_columns_raw if str(c).strip()
        ]
    return opts, output_field, expected_columns


def exact_match_core(
    expected: str, predicted: str, session: Session | None = None
) -> tuple:
    """Exact string comparison (case-insensitive, whitespace-trimmed)."""
    expected = str(expected).strip().lower()
    predicted = str(predicted).strip().lower()
    score = float(expected == predicted)
    if score == 1.0:
        feedback = "Correct! Prediction matches expected value."
    else:
        feedback = f"Incorrect. Expected '{expected}' but got '{predicted}'."
    return score, feedback


def _extract_output_field(predicted: object, output_field: str) -> str:
    """Extract a single output field from a JSON/VARIANT prediction."""
    if predicted is None:
        return ""
    if not output_field:
        return to_text(predicted)

    output_key = str(output_field).upper()
    if isinstance(predicted, dict):
        key_map = {str(k).upper(): k for k in predicted.keys()}
        key = key_map.get(output_key)
        if key is not None:
            return to_text(predicted.get(key, ""))
        # Backward-compatible fallback for single-output structured responses
        # when expected column and output key names differ.
        if len(predicted) == 1:
            return to_text(next(iter(predicted.values())))
        return to_text(predicted)

    if isinstance(predicted, str):
        try:
            parsed = json.loads(predicted)
            if isinstance(parsed, dict):
                key_map = {str(k).upper(): k for k in parsed.keys()}
                key = key_map.get(output_key)
                if key is not None:
                    return to_text(parsed.get(key, ""))
                if len(parsed) == 1:
                    return to_text(next(iter(parsed.values())))
                return to_text(parsed)
        except json.JSONDecodeError:
            return predicted

    return to_text(predicted)


def fuzzy_match_core(
    expected: str,
    predicted: str,
    session: Session | None = None,
    *,
    threshold: float = 0.85,
) -> tuple:
    """Token-based similarity using SequenceMatcher.

    Args:
        threshold: Minimum similarity score to consider a match (default 0.85)
    """
    from difflib import SequenceMatcher

    expected = str(expected).strip().lower()
    predicted = str(predicted).strip().lower()
    similarity = SequenceMatcher(None, expected, predicted).ratio()
    score = float(similarity >= threshold)
    if score == 1.0:
        feedback = f"Correct! Similarity {similarity:.0%} >= threshold {threshold:.0%}."
    else:
        feedback = (
            f"Incorrect. Similarity {similarity:.0%} < threshold {threshold:.0%}."
        )
    return score, feedback


def contains_match_core(
    expected: str, predicted: str, session: Session | None = None
) -> tuple:
    """Checks if expected value is contained within the prediction."""
    expected = str(expected).strip().lower()
    predicted = str(predicted).strip().lower()
    score = float(expected in predicted)
    if score == 1.0:
        feedback = "Correct! Prediction contains expected value."
    else:
        feedback = f"Incorrect. Prediction does not contain '{expected}'."
    return score, feedback


def map_normalized_to_original_index(
    original: str, normalized: str, norm_index: int
) -> int:
    """Map an index from normalized string back to original string position."""
    bracket_pattern = re.compile(r"\[[^\]]*\]")

    orig_idx = 0
    norm_idx = 0

    while norm_idx < norm_index and orig_idx < len(original):
        match = bracket_pattern.match(original[orig_idx:])
        if match:
            orig_idx += len(match.group())
            norm_idx += 2  # "[]" in normalized
        else:
            orig_idx += 1
            norm_idx += 1

    return min(orig_idx, len(original))


def redaction_match_core(
    expected: str, predicted: str, session: Session | None = None
) -> tuple:
    """Checks if two strings match except for content inside brackets [...].

    Compares text outside of bracketed placeholders, allowing different
    redacted values (e.g., [USERNAME], [TIME]) to vary between strings.
    """
    expected = str(expected).strip()
    predicted = str(predicted).strip()

    bracket_pattern = re.compile(r"\[[^\]]*\]")

    expected_normalized = bracket_pattern.sub("[]", expected)
    predicted_normalized = bracket_pattern.sub("[]", predicted)

    score = float(expected_normalized == predicted_normalized)
    if score == 1.0:
        feedback = "Correct! Text matches with redaction placeholders."
    else:
        min_len = min(len(expected_normalized), len(predicted_normalized))
        diff_start = None
        for i in range(min_len):
            if expected_normalized[i] != predicted_normalized[i]:
                diff_start = i
                break
        if diff_start is None and len(expected_normalized) != len(predicted_normalized):
            diff_start = min_len

        if diff_start is not None:
            # Check for preamble (extra text at the beginning)
            if diff_start == 0 and len(predicted_normalized) > len(expected_normalized):
                # Check if expected appears near the end of predicted (preamble case)
                check_len = min(50, len(expected_normalized))
                if check_len > 0 and predicted_normalized.endswith(
                    expected_normalized[-check_len:]
                ):
                    preamble_len = len(predicted_normalized) - len(expected_normalized)
                    preamble = predicted[: min(preamble_len + 20, len(predicted))]
                    feedback = (
                        f"Added preamble: Response has extra text at the beginning that must be removed. "
                        f"Do not include introductory phrases. "
                        f"Unwanted prefix: '{preamble[:100]}{'...' if len(preamble) > 100 else ''}'"
                    )
                    return score, feedback

            # Check for postamble (extra text at the end)
            if diff_start == len(expected_normalized) and len(
                predicted_normalized
            ) > len(expected_normalized):
                orig_postamble_start = len(expected)
                postamble = predicted[max(0, orig_postamble_start - 20) :]
                feedback = (
                    f"Added postamble: Response has extra text at the end that must be removed. "
                    f"Do not include concluding phrases. "
                    f"Unwanted suffix: '{'...' if orig_postamble_start > 20 else ''}{postamble[-100:]}'"
                )
                return score, feedback

            exp_has_bracket = expected_normalized[diff_start : diff_start + 2] == "[]"
            pred_has_bracket = predicted_normalized[diff_start : diff_start + 2] == "[]"

            # Map diff_start from normalized index to original string index
            orig_diff_start = map_normalized_to_original_index(
                expected, expected_normalized, diff_start
            )

            # Use the mapped index for context extraction
            ctx_start = max(0, orig_diff_start - 20)
            ctx_end = min(len(expected), orig_diff_start + 40)
            exp_snippet = expected[ctx_start:ctx_end]

            # Also map for predicted string
            orig_diff_start_pred = map_normalized_to_original_index(
                predicted, predicted_normalized, diff_start
            )
            pred_ctx_end = min(len(predicted), orig_diff_start_pred + 40)
            pred_snippet = predicted[ctx_start:pred_ctx_end]

            prefix = "..." if ctx_start > 0 else ""
            suffix_exp = "..." if ctx_end < len(expected) else ""
            suffix_pred = "..." if pred_ctx_end < len(predicted) else ""

            if exp_has_bracket and not pred_has_bracket:
                feedback = (
                    f"Missed redaction: predicted has literal text where redaction expected. "
                    f"Expected: '{prefix}{exp_snippet}{suffix_exp}' "
                    f"Predicted: '{prefix}{pred_snippet}{suffix_pred}'"
                )
            elif pred_has_bracket and not exp_has_bracket:
                feedback = (
                    f"Over-redacted: predicted redacted something that should be literal. "
                    f"Expected: '{prefix}{exp_snippet}{suffix_exp}' "
                    f"Predicted: '{prefix}{pred_snippet}{suffix_pred}'"
                )
            else:
                feedback = (
                    f"Text modified outside redactions at position {diff_start}. "
                    f"Expected text: '{prefix}{exp_snippet}{suffix_exp}' "
                    f"but got: '{prefix}{pred_snippet}{suffix_pred}'. "
                    f"Preserve original text exactly, only replace PII with redaction placeholders."
                )
        else:
            feedback = "Incorrect. Text outside brackets does not match."
    return score, feedback


_LLM_JUDGE_BINARY_TEMPLATE = (
    "Evaluate if the prediction is semantically correct.\n\n"
    "Task: {task_description}\n"
    "Expected: {expected}\n"
    "Predicted: {predicted}\n\n"
    "Score 1 if the prediction is correct, 0 if incorrect."
)

_LLM_JUDGE_BINARY_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "description": "1 if correct, 0 if incorrect",
        },
        "feedback": {
            "type": "string",
            "description": "Brief explanation for the score",
        },
    },
    "required": ["score", "feedback"],
    "additionalProperties": False,
}

_LLM_JUDGE_CONTINUOUS_TEMPLATE = (
    "You are a precise grading assistant. Evaluate how well the prediction "
    "matches the expected output for the given task.\n\n"
    "Task: {task_description}\n"
    "Expected: {expected}\n"
    "Predicted: {predicted}\n\n"
    "Score from 0.0 to 1.0. Use the full range — assign any value that "
    "reflects the degree of correctness.\n\n"
    "Rubric:\n"
    "- 1.0: Semantically identical or fully correct.\n"
    "- 0.7-0.9: Mostly correct with minor differences that don't change meaning.\n"
    "- 0.4-0.6: Partially correct — captures some key information but misses important parts.\n"
    "- 0.1-0.3: Mostly wrong but contains a small relevant element.\n"
    "- 0.0: Completely wrong or unrelated.\n\n"
    "Use these as guidelines, not hard boundaries. "
    "Prioritize semantic meaning over surface-level wording."
)

_LLM_JUDGE_CONTINUOUS_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "number",
            "description": "Score from 0.0 to 1.0",
        },
        "feedback": {
            "type": "string",
            "description": "Brief explanation for the score",
        },
    },
    "required": ["score", "feedback"],
    "additionalProperties": False,
}


def _parse_binary_result(raw: object) -> tuple[float, str]:
    """Parse a structured JSON judge response into a binary (0/1) score."""
    score, feedback = _parse_metric_result(raw)
    return (1.0 if score >= 0.5 else 0.0), feedback


def _parse_continuous_result(raw: object) -> tuple[float, str]:
    """Parse a structured JSON judge response into (score, feedback).

    Reuses ``_parse_metric_result`` (shared with custom metric UDFs) and
    clamps the score to [0.0, 1.0].
    """
    score, feedback = _parse_metric_result(raw)
    return max(0.0, min(1.0, score)), feedback


_LLM_JUDGE_FILE_ADDENDUM = (
    "\n\nThe attached file shows the actual input. "
    "Use it to verify the prediction."
)


def llm_judge_batch(
    items: list[tuple[str, str]],
    session: Session,
    task_description: str = "",
    model_name: str = LLM_JUDGE_DEFAULT_MODEL,
    temperature: float = LLM_JUDGE_DEFAULT_TEMP,
    max_tokens: int = LLM_JUDGE_DEFAULT_MAX_TOKENS,
    scoring_mode: str = "binary",
    file_paths: list[str] | None = None,
    stage_name: str | list[str] | None = None,
    **_kwargs,
) -> list[tuple[float, str]]:
    """Batched LLM judge -- evaluates all items in a single SQL query.

    This is the core implementation that all llm_judge calls use.
    Even single-item calls go through this for consistency.

    Args:
        items: List of (expected, predicted) tuples to evaluate.
        session: Snowpark session for calling AI_COMPLETE.
        task_description: Description of the task for context.
        model_name: Model to use for evaluation.
        temperature: Temperature for model inference.
        max_tokens: Maximum tokens for response.
        scoring_mode: ``"binary"`` (default) returns 1.0/0.0.
            ``"continuous"`` returns 0.0--1.0, giving GEPA richer
            gradient for optimization.  Both modes use
            structured JSON output.
        file_paths: Optional list of stage-relative file paths,
            one per item.  When provided together with ``stage_name``,
            the judge receives the file via ``TO_FILE()`` for
            multimodal evaluation.
        stage_name: Snowflake stage for the files. Pass a ``str``
            for a single stage or ``list[str]`` for per-row stages.

    Returns:
        List of (score, feedback) tuples in the same order as input items.
    """
    if not items:
        return []

    continuous = scoring_mode == "continuous"
    template = _LLM_JUDGE_CONTINUOUS_TEMPLATE if continuous else _LLM_JUDGE_BINARY_TEMPLATE
    parser = _parse_continuous_result if continuous else _parse_binary_result

    multimodal = bool(file_paths and stage_name)
    if multimodal:
        if len(file_paths) != len(items):
            raise ValueError(
                f"file_paths length ({len(file_paths)}) "
                f"must match items length ({len(items)})"
            )

    file_addendum = _LLM_JUDGE_FILE_ADDENDUM if multimodal else ""

    judge_prompts = [
        template.format(
            task_description=task_description,
            expected=expected,
            predicted=predicted,
        )
        + file_addendum
        for expected, predicted in items
    ]

    responses = RobustAIComplete.call_ai_complete(
        session,
        model=model_name,
        user_prompts=judge_prompts,
        temperature=temperature,
        max_tokens=max_tokens,
        response_schema=_LLM_JUDGE_CONTINUOUS_SCHEMA if continuous else _LLM_JUDGE_BINARY_SCHEMA,
        file_paths=file_paths if multimodal else None,
        stage_name=stage_name if multimodal else None,
    )

    outputs = [parser(r) for r in (responses or [])]

    if len(outputs) != len(items):
        raise RuntimeError(
            f"LLM judge returned {len(outputs)} responses for {len(items)} inputs"
        )

    return outputs


def llm_judge_core(
    expected: str,
    predicted: str,
    session: Session | None = None,
    *,
    task_description: str = "",
    model_name: str = LLM_JUDGE_DEFAULT_MODEL,
    temperature: float = LLM_JUDGE_DEFAULT_TEMP,
    max_tokens: int = LLM_JUDGE_DEFAULT_MAX_TOKENS,
    scoring_mode: str = "binary",
    **kwargs,
) -> tuple:
    """Uses an LLM to evaluate semantic correctness.

    Internally uses batched evaluation for consistency (even for single items).
    Extra ``kwargs`` (e.g. ``file_paths``, ``stage_name``) are
    forwarded to :func:`llm_judge_batch`.
    """
    if session is None:
        raise ValueError("llm_judge requires a session")
    results = llm_judge_batch(
        [(expected, predicted)],
        session,
        task_description,
        model_name,
        temperature,
        max_tokens,
        scoring_mode=scoring_mode,
        **kwargs,
    )
    return results[0] if results else (0.0, "Evaluation failed")


def compute_classification_objectives(items: list[tuple[str, str]]) -> dict[str, float]:
    """Compute precision, recall, F1, and accuracy from expected/predicted label pairs.

    For binary classification, auto-detects the positive class as the less
    frequent expected label (standard convention for imbalanced data).
    For multi-class, computes macro-averaged metrics across all classes.

    Args:
        items: List of (expected, predicted) string pairs.

    Returns:
        Dict with keys: accuracy, precision, recall, f1.
        Returns empty dict if input is empty.
    """
    if not items:
        return {}

    expected_labels, predicted_labels = zip(
        *[(str(e).strip().lower(), str(p).strip().lower()) for e, p in items]
    )

    accuracy = sum(
        1 for e, p in zip(expected_labels, predicted_labels) if e == p
    ) / len(items)

    all_labels = set(expected_labels) | set(predicted_labels)

    if len(all_labels) == 2:
        # Binary classification
        label_counts = Counter(expected_labels)
        pos = min(label_counts, key=label_counts.get)

        tp = sum(
            1
            for e, p in zip(expected_labels, predicted_labels)
            if e == pos and p == pos
        )
        fp = sum(
            1
            for e, p in zip(expected_labels, predicted_labels)
            if e != pos and p == pos
        )
        fn = sum(
            1
            for e, p in zip(expected_labels, predicted_labels)
            if e == pos and p != pos
        )

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            (2 * precision * recall / (precision + recall))
            if (precision + recall) > 0
            else 0.0
        )
    else:
        # Multi-class
        per_class_precision = []
        per_class_recall = []
        per_class_f1 = []

        for label in all_labels:
            tp = sum(
                1
                for e, p in zip(expected_labels, predicted_labels)
                if e == label and p == label
            )
            fp = sum(
                1
                for e, p in zip(expected_labels, predicted_labels)
                if e != label and p == label
            )
            fn = sum(
                1
                for e, p in zip(expected_labels, predicted_labels)
                if e == label and p != label
            )

            p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f = (2 * p * r / (p + r)) if (p + r) > 0 else 0.0

            per_class_precision.append(p)
            per_class_recall.append(r)
            per_class_f1.append(f)

        precision = sum(per_class_precision) / len(per_class_precision)
        recall = sum(per_class_recall) / len(per_class_recall)
        f1 = sum(per_class_f1) / len(per_class_f1)

    return {
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1-score": f1,
    }


def compute_metric(
    metric_name: str,
    expected: str,
    predicted: str,
    session: Session | None = None,
    custom_metric_udf: str | None = None,
    **kwargs,
) -> tuple[float, str]:
    """Dispatch to built-in or custom metric function.

    Args:
        metric_name: Name of the metric to use
        expected: Expected output value
        predicted: Predicted output value
        session: Snowpark session (required for llm_judge and custom metrics)
        custom_metric_udf: Fully qualified name of a Python UDF that
            implements the custom metric. The UDF must accept
            ``(EXPECTED VARCHAR, PREDICTED VARCHAR)`` and return VARIANT
            with ``score`` (float) and ``feedback`` (string) keys.
        **kwargs: Metric-specific options
    """
    metric_functions: dict[str, Callable[..., tuple[float, str]]] = {
        "exact_match": exact_match_core,
        "fuzzy_match": fuzzy_match_core,
        "contains_match": contains_match_core,
        "redaction_match": redaction_match_core,
        "llm_judge": llm_judge_core,
    }
    metric_fn = metric_functions.get(metric_name)
    if metric_fn is not None:
        return metric_fn(expected, predicted, session, **kwargs)

    if custom_metric_udf:
        if session is None:
            raise ValueError("custom_metric_udf requires a session")
        return _call_custom_metric_udf(custom_metric_udf, expected, predicted, session)

    raise ValueError(
        f"Unknown metric: {metric_name}. "
        f"Available built-in: {', '.join(sorted(metric_functions.keys()))}. "
        f"For custom metrics, provide fully qualified custom_metric_udf name."
    )


# Registry of metrics that have optimized batch implementations.
BATCH_FUNCTIONS: dict[str, Callable[..., list[tuple[float, str]]]] = {
    "llm_judge": llm_judge_batch,
}

PredictionExecutor = Callable[[list[dict[str, object]]], list[object]]


def compute_metric_batch(
    metric_name: str,
    items: list[tuple[str, str]],
    session: Session | None = None,
    custom_metric_udf: str | None = None,
    **kwargs,
) -> list[tuple[float, str]]:
    """Batch evaluate multiple (expected, predicted) pairs.

    Uses optimized batch implementation if available, otherwise falls back
    to sequential evaluation.

    Args:
        metric_name: Name of the metric to use
        items: List of (expected, predicted) tuples
        session: Snowpark session (required for llm_judge and custom metrics)
        custom_metric_udf: Fully qualified name of a custom metric UDF
        **kwargs: Metric-specific options

    Returns:
        List of (score, feedback) tuples in same order as input
    """
    if metric_name in BATCH_FUNCTIONS:
        if session is None:
            raise ValueError("batched functions require a session")
        return BATCH_FUNCTIONS[metric_name](items, session, **kwargs)

    if custom_metric_udf:
        if session is None:
            raise ValueError("custom_metric_udf requires a session")
        return _call_custom_metric_udf_batch(custom_metric_udf, items, session)

    return [
        compute_metric(metric_name, exp, pred, session, **kwargs) for exp, pred in items
    ]

def _collect_eval_rows(
    session,
    *,
    test_table: str,
    input_columns: list,
    label_column: str,
    metric_name: str,
    metric_options: dict | None,
    sample_size: int | None,
    function_name: str | None = None,
    include_predicted: bool = False,
) -> tuple[list[str], str | None, dict, list]:
    metric_opts, output_field, multi_expected_cols = parse_metric_options(
        metric_options
    )

    input_cols = [col.strip('"').strip("'") for col in input_columns]
    table_columns = get_table_column_names(session, test_table)
    validate_input_columns(table_columns, input_cols, test_table)

    expected_col_name = resolve_expected_column(table_columns, label_column)

    columns = ", ".join([quote_identifier(col) for col in input_cols])
    expected_expr = f"{quote_identifier(expected_col_name)} AS EXPECTED"

    if metric_name == "llm_judge" and len(multi_expected_cols) > 1:
        resolved_pairs = resolve_multi_output_columns(
            table_columns, multi_expected_cols
        )
        if resolved_pairs:
            output_field = None
            expected_expr = build_object_construct_expr(resolved_pairs, "EXPECTED")
        elif table_columns and expected_col_name.upper() not in table_columns:
            raise ValueError(
                "Expected output columns not found in test table. "
                f"Provided expected_columns={multi_expected_cols}, label_column={label_column}, "
                f"available_columns={sorted(table_columns)}"
            )

    predicted_expr = ""
    if include_predicted:
        if not function_name:
            raise ValueError("function_name is required when include_predicted=True")
        base_function_name = (
            function_name.split("(")[0] if "(" in function_name else function_name
        )
        udf_call = f"{base_function_name}({columns})"
        predicted_expr = f",\n            {udf_call} AS PREDICTED"

    query = f"""
        SELECT
            ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS ROW_ID,
            {columns},
            {expected_expr}{predicted_expr}
        FROM {test_table}
    """
    if sample_size:
        query += f" LIMIT {sample_size}"

    results_data = session.sql(query).collect()
    return input_cols, output_field, metric_opts, results_data


def evaluate(
    session,
    function_name: str,
    test_table: str,
    input_columns: list,
    label_column: str,
    metric_name: str,
    model_name: str = LLM_JUDGE_DEFAULT_MODEL,
    sample_size: int | None = None,
    results_table: str | None = None,
    metric_options: dict | None = None,
    max_length: int = 500,
    custom_metric_udf: str | None = None,
    run_id: str | None = None,
    executor: PredictionExecutor | None = None,
) -> float:
    """Evaluate an AI function against a test dataset.

    The function is called directly without parameter overrides. The model
    and system prompt are baked into the function body. To evaluate with a
    different model or prompt, create a temporary function and pass its
    name as ``function_name``.

    Args:
        session: Snowpark session
        function_name: Fully qualified AI function name
        test_table: Fully qualified test data table
        input_columns: List of column names to pass to function
        label_column: Column containing expected outputs
        metric_name: Metric to use for evaluation
        model_name: Model name for results tracking metadata
        sample_size: Number of rows to evaluate (None = all)
        results_table: Table to save detailed results (None = don't save)
        metric_options: Metric-specific options
        max_length: Max length for truncated fields (default 500)
        custom_metric_udf: Fully qualified name of a custom metric UDF.
        run_id: Optional external run ID for tracking (auto-generated if None).
    """
    input_cols, output_field, metric_opts, results_data = _collect_eval_rows(
        session,
        test_table=test_table,
        input_columns=input_columns,
        label_column=label_column,
        metric_name=metric_name,
        metric_options=metric_options,
        sample_size=sample_size,
        function_name=function_name,
        include_predicted=executor is None,
    )

    validate_stage_file_access(
        session,
        stage_name=metric_opts.get("stage_name"),
        file_columns=metric_opts.get("file_columns"),
        table_name=test_table,
    )

    if not results_data:
        return 0.0

    if executor is None:
        predicted_raw_list = []
        for row in results_data:
            try:
                v = row["PREDICTED"]
            except Exception:
                v = None
            predicted_raw_list.append(v if v is not None else "")
    else:
        executor_rows: list[dict[str, object]] = []
        for row in results_data:
            d = {c: row[c] if c in row else None for c in input_cols}
            executor_rows.append(d)
        predicted_raw_list = executor(executor_rows)

    if len(predicted_raw_list) != len(results_data):
        raise ValueError(
            f"Executor returned {len(predicted_raw_list)} predictions for {len(results_data)} rows"
        )

    results = []
    total_score = 0.0

    # Collect all row metadata and identify items for batch evaluation
    row_metadata = []
    batch_items = []  # (idx, expected, predicted)

    for idx, row in enumerate(results_data):
        row_id = row["ROW_ID"]
        expected = to_text(row["EXPECTED"])

        predicted_raw_obj = predicted_raw_list[idx]
        error_message = None
        if (
            isinstance(predicted_raw_obj, str)
            and predicted_raw_obj.startswith("INFERENCE_ERROR:")
        ):
            error_message = predicted_raw_obj
            predicted_raw = ""
        else:
            predicted_raw = predicted_raw_obj if predicted_raw_obj is not None else ""

        predicted = _extract_output_field(predicted_raw, output_field)

        input_summary = "; ".join(
            [f"{col}={str(row[col])[:max_length]}" for col in input_cols]
        )

        row_metadata.append(
            {
                "row_id": row_id,
                "expected": expected,
                "predicted": predicted,
                "input_summary": input_summary,
                "error_message": error_message,
            }
        )

        if expected and predicted:
            batch_items.append((idx, expected, predicted))

    # Batch evaluate all valid items
    if batch_items:
        items_for_batch = [(exp, pred) for _, exp, pred in batch_items]
        # output_field is consumed above to select the field from VARIANT outputs.
        # Do not pass it into metric functions (e.g., llm_judge_batch), which
        # only accept metric-specific options.
        batch_results = compute_metric_batch(
            metric_name, items_for_batch, session, custom_metric_udf, **metric_opts
        )
        batch_result_map = {
            batch_items[i][0]: batch_results[i] for i in range(len(batch_items))
        }
    else:
        batch_result_map = {}

    # Process results
    for idx, meta in enumerate(row_metadata):
        if not meta["expected"]:
            score, feedback = 0.0, "Empty expected value"
        elif meta.get("error_message"):
            score, feedback = 0.0, meta["error_message"]
        elif not meta["predicted"]:
            score, feedback = 0.0, "Empty predicted value"
        elif idx in batch_result_map:
            score, feedback = batch_result_map[idx]
        else:
            score, feedback = 0.0, "Evaluation error"

        total_score += score
        results.append(
            (
                meta["row_id"],
                meta["input_summary"][:max_length],
                meta["expected"][:max_length],
                meta["predicted"][:max_length],
                score,
                feedback[:max_length] if feedback else None,
                meta.get("error_message"),
            )
        )

    if results_table:
        session.sql(f"""
            CREATE TABLE IF NOT EXISTS {results_table} (
                RUN_ID VARCHAR,
                ROW_ID INTEGER,
                INPUT_TEXT VARCHAR,
                EXPECTED VARCHAR,
                PREDICTED VARCHAR,
                SCORE FLOAT,
                FEEDBACK VARCHAR,
                ERROR_MESSAGE VARCHAR,
                METRIC_NAME VARCHAR,
                MODEL_NAME VARCHAR,
                EVAL_TIMESTAMP TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
            )
        """).collect()

        # Use provided run_id or generate one (format: ai_func_eval_FUNCNAME_timestamp_ms)
        if not run_id:
            func_short_name = function_name.split(".")[-1].split("(")[0]
            run_id = f"ai_func_eval_{func_short_name}_{int(time.time() * 1000)}"

        result_records = [
            {
                "RUN_ID": run_id,
                "ROW_ID": r[0],
                "INPUT_TEXT": r[1] or "",
                "EXPECTED": r[2] or "",
                "PREDICTED": r[3] or "",
                "SCORE": r[4],
                "FEEDBACK": r[5] or "",
                "ERROR_MESSAGE": r[6] or "",
                "METRIC_NAME": metric_name,
                "MODEL_NAME": model_name,
            }
            for r in results
        ]
        results_df = pd.DataFrame(result_records)
        db, schema, table = results_table.split(".")
        session.write_pandas(
            results_df,
            table,
            database=db,
            schema=schema,
            auto_create_table=False,
            overwrite=False,
        )

    avg_score = total_score / len(results_data) if results_data else 0

    # Clean up async task if this was called from one (run_id matches task name)
    if run_id and run_id.startswith("ai_func_eval_"):
        try:
            parts = function_name.split("(")[0].split(".")
            if len(parts) >= 3:
                task_fqn = f"{parts[0]}.{parts[1]}.{run_id}"
                session.sql(f"DROP TASK IF EXISTS {task_fqn}").collect()
        except Exception:
            pass  # Cleanup failure should not break the evaluation

    return avg_score


@with_ai_sql_error_handling_use_fail_on_error_disabled_for_sproc()
@with_custom_ai_function_query_tag("SPROC_EVALUATE")
def evaluate_handler(
    session,
    function_name: str,
    test_table: str,
    input_columns: list,
    label_column: str,
    metric_name: str,
    model_name: str = LLM_JUDGE_DEFAULT_MODEL,
    sample_size: int | None = None,
    results_table: str | None = None,
    metric_options: dict | None = None,
    max_length: int = 500,
    custom_metric_udf: str | None = None,
    run_id: str | None = None,
) -> float:
    """SPROC entry point for EVALUATE_AI_FUNCTION.

    Thin wrapper around :func:`evaluate` that exposes only the parameters
    available through the stored procedure interface.
    """
    return evaluate(
        session,
        function_name,
        test_table,
        input_columns,
        label_column,
        metric_name,
        model_name=model_name,
        sample_size=sample_size,
        results_table=results_table,
        metric_options=metric_options,
        max_length=max_length,
        custom_metric_udf=custom_metric_udf,
        run_id=run_id,
    )
