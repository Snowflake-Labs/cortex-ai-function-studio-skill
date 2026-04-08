# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Filter optimization results to pareto-optimal options.

Pareto-optimal means no other option is both cheaper AND has a higher score.
This ensures users only see meaningful trade-offs between cost and quality.

Cost formula: cost = input_price × prompt_chars + output_price × avg_output_chars.

Example usage:
    uv run python filter_pareto.py --json '[{"model": "llama3.1-8b", "score": 0.82}, ...]' \
        --prompt-chars 200 --avg-output-chars 10

    # Via stdin
    echo '[...]' | uv run python filter_pareto.py --prompt-chars 200 --avg-output-chars 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Load model costs from models.json (relative to llama3.1-8b = 1.0)
_MODELS_JSON_PATH = Path(__file__).parent / "models.json"
with open(_MODELS_JSON_PATH) as f:
    MODELS = json.load(f)


def get_model_cost(model_name: str, prompt_chars: int, avg_output_chars: int) -> float:
    """Calculate model cost based on prompt length and average output length.

    cost = input_price × prompt_chars + output_price × avg_output_chars.

    Args:
        model_name: Name of the model.
        prompt_chars: Character length of the system prompt.
        avg_output_chars: Average output character length from test data.

    Returns:
        Cost estimate (float).
    """
    if model_name not in MODELS:
        raise ValueError(
            f"Model {model_name} not supported, add to {_MODELS_JSON_PATH}."
        )
    model = MODELS[model_name]
    return prompt_chars * model["input_cost"] + avg_output_chars * model["output_cost"]


def filter_pareto_optimal(results: list[dict], prompt_chars: int, avg_output_chars: int) -> list[dict]:
    """Filter to pareto-optimal results based on cost and score.

    An option is pareto-optimal if no other option has:
    - Lower or equal cost AND strictly higher score, OR
    - Strictly lower cost AND higher or equal score

    Args:
        results: List of dicts with 'model' and 'score'.
        prompt_chars: Prompt character length for cost calculation.
        avg_output_chars: Average output character length for cost calculation.

    Returns:
        List of pareto-optimal results, sorted by relative_cost ascending.
    """    
    if not results:
        raise ValueError("No results provided")

    if prompt_chars <= 0:
        raise ValueError("Prompt length must be greater than 0")

    # Calculate relative cost for each result
    for r in results:
        r["relative_cost"] = get_model_cost(r["model"], prompt_chars, avg_output_chars)

    # Sort by (cost ascending, score descending)
    # - Cost ascending: process cheapest options first
    # - Score descending: within same cost, process highest score first
    #   This ensures lower-scoring options at the same cost are correctly
    #   identified as dominated and skipped
    sorted_results = sorted(results, key=lambda x: (x["relative_cost"], -x["score"]))

    # Single-pass pareto filter using a "sweep line" approach:
    # As we move from cheap to expensive, we track the best score seen so far.
    # A result is pareto-optimal only if its score exceeds all cheaper options.
    # If score <= max_score_so_far, a cheaper option dominates it (same or
    # better score at lower cost), so we skip it.
    pareto_optimal = []
    max_score_so_far = -1

    for result in sorted_results:
        if result["score"] > max_score_so_far:
            pareto_optimal.append(result)
            max_score_so_far = result["score"]

    return pareto_optimal


def format_results_table(
    results: list[dict],
    seed_score: float | None = None,
) -> str:
    """Format pareto-optimal results as a markdown table.

    Args:
        results: Pareto-optimal results from filter_pareto_optimal().
        seed_score: Original score to calculate improvement.

    Returns:
        Markdown table string.
    """
    if not results:
        return "No results to display."

    lines = []

    lines.extend(
        [
            "| # | Model | Score | Improvement | Relative Cost |",
            "|---|-------|-------|-------------|---------------|",
        ]
    )

    # Find min cost for labeling
    min_cost = min(r["relative_cost"] for r in results) if results else 1.0

    for i, r in enumerate(results, 1):
        score_pct = f"{r['score'] * 100:.1f}%"
        if seed_score is not None:
            improvement = f"+{(r['score'] - seed_score) * 100:.1f}%"
        else:
            improvement = "-"

        # Format cost as multiplier, mark cheapest
        cost_val = r["relative_cost"]
        if cost_val == min_cost:
            cost_label = f"{cost_val:.1f}x (cheapest)"
        else:
            cost_label = f"{cost_val:.1f}x"

        lines.append(
            f"| {i} | {r['model']} | {score_pct} | {improvement} | {cost_label} |"
        )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Filter optimization results to pareto-optimal options"
    )
    parser.add_argument("--json", type=str, help="JSON array of results")
    parser.add_argument(
        "--seed-score",
        type=float,
        help="Original seed score for improvement calculation",
    )
    parser.add_argument(
        "--prompt-chars",
        type=int,
        required=True,
        help="Length of system prompt in characters",
    )
    parser.add_argument(
        "--avg-output-chars",
        type=int,
        required=True,
        help="Average length of expected output in characters (from test data)",
    )
    parser.add_argument(
        "--format", choices=["json", "table"], default="json", help="Output format"
    )
    args = parser.parse_args()

    # Read input
    if args.json:
        data = json.loads(args.json)
    else:
        data = json.load(sys.stdin)

    # Handle single result dict vs array
    if isinstance(data, dict):
        results = [data]
    else:
        results = data

    # Filter to pareto-optimal
    pareto = filter_pareto_optimal(results, args.prompt_chars, args.avg_output_chars)

    # Output
    if args.format == "table":
        print(format_results_table(pareto, args.seed_score))
    else:
        output = {
            "pareto_optimal": pareto,
        }
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
