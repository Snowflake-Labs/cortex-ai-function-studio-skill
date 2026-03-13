---
name: synthetic-data-generation
description: "Guidelines and examples for generating high-quality synthetic training data for AI function evaluation and optimization."
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Synthetic Data Generation for AI Functions

Generate challenging, realistic test data for evaluating and optimizing AI functions.

## Automated Generation (Recommended)

Use the `GENERATE_SYNTHETIC_DATA` stored procedure for server-side data generation using Cortex LLM.

### When to Use

Load this reference when user intent involves: "generate data", "synthetic data", "test data", "create test cases", "make training data", "pseudo label", or "label my input-only table".

### Workflow

Follow these steps sequentially when generating synthetic data.

#### Step 1: Ensure Infrastructure Exists

Ask user for database and schema where infrastructure should be created (or use existing from prior context).

**Load** `references/infrastructure_setup.md` and run the deploy script shortcut to provision stage, modules, and procedures before generation.

#### Step 2: Infer Task Description (Preferred)

**IMPORTANT**: The GENERATE_SYNTHETIC_DATA procedure requires a **task description** string, NOT a function name.

When an AI function is already known in context (for example from optimize/evaluate workflows), **do not ask the user to retype intention**. Infer `task_description` in this order:

1. Function `COMMENT` (intention text)
2. System prompt from function DDL (fallback)

Then present a quick confirmation/edit prompt:
```
I inferred this task description from your function metadata:
{inferred_task_description}

Press Enter to use this, or edit it if needed.
```

Only if inference fails (no comment and no usable prompt), ask the user for a task description:
```
Describe the task your AI function performs. This guides synthetic test-case generation.

Example: "Classify customer support tickets into categories: billing, technical, account, general. Return the category and priority level."
```

Store the final value as `task_description`.

#### Step 3: Collect Input Columns & Output Schema

Ask the user:
```
What are the input columns (in function argument order)?
What is the output schema for the function? Provide either:
  - An OUTPUT_SCHEMA (JSON object with "properties"), or
  - A FUNCTION_NAME of an existing structured AI function (schema will be inferred from its response_format)

Example:
- Input columns: CLAIM, DOCUMENTS
- Output schema: {"properties": {"VERDICT": {"type": "string"}, "RATIONALE": {"type": "string"}, "EVIDENCE": {"type": "string"}, "CONFIDENCE": {"type": "number"}}}
```

Store `input_columns`. For the SQL call, format as comma-separated, single-quoted values:
`input_columns_csv = 'CLAIM', 'DOCUMENTS'`.
Store `output_schema` as a JSON object, or `function_name` if inferring from an existing function.

#### Step 4: Collect Output Table

Ask the user:
```
What is the fully qualified table name where synthetic data should be stored?

Example: MY_DB.MY_SCHEMA.SYNTHETIC_TEST_DATA
```

Store as `output_table`.

#### Step 5: Collect Number of Examples

Ask the user:
```
How many synthetic examples would you like to generate?

Options:
- 50 (quick test / minimum for evaluation)
- 200 (recommended for test set)
- 300 (recommended for training set)
- 500 (full dataset: split into ~300 train / ~200 test)
- Custom number
```

Store as `num_examples`. Default: 500.

**Guidance:**
- For evaluation only: Generate ~200 examples
- For optimization only: Generate ~300 examples
- For both: Generate ~500 examples, then split 60/40

#### Step 6: Collect Difficulty Distribution

Ask the user:
```
What difficulty distribution would you like for your test cases?

The distribution is controlled by two parameters:
- easy_pct: Percentage of straightforward, common cases
- medium_pct: Percentage of moderately complex cases
- hard_pct: Automatically calculated as (100 - easy_pct - medium_pct)

Recommended distributions:
1. **Balanced** (default): 50% easy, 30% medium, 20% hard
2. **Challenge-focused**: 30% easy, 30% medium, 40% hard
3. **Beginner-friendly**: 70% easy, 20% medium, 10% hard
4. **Custom**: Specify your own percentages
```

Store as `easy_pct` and `medium_pct`. Defaults: easy_pct=50, medium_pct=30.

#### Step 7: Collect Model

**Load** `references/model_selection.md` and follow its full workflow, biasing toward the strongest available model since synthetic data quality depends heavily on model capability.

Store as `model`. Default: `claude-opus-4-6`

#### Step 8: Execute Generation

**Optional: Set session param for `AI_COMPLETE(... return_error_details => TRUE)`**

If you want detailed error info from `return_error_details`, run this **in the same session/connection that executes the SPROC** (the Streamlit app connection):

```sql
ALTER SESSION SET AI_SQL_ERROR_HANDLING_USE_FAIL_ON_ERROR = FALSE;
```

If you can't set this (e.g., different session), proceed anyway; the app handles missing error details.

Call the stored procedure with collected parameters:

**MANDATORY**: Wrap the SPROC `CALL ...` in the query tag wrapper from `references/query_tag.md` (set/restore `QUERY_TAG`). The agent MUST inject its local `CORTEX_SESSION_ID` into the wrapper and record it under the canonical key `__CUSTOM_AI_FUNCTION_CORTEX_SESSION_ID` (merge into JSON tags when possible; otherwise append to string tags).

```sql
CALL {database}.{schema}.GENERATE_SYNTHETIC_DATA(
    '{task_description}',
    '{output_table}',
    ARRAY_CONSTRUCT({input_columns_csv}),   -- INPUT_COLUMNS (required)
    '{model}',                               -- MODEL (required)
    {num_examples},
    {easy_pct},
    {medium_pct},
    100,                                     -- BATCH_SIZE
    NULL,                                    -- SOURCE_TABLE
    '{function_name}',                       -- FUNCTION_NAME (infer output schema) OR NULL
    {output_schema},                         -- OUTPUT_SCHEMA (explicit JSON schema) OR NULL
    NULL                                     -- MAX_SOURCE_ROWS
);
```

**Note**: `INPUT_COLUMNS` is required. Output shape must be provided via `OUTPUT_SCHEMA` or `FUNCTION_NAME`.
**Note**: Outputs are generated under an `outputs` key (JSON object) with keys matching the schema properties.

**Note**: This may take several minutes depending on the number of examples requested.

#### Step 9: Show Results

Display the results to the user:

```
Synthetic data generation complete!

Output table: {output_table}
Total examples generated: {total_generated}
Difficulty distribution:
  - Easy: {easy_count}
  - Medium: {medium_count}
  - Hard: {hard_count}

You can view your data with:
  SELECT * FROM {output_table} LIMIT 10;

The table has the following columns:
  - ID: Auto-incrementing identifier
  - Input columns: One VARCHAR column per input specified by `INPUT_COLUMNS`
  - EXPECTED: VARIANT column containing JSON object with keys from the output schema
  - DIFFICULTY: easy, medium, or hard

To access individual expected values:
  SELECT EXPECTED:key_name FROM {output_table};
```

If there were batch errors, report them but note that partial data may still be usable.

#### Step 10: Continue

Ask the user what they want to do next:

```
What would you like to do next?

1. **View the data** - Preview the generated examples
2. **Evaluate** - Run your AI function against this test data
3. **Optimize** - Use this data to optimize your AI function's prompt
4. **Generate more** - Create additional synthetic data
5. **Done** - Finished with data generation
```

Route based on selection:
- View: Execute `SELECT * FROM {output_table} LIMIT 10;`
- Evaluate: Load `evaluate/SKILL.md`
- Optimize: Load `optimize/SKILL.md`
- Generate more: Return to Step 2
- Done: End workflow

---

## Pseudo-Label Input-Only Tables

Use this flow when the user already has input rows but no expected labels.

### When to Use

- Evaluate or optimize workflows require labeled data, but source table is input-only.
- User wants labels generated and reviewed before full run, with optional model override.

### Requirements

