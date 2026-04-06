<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Custom Evaluation Metrics

Create domain-specific evaluation metrics tailored to your AI function's requirements.

## When to Load

Load from evaluate or optimize skill when user says: "custom metric", "create metric", "define metric", "my own metric", "custom evaluation", "domain-specific metric", "new metric".

## Information Model

| Field | Required | Default | Confirm | Dependencies |
|-------|----------|---------|---------|--------------|
| `metric_name` | Yes | - | No | - |
| `metric_description` | Yes | - | No | - |
| `metric_code` | Yes | (generated) | **Yes** | metric_description |
| `database` | Yes | (from context) | No | - |
| `schema` | Yes | (from context) | No | - |

**Critical fields** (always confirm even if pre-provided): `metric_code`

**Simple fields** (accept silently if pre-provided): `metric_name`, `metric_description`, `database`, `schema`

## Pre-Collection

Before prompting, scan the user's initial message and any prior context for already-provided information:

1. **Metric name**: Look for snake_case names like "keyword_coverage", "json_validator"
2. **Metric description**: Look for descriptions of what to measure ("check if keywords appear", "validate JSON format")
3. **database/schema**: Usually inherited from parent workflow context

For each piece found:
- **Simple fields**: Accept silently, proceed without re-asking
- **Critical fields**: Present for confirmation even if pre-provided ("Here's the metric code I generated — confirm?")

## How Custom Metrics Work

A custom metric is a Python UDF created directly in Snowflake. The UDF accepts two VARCHAR inputs (expected and predicted) and returns a VARIANT containing a numeric `score` and text `feedback`. The evaluation and optimization SPROCs call this UDF by its fully qualified name.

Evaluation metrics produce a numeric score **and** text feedback explaining why a prediction is correct or incorrect. This feedback is critical: during the optimization step, it's fed back to the LLM to help refine the prompt your function uses. Good feedback is specific and actionable -- e.g., "Found 3 of 5 keywords; missing: X, Y" is far more useful than "Partially correct".

### Restriction: Deterministic and Composite Only

Custom metrics must be **deterministic**. They must NOT:
- Call `AI_COMPLETE` or any other LLM
- Call `get_active_session()` or use a Snowpark session
- Implement any form of LLM-as-judge logic
- Attempt to replicate `llm_judge` behavior in any way

If the user asks for an LLM-based custom metric, explain:
```
Custom metrics must be deterministic -- they cannot call LLMs or use AI_COMPLETE.

For LLM-based evaluation, use the built-in 'llm_judge' metric directly, instead of a custom metric:
  METRIC_NAME = 'llm_judge'

If you need LLM judgment combined with deterministic checks, run two
separate evaluations: one with 'llm_judge' and one with your custom metric.
```

Do NOT attempt to work around this restriction. Do NOT offer to embed LLM calls in UDF code. The built-in `llm_judge` is the only supported path for LLM-based evaluation.

### Metric Types

Custom metrics come in two flavors:

1. **Simple** — A single check applied to the entire expected/predicted strings
2. **Composite** — Multiple checks applied to individual fields of structured (JSON) expected/predicted values, combined with configurable weights

Both types use the same UDF contract. The difference is in the internal logic.

### The UDF Contract

Every custom metric UDF must follow this contract:

```sql
CREATE OR REPLACE FUNCTION {database}.{schema}.{metric_name}(
    EXPECTED VARCHAR,
    PREDICTED VARCHAR
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'evaluate'
AS $$
def evaluate(expected, predicted):
    # Your scoring logic here
    score = ...   # float between 0.0 and 1.0
    feedback = ... # string explaining the score
    return {"score": score, "feedback": feedback}
$$;
```

The UDF returns a VARIANT dict with `score` and `feedback` keys.

### Robust JSON Parsing (MANDATORY)

**⚠️ CRITICAL**: When parsing JSON in custom metrics, **never use `json.loads()` directly**. Snowflake VARIANT columns cast to VARCHAR often produce Python-style single-quoted strings (e.g., `{'category': 'billing'}`) instead of valid JSON double-quoted strings. Raw `json.loads()` will fail silently on these, scoring every row as 0.0.

