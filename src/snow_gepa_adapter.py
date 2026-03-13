# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Snowflake adapter for GEPA optimization.

This module provides the adapter classes that connect GEPA's optimization
engine to Snowflake AI functions via UDF invocation with MODEL_NAME and
SYSTEM_PROMPT overrides.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict

from gepa.core.adapter import EvaluationBatch, GEPAAdapter
from metrics_core import (
    quote_identifier,
    to_text,
    build_object_construct_expr,
    compute_metric,
    compute_metric_batch,
    get_table_column_names,
    resolve_expected_column,
    resolve_multi_output_columns,
    validate_input_columns,
)
from custom_ai_function_utils import RobustAIComplete
from snowflake.snowpark import Session


class SnowflakeDataInst(TypedDict):
    """Input data instance for Snowflake-based evaluation."""

    inputs: dict[str, str]
    answer: str


class SnowflakeTrajectory(TypedDict):
    """Trajectory capturing execution details for reflection."""

    data: SnowflakeDataInst
    full_assistant_response: str
    feedback: str


class SnowflakeRolloutOutput(TypedDict):
    """Output from evaluating a candidate."""

    full_assistant_response: str


SnowflakeReflectiveRecord = TypedDict(
    "SnowflakeReflectiveRecord",
    {
        "Inputs": str,
        "Generated Outputs": str,
        "Feedback": str,
    },
)


class EvaluationResult:
    """Result from evaluating a single example."""

    def __init__(
        self,
        score: float,
        feedback: str,
        objective_scores: dict[str, float] | None = None,
    ) -> None:
        self.score = score
        self.feedback = feedback
        self.objective_scores = objective_scores


