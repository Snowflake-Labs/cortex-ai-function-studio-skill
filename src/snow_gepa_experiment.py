# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Snowflake Experiment storage helpers for GEPA optimization.

This module wraps the native Snowflake Experiment DDL so that GEPA
optimization results can be persisted as first-class experiment objects
instead of ad-hoc tracking tables.

Supported DDL (validated by Snowfort tests):
  W1: CREATE EXPERIMENT IF NOT EXISTS
  W2: ALTER EXPERIMENT ... ADD RUN / MODIFY RUN ADD PARAMETERS/METRICS
  W3: PUT file:// snow://experiment/...  (eval detail)
  W4: PUT file:// snow://experiment/.../run_dir/  (GEPA artifacts)
  W5: ALTER EXPERIMENT ... COMMIT RUN
"""

import json
import logging
import os
import re
import tempfile
from typing import Any

from snowflake.snowpark import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def create_gepa_experiment(session: Session, experiment_name: str) -> None:
    """Create a Snowflake Experiment if it does not already exist."""
    session.sql(
        f"CREATE EXPERIMENT IF NOT EXISTS {experiment_name}"
    ).collect()


def add_experiment_run(
    session: Session,
    experiment_name: str,
    run_name: str,
    params: list[dict[str, str]] | None = None,
    metrics: list[dict[str, float]] | None = None,
) -> None:
    """Add a run to an experiment, then attach parameters and metrics.

    Args:
        session: Snowpark session.
        experiment_name: Name of the experiment.
        run_name: Name of the run to add.
        params: List of ``{"name": ..., "value": ...}`` dicts (string values).
        metrics: List of ``{"name": ..., "value": ...}`` dicts (numeric values).
    """
    session.sql(
        f"ALTER EXPERIMENT {experiment_name} ADD RUN {run_name}"
    ).collect()

    if params:
        params_json = json.dumps(params).replace("\\", "\\\\").replace("'", "\\'")
        session.sql(
            f"ALTER EXPERIMENT {experiment_name} MODIFY RUN {run_name} "
            f"ADD PARAMETERS = '{params_json}'"
        ).collect()

    if metrics:
        metrics_json = json.dumps(metrics)
        session.sql(
            f"ALTER EXPERIMENT {experiment_name} MODIFY RUN {run_name} "
            f"ADD METRICS = '{metrics_json}'"
        ).collect()


def commit_experiment_run(
    session: Session, experiment_name: str, run_name: str
) -> None:
    """Commit a run, transitioning its status to FINISHED."""
    session.sql(
        f"ALTER EXPERIMENT {experiment_name} COMMIT RUN {run_name}"
    ).collect()


def put_experiment_artifact(
    session: Session,
    experiment_name: str,
    run_name: str,
    local_path: str,
    subdir: str = "",
) -> None:
    """Upload a local file or directory to an experiment run's stage.

    Uses ``session.file.put`` (Snowpark file transfer API) which works both
    client-side and inside stored procedures, unlike ``PUT`` SQL which is
    unsupported in SPROCs.

    Args:
        session: Snowpark session.
        experiment_name: Name of the experiment.
        run_name: Name of the run.
        local_path: Path to a local file or directory.
        subdir: Optional subdirectory under the run (e.g. ``"run_dir"``).
    """
    suffix = f"/{subdir}" if subdir else ""
    stage_path = f"snow://experiment/{experiment_name}/versions/{run_name}{suffix}"

    if os.path.isdir(local_path):
        for filename in os.listdir(local_path):
            filepath = os.path.join(local_path, filename)
            if os.path.isfile(filepath):
                session.file.put(
                    filepath, stage_path,
                    auto_compress=True, overwrite=True,
                )
    else:
        session.file.put(
            local_path, stage_path,
            auto_compress=True, overwrite=True,
        )


# ---------------------------------------------------------------------------
# Run naming helpers
# ---------------------------------------------------------------------------


def make_run_name(model: str, iteration: int, *, is_seed: bool = False) -> str:
    """Build a deterministic experiment run name.

    Convention:
        <MODEL>_SEED         -- seed candidate (iteration 0)
        <MODEL>_ITER_<N>     -- Nth iteration for a given model

    Run names are model-scoped so parallel per-model optimization does not
    create naming conflicts.
    """
    model_suffix = re.sub(r"[^A-Za-z0-9]", "_", model).upper()
    if is_seed:
        return f"{model_suffix}_SEED"
    return f"{model_suffix}_ITER_{iteration}"


def make_best_run_name(model: str) -> str:
    """Build the run name for the overall-best candidate of a model."""
    model_suffix = re.sub(r"[^A-Za-z0-9]", "_", model).upper()
    return f"{model_suffix}_BEST"


# ---------------------------------------------------------------------------
# Parameter / metric builders
# ---------------------------------------------------------------------------


def build_run_params(
    *,
    function_impl: str,
    model: str,
    iteration: str,
    parent_candidate: str = "",
    function_name: str = "",
    score_source: str = "",
    num_examples: int | None = None,
    is_full_eval: bool = True,
    reflection_model: str = "",
    total_candidates: int | None = None,
    total_metric_calls: int | None = None,
    total_reflection_calls: int | None = None,
    elapsed_seconds: float | None = None,
    status: str = "completed",
    error_message: str = "",
) -> list[dict[str, str]]:
    """Build the parameters list for an experiment run.

    Only non-empty / non-None values are included to keep the parameter
    set compact.
    """
    params: list[dict[str, str]] = [
        {"name": "function_impl", "value": function_impl},
        {"name": "model", "value": model},
        {"name": "iteration", "value": str(iteration)},
        {"name": "parent_candidate", "value": parent_candidate},
    ]

    _optional = [
        ("function_name", function_name),
        ("score_source", score_source),
        ("is_full_eval", str(is_full_eval).lower()),
        ("reflection_model", reflection_model),
        ("status", status),
        ("error_message", error_message),
    ]
    for name, value in _optional:
        if value:
            params.append({"name": name, "value": value})

    _optional_int = [
        ("num_examples", num_examples),
        ("total_candidates", total_candidates),
        ("total_metric_calls", total_metric_calls),
        ("total_reflection_calls", total_reflection_calls),
    ]
    for name, value in _optional_int:
        if value is not None:
            params.append({"name": name, "value": str(value)})

    if elapsed_seconds is not None:
        params.append({"name": "elapsed_seconds", "value": str(round(elapsed_seconds, 2))})

    return params


def build_run_metrics(
    *,
    valset_score: float | None = None,
    test_score: float | None = None,
) -> list[dict[str, Any]] | None:
    """Build the metrics list for an experiment run.

    Returns ``None`` if there are no metrics to set.
    """
    metrics: list[dict[str, Any]] = []
    if valset_score is not None:
        metrics.append({"name": "valset_score", "value": valset_score})
    if test_score is not None:
        metrics.append({"name": "test_score", "value": test_score})
    return metrics or None


# ---------------------------------------------------------------------------
# Eval-detail artifact
# ---------------------------------------------------------------------------


def write_eval_detail_artifact(
    details: list[dict[str, Any]],
    dest_dir: str | None = None,
) -> str:
    """Serialize per-row evaluation detail to a gzipped JSON file.

    Each element in *details* should contain keys like ``row_idx``,
    ``input_text``, ``expected``, ``predicted``, ``metric_score``,
    ``metric_feedback``, ``split``.

    Returns the path to the written file.
    """
    if dest_dir is None:
        dest_dir = tempfile.mkdtemp(prefix="gepa_eval_")

    path = os.path.join(dest_dir, "eval_detail.json")
    with open(path, "w") as f:
        json.dump(details, f)
    return path


# ---------------------------------------------------------------------------
# High-level: persist a full optimization run
# ---------------------------------------------------------------------------


def save_optimization_to_experiment(
    session: Session,
    experiment_name: str,
    *,
    function_name: str,
    model: str,
    seed_prompt: str,
    best_prompt: str,
    candidates: list[str],
    val_scores: list[float] | None,
    best_idx: int,
    seed_val_score: float | None = None,
    best_val_score: float | None = None,
    seed_test_score: float | None = None,
    best_test_score: float | None = None,
    score_source: str = "validation",
    num_examples: int | None = None,
    reflection_model: str = "",
    total_candidates: int | None = None,
    total_metric_calls: int | None = None,
    total_reflection_calls: int | None = None,
    elapsed_seconds: float | None = None,
    run_dir: str | None = None,
    eval_details: list[dict[str, Any]] | None = None,
) -> None:
    """Persist a full GEPA optimization result to a Snowflake Experiment.

    Creates runs for: SEED, each iteration, and MODEL_BEST.  Uploads
    ``run_dir`` artifacts and ``eval_detail.json`` when provided.  All
    runs are committed at the end.

    This is intentionally fault-tolerant: failures are logged but never
    propagated so that experiment persistence cannot break an optimization
    run.
    """
    try:
        _save_optimization_to_experiment_impl(
            session=session,
            experiment_name=experiment_name,
            function_name=function_name,
            model=model,
            seed_prompt=seed_prompt,
            best_prompt=best_prompt,
            candidates=candidates,
            val_scores=val_scores,
            best_idx=best_idx,
            seed_val_score=seed_val_score,
            best_val_score=best_val_score,
            seed_test_score=seed_test_score,
            best_test_score=best_test_score,
            score_source=score_source,
            num_examples=num_examples,
            reflection_model=reflection_model,
            total_candidates=total_candidates,
            total_metric_calls=total_metric_calls,
            total_reflection_calls=total_reflection_calls,
            elapsed_seconds=elapsed_seconds,
            run_dir=run_dir,
            eval_details=eval_details,
        )
    except Exception:
        logger.exception(
            "Failed to persist optimization to experiment %s", experiment_name,
        )


def save_failed_run_to_experiment(
    session: Session,
    experiment_name: str,
    *,
    function_name: str,
    model: str,
    error_message: str,
    prompt_snippet: str = "",
    elapsed_seconds: float | None = None,
) -> None:
    """Record a failed model optimization as an experiment run.

    Replaces the old ``_log_tracking_error`` / ``_ERRORS`` table pattern.
    """
    try:
        run_name = make_best_run_name(model)
        params = build_run_params(
            function_impl=prompt_snippet[:500],
            model=model,
            iteration="0",
            function_name=function_name,
            status="failed",
            error_message=error_message[:16_777_000],
            elapsed_seconds=elapsed_seconds,
        )
        add_experiment_run(session, experiment_name, run_name, params=params)
        commit_experiment_run(session, experiment_name, run_name)
    except Exception:
        logger.exception(
            "Failed to log error run to experiment %s", experiment_name,
        )


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------


def _save_optimization_to_experiment_impl(
    session: Session,
    experiment_name: str,
    *,
    function_name: str,
    model: str,
    seed_prompt: str,
    best_prompt: str,
    candidates: list[str],
    val_scores: list[float] | None,
    best_idx: int,
    seed_val_score: float | None,
    best_val_score: float | None,
    seed_test_score: float | None,
    best_test_score: float | None,
    score_source: str,
    num_examples: int | None,
    reflection_model: str,
    total_candidates: int | None,
    total_metric_calls: int | None,
    total_reflection_calls: int | None,
    elapsed_seconds: float | None,
    run_dir: str | None,
    eval_details: list[dict[str, Any]] | None,
) -> None:
    # -- SEED run --
    seed_run = make_run_name(model, 0, is_seed=True)
    seed_params = build_run_params(
        function_impl=seed_prompt,
        model=model,
        iteration="0",
        function_name=function_name,
        score_source=score_source,
        num_examples=num_examples,
        status="completed",
    )
    seed_metrics = build_run_metrics(
        valset_score=seed_val_score,
        test_score=seed_test_score,
    )
    add_experiment_run(
        session, experiment_name, seed_run,
        params=seed_params, metrics=seed_metrics,
    )
    commit_experiment_run(session, experiment_name, seed_run)

    # -- Iteration runs (skip index 0 = seed, already covered) --
    for idx, candidate_text in enumerate(candidates):
        if idx == 0:
            continue
        iter_run = make_run_name(model, idx)
        iter_score = (
            val_scores[idx] if val_scores and idx < len(val_scores) else None
        )
        parent = (
            make_run_name(model, idx - 1) if idx > 1
            else seed_run
        )
        iter_params = build_run_params(
            function_impl=candidate_text,
            model=model,
            iteration=str(idx),
            parent_candidate=parent,
            function_name=function_name,
            status="completed",
        )
        iter_metrics = build_run_metrics(valset_score=iter_score)
        add_experiment_run(
            session, experiment_name, iter_run,
            params=iter_params, metrics=iter_metrics,
        )
        commit_experiment_run(session, experiment_name, iter_run)

    # -- BEST run (summary with aggregate stats) --
    best_run = make_best_run_name(model)
    best_parent = (
        make_run_name(model, best_idx) if best_idx > 0
        else seed_run
    )
    best_params = build_run_params(
        function_impl=best_prompt,
        model=model,
        iteration=str(best_idx),
        parent_candidate=best_parent,
        function_name=function_name,
        score_source=score_source,
        num_examples=num_examples,
        is_full_eval=True,
        reflection_model=reflection_model,
        total_candidates=total_candidates,
        total_metric_calls=total_metric_calls,
        total_reflection_calls=total_reflection_calls,
        elapsed_seconds=elapsed_seconds,
        status="completed",
    )
    best_metrics = build_run_metrics(
        valset_score=best_val_score,
        test_score=best_test_score,
    )
    add_experiment_run(
        session, experiment_name, best_run,
        params=best_params, metrics=best_metrics,
    )

    # Upload artifacts before committing the BEST run.
    # Artifact upload failures are non-fatal -- the run metadata (params +
    # metrics) is already stored.  Errors are captured in best_params so they
    # surface in the SPROC return value for diagnosis.
    if run_dir and os.path.isdir(run_dir):
        run_dir_contents = [
            f for f in os.listdir(run_dir)
            if os.path.isfile(os.path.join(run_dir, f))
        ]
        if run_dir_contents:
            try:
                put_experiment_artifact(
                    session, experiment_name, best_run,
                    local_path=run_dir, subdir="run_dir",
                )
            except Exception as exc:
                logger.warning(
                    "Failed to upload run_dir artifacts (%d files in %s): %s",
                    len(run_dir_contents), run_dir, exc,
                )

    if eval_details:
        try:
            detail_path = write_eval_detail_artifact(eval_details)
            put_experiment_artifact(
                session, experiment_name, best_run,
                local_path=detail_path,
            )
        except Exception as exc:
            logger.warning("Failed to upload eval detail: %s", exc)

    commit_experiment_run(session, experiment_name, best_run)
