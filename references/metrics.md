<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Evaluation Metrics

## When to Load

Load from evaluate/optimize workflows when selecting a metric. Triggers: "which metric", "select metric", "evaluation metric", "how to score".

## Architecture

Metrics are implemented as core Python functions with no external dependencies (except stdlib `difflib`).

**Implementation:** See `src/metrics_core.py`

## Available Metrics

| Metric | Use When | Score |
|--------|----------|-------|
| `exact_match` | Classification, categorical outputs, yes/no | 1.0 if exact match, else 0.0 |
| `fuzzy_match` | Minor variations, typos acceptable | 1.0 if similarity >= threshold |
| `contains_match` | Key answer embedded in verbose output | 1.0 if expected in predicted |
| `redaction_match` | PII redaction, placeholder content varies | 1.0 if text matches outside brackets |
| `llm_judge` | Open-ended, paraphrases acceptable | 1.0 if LLM judges correct |

## Core Function Signatures

All core functions return `tuple[float, str]` = (score, feedback).

```python
def exact_match_core(expected: str, predicted: str, **kwargs) -> tuple[float, str]
def fuzzy_match_core(expected: str, predicted: str, **kwargs) -> tuple[float, str]
def contains_match_core(expected: str, predicted: str, **kwargs) -> tuple[float, str]
def redaction_match_core(expected: str, predicted: str, **kwargs) -> tuple[float, str]
def llm_judge_core(expected: str, predicted: str, **kwargs) -> tuple[float, str]
```

**Options passed via kwargs:**

| Metric | Option | Type | Default | Description |
|--------|--------|------|---------|-------------|
| fuzzy_match | threshold | float | 0.85 | Minimum similarity score |
| llm_judge | task_description | str | '' | Task context for the judge |
| llm_judge | llm_call | callable | required | Function to call LLM |

## Usage

### In Evaluate SPROC

The SPROC imports `metrics_core.py` from the stage and uses `compute_metric()`:

```python
from metrics_core import compute_metric

score, feedback = compute_metric(metric_name, expected, predicted, **options)
```

### In Optimize

The optimizer imports `metrics_core.py` and uses the same `compute_metric()` function to score prompt variations:

```python
from metrics_core import compute_metric

score, feedback = compute_metric(metric_name, expected, predicted, **metric_options)
```

## Metric Selection Guide

| Task Type | Recommended Metric |
|-----------|-------------------|
| Classification | exact_match |
| Named Entity Extraction | fuzzy_match |
| Q&A with fixed answers | exact_match, contains_match |
| Open-ended generation | llm_judge |
| Verbose model outputs | contains_match |
| PII redaction | redaction_match |

## Metric Selection Prompt

Present this menu when asking users to select a metric:

```
Which metric would you like to use?

Built-in metrics:
1. exact_match - Score 1.0 if strings match exactly
2. fuzzy_match - Score based on string similarity (configurable threshold)
3. contains_match - Score 1.0 if expected is contained in predicted
4. redaction_match - Match text with redacted placeholders like [NAME]
5. llm_judge - Use LLM to judge correctness (requires task description)

Or:
6. Create custom metric - Build your own evaluation metric
```

**If user selects option 6:** Load `references/custom_metrics.md` to guide through custom metric creation. Preserve workflow context (function name, tables, columns) and return to the calling workflow after creation.