class Evaluator:
    """Encapsulates metric evaluation with optional batch optimization.

    This class provides a clean interface for evaluating AI function outputs
    against expected values using various metrics.

    Args:
        metric_name: Name of the metric (exact_match, fuzzy_match, llm_judge, etc.)
        session: Snowpark session (required for llm_judge, optional for others)
        **kwargs: Metric-specific options (e.g., threshold for fuzzy_match,
            task_description for llm_judge)

    Example:
        evaluator = Evaluator("fuzzy_match", threshold=0.9)
        result = evaluator(data, response)

        evaluator = Evaluator("llm_judge", session, task_description="...")
        results = evaluator.evaluate_batch(items)
    """

    def __init__(
        self,
        metric_name: str,
        session: Session | None = None,
        custom_metric_udf: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.metric_name = metric_name
        self.session = session
        self.custom_metric_udf = custom_metric_udf
        self.kwargs = kwargs

    def __call__(self, data: "SnowflakeDataInst", response: str) -> EvaluationResult:
        """Single-item evaluation."""
        expected = data["answer"]
        score, feedback = compute_metric(
            self.metric_name,
            expected,
            response,
            self.session,
            self.custom_metric_udf,
            **self.kwargs,
        )
        return EvaluationResult(score=score, feedback=feedback)

    def evaluate_batch(
        self, items: list[tuple["SnowflakeDataInst", str]]
    ) -> list[EvaluationResult]:
        """Batched evaluation with automatic optimization when available.

        Metrics with optimized batch implementations (e.g., llm_judge) are
        evaluated in a single call. Others fall back to sequential evaluation.

        Args:
            items: List of (data, response) tuples to evaluate

        Returns:
            List of EvaluationResult in the same order as input items
        """
        if not items:
            return []
        batch_items = [(data["answer"], response) for data, response in items]
        results = compute_metric_batch(
            self.metric_name,
            batch_items,
            self.session,
            self.custom_metric_udf,
            **self.kwargs,
        )
        return [EvaluationResult(score=s, feedback=f) for s, f in results]


class SnowflakeLLM:
    """LLM wrapper using Snowflake AI_COMPLETE.

    Implements the gepa LanguageModel protocol for reflection calls.
    """

    def __init__(
        self,
        session: Session,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> None:
        self.session = session
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def __call__(self, prompt: str) -> str:
        responses = RobustAIComplete.call_ai_complete(
            self.session,
            model=self.model,
            user_prompts=[prompt],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            response_schema=None,
        )
        response = responses[0] if responses else None
        return "" if response is None else str(response)


class SnowflakeAdapter(
    GEPAAdapter[SnowflakeDataInst, SnowflakeTrajectory, SnowflakeRolloutOutput]
):
    """GEPA adapter for Snowflake AI functions.

    This adapter evaluates candidates by calling the target UDF with
    MODEL_NAME and SYSTEM_PROMPT overrides, then scores responses using
    a user-provided evaluator function.
    """

    def __init__(
        self,
        session: Any,
        evaluator: Any,
        function_name: str,
        input_columns: list[str],
        model: str,
        tracking_callback: Callable[[dict[str, str], float], None] | None = None,
        detailed_tracking_callback: Callable[[dict], None] | None = None,
    ) -> None:
        self.session = session
        self.evaluator = evaluator
        self.function_name = function_name
        self.input_columns = input_columns
        self.model = model
        self.tracking_callback = tracking_callback
        self.detailed_tracking_callback = detailed_tracking_callback

    def _format_inputs_for_display(self, inputs: dict[str, str]) -> str:
        """Formats inputs dict as string for tracking/reflection."""
        return "\n".join(f"{k}: {v}" for k, v in inputs.items())

    def _call_udf_batch(
        self,
        system_prompt: str,
        batch: list[SnowflakeDataInst],
    ) -> list[str]:
        """Calls the target UDF for all inputs in a single query.

        Uses MODEL_NAME and SYSTEM_PROMPT override parameters to test
        different prompt candidates without recreating the function.
        """
        if not batch:
            return []

        # Build column expressions for the UDF call
        # Each input column is referenced from the VALUES table
        input_col_refs = ", ".join(
            [f"t.{quote_identifier(col)}" for col in self.input_columns]
        )

        # Build VALUES clause with all input data
        # Format: (idx, col1_val, col2_val, ...)
        value_placeholders = []
        bind_params: list[object] = []

        for idx, data in enumerate(batch):
            col_qmarks = ", ".join(["?"] * len(self.input_columns))
            value_placeholders.append(f"({idx}, {col_qmarks})")
            for col in self.input_columns:
                bind_params.append(data["inputs"].get(col, ""))

        values_clause = ", ".join(value_placeholders)

        # Build the SQL query that calls the UDF with overrides
        sql = f"""
            SELECT
                t.idx,
                {self.function_name}(
                    {input_col_refs},
                    MODEL_NAME => '{self.model}',
                    SYSTEM_PROMPT => ?
                ) AS result
            FROM (
                SELECT * FROM VALUES {values_clause}
                AS t(idx, {', '.join([quote_identifier(col) for col in self.input_columns])})
            ) AS t
            ORDER BY t.idx
        """

        # System prompt is the first bind parameter
        all_params = [system_prompt] + bind_params

        results = self.session.sql(sql, params=all_params).collect()

        responses = []
        for row in results:
            result_val = row["RESULT"]
            # Convert result to string (handles VARCHAR, VARIANT, etc.)
            responses.append(to_text(result_val))

        return responses

    def evaluate(
        self,
        batch: list[SnowflakeDataInst],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> EvaluationBatch[SnowflakeTrajectory, SnowflakeRolloutOutput]:
        outputs: list[SnowflakeRolloutOutput] = []
        scores: list[float] = []
        objective_scores: list[dict[str, float] | None] = []
        trajectories: list[SnowflakeTrajectory] | None = [] if capture_traces else None

        system_prompt = next(iter(candidate.values()))

        responses = self._call_udf_batch(system_prompt, batch)

        # Single code path - evaluator handles batching internally
        items = list(zip(batch, responses))
        eval_results = self.evaluator.evaluate_batch(items)

        for data, response, eval_result in zip(batch, responses, eval_results):
            output: SnowflakeRolloutOutput = {"full_assistant_response": response}
            outputs.append(output)
            scores.append(eval_result.score)
            objective_scores.append(getattr(eval_result, "objective_scores", None))

            if trajectories is not None:
                trajectories.append(
                    {
                        "data": data,
                        "full_assistant_response": response,
                        "feedback": eval_result.feedback,
                    }
                )

        # Call tracking callback with candidate, average score, and batch size after each evaluation
        if self.tracking_callback is not None and scores:
            avg_score = sum(scores) / len(scores)
            self.tracking_callback(candidate, avg_score, len(batch))

        # Call detailed tracking callback with per-row evaluation details
        if self.detailed_tracking_callback is not None:
            prompt_text = next(iter(candidate.values()))
            for idx, (data, response, eval_result) in enumerate(
                zip(batch, responses, eval_results)
            ):
                detail = {
                    "row_idx": idx,
                    "prompt_text": prompt_text,
                    "input_text": self._format_inputs_for_display(data["inputs"]),
                    "expected": data["answer"],
                    "predicted": response,
                    "metric_score": eval_result.score,
                    "metric_feedback": eval_result.feedback,
                }
                self.detailed_tracking_callback(detail)

        return EvaluationBatch(
            outputs=outputs,
            scores=scores,
            trajectories=trajectories,
        )

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: EvaluationBatch[SnowflakeTrajectory, SnowflakeRolloutOutput],
        components_to_update: list[str],
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        ret_d: dict[str, list[SnowflakeReflectiveRecord]] = {}

        assert len(components_to_update) == 1
        comp = components_to_update[0]

        trajectories = eval_batch.trajectories
        assert trajectories is not None

        items: list[SnowflakeReflectiveRecord] = []
        for traj in trajectories:
            formatted_inputs = self._format_inputs_for_display(traj["data"]["inputs"])
            d: SnowflakeReflectiveRecord = {
                "Inputs": formatted_inputs,
                "Generated Outputs": traj["full_assistant_response"],
                "Feedback": traj["feedback"],
            }
            items.append(d)

        ret_d[comp] = items

        if len(items) == 0:
            raise ValueError("No valid predictions found for reflection.")

        return ret_d


def load_dataset(
    session: Session,
    table_name: str,
    input_columns: list[str],
    label_column: str,
    expected_columns: list[str] | None = None,
) -> list[SnowflakeDataInst]:
    """Load data from a Snowflake table into SnowflakeDataInst format.

    Args:
        session: Snowpark session
        table_name: Fully qualified table name
        input_columns: List of column names to use as inputs
        label_column: Column containing expected outputs
        expected_columns: Optional list of expected output columns. When multiple
            columns are provided, they are combined into a single OBJECT for
            object-level evaluation (used by multi-output llm_judge).

    Returns:
        List of SnowflakeDataInst dictionaries

    Raises:
        ValueError: If any input column is not found in the table
    """
    columns = ", ".join([quote_identifier(col) for col in input_columns])
    table_columns = get_table_column_names(session, table_name)
    validate_input_columns(table_columns, input_columns, table_name)

    label_col_name = resolve_expected_column(table_columns, label_column)

    answer_expr = f"{quote_identifier(label_col_name)} AS answer"
    if isinstance(expected_columns, list) and len(expected_columns) > 1:
        resolved_pairs = resolve_multi_output_columns(table_columns, expected_columns)
        if resolved_pairs:
            answer_expr = build_object_construct_expr(resolved_pairs, "answer")
    query = f"SELECT {columns}, {answer_expr} FROM {table_name}"

    rows = session.sql(query).collect()

    dataset = []
    for row in rows:
        inputs = {
            col: str(row[col]) if row[col] is not None else "" for col in input_columns
        }
        answer = to_text(row["ANSWER"])
        dataset.append(SnowflakeDataInst(inputs=inputs, answer=answer))

    return dataset