**Every custom metric that parses JSON must include and use this `_parse_json` helper:**

```python
import ast
import json

def _parse_json(text):
    """Parse JSON that may use single quotes (common from Snowflake VARIANT).
    
    Snowflake VARIANT-to-VARCHAR casts often produce Python-style dicts with
    single quotes instead of standard JSON double quotes. This helper handles
    both formats gracefully.
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        pass
    return None
```

**Usage pattern** — always check for `None` after parsing:

```python
exp = _parse_json(expected)
pred = _parse_json(predicted)
if exp is None or pred is None:
    return {"score": 0.0, "feedback": "Could not parse expected/predicted as JSON"}
```

This helper is required in all composite metrics and any simple metric that parses structured data. Do NOT skip it or replace it with raw `json.loads()`.

## Workflow

### Step 1: Understand What the User Wants to Measure

**If `metric_name` and `metric_description` already collected** (user provided upfront):
- Skip the prompt — proceed directly to Step 2
- Acknowledge: "I'll create the `{metric_name}` metric for {metric_description}"

**If not collected**, have a conversation to understand the metric. Ask:

```
What would you like to measure? Describe what makes a prediction correct,
partially correct, or incorrect for your use case.
```

Then determine the metric type:

```
Is this a:
1. Simple metric - One check on the full expected/predicted text
2. Composite metric - Different checks on different fields of structured JSON output
```

**For simple metrics**, gather:
- What it measures and how scoring works
- What feedback should look like

Then suggest a snake_case name and confirm with the user:

```
Based on your description, I'd suggest naming this metric: {suggested_name}

Would you like to use this name, or choose your own?
(Names must be snake_case, e.g., keyword_coverage, format_check, field_completeness)
```

If the user provides their own name, enforce snake_case: lowercase, underscores only, no spaces or special characters. If their name doesn't comply, convert it and confirm:
```
I've converted that to snake_case: {converted_name}
Does that work?
```

Then proceed to Step 2.

**For composite metrics**, run the field configuration workflow:

**Step 1a: Identify fields from context**

You should already know the AI function's output fields from the evaluate/optimize workflow context (the function's response schema). List them:

```
Your function produces these output fields:
- category (string)
- summary (string)  
- entities (array)

I'll configure a check for each field. Some common check types:

| Check Type | Description |
|------------|-------------|
| exact_match | Score 1.0 if values match exactly (case-insensitive) |
| fuzzy_match | Score based on string similarity (uses SequenceMatcher) |
| contains_match | Score 1.0 if expected value appears within predicted |
| keyword_overlap | Score based on fraction of expected keywords found in predicted |
| skip | Exclude this field from scoring |

You can describe custom logic for every field -- just tell me what you want
and I'll write the check for it.
```

**Step 1b: Configure each field**

Go field by field. For each field, ask:

```
Field: {field_name} ({field_type})
How should this field be scored?
- Pick a common check type (exact_match / fuzzy_match / contains_match / keyword_overlap / skip)
- Or describe what you want (e.g., "score based on number of matching list items",
  "1.0 if the value is a valid date, 0.0 otherwise", "check that it starts with a specific prefix")
Weight? (0.0 - 1.0, will be normalized)
```

If the user picks a common check type, use the corresponding code pattern from the check type implementations below. If they describe custom logic, write a deterministic implementation that matches their description. All custom logic must be deterministic -- no LLM calls.

Build a configuration table as you go:

```
Field configuration:

| Field | Check | Weight |
|-------|-------|--------|
| category | exact_match | 0.3 |
| summary | fuzzy_match | 0.5 |
| entities | items in expected list found in predicted | 0.2 |

Does this look right?
```

After the field configuration is approved, suggest a snake_case name and confirm:

```
Based on your field configuration, I'd suggest naming this metric: {suggested_name}

Would you like to use this name, or choose your own?
(Names must be snake_case, e.g., article_quality, output_accuracy, multi_field_check)
```

If the user provides their own name, enforce snake_case: lowercase, underscores only, no spaces or special characters. If their name doesn't comply, convert it and confirm:
```
I've converted that to snake_case: {converted_name}
Does that work?
```

