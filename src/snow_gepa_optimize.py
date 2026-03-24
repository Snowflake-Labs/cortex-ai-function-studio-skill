# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Public GEPA optimization API for Snowflake.

This module provides the main optimize() function that runs GEPA prompt
optimization using Snowflake AI functions via UDF invocation.
"""

import logging
import random
import re
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Literal, TypedDict

import gepa as gepa_pkg
from gepa import NoImprovementStopper
from gepa.core.result import GEPAResult
from snowflake.snowpark import Session

from metrics_core import (
    evaluate as evaluate_ai_function,
    get_table_column_names,
    parse_metric_options,
    resolve_expected_column,
    validate_input_columns,
)
from snow_gepa_adapter import (
    EvaluationResult,
    Evaluator,
    SnowflakeAdapter,
    SnowflakeDataInst,
    SnowflakeLLM,
    load_dataset,
)
from custom_ai_function_utils import (
    build_temp_function_name,
    with_custom_ai_function_query_tag,
)

logger = logging.getLogger(__name__)


class PythonLoggingAdapter:
    """Adapts Python logging to GEPA's LoggerProtocol."""

    def __init__(self, py_logger: logging.Logger) -> None:
        self._logger = py_logger

    def log(self, message: str) -> None:
        self._logger.info(message)


AUTO_BUDGET_SETTINGS = {
    "light": {"n": 6},
    "medium": {"n": 12},
    "heavy": {"n": 18},
}


class TrackingDetail(TypedDict):
    """Per-row evaluation detail for detailed tracking."""

    row_idx: int
    prompt_text: str
    input_text: str
    expected: str
    predicted: str
    metric_score: float
    metric_feedback: str


def _resolve_budget(
    auto: Literal["light", "medium", "heavy"] | None,
    max_metric_calls: int | None,
    num_components: int,
    valset_size: int,
    full_eval_steps: int = 5,
) -> int | None:
    """Resolves the optimization budget based on auto setting or explicit value.

    Matches DSPy's auto_budget calculation logic:
    1. Compute num_trials based on num_components and num_candidates
    2. Account for initial full evaluation, bootstrapping, minibatch evaluations,
       and periodic full evals

    Args:
        auto: Budget preset ("light", "medium", "heavy") or None for explicit budget
        max_metric_calls: Explicit budget (used if auto is None)
        num_components: Number of prompt components being optimized
        valset_size: Size of validation set
        full_eval_steps: How often to run full evaluation (default 5)

    Returns:
        Maximum number of metric calls allowed
    """
    import math

    if auto is None:
        return max_metric_calls

    if auto not in AUTO_BUDGET_SETTINGS:
        raise ValueError(f"auto must be one of {list(AUTO_BUDGET_SETTINGS.keys())}")

    num_candidates = AUTO_BUDGET_SETTINGS[auto]["n"]

    # DSPy formula for num_trials
    num_trials = int(
        max(2 * (num_components * 2) * math.log2(num_candidates), 1.5 * num_candidates)
    )

    V = valset_size
    N = num_trials
    M = 35  # DSPy uses fixed minibatch size of 35

    # Initial full evaluation on the seed candidate
    total = V

    # Bootstrapping: 5 trials per candidate
    total += num_candidates * 5

    # N minibatch evaluations (reflection steps)
    total += N * M

    if N == 0:
        return total

    # Periodic full evals: occur when trial_num % full_eval_steps == 0
    periodic_fulls = (N + 1) // full_eval_steps + 1
    # Extra final eval if N < full_eval_steps
    extra_final = 1 if N < full_eval_steps else 0
    total += (periodic_fulls + extra_final) * V

    return total


