# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Snowflake adapter for GEPA optimization.

This module provides the adapter classes that connect GEPA's optimization
engine to Snowflake AI functions. During optimization, temporary functions
are created with candidate model/prompt combinations baked in, rather than
overriding parameters at call time.
"""

from collections.abc import Callable, Mapping, Sequence
import json
from typing import Any, TypedDict

from gepa.core.adapter import EvaluationBatch, GEPAAdapter
from metrics_core import (
    quote_identifier,
    to_text,
    build_object_construct_expr,
    compute_classification_objectives,
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
        aggregation_metric: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.metric_name = metric_name
        self.session = session
        self.custom_metric_udf = custom_metric_udf
        self.aggregation_metric = aggregation_metric
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
        eval_results = [EvaluationResult(score=s, feedback=f) for s, f in results]

        if self.aggregation_metric:
            label_pairs = [
                (expected, predicted)
                for (expected, predicted), _ in zip(batch_items, results)
            ]
            # For classification tasks, always compute precision, recall, F1, and accuracy across each evaluation batch.
            objectives = compute_classification_objectives(label_pairs)
            if objectives:
                for er in eval_results:
                    er.objective_scores = objectives

                # If aggregation_metric is requested, but not "accuracy",
                # override model scores to use requested aggregation metric to filter candidates
                if (
                    self.aggregation_metric != "accuracy"
                    and self.aggregation_metric in objectives
                ):
                    agg_score = objectives[self.aggregation_metric]
                    for er in eval_results:
                        er.score = agg_score

        return eval_results


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

    This adapter evaluates candidates by creating temporary functions with
    the candidate model/prompt baked in, then calling the temp function and
    scoring responses using a user-provided evaluator function.
    """

    def __init__(
        self,
        session: Any,
        evaluator: Any,
        function_name: str,
        input_columns: list[str],
        model: str,
        original_ddl: str,
        temp_function_name: str,
        tracking_callback: Callable[[dict[str, str], float], None] | None = None,
        detailed_tracking_callback: Callable[[dict], None] | None = None,
    ) -> None:
        self.session = session
        self.evaluator = evaluator
        self.function_name = function_name
        self.input_columns = input_columns
        self.model = model
        self.original_ddl = original_ddl
        self.temp_function_name = temp_function_name
        self.tracking_callback = tracking_callback
        self.detailed_tracking_callback = detailed_tracking_callback

    def _format_inputs_for_display(self, inputs: dict[str, str]) -> str:
        """Formats inputs dict as string for tracking/reflection."""
        return "\n".join(f"{k}: {v}" for k, v in inputs.items())

    def _ensure_temp_function(self, system_prompt: str) -> None:
        """Create or replace the temp function with the given model and prompt."""
        from snow_gepa_optimize import create_temp_function_ddl

        ddl = create_temp_function_ddl(
            self.original_ddl, self.temp_function_name, self.model, system_prompt
        )
        self.session.sql(ddl).collect()

    def cleanup(self) -> None:
        """Drop the temporary function used during optimization."""
        try:
            self.session.sql(
                f"DROP FUNCTION IF EXISTS {self.temp_function_name}"
            ).collect()
        except Exception:
            pass

    def _call_udf_batch(
        self,
        system_prompt: str,
        batch: list[SnowflakeDataInst],
    ) -> list[str]:
        """Calls a temp function for all inputs in a single query.

        Creates (or replaces) a temporary function with the candidate
        model and prompt baked in, then calls it without overrides.
        """
        if not batch:
            return []

        self._ensure_temp_function(system_prompt)

        input_col_refs = ", ".join(
            [f"t.{quote_identifier(col)}" for col in self.input_columns]
        )

        # Build UNION ALL clause with all input data
        # Format: SELECT idx AS idx, col1_val AS col1, col2_val AS col2, ...
        select_statements = []
        bind_params: list[object] = []

        for idx, data in enumerate(batch):
            col_exprs = []
            for col in self.input_columns:
                val = data["inputs"].get(col, "")
                if isinstance(val, (list, tuple)):
                    col_exprs.append(f"PARSE_JSON(?) AS {quote_identifier(col)}")
                    bind_params.append(json.dumps(val))
                else:
                    col_exprs.append(f"? AS {quote_identifier(col)}")
                    bind_params.append(val)
            select_statements.append(f"SELECT {idx} AS idx, {', '.join(col_exprs)}")

        subquery = " UNION ALL ".join(select_statements)

        sql = f"""
            SELECT
                t.idx,
                {self.temp_function_name}(
                    {input_col_refs}
                ) AS result
            FROM ({subquery}) AS t
            ORDER BY t.idx
        """

        results = self.session.sql(sql, params=bind_params).collect()

        responses = []
        for row in results:
            result_val = row["RESULT"]
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
            objective_scores=objective_scores
            if any(o is not None for o in objective_scores)
            else None,
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
        inputs = {}
        for col in input_columns:
            val = row[col]
            if val is None:
                inputs[col] = ""
            elif isinstance(val, str) and val.strip().startswith("["):
                try:
                    inputs[col] = json.loads(val)
                except json.JSONDecodeError:
                    inputs[col] = val
            else:
                inputs[col] = val
        answer = to_text(row["ANSWER"])
        dataset.append(SnowflakeDataInst(inputs=inputs, answer=answer))

    return dataset