**⚠️ STOP**: Get user approval on the field configuration and metric name before proceeding to Step 2.

Collect the final configuration as a structured spec:
- `metric_name`: snake_case name
- `fields`: list of `{name, check_type, weight}`

This spec drives code generation in Step 2.

### Step 2: Write the Metric

Write the Python logic that will go inside the UDF. The code must be **deterministic** — no LLM calls, no session usage, no AI_Complete. If the user asks for LLM-based checks, redirect them to the built-in `llm_judge` metric.

All metrics must:
1. Have a handler function named `evaluate`
2. Accept `(expected: str, predicted: str)` parameters
3. Return a dict with `score` (float 0.0-1.0) and `feedback` (string)
4. Feedback explains the score in a way that helps prompt optimization
5. **Use `_parse_json()` instead of `json.loads()` for any JSON parsing** (see Robust JSON Parsing section above)

Write the file to `/tmp/{metric_name}.py` for local testing first.

#### Simple Metrics

For simple metrics, write the logic directly. Available standard library modules: `json`, `ast`, `re`, `difflib`, `math`, `collections`, `string`.

**Example -- keyword coverage metric:**

```python
def evaluate(expected, predicted):
    """Measures what fraction of expected keywords appear in the prediction."""
    expected_lower = expected.lower()
    predicted_lower = predicted.lower()

    expected_words = {w for w in expected_lower.split() if len(w) >= 3}
    predicted_words = set(predicted_lower.split())

    if not expected_words:
        return {"score": 1.0, "feedback": "No keywords to match"}

    matched = expected_words & predicted_words
    score = len(matched) / len(expected_words)

    if score == 1.0:
        return {"score": 1.0, "feedback": f"All {len(expected_words)} keywords found"}

    missing = expected_words - matched
    return {
        "score": score,
        "feedback": f"Found {len(matched)}/{len(expected_words)} keywords. Missing: {', '.join(sorted(missing))}",
    }
```

#### Composite Metrics

For composite metrics, use the field configuration from Step 1b to generate the code. The generated code must follow this structure:

1. Parse expected/predicted as JSON
2. For each configured field, apply the specified check
3. Collect `(field:check_description, score, weight)` tuples
4. Combine with weighted average
5. Build feedback with per-field detail and weight breakdown

**Standard check types** — use these when generating field blocks:

| Check Type | Logic | Score |
|------------|-------|-------|
| `exact_match` | Case-insensitive string equality | 0.0 or 1.0 |
| `fuzzy_match` | `SequenceMatcher(None, exp.lower(), pred.lower()).ratio()` | 0.0–1.0 continuous |
| `contains_match` | `exp.lower() in pred.lower()` | 0.0 or 1.0 |
| `keyword_overlap` | Fraction of expected words (len≥3) found in predicted | 0.0–1.0 continuous |
| `array_contains` | Fraction of expected list items found in `json.dumps(pred).lower()` | 0.0–1.0 continuous |

Each field block should: extract values via `exp.get("{field}", "")` / `pred.get("{field}", "")`, compute a score, and append `(name, score, weight)` to `sub_scores` and a description to `feedback_parts`.

**Custom per-field logic:** If the user describes custom logic instead of a standard check, write a deterministic implementation. Same pattern: extract, score 0.0–1.0, append to `sub_scores` and `feedback_parts`. No LLM calls.

**Composite code template** — assemble the field blocks into this structure:

```python
import ast
import json
from difflib import SequenceMatcher

# Include the _parse_json helper from the "Robust JSON Parsing" section above

def evaluate(expected, predicted):
    """Composite metric: {one-line description from field config}."""
    exp = _parse_json(expected)
    pred = _parse_json(predicted)
    if exp is None or pred is None:
        return {"score": 0.0, "feedback": "Could not parse expected/predicted as JSON"}

    sub_scores = []
    feedback_parts = []

    # --- {field_1}: {check_type_1} (weight: {weight_1}) ---
    {field_1_block}

    # --- {field_2}: {check_type_2} (weight: {weight_2}) ---
    {field_2_block}

    # ... one block per configured field ...

    # --- Combine weighted scores ---
    total_weight = sum(w for _, _, w in sub_scores)
    combined_score = sum(s * w for _, s, w in sub_scores) / total_weight if total_weight > 0 else 0.0

    detail = " | ".join(feedback_parts)
    breakdown = ", ".join(f"{name}={s:.2f}*{w}" for name, s, w in sub_scores)
    feedback = f"{detail} [weights: {breakdown}]"

    return {"score": combined_score, "feedback": feedback}
```