def optimize(
    seed_candidate: dict[str, str],
    trainset: list[SnowflakeDataInst],
    evaluator: Callable[[SnowflakeDataInst, str], EvaluationResult],
    session: Session,
    valset: list[SnowflakeDataInst],
    function_name: str,
    input_columns: list[str],
    model: str,
    reflection_model: str,
    auto: Literal["light", "medium", "heavy"] | None,
    max_metric_calls: int | None,
    original_ddl: str = "",
    temp_function_name: str = "",
    reflection_minibatch_size: int = 10,
    skip_perfect_score: bool = True,
    perfect_score: float = 1.0,
    candidate_selection_strategy: Literal[
        "pareto", "current_best", "epsilon_greedy"
    ] = "pareto",
    use_merge: bool = True,
    max_merge_invocations: int = 5,
    no_improvement_patience: int | None = None,
    seed: int = 0,
    log_dir: str | None = None,
    tracking_callback: Callable[[dict[str, str], float, int], None] | None = None,
    detailed_tracking_callback: Callable[[dict], None] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 8192,
) -> GEPAResult:
    """Optimizes prompts using the GEPA algorithm with Snowflake AI function invocation.

    Args:
        seed_candidate: Initial candidate mapping component names to prompt
            text. Example: {"instruction": "You are a helpful assistant..."}
        trainset: Training examples for reflection (learning from failures).
            Each example should have 'inputs' (dict) and 'answer' keys.
            Typically 1/3 of your labeled data.
        evaluator: Function to evaluate a response. Takes (data, response) and
            returns EvaluationResult with score and feedback.
        session: Snowflake Snowpark session.
        valset: Validation examples for scoring candidates. Typically 2/3 of
            your labeled data. Must be provided (no default).
        function_name: Fully qualified UDF name (DB.SCHEMA.FUNC) to optimize.
        input_columns: List of input column names that map to UDF parameters.
        model: Snowflake Cortex model for task execution.
        reflection_model: Model for reflection/mutation. Defaults to same as model.
        auto: Auto budget preset. One of "light", "medium", "heavy".
        max_metric_calls: Maximum total evaluations (budget limit).
        original_ddl: DDL of the original function for creating temp functions.
        temp_function_name: Fully qualified name for the temp function.
        reflection_minibatch_size: Examples per reflection step. Default 3.
        skip_perfect_score: Skip reflection when all scores are perfect.
        perfect_score: Score threshold considered perfect. Default 1.0.
        candidate_selection_strategy: How to select parent candidate.
        use_merge: Whether to use merge-based optimization. Default True.
        max_merge_invocations: Maximum merge operations. Default 5.
        no_improvement_patience: Stop optimization if no improvement after this
            many iterations. Set to None to disable (DSPy default). Default None.
        seed: Random seed for reproducibility. Default 0.
        log_dir: Directory for saving logs (optional).
        tracking_callback: Optional callback function called after each candidate
            evaluation with (candidate_dict, average_score).
        detailed_tracking_callback: Optional callback for per-row evaluation details.
        temperature: LLM sampling temperature for reflection. Default 0.7.
        max_tokens: Maximum tokens for reflection responses. Default 8192.

    Returns:
        GEPAResult containing:
            - candidates: All proposed candidates.
            - val_aggregate_scores: Per-candidate average validation scores.
            - best_candidate: The highest-scoring candidate.
            - best_idx: Index of best candidate.
    """
    if auto is not None and max_metric_calls is not None:
        raise ValueError("Cannot specify both 'auto' and 'max_metric_calls'")

    reflection_model = reflection_model or model

    resolved_budget = _resolve_budget(
        auto=auto,
        max_metric_calls=max_metric_calls,
        num_components=len(seed_candidate),
        valset_size=len(valset),
    )

    adapter = SnowflakeAdapter(
        session=session,
        evaluator=evaluator,
        function_name=function_name,
        input_columns=input_columns,
        model=model,
        original_ddl=original_ddl,
        temp_function_name=temp_function_name,
        tracking_callback=tracking_callback,
        detailed_tracking_callback=detailed_tracking_callback,
    )

    reflection_lm = SnowflakeLLM(
        session=session,
        model=reflection_model,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    stop_callbacks = []
    if no_improvement_patience is not None:
        stop_callbacks.append(
            NoImprovementStopper(
                max_iterations_without_improvement=no_improvement_patience
            )
        )

    try:
        return gepa_pkg.optimize(
            seed_candidate=seed_candidate,
            trainset=trainset,
            valset=valset,
            adapter=adapter,
            reflection_lm=reflection_lm,
            candidate_selection_strategy=candidate_selection_strategy,
            skip_perfect_score=skip_perfect_score,
            reflection_minibatch_size=reflection_minibatch_size,
            perfect_score=perfect_score,
            use_merge=use_merge,
            max_merge_invocations=max_merge_invocations,
            max_metric_calls=resolved_budget,
            stop_callbacks=stop_callbacks if stop_callbacks else None,
            logger=PythonLoggingAdapter(logger),
            seed=seed,
            run_dir=log_dir,
        )
    finally:
        adapter.cleanup()


def split_dataset(
    dataset: list[SnowflakeDataInst],
    validation_fraction: float,
    seed: int = 42,
) -> tuple[list[SnowflakeDataInst], list[SnowflakeDataInst]]:
    """Split dataset into validation and training sets.

    Args:
        dataset: Full dataset to split
        validation_fraction: Fraction for validation (e.g., 0.667 = 2/3)
        seed: Random seed for reproducibility

    Returns:
        (valset, trainset) - validation set and training set
    """
    shuffled = dataset.copy()
    random.Random(seed).shuffle(shuffled)

    split_idx = int(len(shuffled) * validation_fraction)
    valset = shuffled[:split_idx]
    trainset = shuffled[split_idx:]

    if len(trainset) == 0:
        trainset = valset[: max(1, len(valset) // 3)]

    return valset, trainset


def create_tracking_table(session: Session, tracking_table: str) -> None:
    """Create the tracking table if it doesn't exist.

    Uses CREATE TABLE IF NOT EXISTS to preserve history across optimization runs.
    Each run is identified by RUN_ID.
    """
    session.sql(f"""
        CREATE TABLE IF NOT EXISTS {tracking_table} (
            RUN_ID VARCHAR,
            FUNCTION_NAME VARCHAR,
            MODEL_NAME VARCHAR,
            CANDIDATE_INDEX INTEGER,
            PROMPT_TEXT VARCHAR(16777216),
            EVAL_TYPE VARCHAR,
            METRIC_SCORE FLOAT,
            NUM_EXAMPLES INTEGER,
            IS_FULL_EVAL BOOLEAN,
            CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
        )
    """).collect()


def _log_tracking_error(
    session: Session,
    table_name: str,
    run_id: str,
    model: str,
    error_msg: str,
    prompt_snippet: str,
) -> None:
    """Log tracking errors to {table_name}_ERRORS for debugging.

    Creates the errors table if it doesn't exist, then inserts the error record.
    This function is intentionally fault-tolerant - if error logging itself fails,
    it silently continues to avoid cascading failures.
    """
    errors_table = f"{table_name}_ERRORS"
    try:
        session.sql(f"""
            CREATE TABLE IF NOT EXISTS {errors_table} (
                RUN_ID VARCHAR,
                MODEL_NAME VARCHAR,
                ERROR_MESSAGE VARCHAR(16777216),
                PROMPT_SNIPPET VARCHAR(500),
                CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
            )
        """).collect()
        session.sql(
            f"""
            INSERT INTO {errors_table} 
            (RUN_ID, MODEL_NAME, ERROR_MESSAGE, PROMPT_SNIPPET)
            VALUES (?, ?, ?, ?)
        """,
            params=[run_id, model, str(error_msg)[:500], prompt_snippet[:200]],
        ).collect()
    except Exception:
        pass  # Don't fail if error logging itself fails


class IncrementalTracker:
    """Tracks candidates incrementally during optimization.

    This class saves each candidate to the tracking table immediately after evaluation.
    """

    def __init__(
        self,
        session: Session,
        table_name: str,
        model: str,
        run_id: str,
        function_name: str,
        valset_size: int,
    ) -> None:
        self.session = session
        self.table_name = table_name
        self.model = model
        self.run_id = run_id
        self.function_name = function_name
        self.valset_size = valset_size
        self.seen_prompts: set[str] = set()
        self.candidate_index = 0

    def track_candidate(
        self, candidate: dict[str, str], score: float, num_examples: int
    ) -> None:
        """Save a single candidate to the tracking table immediately.

        Args:
            candidate: The candidate dict (e.g., {"instruction": "..."})
            score: The evaluation score for this candidate
            num_examples: Number of examples used in the evaluation
        """
        prompt = candidate.get("instruction", "")

        # Skip duplicates (same prompt might be evaluated multiple times)
        prompt_hash = hash(prompt)
        if prompt_hash in self.seen_prompts:
            return
        self.seen_prompts.add(prompt_hash)

        self.candidate_index += 1

        # Mark as full eval if num_examples matches the validation set size
        is_full_eval = num_examples == self.valset_size

        self.session.sql(
            f"""
            INSERT INTO {self.table_name} 
            (RUN_ID, FUNCTION_NAME, MODEL_NAME, CANDIDATE_INDEX, 
             PROMPT_TEXT, EVAL_TYPE, METRIC_SCORE, NUM_EXAMPLES, IS_FULL_EVAL)
            VALUES (?, ?, ?, ?, ?, 'incremental', ?, ?, ?)
        """,
            params=[
                self.run_id,
                self.function_name,
                self.model,
                self.candidate_index,
                prompt,
                score,
                num_examples,
                is_full_eval,
            ],
        ).collect()


def create_detailed_tracking_table(session: Session, table_name: str) -> None:
    """Create table for per-row evaluation details.

    This table stores detailed information about each evaluation for debugging
    GEPA optimization runs. Each row represents one input/output pair evaluated.
    """
    session.sql(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            RUN_ID VARCHAR,
            FUNCTION_NAME VARCHAR,
            MODEL_NAME VARCHAR,
            CANDIDATE_IDX INTEGER,
            PROMPT_TEXT VARCHAR(16777216),
            ROW_IDX INTEGER,
            INPUT_TEXT VARCHAR(16777216),
            EXPECTED VARCHAR(16777216),
            PREDICTED VARCHAR(16777216),
            METRIC_SCORE FLOAT,
            METRIC_FEEDBACK VARCHAR(16777216),
            SPLIT VARCHAR,
            EVAL_TYPE VARCHAR,
            CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
        )
    """).collect()


class DetailedTracker:
    """Tracks per-row evaluation details during optimization.

    This class saves detailed information about each evaluation row, including
    input, expected output, predicted output, and metric feedback. Useful for
    debugging why prompts are scoring poorly.

    Uses batched inserts - accumulates rows in memory and flushes when the
    prompt changes (i.e., per candidate) to minimize INSERT overhead.
    """

    def __init__(
        self,
        session: Session,
        table_name: str,
        model: str,
        run_id: str,
        function_name: str,
        split: str = "valset",
    ) -> None:
        self.session = session
        self.table_name = table_name
        self.model = model
        self.run_id = run_id
        self.function_name = function_name
        self.split = split
        self.candidate_idx = 0
        self.current_prompt_hash: int | None = None
        self.pending_rows: list[TrackingDetail] = []  # Batch accumulator

    def track_detail(self, detail: TrackingDetail) -> None:
        """Save a single row's evaluation detail.

        Rows are batched in memory and flushed when the prompt changes.

        Args:
            detail: Dict with keys: row_idx, prompt_text, input_text, expected,
                    predicted, metric_score, metric_feedback
        """
        prompt_text = detail.get("prompt_text", "")
        prompt_hash = hash(prompt_text)

        # Flush pending rows when prompt changes (new candidate)
        if prompt_hash != self.current_prompt_hash:
            self._flush_batch()
            self.current_prompt_hash = prompt_hash
            self.candidate_idx += 1

        # Add to batch
        self.pending_rows.append(
            {
                "prompt_text": prompt_text,
                "row_idx": detail.get("row_idx", 0),
                "input_text": detail.get("input_text", ""),
                "expected": detail.get("expected", ""),
                "predicted": detail.get("predicted", ""),
                "metric_score": detail.get("metric_score", 0),
                "metric_feedback": detail.get("metric_feedback", ""),
            }
        )

    def _flush_batch(self) -> None:
        """Flush all pending rows to the database in a single INSERT."""
        if not self.pending_rows:
            return

        # Build parameterized batch INSERT with bind variables.
        value_qmarks = []
        bind_params: list[object] = []
        for row in self.pending_rows:
            prompt_text = (
                str(row["prompt_text"]) if row["prompt_text"] is not None else ""
            )
            input_text = str(row["input_text"]) if row["input_text"] is not None else ""
            expected = str(row["expected"]) if row["expected"] is not None else ""
            predicted = str(row["predicted"]) if row["predicted"] is not None else ""
            feedback = (
                str(row["metric_feedback"])
                if row["metric_feedback"] is not None
                else ""
            )

            value_qmarks.append("(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'incremental')")
            bind_params.extend(
                [
                    self.run_id,
                    self.function_name,
                    self.model,
                    self.candidate_idx,
                    prompt_text,
                    row["row_idx"],
                    input_text,
                    expected,
                    predicted,
                    row["metric_score"],
                    feedback,
                    self.split,
                ]
            )

        values_clause = ", ".join(value_qmarks)

        self.session.sql(
            f"""
            INSERT INTO {self.table_name}
            (RUN_ID, FUNCTION_NAME, MODEL_NAME,
             CANDIDATE_IDX, PROMPT_TEXT, ROW_IDX,
             INPUT_TEXT, EXPECTED, PREDICTED, METRIC_SCORE, METRIC_FEEDBACK,
             SPLIT, EVAL_TYPE)
            VALUES {values_clause}
        """,
            params=bind_params,
        ).collect()
        self.pending_rows = []

    def flush(self) -> None:
        """Public method to flush any remaining rows at end of optimization."""
        self._flush_batch()


def save_tracking(
    session: Session,
    table_name: str,
    result: GEPAResult,
    model: str,
    run_id: str,
    function_name: str,
    valset_size: int | None = None,
) -> None:
    """Save final optimization results to a tracking table.

    Inserts final summary rows for each candidate with eval_type='final'.
    Called at the end of optimization.
    """
    # Insert final summary rows with eval_type='final'
    for idx, candidate in enumerate(result.candidates):
        prompt = candidate.get("instruction", "")
        score = (
            result.val_aggregate_scores[idx]
            if idx < len(result.val_aggregate_scores)
            else None
        )

        # Use parameterized query — bind variables keep data out of the SQL parser.
        score_val = score if score is not None else None
        num_examples_val = valset_size if valset_size is not None else None

        try:
            session.sql(
                f"""
                INSERT INTO {table_name} 
                (RUN_ID, FUNCTION_NAME, MODEL_NAME, CANDIDATE_INDEX, 
                 PROMPT_TEXT, EVAL_TYPE, METRIC_SCORE, NUM_EXAMPLES, IS_FULL_EVAL)
                VALUES (?, ?, ?, ?, ?, 'final', ?, ?, TRUE)
            """,
                params=[
                    run_id,
                    function_name,
                    model,
                    idx,
                    prompt,
                    score_val,
                    num_examples_val,
                ],
            ).collect()
        except Exception:
            pass  # Don't fail if tracking insert fails


def create_opt_results_table(session: Session, results_table: str) -> None:
    """Create the optimization results table if it doesn't exist.

    Stores per-model summary results (seed and optimized) for each optimization run.
    Modeled after the eval results table pattern.
    """
    session.sql(f"""
        CREATE TABLE IF NOT EXISTS {results_table} (
            RUN_ID VARCHAR,
            FUNCTION_NAME VARCHAR,
            MODEL_NAME VARCHAR,
            EVAL_TYPE VARCHAR,
            PROMPT_TEXT VARCHAR(16777216),
            TEST_SCORE FLOAT,
            VAL_SCORE FLOAT,
            NUM_TEST_EXAMPLES INTEGER,
            NUM_VAL_EXAMPLES INTEGER,
            TOTAL_CANDIDATES INTEGER,
            TOTAL_METRIC_CALLS INTEGER,
            ELAPSED_SECONDS FLOAT,
            REFLECTION_MODEL VARCHAR,
            CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
        )
    """).collect()


def save_opt_results(
    session: Session,
    results_table: str,
    run_id: str,
    function_name: str,
    model_results: list[dict],
    seed_prompt: str,
    valset_size: int,
) -> None:
    """Save optimization results to the results table.

    Writes two rows per model: one for the seed prompt and one for the best
    optimized prompt, with both validation and test scores.
    """
    for model_output in model_results:
        if model_output.get("status") != "completed":
            continue

        model = model_output["model"]
        reflection_model = model_output.get("reflection_model", "")
        elapsed = model_output.get("elapsed_seconds", 0)
        total_candidates = model_output.get("total_candidates", 0)
        total_metric_calls = model_output.get("total_metric_calls", 0)
        num_test = model_output.get("num_test_examples")

        # Seed row
        try:
            session.sql(
                f"""
                INSERT INTO {results_table}
                (RUN_ID, FUNCTION_NAME, MODEL_NAME, EVAL_TYPE, PROMPT_TEXT,
                 TEST_SCORE, VAL_SCORE, NUM_TEST_EXAMPLES, NUM_VAL_EXAMPLES,
                 TOTAL_CANDIDATES, TOTAL_METRIC_CALLS, ELAPSED_SECONDS, REFLECTION_MODEL)
                VALUES (?, ?, ?, 'seed', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                params=[
                    run_id,
                    function_name,
                    model,
                    seed_prompt,
                    model_output.get("seed_test_score"),
                    model_output.get("seed_val_score"),
                    num_test,
                    valset_size,
                    total_candidates,
                    total_metric_calls,
                    elapsed,
                    reflection_model,
                ],
            ).collect()
        except Exception:
            pass

        # Optimized row
        try:
            session.sql(
                f"""
                INSERT INTO {results_table}
                (RUN_ID, FUNCTION_NAME, MODEL_NAME, EVAL_TYPE, PROMPT_TEXT,
                 TEST_SCORE, VAL_SCORE, NUM_TEST_EXAMPLES, NUM_VAL_EXAMPLES,
                 TOTAL_CANDIDATES, TOTAL_METRIC_CALLS, ELAPSED_SECONDS, REFLECTION_MODEL)
                VALUES (?, ?, ?, 'optimized', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                params=[
                    run_id,
                    function_name,
                    model,
                    model_output.get("best_prompt", ""),
                    model_output.get("best_test_score"),
                    model_output.get("best_val_score"),
                    num_test,
                    valset_size,
                    total_candidates,
                    total_metric_calls,
                    elapsed,
                    reflection_model,
                ],
            ).collect()
        except Exception:
            pass


def get_function_ddl(
    session: Session, function_name: str, *, unescape: bool = True
) -> str:
    """Fetch DDL for an AI function.

    Args:
        session: Snowpark session
        function_name: Fully qualified function name (e.g., DB.SCHEMA.FUNC or
            DB.SCHEMA.FUNC(VARCHAR, ARRAY))
        unescape: If True (default), unescape SQL quotes for easier parsing.
            Set to False to return raw executable SQL from GET_DDL.

    Returns:
        DDL string (unescaped for parsing, or raw for execution)

    Raises:
        ValueError: If the DDL cannot be retrieved
    """
    base_name = function_name
    provided_signature = None
    if "(" in function_name:
        paren_idx = function_name.index("(")
        base_name = function_name[:paren_idx]
        provided_signature = function_name[paren_idx:]

    parts = base_name.split(".")
    if len(parts) != 3:
        raise ValueError(
            f"Function name must be fully qualified (DB.SCHEMA.FUNC): {function_name}"
        )
    db, schema, func = parts

    rows = session.sql(
        f"SHOW FUNCTIONS LIKE '{func}' IN SCHEMA {db}.{schema}"
    ).collect()
    if not rows:
        raise ValueError(f"Function not found: {function_name}")

    if provided_signature and len(rows) > 1:
        target_sig = f"{func}{provided_signature}"
        matching_row = None
        for row in rows:
            if row["arguments"].upper() == target_sig.upper():
                matching_row = row
                break
        if matching_row is None:
            raise ValueError(f"No function overload matches signature: {function_name}")
        arguments = matching_row["arguments"]
    else:
        arguments = rows[0]["arguments"]

    match = re.search(r"\((.*?)\)", arguments)
    if not match:
        raise ValueError(f"Could not parse function signature: {arguments}")
    param_types = match.group(1)

    full_signature = f"{base_name}({param_types})"
    result = session.sql(f"SELECT GET_DDL('FUNCTION', '{full_signature}')").collect()
    if not result:
        raise ValueError(f"Could not retrieve DDL for function: {full_signature}")

    ddl = result[0][0]
    if unescape:
        ddl = ddl.replace("\\'", "'").replace("''", "'")
    return ddl


def _get_prompt_from_ddl(session: Session, function_name: str) -> str:
    """Extract the system prompt from a UDF's DDL.

    Parses the hardcoded system prompt from the function body where it appears
    as the 'content' value in the system message OBJECT_CONSTRUCT.

    Args:
        session: Snowpark session
        function_name: Fully qualified function name (e.g., DB.SCHEMA.FUNC or
            DB.SCHEMA.FUNC(VARCHAR, ARRAY))

    Returns:
        The system prompt extracted from the DDL

    Raises:
        ValueError: If the DDL cannot be retrieved or parsed
    """
    ddl = get_function_ddl(session, function_name)
    return extract_prompt_from_ddl_string(ddl, function_name)


def extract_prompt_from_ddl_string(ddl: str, function_name: str = "") -> str:
    """Extract the system prompt from a raw DDL string.

    Looks for the pattern: 'role', 'system', 'content', '<prompt>'
    """
    prompt_pattern = r"'role'\s*,\s*'system'\s*,\s*'content'\s*,\s*'((?:[^']|'')*)'"
    match = re.search(prompt_pattern, ddl, re.DOTALL)

    if not match:
        raise ValueError(
            f"Could not extract system prompt from DDL for function: {function_name}. "
            f"Expected hardcoded system prompt in OBJECT_CONSTRUCT('role', 'system', 'content', '...')."
        )

    return match.group(1)


def get_model_from_ddl(session: Session, function_name: str) -> str:
    """Extract the model name from a UDF's DDL.

    Parses the hardcoded model from the function body where it appears
    as ``model=>'model_name'``.

    Args:
        session: Snowpark session
        function_name: Fully qualified function name

    Returns:
        The model name extracted from the DDL

    Raises:
        ValueError: If the DDL cannot be retrieved or parsed
    """
    ddl = get_function_ddl(session, function_name)
    return extract_model_from_ddl_string(ddl, function_name)


def extract_model_from_ddl_string(ddl: str, function_name: str = "") -> str:
    """Extract the model name from a raw DDL string.

    Looks for the pattern: model=>'model_name'
    """
    model_pattern = r"model\s*=>\s*'([^']*)'"
    match = re.search(model_pattern, ddl, re.IGNORECASE)

    if not match:
        raise ValueError(
            f"Could not extract model name from DDL for function: {function_name}. "
            f"Expected hardcoded model in AI_COMPLETE(model=>'...')."
        )

    return match.group(1)


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


def create_temp_function_ddl(
    original_ddl: str,
    temp_function_name: str,
    candidate_model: str,
    candidate_prompt: str,
) -> str:
    """Create DDL for a temporary function by substituting model and prompt.

    Takes the original function's DDL (raw from ``GET_DDL`` or locally
    generated) and produces a new ``CREATE OR REPLACE FUNCTION`` statement
    with:

    - The function name replaced by ``temp_function_name``
    - The model literal replaced by ``candidate_model``
    - The system prompt replaced by ``candidate_prompt``

    The DDL is first normalized to use ``$$`` body delimiters so that
    internal single-quote patterns can be matched without double-escaping
    issues.

    Args:
        original_ddl: The DDL of the original function (raw or normalized)
        temp_function_name: Fully qualified name for the temp function
        candidate_model: Model to substitute
        candidate_prompt: System prompt to substitute

    Returns:
        SQL DDL string for the temp function
    """
    ddl = normalize_ddl_to_dollar_quoting(original_ddl)

    # Replace function name in CREATE ... FUNCTION <name>(
    ddl = re.sub(
        r"(CREATE\s+OR\s+REPLACE\s+FUNCTION\s+)\S+(\s*\()",
        rf"\g<1>{temp_function_name}\2",
        ddl,
        count=1,
        flags=re.IGNORECASE,
    )

    # Replace model literal: model=>'old' -> model=>'new'
    escaped_model = candidate_model.replace("'", "''")
    ddl = re.sub(
        r"(model\s*=>\s*')[^']*(')",
        rf"\g<1>{escaped_model}\2",
        ddl,
        count=1,
        flags=re.IGNORECASE,
    )

    # Replace system prompt: the content value in OBJECT_CONSTRUCT('role', 'system', 'content', '...')
    escaped_prompt = candidate_prompt.replace("'", "''")
    ddl = re.sub(
        r"('role'\s*,\s*'system'\s*,\s*'content'\s*,\s*')(?:[^']|'')*(')",
        rf"\g<1>{escaped_prompt}\2",
        ddl,
        count=1,
        flags=re.DOTALL,
    )

    return ddl


def _run_single_model_optimization(
    model: str,
    session: Session,
    seed_candidate: dict,
    trainset: list,
    valset: list,
    evaluator: Callable,
    resolved_budget: int,
    reflection_model: str,
    temperature: float,
    max_tokens: int,
    function_name: str,
    input_columns: list,
    tracking_table: str,
    test_table: str,
    label_column: str,
    expected_columns: list[str] | None,
    seed_prompt: str,
    run_id: str,
    enable_detailed_tracking: bool = False,
    aggregation_metric: str | None = None,
    original_ddl: str = "",
) -> dict:
    """Run optimization for a single model. Designed to be called in parallel.

    Args:
        model: Model name to optimize with
        session: Snowpark session (thread-safe for reads)
        seed_candidate: Initial candidate
        trainset: Training examples
        valset: Validation examples
        evaluator: Evaluation function
        resolved_budget: Pre-calculated budget
        reflection_model: Reflection model name
        temperature: LLM temperature
        max_tokens: Max tokens
        function_name: Fully qualified name of the function being optimized
        input_columns: Input column names
        tracking_table: Table for tracking results
        test_table: Optional test table for final evaluation
        label_column: Label column name
        expected_columns: Optional expected output columns for object-level eval
        seed_prompt: Original seed prompt
        run_id: Unique identifier for this optimization run
        enable_detailed_tracking: If True, log per-row evaluation details to
            {tracking_table}_DETAILS for debugging. Off by default for performance.

    Returns:
        Dict with model results
    """
    model_start_time = time.time()

    # Set up incremental tracking for this model
    tracking_callback = None
    detailed_tracking_callback = None
    detailed_tracker = None
    if tracking_table:
        tracker = IncrementalTracker(
            session,
            tracking_table,
            model,
            run_id,
            function_name,
            valset_size=len(valset),
        )
        tracking_callback = tracker.track_candidate

        # Set up detailed tracking table (appends _DETAILS to tracking table name)
        # Only enabled when explicitly requested for debugging
        if enable_detailed_tracking:
            detailed_table = f"{tracking_table}_DETAILS"
            create_detailed_tracking_table(session, detailed_table)
            detailed_tracker = DetailedTracker(
                session, detailed_table, model, run_id, function_name
            )
            detailed_tracking_callback = detailed_tracker.track_detail

    temp_fn = build_temp_function_name(function_name, "__OPT_TEMP")

    try:
        result = optimize(
            seed_candidate=seed_candidate,
            trainset=trainset,
            evaluator=evaluator,
            session=session,
            valset=valset,
            function_name=function_name,
            input_columns=input_columns,
            auto=None,
            max_metric_calls=resolved_budget,
            original_ddl=original_ddl,
            temp_function_name=temp_fn,
            model=model,
            reflection_model=reflection_model or model,
            no_improvement_patience=None,
            temperature=temperature,
            max_tokens=max_tokens,
            tracking_callback=tracking_callback,
            detailed_tracking_callback=detailed_tracking_callback,
        )

        if tracking_table:
            # Final save for this model
            save_tracking(
                session,
                tracking_table,
                result,
                model,
                run_id,
                function_name,
                valset_size=len(valset),
            )
            # Flush any remaining detailed tracking rows
            if detailed_tracker is not None:
                detailed_tracker.flush()

        model_elapsed = round(time.time() - model_start_time, 2)

        best_val_score = (
            result.val_aggregate_scores[result.best_idx]
            if result.val_aggregate_scores
            else None
        )
        seed_val_score = (
            result.val_aggregate_scores[0] if result.val_aggregate_scores else None
        )

        best_prompt_raw = result.best_candidate.get("instruction", "")

        model_output = {
            "model": model,
            "status": "completed",
            "elapsed_seconds": model_elapsed,
            "best_prompt": best_prompt_raw,
            "best_val_score": best_val_score,
            "seed_val_score": seed_val_score,
            "total_candidates": len(result.candidates),
            "total_metric_calls": result.total_metric_calls,
            "all_val_scores": result.val_aggregate_scores,
            "reflection_model": reflection_model or model,
        }

        subscores = getattr(result, "val_aggregate_subscores", None) or []
        if (
            subscores
            and result.best_idx < len(subscores)
            and subscores[result.best_idx]
        ):
            model_output["best_val_subscores"] = subscores[result.best_idx]

        # Test set evaluation using temp functions
        if test_table and original_ddl:
            eval_metric_options = (
                dict(evaluator.kwargs) if hasattr(evaluator, "kwargs") else {}
            )
            if expected_columns:
                eval_metric_options["expected_columns"] = expected_columns

            test_temp_fn = build_temp_function_name(function_name, "__OPT_TEST")

            try:
                # Evaluate seed: create temp function with seed model+prompt
                seed_ddl = create_temp_function_ddl(
                    original_ddl, test_temp_fn, model, seed_prompt
                )
                session.sql(seed_ddl).collect()
                seed_test_score = evaluate_ai_function(
                    session=session,
                    function_name=test_temp_fn,
                    test_table=test_table,
                    input_columns=input_columns,
                    label_column=label_column,
                    metric_name=evaluator.metric_name,
                    custom_metric_udf=evaluator.custom_metric_udf,
                    metric_options=eval_metric_options,
                    model_name=model,
                )

                # Evaluate best: create temp function with best model+prompt
                best_ddl = create_temp_function_ddl(
                    original_ddl,
                    test_temp_fn,
                    model,
                    result.best_candidate["instruction"],
                )
                session.sql(best_ddl).collect()
                best_test_score = evaluate_ai_function(
                    session=session,
                    function_name=test_temp_fn,
                    test_table=test_table,
                    input_columns=input_columns,
                    label_column=label_column,
                    metric_name=evaluator.metric_name,
                    custom_metric_udf=evaluator.custom_metric_udf,
                    metric_options=eval_metric_options,
                    model_name=model,
                )

                model_output["seed_test_score"] = seed_test_score
                model_output["best_test_score"] = best_test_score
                test_count = session.sql(
                    f"SELECT COUNT(*) FROM {test_table}"
                ).collect()[0][0]
                model_output["num_test_examples"] = test_count
            finally:
                try:
                    session.sql(f"DROP FUNCTION IF EXISTS {test_temp_fn}").collect()
                except Exception:
                    pass

        return model_output

    except (ValueError, json.JSONDecodeError):
        # Parsing/validation errors - re-raise to fail fast for debugging
        raise

    except Exception as e:
        model_elapsed = round(time.time() - model_start_time, 2)
        error_msg = str(e)
        print(f"[OPTIMIZATION_ERROR] {model}: {error_msg}")

        # Log the error to tracking errors table
        if tracking_table:
            _log_tracking_error(
                session,
                tracking_table,
                run_id,
                model,
                error_msg,
                seed_prompt[:200] if seed_prompt else "N/A",
            )

        return {
            "model": model,
            "status": "failed",
            "error": error_msg,
            "elapsed_seconds": model_elapsed,
        }


@with_custom_ai_function_query_tag("SPROC_OPTIMIZATION")
def run_optimization(
    session: Session,
    function_name: str,
    training_table: str,
    label_column: str,
    input_columns: list,
    metric_name: str,
    models: list,
    reflection_model: str,
    test_table: str = None,
    auto_budget: Literal["light", "medium", "heavy"] = "light",
    tracking_table: str = None,
    results_table: str = None,
    validation_fraction: float = 0.667,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    metric_options: dict = None,
    custom_metric_udf: str = None,
    enable_detailed_tracking: bool = False,
    run_id: str = None,
    aggregation_metric: str | None = None,
) -> dict:
    """Run GEPA optimization on a prompt. SPROC handler function.

    The training_table data is split into:
    - valset (validation_fraction, default 2/3): Used for scoring candidates
    - trainset (remainder, default 1/3): Used for reflection/learning from failures

    The test_table (if provided) is ONLY used for final evaluation after
    optimization completes - it is never touched during the optimization process.

    Args:
        session: Snowpark session
        function_name: Fully qualified name of the AI function to optimize
        training_table: Table with training data (split into valset + trainset)
        label_column: Column containing expected outputs
        input_columns: List of input column names
        metric_name: Metric to use (exact_match, fuzzy_match, redaction_match, etc.)
        models: List of models to optimize with (required)
        reflection_model: Model for reflection (required)
        test_table: Optional held-out test table for final evaluation only
        auto_budget: Budget preset - "light", "medium", or "heavy"
        tracking_table: Optional table to save optimization history
        validation_fraction: Fraction of training data for validation (default 0.667 = 2/3)
        temperature: LLM sampling temperature. Default 0.0.
        max_tokens: Maximum tokens in LLM response. Default 8192.
        metric_options: Metric-specific options (e.g., threshold for fuzzy_match,
            task_description for llm_judge). Default None.
        custom_metric_udf: Fully qualified name of a custom metric UDF
            (e.g., ``DB.SCHEMA.MY_METRIC``). The UDF must accept
            ``(EXPECTED VARCHAR, PREDICTED VARCHAR)`` and return VARIANT
            with ``score`` and ``feedback`` keys.
        enable_detailed_tracking: If True, log per-row evaluation details to
            {tracking_table}_DETAILS for debugging. Off by default for performance.
        run_id: Unique identifier for this optimization run. Auto-generated if not provided.
        aggregation_metric: Optional batch-level classification metric to use for
            selecting the best prompt. Supported: "accuracy", "f1-score". When provided,
            the final best prompt is chosen by the highest value of this metric
            across all candidates.

    Returns:
        Dict with optimization results including best_prompt and scores for each model
    """
    start_time = time.time()

    if not run_id:
        func_short_name = function_name.split(".")[-1].split("(")[0]
        run_id = f"ai_func_opt_{func_short_name}_{int(time.time() * 1000)}"

    # Validate required parameters
    if models is None or len(models) == 0:
        return {
            "error": "models parameter is required and cannot be empty",
            "status": "failed",
        }
    if reflection_model is None:
        return {"error": "reflection_model parameter is required", "status": "failed"}

    try:
        parsed_ddl = get_function_ddl(session, function_name, unescape=True)
        seed_prompt = extract_prompt_from_ddl_string(parsed_ddl, function_name)
        seed_model = extract_model_from_ddl_string(parsed_ddl, function_name)
        # Raw DDL (not unescaped) is valid SQL for creating temp functions
        original_ddl = get_function_ddl(session, function_name, unescape=False)
    except ValueError as e:
        return {"error": str(e), "status": "failed"}

    # Validate metric name
    valid_metrics = {
        "exact_match",
        "fuzzy_match",
        "contains_match",
        "redaction_match",
        "llm_judge",
    }
    if metric_name not in valid_metrics and not custom_metric_udf:
        return {
            "error": f"Unknown metric: {metric_name}. Available: {', '.join(sorted(valid_metrics))}. "
            f"For custom metrics, provide custom_metric_udf parameter.",
            "status": "failed",
        }

    input_col_names = [col.strip('"').strip("'") for col in input_columns]

    training_columns = get_table_column_names(session, training_table)
    try:
        validate_input_columns(training_columns, input_col_names, training_table)
    except ValueError as e:
        return {"error": str(e), "status": "failed"}

    resolved_label = resolve_expected_column(training_columns, label_column)
    if training_columns and resolved_label.upper() not in training_columns:
        return {
            "error": f"Label column '{label_column}' not found in training table {training_table}. "
            f"Available columns: {sorted(training_columns)}",
            "status": "failed",
        }

    if test_table:
        test_columns = get_table_column_names(session, test_table)
        try:
            validate_input_columns(test_columns, input_col_names, test_table)
        except ValueError as e:
            return {"error": str(e), "status": "failed"}

        test_label = resolve_expected_column(test_columns, label_column)
        if test_columns and test_label.upper() not in test_columns:
            return {
                "error": f"Label column '{label_column}' not found in test table {test_table}. "
                f"Available columns: {sorted(test_columns)}. "
                f"Training and test tables must use the same column names for the label/expected column.",
                "status": "failed",
            }

    metric_opts, _, expected_columns = parse_metric_options(metric_options)

    if len(expected_columns) > 1 and metric_name != "llm_judge":
        return {
            "error": "Multi-output optimization requires metric_name='llm_judge'.",
            "status": "failed",
        }

    valid_agg_metrics = {"accuracy", "f1-score"}
    if aggregation_metric and aggregation_metric not in valid_agg_metrics:
        return {
            "error": f"Unknown aggregation_metric: '{aggregation_metric}'. "
            f"Available: {', '.join(sorted(valid_agg_metrics))}",
            "status": "failed",
        }

    evaluator = Evaluator(
        metric_name,
        session=session,
        custom_metric_udf=custom_metric_udf,
        aggregation_metric=aggregation_metric,
        **metric_opts,
    )

    dataset_expected_columns = expected_columns if len(expected_columns) > 1 else None
    full_dataset = load_dataset(
        session,
        training_table,
        input_columns,
        label_column,
        expected_columns=dataset_expected_columns,
    )
    if not full_dataset:
        return {
            "error": f"No data found in training table: {training_table}",
            "status": "failed",
        }

    valset, trainset = split_dataset(full_dataset, validation_fraction)

    seed_candidate = {"instruction": seed_prompt}

    # Create tracking table once at the start (clears previous data)
    if tracking_table:
        create_tracking_table(session, tracking_table)

    # Calculate budget once upfront - same budget for ALL models
    # This ensures consistent budget across all models regardless of run order
    resolved_budget = _resolve_budget(
        auto=auto_budget,
        max_metric_calls=None,
        num_components=len(seed_candidate),
        valset_size=len(valset),
    )

    # Run optimization for each model IN PARALLEL using ThreadPoolExecutor
    # This significantly speeds up multi-model optimization
    model_results = []
    overall_best_score = -1
    overall_best_model = None
    overall_best_prompt = seed_prompt

    # Use ThreadPoolExecutor to run models in parallel
    # max_workers=len(models) allows all models to run simultaneously
    with ThreadPoolExecutor(max_workers=len(models)) as executor:
        # Submit all model optimization tasks
        future_to_model = {
            executor.submit(
                _run_single_model_optimization,
                model=model,
                session=session,
                seed_candidate=seed_candidate,
                trainset=trainset,
                valset=valset,
                evaluator=evaluator,
                resolved_budget=resolved_budget,
                reflection_model=reflection_model,
                temperature=temperature,
                max_tokens=max_tokens,
                function_name=function_name,
                input_columns=input_columns,
                tracking_table=tracking_table,
                test_table=test_table,
                label_column=label_column,
                expected_columns=dataset_expected_columns,
                seed_prompt=seed_prompt,
                run_id=run_id,
                enable_detailed_tracking=enable_detailed_tracking,
                aggregation_metric=aggregation_metric,
                original_ddl=original_ddl,
            ): model
            for model in models
        }

        # Collect results as they complete
        for future in as_completed(future_to_model):
            model = future_to_model[future]
            try:
                model_output = future.result()
                model_results.append(model_output)

                # Track overall best
                if model_output.get("status") == "completed":
                    best_val_score = model_output.get("best_val_score")
                    if (
                        best_val_score is not None
                        and best_val_score > overall_best_score
                    ):
                        overall_best_score = best_val_score
                        overall_best_model = model_output["model"]
                        overall_best_prompt = model_output["best_prompt"]

            except Exception as e:
                # This catches exceptions from future.result() itself
                model_results.append(
                    {
                        "model": model,
                        "status": "failed",
                        "error": f"Future execution error: {str(e)}",
                        "elapsed_seconds": 0,
                    }
                )

    # Persist optimization results if a results table was requested
    if results_table:
        try:
            create_opt_results_table(session, results_table)
            save_opt_results(
                session=session,
                results_table=results_table,
                run_id=run_id,
                function_name=function_name,
                model_results=model_results,
                seed_prompt=seed_prompt,
                valset_size=len(valset),
            )
        except Exception:
            # Results persistence failure should not break optimization
            pass

    elapsed_seconds = round(time.time() - start_time, 2)

    output = {
        "status": "completed",
        "run_id": run_id,
        "elapsed_seconds": elapsed_seconds,
        "function_name": function_name,
        "seed_prompt": seed_prompt,
        "metric": metric_name,
        "training_table": training_table,
        "trainset_size": len(trainset),
        "valset_size": len(valset),
        "validation_fraction": validation_fraction,
        "auto_budget": auto_budget,
        "resolved_budget": resolved_budget,  # Same budget applied to all models
        "models": models,
        "model_results": model_results,
        "overall_best_model": overall_best_model,
        "overall_best_prompt": overall_best_prompt,
        "overall_best_val_score": overall_best_score
        if overall_best_score >= 0
        else None,
    }

    if aggregation_metric:
        output["aggregation_metric"] = aggregation_metric

    if dataset_expected_columns:
        output["expected_columns"] = dataset_expected_columns

    if test_table:
        output["test_table"] = test_table

    # Clean up async task if this was called from one (run_id matches task name)
    if run_id and run_id.startswith("ai_func_opt_"):
        try:
            parts = function_name.split("(")[0].split(".")
            if len(parts) >= 3:
                task_fqn = f"{parts[0]}.{parts[1]}.{run_id}"
                session.sql(f"DROP TASK IF EXISTS {task_fqn}").collect()
        except Exception:
            pass  # Cleanup failure should not break optimization

    return output
