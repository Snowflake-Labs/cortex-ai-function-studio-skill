---
name: synthetic-data
description: "Generate synthetic data or pseudo-label input-only tables for AI function evaluation and optimization. Triggers: generate data, synthetic data, test data, create test cases, make training data, pseudo label, label my input-only table."
parent_skill: cortex-ai-function-studio
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Synthetic Data Generation for AI Functions

Generate realistic data for evaluating and optimizing AI functions.

## Automated Generation (Recommended)

Use the `GENERATE_SYNTHETIC_DATA` stored procedure for server-side data generation using Cortex LLM.

### When to Use

Load this sub-skill when user intent involves: "generate data", "synthetic data", "test data", "create test cases", "make training data", "pseudo label", or "label my input-only table".

### Workflow

Follow these steps sequentially when generating synthetic data.

#### Step 1: Infer Task Description (Preferred)

**IMPORTANT**: The GENERATE_SYNTHETIC_DATA procedure requires a **task description** string, NOT a function name.

When an AI function is already known in context (for example from optimize/evaluate workflows), **do not ask the user to retype intention**. Infer `task_description` in this order:

1. Function `COMMENT` (intention text)
2. System prompt from function DDL (fallback)

Then present a quick confirmation/edit prompt:
```
I inferred this task description from your function metadata:
{inferred_task_description}

Use this task description? (y/n) If no, provide an updated description.
```

Only if inference fails (no comment and no usable prompt), ask the user for a task description:
```
Describe the task your AI function performs. This guides synthetic test-case generation.

Example: "Classify customer support tickets into categories: billing, technical, account, general. Return the category and priority level."
```

Store the final value as `task_description`.

#### Step 2: Collect Input Columns & Output Schema

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

#### Step 3: Collect Output Table

Ask the user:
```
What is the fully qualified table name where synthetic data should be stored?

Example: MY_DB.MY_SCHEMA.SYNTHETIC_DATA
```

Store as `output_table`.

#### Step 4: Collect Model

**Load** `references/model_selection.md` and follow its full workflow, biasing toward the strongest available model since synthetic data quality depends heavily on model capability.

Store as `model`. Default: `claude-opus-4-6`

#### Step 5: Execute Generation

Run the generation script directly. It renders the anonymous SPROC, appends the CALL, and executes everything in a single Snowpark session. **Always pass every flag** — use `none` for unused optional parameters:

```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/run.py synthetic \
    --database {database} \
    --schema {schema} \
    --connection {connection} \
    --task-description "{task_description}" \
    --output-table {output_table} \
    --input-columns {input_columns...} \
    --model {model} \
    --num-examples 50 \
    --source-table none \
    --function-name {function_name or none} \
    --output-schema {output_schema_json or none} \
    --max-source-rows none
```

| Flag                  | Description                                                                                       |
|-----------------------|---------------------------------------------------------------------------------------------------|
| `--database`          | Database for the anonymous SPROC context                                                          |
| `--schema`            | Schema for the anonymous SPROC context                                                            |
| `--connection`        | Snowflake connection name from `connections.toml`                                                 |
| `--task-description`  | Description of the AI function task (NOT a function name). Guides generation.                     |
| `--output-table`      | Fully qualified table name for generated data (e.g., `DB.SCHEMA.SYNTHETIC_DATA`)                 |
| `--input-columns`     | Space-separated input column names in argument order                                              |
| `--model`             | Cortex model for generation, or `none` to use default (`claude-opus-4-6`)                        |
| `--num-examples`      | Total number of examples to generate (default: 50)                                                |
| `--source-table`      | Existing input-only table for pseudo-label mode, or `none` for synthetic generation               |
| `--function-name`     | Existing function to infer output schema from, or `none`                                          |
| `--output-schema`     | JSON output schema string (e.g., `'{"properties":{"LABEL":{"type":"string"}}}'`), or `none`      |
| `--max-source-rows`   | Row cap for pseudo-label preview, or `none` to process all rows                                   |

The script prints a JSON result to stdout:
```json
{"status": "success", "result": {...}, "output_table": "DB.SCHEMA.SYNTHETIC_DATA"}
```