- No pre-existing function is required.
- Define output shape via `OUTPUT_SCHEMA` (JSON object with `properties`) or `FUNCTION_NAME` (infers schema from an existing structured function's `response_format`).

### Pseudo-Label Workflow

1. Confirm source table and input columns.
2. Confirm task description for expected-label generation.
3. Define output shape (`OUTPUT_SCHEMA` or `FUNCTION_NAME`).
4. Choose destination labeled table (same customer schema).
5. Choose pseudo-label model (default `claude-opus-4-6`; allow user override).
6. Run a preview with a small row cap.
7. Show preview (`INPUT_*`, `EXPECTED`) and allow revise/regenerate.
8. On approval, run full labeling (all rows) and overwrite destination table.
9. Continue to evaluate/optimize with:
   - `expected_column` / `label_column` = `EXPECTED`
   - `input_columns` unchanged

### Preview Call (sample rows)

```sql
CALL {database}.{schema}.GENERATE_SYNTHETIC_DATA(
    '{task_description}',
    '{output_table}',
    ARRAY_CONSTRUCT({input_columns_csv}),
    NULL,                            -- MODEL optional; NULL defaults to claude-opus-4-6
    50,                              -- ignored in pseudo-label mode
    50,                              -- ignored in pseudo-label mode
    30,                              -- ignored in pseudo-label mode
    20,                              -- BATCH_SIZE
    '{source_table}',                -- SOURCE_TABLE (input-only table)
    '{function_name}',               -- FUNCTION_NAME (infer output schema) OR NULL
    {output_schema},                 -- OUTPUT_SCHEMA (explicit JSON schema) OR NULL
    20                               -- MAX_SOURCE_ROWS preview cap
);
```

### Full Run Call (all rows, overwrite table)

```sql
CALL {database}.{schema}.GENERATE_SYNTHETIC_DATA(
    '{task_description}',
    '{output_table}',
    ARRAY_CONSTRUCT({input_columns_csv}),
    '{model}',                        -- optional override; NULL uses default
    50,
    50,
    30,
    100,                             -- BATCH_SIZE
    '{source_table}',
    '{function_name}',                -- FUNCTION_NAME (infer output schema) OR NULL
    {output_schema},                  -- OUTPUT_SCHEMA (explicit JSON schema) OR NULL
    NULL                             -- MAX_SOURCE_ROWS NULL => label all rows
);
```

**Output table shape (same as synthetic mode):**
- Input columns (`VARCHAR`)
- `EXPECTED` (`VARIANT`, typed JSON object)
- `DIFFICULTY` (`VARCHAR`, set to `pseudo`)

---

### Parameters Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| TASK_DESCRIPTION | VARCHAR | required | Description of the AI function task (NOT a function name) |
| OUTPUT_TABLE | VARCHAR | required | Fully qualified table name for output |
| INPUT_COLUMNS | ARRAY | required | Required input column names in argument order |
| MODEL | VARCHAR | NULL | Synthetic mode: required. Pseudo-label mode: optional (defaults to `claude-opus-4-6` if omitted). |
| NUM_EXAMPLES | INTEGER | 50 | Total number of examples to generate |
| EASY_PCT | INTEGER | 50 | Percentage of easy examples (0-100) |
| MEDIUM_PCT | INTEGER | 30 | Percentage of medium examples (0-100) |
| BATCH_SIZE | INTEGER | 100 | Maximum examples to generate per LLM call |
| SOURCE_TABLE | VARCHAR | NULL | If set, enables pseudo-label mode from existing input rows |
| FUNCTION_NAME | VARCHAR | NULL | Optional: infer schema from an existing structured function |
| OUTPUT_SCHEMA | VARIANT | NULL | Optional explicit output schema override |
| MAX_SOURCE_ROWS | INTEGER | NULL | Optional row cap for pseudo-label preview |

### Output Table Schema

The SPROC creates a table with the following columns:

| Column | Type | Description |
|--------|------|-------------|
| ID | INT | Auto-incrementing ID |
| Input columns | VARCHAR | One column per input specified by `INPUT_COLUMNS` |
| EXPECTED | VARIANT | JSON object containing all output keys from the output schema |
| DIFFICULTY | VARCHAR | easy, medium, or hard |

**Accessing output values**: Use Snowflake's VARIANT syntax to access individual output keys:
```sql
SELECT EXPECTED:verdict, EXPECTED:rationale FROM {output_table};
-- Or cast to string: EXPECTED:verdict::STRING
```

### Return Value

```json
{
    "success": true,
    "output_table": "MY_DB.MY_SCHEMA.SYNTHETIC_TEST_DATA",
    "total_generated": 50,
    "input_columns": ["CLAIM", "DOCUMENTS"],
    "expected_keys": ["VERDICT", "RATIONALE"],
    "difficulty_distribution": {"easy": 25, "medium": 15, "hard": 10},
    "batch_errors": null
}
```

### Information Checklist

Before calling `GENERATE_SYNTHETIC_DATA`, ensure you have collected:

| Field | Description | Required | Default |
|-------|-------------|----------|---------|
| `task_description` | Description of the AI function task (or expected-label task in pseudo-label mode) | Yes | - |
| `output_table` | Fully qualified output table name | Yes | - |
| `input_columns` | Input column names in argument order (become separate VARCHAR columns) | Yes | - |
| `output_schema` or `function_name` | Output schema (one is required). Provide `output_schema` as a JSON object with `properties`, or `function_name` to infer from an existing function. | Yes (one of) | - |
| `model` | Cortex model (`required` in synthetic mode; optional in pseudo-label mode) | Conditionally | pseudo-label default: claude-opus-4-6 |
| `num_examples` | Number of examples to generate | No | 50 |
| `easy_pct` | Percentage of easy examples | No | 50 |
| `medium_pct` | Percentage of medium examples | No | 30 |
| `batch_size` | Maximum examples per LLM call | No | 100 |
| `source_table` | Existing input-only source table (pseudo-label mode) | No | NULL |
| `max_source_rows` | Preview cap for pseudo-label mode | No | NULL |

---

## Multi-Hop Reasoning Patterns

When generating challenging test cases (manually or reviewing SPROC output), include these reasoning patterns:

| Pattern | Description | Example |
|---------|-------------|---------|
| **Sequential Chain** | A causes B causes C | "First X happened, which caused Y, leading to Z outcome" |
| **Scattered Information** | Key facts spread across text | Important details buried in middle of long description |
| **Contradictory Signals** | Mixed positive/negative requiring judgment | "Product is great BUT..." or "Had issues BUT resolved" |
| **Temporal Sequences** | Order of events matters | "Initially bad → improved over time" vs "Started good → degraded" |
| **Conditional Logic** | Different conditions yield different outcomes | "If professional use: inadequate. If hobby use: perfect" |
| **Hidden Priority** | Urgency buried in routine request | "Minor question... by the way, this is for a $500K decision" |

**Distribution guideline:** ~60% common cases, ~20% edge cases, ~20% multi-hop reasoning.
