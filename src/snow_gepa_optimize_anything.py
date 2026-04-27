# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Function body optimization for Snowflake AI functions using GEPA optimize_anything.

This module provides ``run_body_optimization``, the default optimization
path that optimises the **entire SQL function body** (not just the system
prompt).  It is invoked from ``run_optimization`` in ``snow_gepa_optimize``
when ``optimize_mode="body"`` (the default).

The candidate in this mode is a raw SQL body string.  The reflection LLM
is free to change model references, add SQL post-processing, restructure
the pipeline, etc. -- anything that produces valid Snowflake SQL with at
least one ``AI_COMPLETE`` call while preserving the function signature.

GEPA's ``OptimizeAnythingAdapter`` is replaced during ``run_body_optimization``
so each ``evaluate(batch, candidate)`` call performs **one** Snowpark
``collect()`` for the whole batch (after optional per-row adapter-cache hits).
The per-example ``make_body_evaluator`` closure remains for direct / unit tests.
"""

import json
import contextlib
import logging
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from textwrap import dedent
from typing import Any, Callable, Literal

from gepa.adapters.optimize_anything_adapter import optimize_anything_adapter
from gepa.adapters.optimize_anything_adapter.optimize_anything_adapter import (
    OptimizeAnythingAdapter,
)
from gepa.core.adapter import EvaluationBatch
import gepa.optimize_anything as _oa_module
from gepa.logging.logger import StdOutLogger
from gepa.optimize_anything import (
    GEPAConfig,
    EngineConfig,
    MergeConfig,
    ReflectionConfig,
    TrackingConfig,
    _build_reflection_prompt_template,
    log,
    optimize_anything,
)
from snowflake.snowpark import Session
from snowflake.snowpark.functions import call_function, col, parse_json

from metrics_core import (
    evaluate,
    get_table_column_names,
    parse_metric_options,
    resolve_expected_column,
    validate_input_columns,
    compute_metric,
    compute_metric_batch,
)
from snow_gepa_adapter import (
    Evaluator,
    SnowflakeDataInst,
    SnowflakeLLM,
    load_dataset,
)
from snow_gepa_optimize import (
    MaxTotalBudgetStopper,
    split_dataset,
    get_function_ddl,
)

from custom_ai_function_utils import (
    _extract_semi_structured_params,
    build_temp_ddl_from_body,
    build_temp_function_name,
    extract_file_type_params,
    extract_to_file_refs,
    normalize_ddl_to_dollar_quoting,
    validate_stage_file_access,
)
from snow_gepa_experiment import (
    create_gepa_experiment,
    save_failed_run_to_experiment,
    save_optimization_to_experiment,
)

logger = logging.getLogger(__name__)

DEFAULT_REFLECTION_MINIBATCH_SIZE = 10
DEFAULT_PERFECT_SCORE = 1.0
DEFAULT_AUTO_BUDGET: Literal["demo", "light", "medium", "heavy"] = "light"
DEFAULT_VALIDATION_FRACTION = 0.5
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 8192

# Batched adapter uses one Snowflake round-trip per GEPA ``evaluate`` call;
# engine-level parallel eval is disabled (redundant and unsafe with temp DDL).
DEFAULT_ENGINE_PARALLEL = False

# Identical (candidate, example) pairs reuse scores (e.g. repeated val passes).
DEFAULT_CACHE_EVALUATIONS = True
DEFAULT_MAX_MERGE_INVOCATIONS = 5

_STR_CANDIDATE_KEY = "current_candidate"


@dataclass
class _BodyBatchEvalContext:
    """Holds Snowflake + metric state for batched adapter evaluation."""

    session: Session
    original_ddl: str
    temp_function_name: str
    input_columns: list[str]
    metric_evaluator: Evaluator
    compile_lock: threading.Lock = field(default_factory=threading.Lock)
    compiled_body: str | None = None
    compile_error: str | None = None


_thread_local = threading.local()


def _unwrap_str_candidate(candidate: dict[str, str]) -> str:
    body = candidate.get(_STR_CANDIDATE_KEY)
    if body is not None:
        return str(body)
    if candidate:
        return str(next(iter(candidate.values())))
    return ""


def _put_adapter_eval_cache(
    adapter: OptimizeAnythingAdapter,
    candidate: dict[str, str],
    example: object,
    result: tuple[float, Any, dict],
) -> None:
    if adapter.cache_mode == "off":
        return
    cache_key = adapter._cache_key(candidate, example)
    with adapter._eval_cache_lock:
        adapter._eval_cache[cache_key] = result
        if adapter.cache_mode == "disk":
            adapter._save_cache_entry(cache_key, result)


class _BatchedBodyOptimizeAnythingAdapter(OptimizeAnythingAdapter):
    """Runs the temp UDF once per ``evaluate()`` batch (one ``collect()``)."""

    def evaluate(
        self,
        batch: list,
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> EvaluationBatch:
        ctx = getattr(_thread_local, "body_batch_ctx", None)
        if ctx is None or self.refiner_config is not None or len(batch) == 0:
            return super().evaluate(batch, candidate, capture_traces=capture_traces)

        raw_results = self._sql_body_batched_raw_results(batch, candidate, ctx)
        eval_output: list = []
        for score, _, side_info in raw_results:
            out = (score, candidate, side_info)
            eval_output.append((score, out, side_info))
        for example, (score, _, side_info) in zip(batch, eval_output, strict=True):
            self._update_best_example_evals(example, score, side_info)

        scores = [score for score, _, _ in eval_output]
        side_infos = [info for _, _, info in eval_output]
        outputs = [out for _, out, _ in eval_output]
        objective_scores: list[dict[str, float]] = []
        for side_info in side_infos:
            objective_score: dict[str, float] = {}
            if "scores" in side_info:
                objective_score.update(side_info["scores"])
            for param_name in candidate.keys():
                key = param_name + "_specific_info"
                if key in side_info and "scores" in side_info[key]:
                    objective_score.update(
                        {
                            f"{param_name}::{k}": v
                            for k, v in side_info[key]["scores"].items()
                        }
                    )
            objective_scores.append(objective_score)

        return EvaluationBatch(
            outputs=outputs,
            scores=scores,
            trajectories=side_infos,
            objective_scores=objective_scores,
        )

    def _sql_body_batched_raw_results(
        self,
        batch: list,
        candidate: dict[str, str],
        ctx: _BodyBatchEvalContext,
    ) -> list[tuple[float, Any, dict]]:
        body = _unwrap_str_candidate(candidate)
        n = len(batch)
        raw: list[tuple[float, Any, dict] | None] = [None] * n

        with ctx.compile_lock:
            if ctx.compiled_body != body:
                ctx.compiled_body = body
                ctx.compile_error = None
                try:
                    _compile_temp_body_function(
                        ctx.session,
                        ctx.original_ddl,
                        body,
                        ctx.temp_function_name,
                    )
                except Exception as e:
                    ctx.compile_error = str(e)
                    log(f"Compilation failed: {e}")

        if ctx.compile_error is not None:
            err = ctx.compile_error
            for i, example in enumerate(batch):
                side_info = {
                    "Error": err,
                    "Candidate (truncated)": body[:500],
                    "Feedback": (
                        f"Function body failed SQL compilation: {err}. "
                        "Fix the SQL syntax."
                    ),
                }
                triple = (0.0, None, side_info)
                raw[i] = triple
                _put_adapter_eval_cache(self, candidate, example, triple)
            return [r for r in raw]  # type: ignore[misc]

        need_indices: list[int] = []
        for i, example in enumerate(batch):
            if self.cache_mode != "off":
                ck = self._cache_key(candidate, example)
                with self._eval_cache_lock:
                    hit = self._eval_cache.get(ck)
                if hit is not None:
                    raw[i] = hit
                    continue
            need_indices.append(i)

        if need_indices:
            rows = [
                {c: batch[i]["inputs"].get(c, "") for c in ctx.input_columns}
                for i in need_indices
            ]
            try:
                outs = _call_compiled_temp_function(
                    ctx.session,
                    ctx.temp_function_name,
                    rows,
                    original_ddl=ctx.original_ddl,
                )
            except Exception as e:
                log(f"Runtime error (batch): {e}")
                for i in need_indices:
                    example = batch[i]
                    side_info = {
                        "Error": str(e),
                        "Candidate (truncated)": body[:500],
                        "Feedback": (f"Function body produced runtime error: {e}."),
                    }
                    triple = (0.0, None, side_info)
                    raw[i] = triple
                    _put_adapter_eval_cache(self, candidate, example, triple)
            else:
                items = []
                responses = []
                for j, i in enumerate(need_indices):
                    response = str(outs[j]) if j < len(outs) else ""
                    responses.append(response)
                    items.append((batch[i]["answer"], response))

                batch_results = compute_metric_batch(
                    ctx.metric_evaluator.metric_name,
                    items,
                    ctx.session,
                    ctx.metric_evaluator.custom_metric_udf,
                    **dict(ctx.metric_evaluator.kwargs),
                )

                for j, i in enumerate(need_indices):
                    example = batch[i]
                    quality_score, feedback = batch_results[j]
                    log(
                        f"Quality: {quality_score:.3f}, Feedback: {feedback}",
                    )
                    side_info = {
                        "Input": "\n".join(
                            f"{k}: {v}" for k, v in example["inputs"].items()
                        ),
                        "Output": responses[j],
                        "Expected": example["answer"],
                        "Feedback": feedback,
                    }
                    triple = (quality_score, None, side_info)
                    raw[i] = triple
                    _put_adapter_eval_cache(self, candidate, example, triple)

        assert all(r is not None for r in raw)
        return [r for r in raw]  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DDL helpers
# ---------------------------------------------------------------------------


def extract_body_from_ddl_string(ddl: str, function_name: str = "") -> str:
    """Extract the SQL body from a function DDL string.

    The DDL is first normalised to ``$$`` quoting so the body sits between
    two ``$$`` markers.
    """
    ddl = normalize_ddl_to_dollar_quoting(ddl)
    match = re.search(r"\$\$(.*?)\$\$", ddl, re.DOTALL)
    if not match:
        raise ValueError(
            f"Could not extract function body from DDL for: {function_name}. "
            "Expected body between $$ delimiters."
        )
    return match.group(1).strip()


def extract_signature_from_ddl_string(ddl: str) -> str:
    """Extract the parameter list and RETURNS clause from DDL.

    Returns a string like ``(input_text VARCHAR) RETURNS VARCHAR``.
    """
    ddl = normalize_ddl_to_dollar_quoting(ddl)
    match = re.search(
        r"FUNCTION\s+\S+\s*(\([^)]*\)\s+RETURNS\s+\S+)",
        ddl,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    raise ValueError(
        "Could not extract function signature (parameters and RETURNS clause) from DDL."
    )


def reconstruct_ddl(original_ddl: str, optimized_body: str) -> str:
    """Build a deployable CREATE FUNCTION DDL with a new body."""
    ddl = normalize_ddl_to_dollar_quoting(original_ddl)
    return re.sub(
        r"\$\$.*?\$\$",
        f"$$\n{optimized_body}\n$$",
        ddl,
        flags=re.DOTALL,
    )


def extract_model_from_ddl_string(ddl: str, function_name: str = "") -> str:
    """Extract the model name from a raw DDL string."""
    ddl = normalize_ddl_to_dollar_quoting(ddl)
    match = re.search(r"model\s*=>\s*'([^']*)'", ddl, re.IGNORECASE)
    if not match:
        raise ValueError(
            f"Could not extract model name from DDL for function: {function_name}."
        )
    return match.group(1)


# ---------------------------------------------------------------------------
# Objective / background construction
# ---------------------------------------------------------------------------


def build_objective_and_background(
    function_name: str,
    function_signature: str,
    original_body: str,
    available_models: list[str],
    metric_name: str,
) -> tuple[str, str]:
    """Build the objective and background strings for the reflection LLM."""
    objective = (
        f"Optimize the SQL function body of {function_name} to maximize quality "
        f"(measured by {metric_name}). "
        f"The function must maintain the same input/output contract."
    )

    background = dedent(f"""
        Function signature: {function_signature}

        Available Snowflake Cortex models: {', '.join(available_models)}

        Constraints:
        - The function body MUST call AI_COMPLETE at least once
        - The function MUST accept the same input parameters and return the same type
        - Valid Snowflake SQL syntax is required
        - Output the COMPLETE function body only, with no surrounding DDL
        - Preserve any result accessor suffix (e.g. :field_name::TYPE) at the end of the expression

        AI_COMPLETE correct syntax:
        AI_COMPLETE(
            model=>'model_name',
            messages=>ARRAY_CONSTRUCT(
                OBJECT_CONSTRUCT('role', 'system', 'content', '<system_prompt>'),
                OBJECT_CONSTRUCT('role', 'user', 'content', <input_expression>)
            ),
            response_format=>PARSE_JSON('<json_schema>')
        )

        Available SQL functions: CONCAT, REPLACE, REGEXP_REPLACE, SPLIT, ARRAY_AGG,
        PARSE_JSON, OBJECT_CONSTRUCT, TRY_PARSE_JSON, FLATTEN, CASE, IFF, etc.

        Current implementation for reference:
        {original_body}
    """).strip()
    return objective, background


def estimate_body_reflection_weight(
    seed_body: str,
    objective: str,
    background: str,
    trainset: list[SnowflakeDataInst],
    metric_name: str = "exact_match",
    reflection_minibatch_size: int = DEFAULT_REFLECTION_MINIBATCH_SIZE,
) -> int:
    """Estimate the relative cost of one reflection call vs one metric call.

    Similar to ``MaxTotalBudgetStopper.estimate_reflection_weight`` in
    ``snow_gepa_optimize`` but adapted for body-mode where the reflection
    prompt includes objective, background, and the full candidate body.

    The reflection prompt is built via GEPA's
    ``_build_reflection_prompt_template`` so the estimate matches the actual
    template used during optimization.
    """
    if not trainset:
        return 1

    avg_input_len = sum(
        sum(len(str(v)) for v in item["inputs"].values()) for item in trainset
    ) / len(trainset)

    metric_prompt_len = len(seed_body) + avg_input_len
    if metric_prompt_len == 0:
        return 1

    template = _build_reflection_prompt_template(
        objective=objective,
        background=background,
    )
    sample = trainset[:reflection_minibatch_size]
    side_info_sample = "\n".join(
        f"Input: {' '.join(str(v) for v in item['inputs'].values())} | "
        f"Expected: {item.get('answer', '')} | Feedback: Needs improvement."
        for item in sample
    )
    reflection_prompt_len = len(template) + len(seed_body) + len(side_info_sample)

    return max(1, round(reflection_prompt_len / metric_prompt_len))


# ---------------------------------------------------------------------------
# Body-based evaluator
# ---------------------------------------------------------------------------


def _compile_temp_body_function(
    session: Session,
    original_ddl: str,
    candidate_body: str,
    temp_function_name: str,
) -> None:
    """Create a temp function from *candidate_body* (compile-only).

    Raises on SQL compilation errors (invalid syntax, unknown functions, etc.)
    so the caller can discard the candidate immediately without running it.
    """
    temp_ddl = build_temp_ddl_from_body(
        original_ddl, candidate_body, temp_function_name
    )
    session.sql(temp_ddl).collect()


def _call_compiled_temp_function(
    session: Session,
    temp_function_name: str,
    rows: list[dict[str, object]],
    original_ddl: str = "",
) -> list[object]:
    """Call a previously compiled temp function on *rows*.

    The temp function must already exist (created by
    ``_compile_temp_body_function``).  Returns one result per row.

    When *original_ddl* is provided, semi-structured parameters (ARRAY,
    VARIANT, OBJECT) are detected and normalized.  Training data values
    arrive in mixed forms: ``load_dataset`` may ``json.loads`` a stored
    JSON string into a Python list/dict, while other paths pass the raw
    string.  To avoid mixed-type columns (which break Snowpark schema
    inference), Python lists/dicts are serialized to JSON strings so
    every value is VARCHAR, then ``parse_json(col(...))`` uniformly
    restores the semi-structured type at call time.  ``PARSE_JSON``
    returns ``VARIANT`` which Snowflake auto-casts to ``ARRAY`` or
    ``OBJECT`` as needed by the function signature.  This mirrors
    ``TempAIFunction.call_rows``.
    """
    if not rows:
        return []

    structured_params = (
        _extract_semi_structured_params(original_ddl) if original_ddl else set()
    )

    # Normalize semi-structured values to JSON strings so the column is
    # uniformly VARCHAR (avoids mixed-type schema inference issues).
    indexed_rows = []
    for idx, row in enumerate(rows):
        r: dict[str, object] = {"__ROW_ID": idx}
        for k, v in row.items():
            if (
                structured_params
                and k.upper() in structured_params
                and isinstance(v, (list, tuple, dict))
            ):
                r[k] = json.dumps(v)
            else:
                r[k] = v
        indexed_rows.append(r)

    all_cols: list[str] = ["__ROW_ID"]
    seen = {"__ROW_ID"}
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                all_cols.append(k)

    arg_cols = []
    for c in all_cols:
        if c == "__ROW_ID":
            continue
        if structured_params and c.upper() in structured_params:
            arg_cols.append(parse_json(col(c)))
        else:
            arg_cols.append(col(c))

    df = session.create_dataframe(indexed_rows)
    df = df.select(*[col(c) for c in all_cols])

    result_col = call_function(temp_function_name, *arg_cols).alias("RESULT")
    res_df = df.select(col("__ROW_ID").alias("ROW_ID"), result_col)
    collected = res_df.sort(col("ROW_ID")).collect()

    out: list[object] = []
    for r in collected:
        v = r["RESULT"]
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(v if v is not None else "")
    return out


def _call_temp_body_function(
    session: Session,
    original_ddl: str,
    candidate_body: str,
    temp_function_name: str,
    rows: list[dict[str, object]],
) -> list[object]:
    """Create a temp function from *candidate_body* and call it on *rows*.

    Convenience wrapper that combines compile + call.  Used by
    ``_make_body_executor`` for test-set evaluation where there is no
    need for per-candidate caching.
    """
    _compile_temp_body_function(
        session, original_ddl, candidate_body, temp_function_name
    )
    return _call_compiled_temp_function(
        session,
        temp_function_name,
        rows,
        original_ddl=original_ddl,
    )


def make_body_evaluator(
    session: Session,
    original_ddl: str,
    temp_function_name: str,
    input_columns: list[str],
    metric_evaluator: Evaluator,
) -> Callable:
    """Factory returning a closure that conforms to the optimize_anything Evaluator protocol.

    The evaluator compiles each new candidate body into a temp function
    **once** and caches the result.  If compilation fails (SQL syntax
    error), all examples in the same minibatch immediately receive
    ``score=0`` without attempting execution -- this avoids wasting
    evaluation budget on candidates that cannot compile.

    When GEPA uses ``parallel=True``, multiple threads may evaluate different
    examples for the same candidate concurrently.  Only compilation + cache
    updates are serialized (same temp UDF name); Snowflake execution and metric
    scoring can overlap across threads on a thread-safe Snowpark session.
    """
    compiled_candidate: str | None = None
    compile_error: str | None = None
    _compile_lock = threading.Lock()

    def evaluator(candidate: str, example: dict) -> tuple[float, dict]:
        nonlocal compiled_candidate, compile_error

        # --- Compile once per candidate (serialized across threads) ---
        if candidate != compiled_candidate:
            with _compile_lock:
                if candidate != compiled_candidate:
                    compiled_candidate = candidate
                    compile_error = None
                    try:
                        _compile_temp_body_function(
                            session,
                            original_ddl,
                            candidate,
                            temp_function_name,
                        )
                    except Exception as e:
                        compile_error = str(e)
                        log(f"Compilation failed: {e}")

        if compile_error is not None:
            return 0.0, {
                "Error": compile_error,
                "Candidate (truncated)": candidate[:500],
                "Feedback": (
                    f"Function body failed SQL compilation: {compile_error}. "
                    "Fix the SQL syntax."
                ),
            }

        # --- Execute + score ---
        try:
            row = {c: example["inputs"].get(c, "") for c in input_columns}
            results = _call_compiled_temp_function(
                session,
                temp_function_name,
                [row],
                original_ddl=original_ddl,
            )
            response = str(results[0]) if results else ""

            quality_score, feedback = compute_metric(
                metric_evaluator.metric_name,
                example["answer"],
                response,
                session,
                metric_evaluator.custom_metric_udf,
                **dict(metric_evaluator.kwargs),
            )

            log(f"Quality: {quality_score:.3f}, Feedback: {feedback}")

            return quality_score, {
                "Input": "\n".join(f"{k}: {v}" for k, v in example["inputs"].items()),
                "Output": response,
                "Expected": example["answer"],
                "Feedback": feedback,
            }
        except Exception as e:
            log(f"Runtime error: {e}")
            return 0.0, {
                "Error": str(e),
                "Candidate (truncated)": candidate[:500],
                "Feedback": f"Function body produced runtime error: {e}.",
            }

    return evaluator


def _make_body_executor(
    session: Session,
    original_ddl: str,
    candidate_body: str,
    temp_function_name: str,
) -> Callable[[list[dict[str, object]]], list[object]]:
    """Build a PredictionExecutor for ``evaluate``."""

    def executor(rows: list[dict[str, object]]) -> list[object]:
        return _call_temp_body_function(
            session,
            original_ddl,
            candidate_body,
            temp_function_name,
            rows,
        )

    return executor


def _swap_model_in_body(body: str, new_model: str) -> str:
    """Replace the first ``model=>'...'`` reference in a SQL body."""
    return re.sub(
        r"(model\s*=>\s*')[^']*(')",
        rf"\g<1>{new_model}\2",
        body,
        count=1,
        flags=re.IGNORECASE,
    )


def _run_single_model_body_optimization(
    *,
    model: str,
    session: Session,
    original_ddl: str,
    seed_body: str,
    function_name: str,
    function_signature: str,
    trainset: list[SnowflakeDataInst],
    valset: list[SnowflakeDataInst],
    input_col_names: list[str],
    input_columns: list,
    metric_evaluator: Evaluator,
    reflection_model: str,
    temperature: float,
    max_tokens: int,
    resolved_budget: int,
    reflection_weight: int,
    models: list[str],
    metric_name: str,
    test_table: str | None,
    label_column: str,
    dataset_expected_columns: list[str] | None,
    run_id: str,
    aggregation_metric: str | None,
    experiment_name: str | None = None,
) -> dict:
    """Run body optimization for a single model. Designed for parallel execution."""
    model_start = time.time()

    model_seed_body = _swap_model_in_body(seed_body, model)

    model_suffix = re.sub(r"[^A-Za-z0-9]", "_", model).upper()
    temp_fn = build_temp_function_name(function_name, f"__OPT_BODY_{model_suffix}")

    body_evaluator = make_body_evaluator(
        session,
        original_ddl,
        temp_fn,
        input_col_names,
        metric_evaluator,
    )
    objective, background = build_objective_and_background(
        function_name,
        function_signature,
        model_seed_body,
        models,
        metric_name,
    )

    reflection_lm = SnowflakeLLM(
        session=session,
        model=reflection_model or model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    budget_stopper = MaxTotalBudgetStopper(
        reflection_lm,
        max_budget=resolved_budget,
        reflection_call_weight=reflection_weight,
    )

    run_dir_ctx = (
        tempfile.TemporaryDirectory(prefix="gepa_body_")
        if experiment_name
        else contextlib.nullcontext(None)
    )

    with run_dir_ctx as gepa_run_dir:
        batch_ctx = _BodyBatchEvalContext(
            session=session,
            original_ddl=original_ddl,
            temp_function_name=temp_fn,
            input_columns=input_col_names,
            metric_evaluator=metric_evaluator,
        )
        _thread_local.body_batch_ctx = batch_ctx
        try:
            engine_kwargs: dict = dict(
                max_metric_calls=None,
                candidate_selection_strategy="pareto",
                parallel=DEFAULT_ENGINE_PARALLEL,
                raise_on_exception=False,
                cache_evaluation=DEFAULT_CACHE_EVALUATIONS,
                cache_evaluation_storage="memory",
                # Use 'instance' frontier since our evaluators return per-row
                # scores only, not objective_scores required by 'hybrid'.
                frontier_type="instance",
            )
            if gepa_run_dir is not None:
                engine_kwargs["run_dir"] = gepa_run_dir

            result = optimize_anything(
                seed_candidate=model_seed_body,
                evaluator=body_evaluator,
                dataset=trainset,
                valset=valset,
                objective=objective,
                background=background,
                config=GEPAConfig(
                    engine=EngineConfig(**engine_kwargs),
                    reflection=ReflectionConfig(
                        reflection_lm=reflection_lm,
                        skip_perfect_score=True,
                        perfect_score=DEFAULT_PERFECT_SCORE,
                        reflection_minibatch_size=DEFAULT_REFLECTION_MINIBATCH_SIZE,
                    ),
                    merge=MergeConfig(
                        max_merge_invocations=DEFAULT_MAX_MERGE_INVOCATIONS,
                    ),
                    # Always pass a safe logger to prevent GEPA from creating
                    # a file-based Logger that mutates global sys.stdout/stderr
                    # — unsafe when multiple models run via ThreadPoolExecutor.
                    tracking=TrackingConfig(logger=StdOutLogger()),
                    stop_callbacks=[budget_stopper],
                ),
            )
        except Exception as e:
            error_msg = str(e)
            logger.error("[OPTIMIZATION_ERROR] %s: %s", model, error_msg)
            if experiment_name:
                save_failed_run_to_experiment(
                    session,
                    experiment_name,
                    function_name=function_name,
                    model=model,
                    error_message=error_msg,
                    prompt_snippet=model_seed_body[:200],
                    elapsed_seconds=round(time.time() - model_start, 2),
                )
            return {
                "model": model,
                "status": "failed",
                "error": error_msg,
                "elapsed_seconds": round(time.time() - model_start, 2),
            }
        finally:
            _thread_local.body_batch_ctx = None

        best_body = result.best_candidate
        if isinstance(best_body, dict):
            best_body = next(iter(best_body.values()))
        best_body = str(best_body)

        best_val_score = (
            result.val_aggregate_scores[result.best_idx]
            if result.val_aggregate_scores
            else None
        )
        seed_val_score = (
            result.val_aggregate_scores[0] if result.val_aggregate_scores else None
        )

        model_elapsed = round(time.time() - model_start, 2)
        model_output: dict = {
            "model": model,
            "status": "completed",
            "elapsed_seconds": model_elapsed,
            "best_prompt": best_body,
            "best_val_score": best_val_score,
            "seed_val_score": seed_val_score,
            "total_candidates": len(result.candidates),
            "total_metric_calls": result.total_metric_calls,
            "total_reflection_calls": reflection_lm.call_count,
            "all_val_scores": result.val_aggregate_scores,
            "reflection_model": reflection_model or model,
        }

        # Test-set evaluation and experiment storage are wrapped in
        # try/except so that transient session errors (e.g. shared-session
        # I/O races across threads) degrade gracefully to validation-only
        # scores instead of losing the entire optimization result.
        try:
            if test_table and original_ddl:
                eval_metric_options = dict(metric_evaluator.kwargs)
                if metric_evaluator.metric_name == "llm_judge":
                    eval_metric_options["scoring_mode"] = "binary"
                if dataset_expected_columns:
                    eval_metric_options["expected_columns"] = dataset_expected_columns

                test_temp_fn = build_temp_function_name(
                    function_name,
                    f"__OPT_TEST_{model_suffix}",
                )
                seed_executor = _make_body_executor(
                    session,
                    original_ddl,
                    model_seed_body,
                    test_temp_fn,
                )
                seed_test_score, seed_eval_details = evaluate(
                    session=session,
                    function_name=test_temp_fn,
                    test_table=test_table,
                    input_columns=input_columns,
                    label_column=label_column,
                    metric_name=metric_evaluator.metric_name,
                    custom_metric_udf=metric_evaluator.custom_metric_udf,
                    metric_options=eval_metric_options,
                    model_name=model,
                    executor=seed_executor,
                    run_id=run_id,
                    split="test_seed",
                )
                best_executor = _make_body_executor(
                    session,
                    original_ddl,
                    best_body,
                    test_temp_fn,
                )
                best_test_score, best_eval_details = evaluate(
                    session=session,
                    function_name=test_temp_fn,
                    test_table=test_table,
                    input_columns=input_columns,
                    label_column=label_column,
                    metric_name=metric_evaluator.metric_name,
                    custom_metric_udf=metric_evaluator.custom_metric_udf,
                    metric_options=eval_metric_options,
                    model_name=model,
                    executor=best_executor,
                    run_id=run_id,
                    split="test_best",
                )
                model_output["seed_test_score"] = seed_test_score
                model_output["best_test_score"] = best_test_score
                model_output["_seed_eval_details"] = seed_eval_details
                model_output["_best_eval_details"] = best_eval_details
                test_count = session.sql(
                    f"SELECT COUNT(*) FROM {test_table}"
                ).collect()[0][0]
                model_output["num_test_examples"] = test_count
        except Exception as test_eval_err:
            logger.warning(
                "[TEST_EVAL_ERROR] %s: test-set evaluation failed, "
                "falling back to validation scores: %s",
                model,
                test_eval_err,
            )

        if "seed_test_score" in model_output:
            model_output["seed_score"] = model_output["seed_test_score"]
            model_output["best_score"] = model_output["best_test_score"]
            model_output["score_source"] = "test"
        else:
            model_output["seed_score"] = seed_val_score
            model_output["best_score"] = best_val_score
            model_output["score_source"] = "validation"

        # -- Experiment storage --
        try:
            if experiment_name:
                candidates_text = []
                for c in result.candidates:
                    if isinstance(c, dict):
                        candidates_text.append(str(next(iter(c.values())) if c else ""))
                    else:
                        candidates_text.append(str(c))

                num_summary_examples = model_output.get(
                    "num_test_examples", len(valset)
                )
                save_optimization_to_experiment(
                    session,
                    experiment_name,
                    function_name=function_name,
                    model=model,
                    seed_prompt=model_seed_body,
                    best_prompt=best_body,
                    candidates=candidates_text,
                    val_scores=result.val_aggregate_scores,
                    best_idx=result.best_idx,
                    seed_val_score=seed_val_score,
                    best_val_score=best_val_score,
                    seed_test_score=model_output.get("seed_test_score"),
                    best_test_score=model_output.get("best_test_score"),
                    score_source=model_output["score_source"],
                    num_examples=num_summary_examples,
                    reflection_model=reflection_model or model,
                    total_candidates=len(result.candidates),
                    total_metric_calls=result.total_metric_calls,
                    total_reflection_calls=reflection_lm.call_count,
                    elapsed_seconds=model_elapsed,
                    run_dir=gepa_run_dir,
                    seed_eval_details=model_output.get("_seed_eval_details"),
                    best_eval_details=model_output.get("_best_eval_details"),
                )
        except Exception as exp_err:
            logger.warning(
                "[EXPERIMENT_SAVE_ERROR] %s: failed to save to experiment: %s",
                model,
                exp_err,
            )

        # Strip internal-only details before returning to the caller.
        model_output.pop("_seed_eval_details", None)
        model_output.pop("_best_eval_details", None)
        return model_output


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_body_optimization(
    session: Session,
    function_name: str,
    training_table: str,
    label_column: str,
    input_columns: list,
    metric_name: str,
    models: list,
    reflection_model: str,
    test_table: str | None = None,
    auto_budget: Literal["demo", "light", "medium", "heavy"] = DEFAULT_AUTO_BUDGET,
    validation_fraction: float = DEFAULT_VALIDATION_FRACTION,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    metric_options: dict | None = None,
    custom_metric_udf: str | None = None,
    run_id: str | None = None,
    aggregation_metric: str | None = None,
    experiment_name: str | None = None,
) -> dict:
    """Run function body optimization using GEPA ``optimize_anything``.

    This function mirrors the parameter list and output format of
    ``run_optimization`` in ``snow_gepa_optimize`` so the caller sees a
    consistent interface regardless of ``optimize_mode``.

    Args:
        experiment_name: If provided, optimization results are persisted to a
            Snowflake Experiment object.
    """
    start_time = time.time()

    # ---- run_id ----
    if not run_id:
        func_short_name = function_name.split(".")[-1].split("(")[0]
        run_id = f"ai_func_opt_{func_short_name}_{int(time.time() * 1000)}"

    # ----------------------------------------------------------------
    # Parameter validation (same as existing code path)
    # ----------------------------------------------------------------
    if models is None or len(models) == 0:
        return {
            "error": "models parameter is required and cannot be empty",
            "status": "failed",
        }
    if reflection_model is None:
        return {"error": "reflection_model parameter is required", "status": "failed"}

    # ---- DDL extraction ----
    try:
        original_ddl = get_function_ddl(session, function_name, unescape=False)
        seed_body = extract_body_from_ddl_string(original_ddl, function_name)
        function_signature = extract_signature_from_ddl_string(original_ddl)
    except ValueError as e:
        return {"error": str(e), "status": "failed"}

    # ---- Metric validation ----
    valid_metrics = {
        "exact_match",
        "fuzzy_match",
        "contains_match",
        "redaction_match",
        "llm_judge",
    }
    if metric_name not in valid_metrics and not custom_metric_udf:
        return {
            "error": (
                f"Unknown metric: {metric_name}. "
                f"Available: {', '.join(sorted(valid_metrics))}. "
                f"For custom metrics, provide custom_metric_udf parameter."
            ),
            "status": "failed",
        }

    input_col_names = [col_name.strip('"').strip("'") for col_name in input_columns]

    # ---- Column validation (training table) ----
    training_columns = get_table_column_names(session, training_table)
    try:
        validate_input_columns(training_columns, input_col_names, training_table)
    except ValueError as e:
        return {"error": str(e), "status": "failed"}

    resolved_label = resolve_expected_column(training_columns, label_column)
    if training_columns and resolved_label.upper() not in training_columns:
        return {
            "error": (
                f"Label column '{label_column}' not found in training table "
                f"{training_table}. Available columns: {sorted(training_columns)}"
            ),
            "status": "failed",
        }

    # ---- Column validation (test table) ----
    if test_table:
        test_columns = get_table_column_names(session, test_table)
        try:
            validate_input_columns(test_columns, input_col_names, test_table)
        except ValueError as e:
            return {"error": str(e), "status": "failed"}
        test_label = resolve_expected_column(test_columns, label_column)
        if test_columns and test_label.upper() not in test_columns:
            return {
                "error": (
                    f"Label column '{label_column}' not found in test table "
                    f"{test_table}. Available columns: {sorted(test_columns)}. "
                    "Training and test tables must use the same column names."
                ),
                "status": "failed",
            }

    # ---- Metric options ----
    metric_opts, _, expected_columns = parse_metric_options(metric_options)

    if len(expected_columns) > 1 and metric_name != "llm_judge":
        return {
            "error": "Multi-output optimization requires metric_name='llm_judge'.",
            "status": "failed",
        }

    valid_agg_metrics = {"accuracy", "f1-score"}
    if aggregation_metric and aggregation_metric not in valid_agg_metrics:
        return {
            "error": (
                f"Unknown aggregation_metric: '{aggregation_metric}'. "
                f"Available: {', '.join(sorted(valid_agg_metrics))}"
            ),
            "status": "failed",
        }

    # ---- llm_judge file/multimodal auto-detection ----
    gepa_metric_opts = dict(metric_opts)
    if metric_name == "llm_judge":
        gepa_metric_opts.setdefault("scoring_mode", "continuous")

        if "file_columns" not in gepa_metric_opts:
            all_file_columns: list[str] = []

            detected = extract_to_file_refs(original_ddl)
            if detected:
                stage, columns = detected
                gepa_metric_opts.setdefault("stage_name", stage)
                all_file_columns.extend(columns)

            file_params = extract_file_type_params(original_ddl)
            if file_params:
                all_file_columns.extend(
                    p for p in file_params if p not in all_file_columns
                )
                gepa_metric_opts.setdefault("file_type_params", file_params)

            if all_file_columns:
                gepa_metric_opts["file_columns"] = all_file_columns

    # ---- Dataset loading ----
    dataset_expected_columns = expected_columns if len(expected_columns) > 1 else None
    dataset_result = load_dataset(
        session,
        training_table,
        input_columns,
        label_column,
        expected_columns=dataset_expected_columns,
    )
    if not dataset_result:
        return {
            "error": f"No data found in training table: {training_table}",
            "status": "failed",
        }

    if dataset_result.file_stage_name:
        gepa_metric_opts.setdefault("stage_name", dataset_result.file_stage_name)

    metric_evaluator = Evaluator(
        metric_name,
        session=session,
        custom_metric_udf=custom_metric_udf,
        aggregation_metric=aggregation_metric,
        **gepa_metric_opts,
    )

    full_dataset = dataset_result.dataset

    try:
        validate_stage_file_access(
            session,
            stage_name=gepa_metric_opts.get("stage_name"),
            file_columns=gepa_metric_opts.get("file_columns"),
            dataset=full_dataset,
        )
    except ValueError as e:
        return {"error": str(e), "status": "failed"}

    valset, trainset = split_dataset(full_dataset, validation_fraction)

    if experiment_name:
        create_gepa_experiment(session, experiment_name)

    # ---- Budget (computed once, shared across all models) ----
    objective, background = build_objective_and_background(
        function_name,
        function_signature,
        seed_body,
        models,
        metric_name,
    )
    reflection_weight = estimate_body_reflection_weight(
        seed_body=seed_body,
        objective=objective,
        background=background,
        trainset=trainset,
        metric_name=metric_name,
        reflection_minibatch_size=DEFAULT_REFLECTION_MINIBATCH_SIZE,
    )
    resolved_budget = MaxTotalBudgetStopper.resolve_budget(
        auto=auto_budget,
        num_components=1,
        valset_size=len(valset),
        reflection_call_weight=reflection_weight,
    )

    # ================================================================
    # Run optimization for each model in parallel
    # ================================================================
    _saved_adapter_cls = optimize_anything_adapter.OptimizeAnythingAdapter
    _saved_oa_ref = getattr(_oa_module, "OptimizeAnythingAdapter", None)
    optimize_anything_adapter.OptimizeAnythingAdapter = (
        _BatchedBodyOptimizeAnythingAdapter
    )
    _oa_module.OptimizeAnythingAdapter = _BatchedBodyOptimizeAnythingAdapter
    try:
        model_results: list[dict] = []
        overall_best_score = -1.0
        overall_best_score_source = "validation"
        overall_best_model = models[0]
        overall_best_body = seed_body

        common_kwargs = dict(
            session=session,
            original_ddl=original_ddl,
            seed_body=seed_body,
            function_name=function_name,
            function_signature=function_signature,
            trainset=trainset,
            valset=valset,
            input_col_names=input_col_names,
            input_columns=input_columns,
            metric_evaluator=metric_evaluator,
            reflection_model=reflection_model,
            temperature=temperature,
            max_tokens=max_tokens,
            resolved_budget=resolved_budget,
            reflection_weight=reflection_weight,
            models=models,
            metric_name=metric_name,
            test_table=test_table,
            label_column=label_column,
            dataset_expected_columns=dataset_expected_columns,
            run_id=run_id,
            aggregation_metric=aggregation_metric,
            experiment_name=experiment_name,
        )

        with ThreadPoolExecutor(max_workers=len(models)) as executor:
            future_to_model = {
                executor.submit(
                    _run_single_model_body_optimization,
                    model=model,
                    **common_kwargs,
                ): model
                for model in models
            }

            for future in as_completed(future_to_model):
                model = future_to_model[future]
                try:
                    model_output = future.result()
                except Exception as e:
                    model_output = {
                        "model": model,
                        "status": "failed",
                        "error": f"Future execution error: {e}",
                        "elapsed_seconds": 0,
                    }
                    if experiment_name:
                        save_failed_run_to_experiment(
                            session,
                            experiment_name,
                            function_name=function_name,
                            model=model,
                            error_message=f"Future execution error: {e}",
                        )
                model_results.append(model_output)

                if model_output.get("status") == "completed":
                    score = model_output.get("best_score")
                    if score is not None and score > overall_best_score:
                        overall_best_score = score
                        overall_best_model = model_output["model"]
                        overall_best_body = model_output["best_prompt"]
                        overall_best_score_source = model_output.get(
                            "score_source",
                            "validation",
                        )
    finally:
        optimize_anything_adapter.OptimizeAnythingAdapter = _saved_adapter_cls
        if _saved_oa_ref is not None:
            _oa_module.OptimizeAnythingAdapter = _saved_oa_ref

    elapsed = round(time.time() - start_time, 2)

    overall_best_val_score = None
    for mr in model_results:
        if mr.get("model") == overall_best_model and mr.get("status") == "completed":
            overall_best_val_score = mr.get("best_val_score")
            break

    output: dict = {
        "status": "completed",
        "run_id": run_id,
        "elapsed_seconds": elapsed,
        "function_name": function_name,
        "seed_body": seed_body,
        "best_body": overall_best_body,
        "best_ddl": reconstruct_ddl(original_ddl, overall_best_body),
        "metric": metric_name,
        "training_table": training_table,
        "trainset_size": len(trainset),
        "valset_size": len(valset),
        "validation_fraction": validation_fraction,
        "auto_budget": auto_budget,
        "resolved_budget": resolved_budget,
        "models": models,
        "model_results": model_results,
        "overall_best_model": overall_best_model,
        "overall_best_prompt": overall_best_body,
        "overall_best_val_score": overall_best_val_score,
        "overall_best_score": overall_best_score if overall_best_score >= 0 else None,
        "overall_best_score_source": overall_best_score_source,
    }

    if aggregation_metric:
        output["aggregation_metric"] = aggregation_metric
    if dataset_expected_columns:
        output["expected_columns"] = dataset_expected_columns
    if test_table:
        output["test_table"] = test_table

    # ---- Task cleanup ----
    if run_id and run_id.startswith("ai_func_opt_"):
        try:
            parts = function_name.split("(")[0].split(".")
            if len(parts) >= 3:
                task_fqn = f"{parts[0]}.{parts[1]}.{run_id}"
                session.sql(f"DROP TASK IF EXISTS {task_fqn}").collect()
        except Exception as e:
            logger.debug("Task cleanup failed (non-critical): %s", e)

    return output