**Note**: `--input-columns` is required. Output shape must be provided via `--output-schema` or `--function-name` (at least one must not be `none`).
**Note**: Outputs are generated under an `outputs` key (JSON object) with keys matching the schema properties.

#### Step 6: Show Results

Display the results to the user:

```
Synthetic data generation complete!

Output table: {output_table}
Total examples generated: {total_generated}

You can view your data with:
  SELECT * FROM {output_table} LIMIT 10;

The table has the following columns:
  - ID: Auto-incrementing identifier
  - Input columns: One VARCHAR column per input specified by `INPUT_COLUMNS`
  - EXPECTED: VARIANT column containing JSON object with keys from the output schema

To access individual expected values:
  SELECT EXPECTED:key_name FROM {output_table};
```

If there were batch errors, report them but note that partial data may still be usable.

#### Step 7: Continue

Ask the user what they want to do next:

```
What would you like to do next?

1. **View the data** - Preview the generated examples
2. **Evaluate** - Run your AI function against this data
3. **Optimize** - Use this data to optimize your AI function's prompt
4. **Generate more** - Create additional synthetic data
5. **Done** - Finished with data generation
```

Route based on selection:
- View: Execute `SELECT * FROM {output_table} LIMIT 10;`
- Evaluate: Load `evaluate/SKILL.md`
- Optimize: Load `optimize/SKILL.md`
- Generate more: Return to Step 1
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
   - `label_column` = `EXPECTED`
   - `input_columns` unchanged

### Preview Call (sample rows)

Run the same script with `--source-table` and `--max-source-rows` to cap the preview:

```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/run.py synthetic \
    --database {database} \
    --schema {schema} \
    --connection {connection} \
    --task-description "{task_description}" \
    --output-table {output_table} \
    --input-columns {input_columns...} \
    --model none \
    --num-examples 50 \
    --source-table {source_table} \
    --function-name {function_name or none} \
    --output-schema {output_schema_json or none} \
    --max-source-rows 20
```

### Full Run Call (all rows, overwrite table)

```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/run.py synthetic \
    --database {database} \
    --schema {schema} \
    --connection {connection} \
    --task-description "{task_description}" \
    --output-table {output_table} \
    --input-columns {input_columns...} \
    --model {model or none} \
    --num-examples 50 \
    --source-table {source_table} \
    --function-name {function_name or none} \
    --output-schema {output_schema_json or none} \
    --max-source-rows none
```

**Output table shape (same as synthetic mode):**
- Input columns (`VARCHAR`)
- `EXPECTED` (`VARIANT`, typed JSON object)

---

### Parameters Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| TASK_DESCRIPTION | VARCHAR | required | Description of the AI function task (NOT a function name) |
| OUTPUT_TABLE | VARCHAR | required | Fully qualified table name for output |
| INPUT_COLUMNS | ARRAY | required | Required input column names in argument order |
| MODEL | VARCHAR | NULL | Synthetic mode: required. Pseudo-label mode: optional (defaults to `claude-opus-4-6` if omitted). |
| NUM_EXAMPLES | INTEGER | 50 | Total number of examples to generate |
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

**Accessing output values**: Use Snowflake's VARIANT syntax to access individual output keys:
```sql
SELECT EXPECTED:verdict, EXPECTED:rationale FROM {output_table};
-- Or cast to string: EXPECTED:verdict::STRING
```

### Return Value

```json
{
    "success": true,
    "output_table": "MY_DB.MY_SCHEMA.SYNTHETIC_DATA",
    "total_generated": 50,
    "input_columns": ["CLAIM", "DOCUMENTS"],
    "expected_keys": ["VERDICT", "RATIONALE"],
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
| `source_table` | Existing input-only source table (pseudo-label mode) | No | NULL |
| `max_source_rows` | Preview cap for pseudo-label mode | No | NULL |

---

## Stopping Points

- ✋ Step 1: After inferring/collecting task description
- ✋ Step 4: After model selection
- ✋ Step 6: After showing results
- ✋ Step 7: After presenting next steps
- ✋ Pseudo-Label Step 7: After showing preview for approval

## Output

- Snowflake table with synthetic or pseudo-labeled data
- Columns: ID, input columns (VARCHAR), EXPECTED (VARIANT)