Do NOT deviate from this structure for composite metrics. Each field gets one block that computes a score and appends feedback. All field logic must be deterministic.

**⚠️ STOP**: Show the generated code to the user for review and approval before proceeding.

### Step 3: Generate Test Cases for Preview

Before creating the UDF, generate 3-5 representative test cases for the user to review. These should cover:
- A perfect match (expected score ~1.0)
- A complete mismatch (expected score ~0.0)
- One or two partial matches (expected score between 0.0 and 1.0)
- An edge case relevant to the use case

Present them in a table:

Example (keyword coverage metric):
```
I've generated some test cases for your metric. Please review:

| # | Expected | Predicted | Target Score | Description |
|---|----------|-----------|-------------|-------------|
| 1 | "the cat sat on the mat" | "the cat sat on the mat" | ~1.0 | Perfect match |
| 2 | "the cat sat on the mat" | "a dog ran in the park" | ~0.0 | No overlap |
| 3 | "the cat sat on the mat" | "the cat on mat" | ~0.5 | Partial match |
| 4 | "" | "some text" | ~1.0 | Empty expected (edge case) |

Do these test cases look right? Feel free to modify any or add your own.
```

**⚠️ STOP**: Get user approval on the test cases before running them.

### Step 4: Test with Preview

Write a test script at `/tmp/test_{metric_name}.py` that:
1. Imports `evaluate` from `/tmp/{metric_name}.py`
2. Runs each approved test case, comparing actual score to target (tolerance ±0.05)
3. Prints PASS/FAIL per case with score, target, and feedback

Run with:
```bash
uv run --project <SKILL_DIRECTORY> python /tmp/test_{metric_name}.py
```

Present results as a table with columns: `#`, `Description`, `Score`, `Target`, `Status`, `Feedback`.

**⚠️ STOP**: All tests must pass and user must approve the results before creating the UDF. If tests fail or feedback doesn't look right, fix the metric and re-test.

### Step 5: Create the UDF

Generate and execute the UDF DDL:

```sql
CREATE OR REPLACE FUNCTION {database}.{schema}.{metric_name}(
    EXPECTED VARCHAR,
    PREDICTED VARCHAR
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'evaluate'
AS $$
{metric_code}
$$;
```

Where `{metric_code}` is the Python code from Step 2 (the `evaluate` function and any imports).

**⚠️ STOP**: Show the full DDL to the user for review before executing.

Verify the UDF was created:
```sql
DESCRIBE FUNCTION {database}.{schema}.{metric_name}(VARCHAR, VARCHAR);
```

Quick smoke test in SQL:
```sql
SELECT {database}.{schema}.{metric_name}('expected text', 'predicted text') AS result;
```

### Step 6: Use It

The metric is now available by its fully qualified UDF name. To use it in evaluation or optimization:

- Pass `'{metric_name}'` as the `METRIC_NAME` parameter
- Pass `'{database}.{schema}.{metric_name}'` as the `CUSTOM_METRIC_UDF` parameter

See `evaluate/SKILL.md` Step 4 or `optimize/SKILL.md` Step 5 for full SPROC call syntax.

```
Custom metric "{metric_name}" is ready!

UDF: {database}.{schema}.{metric_name}

What would you like to do next?
1. **Evaluate** - Test your AI function with this metric
2. **Optimize** - Optimize your AI function with this metric
3. **Create Another** - Define another custom metric
4. **Done** - Exit
```

If Evaluate -> Load `evaluate/SKILL.md` with metric pre-selected.
If Optimize -> Load `optimize/SKILL.md` with metric pre-selected.

## Stopping Points

- Step 2: Review generated code before testing
- Step 3: Review and approve test cases
- Step 4: All tests must pass and results approved
- Step 5: Review DDL before executing
