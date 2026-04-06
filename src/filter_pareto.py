# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Filter optimization results to pareto-optimal options.

Pareto-optimal means no other option is both cheaper AND has a higher score.
This ensures users only see meaningful trade-offs between cost and quality.

Example usage:
    # With character counts for automatic ratio calculation
    uv run python filter_pareto.py --json '[{"model": "llama3.1-8b", "score": 0.82}, ...]' \
        --prompt-chars 200 --avg-input-chars 500 --avg-output-chars 10

    # Via stdin
    echo '[...]' | uv run python filter_pareto.py --prompt-chars 200 --avg-input-chars 500 --avg-output-chars 10
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


def calculate_input_ratio(
    prompt_chars: int, avg_input_chars: float, avg_output_chars: float
) -> float:
    """Calculate the input token ratio from character counts.

    Args:
        prompt_chars: Length of system prompt in characters.
        avg_input_chars: Average length of user input in characters.
        avg_output_chars: Average length of expected output in characters.

    Returns:
        Input ratio (0.0 to 1.0) representing fraction of tokens that are input.
    """
    total_input = prompt_chars + avg_input_chars
    total = total_input + avg_output_chars
    if total <= 0:
        return 0.5  # Default to balanced if no data
    return total_input / total


def get_model_cost(model_name: str, input_ratio: float = 0.5) -> float:
    """Calculate relative model cost based on input/output token ratio.

    Args:
        model_name: Name of the model
        input_ratio: Ratio of input tokens to total tokens (0.0 to 1.0)
                    Default 0.5 means equal input and output tokens.

    Returns:
        Relative cost (float), normalized to llama3.1-8b = 1.0
    """
    if model_name not in MODELS:
        raise ValueError(
            f"Model {model_name} not supported, add to {_MODELS_JSON_PATH}."
        )
    model = MODELS[model_name]
    output_ratio = 1.0 - input_ratio
    return model["input_cost"] * input_ratio + model["output_cost"] * output_ratio


def filter_pareto_optimal(results: list[dict], input_ratio: float = 0.5) -> list[dict]:
    """Filter to pareto-optimal results based on cost and score.

    An option is pareto-optimal if no other option has:
    - Lower or equal cost AND strictly higher score, OR
    - Strictly lower cost AND higher or equal score

    Args:
        results: List of dicts with 'model' and 'score'.
        input_ratio: Ratio of input tokens for cost calculation.

    Returns:
        List of pareto-optimal results, sorted by relative_cost ascending.
    """
    if not results:
        return []

    # Calculate relative cost for each result
    for r in results:
        r["relative_cost"] = get_model_cost(r["model"], input_ratio)

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
    input_ratio: float | None = None,
) -> str:
    """Format pareto-optimal results as a markdown table.

    Args:
        results: Pareto-optimal results from filter_pareto_optimal().
        seed_score: Original score to calculate improvement.
        input_ratio: Calculated input ratio to display in header.

    Returns:
        Markdown table string.
    """
    if not results:
        return "No results to display."

    lines = []

    # Add input ratio info if provided
    if input_ratio is not None:
        lines.append(
            f"Input ratio: {input_ratio:.2f} ({input_ratio * 100:.0f}% input tokens)"
        )
        lines.append("")

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
        default=0,
        help="Length of system prompt in characters",
    )
    parser.add_argument(
        "--avg-input-chars",
        type=float,
        default=0,
        help="Average length of user input in characters (from SQL query)",
    )
    parser.add_argument(
        "--avg-output-chars",
        type=float,
        default=0,
        help="Average length of expected output in characters (from SQL query)",
    )
    parser.add_argument(
        "--format", choices=["json", "table"], default="json", help="Output format"
    )
    args = parser.parse_args()

    # Calculate input ratio from character counts
    input_ratio = calculate_input_ratio(
        args.prompt_chars, args.avg_input_chars, args.avg_output_chars
    )

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
    pareto = filter_pareto_optimal(results, input_ratio)

    # Output
    if args.format == "table":
        print(format_results_table(pareto, args.seed_score, input_ratio))
    else:
        # Include input_ratio in JSON output
        output = {
            "input_ratio": input_ratio,
            "pareto_optimal": pareto,
        }
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
