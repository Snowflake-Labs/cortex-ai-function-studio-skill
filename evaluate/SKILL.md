---
name: evaluate-ai-function
description: "Evaluate an AI function's performance against a labeled dataset using a Python stored procedure."
parent_skill: custom-ai-function
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Evaluate AI Function

## When to Load

Load from main skill when user intent matches EVALUATE: "evaluate", "test", "measure", "score".

## Information Model

| Field | Required | Default | Confirm | Dependencies |
|-------|----------|---------|---------|--------------|
| `function_name` | Yes | - | No | - |
| `function_model` | Yes | (extracted) | No | function_name |
| `test_table` | Yes | - | No | - |
| `input_columns` | Yes | - | **Yes** | test_table |
| `label_column` | Yes | - | **Yes** | test_table |
| `stage` | Yes | (generated) | No | - |
| `sample_size` | No | all | No | - |
| `metric` | Yes | exact_match | No | - |
| `results_table` | No | (generated) | No | function_name |

**Critical fields** (always confirm even if pre-provided): `input_columns`, `label_column`

**Simple fields** (accept silently if pre-provided): `function_name`, `test_table`, `stage`, `sample_size`, `metric`, `results_table`

## Pre-Collection

Before prompting, scan the user's initial message and any prior context for already-provided information:

1. **Function name**: Look for fully qualified names like `DB.SCHEMA.FUNCTION_NAME`
2. **Test table**: Look for table references like `DB.SCHEMA.TABLE`
3. **Column mappings**: Look for phrases like "input column X", "label column Y", "map X to Y"
4. **Metric**: Look for evaluation metrics like "exact_match" or "llm_judge". Note these are not the only names, and users can build custom evaluation metrics. If you aren't sure, ask the user. 

For each piece found:
- **Simple fields**: Accept silently, proceed without re-asking
- **Critical fields**: Present for confirmation even if pre-provided ("I see you want to use columns X, Y — confirm?")

## Workflow

### Step 1: Get AI Function

**If `function_name` already collected** (user provided function name upfront):
- Skip the prompt — proceed directly to validation
- Acknowledge: "I'll evaluate `{function_name}`"

**If not collected**, ask user:
```
What AI function would you like to evaluate?

Provide the fully qualified function name (e.g., DB.SCHEMA.FUNCTION_NAME)
```

Validate function exists:
```sql
DESCRIBE FUNCTION <function_name>;
```

If not found confirm name or redirect to `create/SKILL.md` first.

**Extract the model from the function's DDL** for use in evaluation tracking:
```sql
SELECT GET_DDL('FUNCTION', '<function_name>(<param_types>)');
```

Parse the DDL to find the default model name in the function signature. The model appears as a parameter with a DEFAULT value:
```
MODEL_NAME VARCHAR DEFAULT ''model-name''
```

Store this as `{function_model}` for use when calling EVALUATE_AI_FUNCTION.

### Step 2: Get Test Data Table

**If `test_table` already collected** (user provided table name upfront):
- Skip to table validation, acknowledge: "I'll use `{test_table}` for evaluation"

**If coming from optimize workflow:** Pre-populate with the test table used during optimization:
```
The optimize workflow used test table: {test_table_from_optimize}
For consistent results, we recommend using the same test data.
Press Enter to confirm, or provide a different table name.
```

**If returning from synthetic data generation or pseudo-label generation:** After data has been created, you MUST confirm which table to use:
```
Data generation complete.

Which table would you like to use for evaluation?
1. **Use the generated table** - {generated_table_name}
2. **Specify a different table** - I have another table to use
```

**Otherwise:** **Load** `references/data_preparation.md` with context:
- `workflow`: "evaluate"
- Keep function context from Step 1 for synthetic or pseudo-label routes.

After data preparation completes, validate the table:
```sql
DESCRIBE TABLE <table_name>;
SELECT COUNT(*) AS row_count FROM <table_name>;
```
Store the column list from the DESCRIBE output — you will need it for column mapping validation below.

**If `input_columns` and `label_column` already collected** (user provided column mappings upfront):
- Present for confirmation (critical fields): "I'll use input columns `{input_columns}` and label column `{label_column}` — confirm?"

**Otherwise:** Follow column mapping in `references/data_preparation.md` Step 4.

**⚠️ STOP**: Always confirm column mapping before proceeding (critical fields).

After confirmation, validate columns per `references/data_preparation.md` Step 5.

### Step 3: Configure Evaluation

**If `stage`, `sample_size`, and `metric` already collected** (user provided configuration upfront):
- Accept silently (simple fields) — skip this step entirely
- Use defaults for any not provided: sample_size=all, metric=exact_match

**If not collected**, ask user:
```
Evaluation configuration:

- Stage: [e.g., DB.SCHEMA.AI_FUNCTIONS] - Stage for metrics code
- Sample size: [all] - Number of rows to evaluate (or 'all')
- Save detailed results? [yes/no] - Results saved to function-specific table
```

**Results Table Convention:** Results are saved to a function-specific table following the pattern `{FUNCTION_NAME}_EVAL_RESULTS`. Each evaluation run is tagged with a unique `run_id` like `eval_MY_FUNC_1739919133000` for tracking multiple evaluation experiments.

Note: The metric is selected at runtime when calling the SPROC, not at creation time.

### Step 4: Setup Infrastructure

Explain to the user:
```
To evaluate your AI function, I need to set up infrastructure:
1. Create a stage for Python code
2. Upload metrics code for scoring
3. Create the evaluation procedure

Stage location: {stage_name}
```

**⚠️ STOP**: Get user confirmation before proceeding.

**Load** `references/infrastructure_setup.md` and run the deploy script shortcut to provision stage, modules, and procedures before evaluation.

### Step 5: Run Evaluation

**MANDATORY**: Wrap the SPROC `CALL ...` in the query tag wrapper from `references/query_tag.md` (set/restore `QUERY_TAG`). The agent MUST inject its local `CORTEX_SESSION_ID` into the wrapper and record it under the canonical key `__CUSTOM_AI_FUNCTION_CORTEX_SESSION_ID` (merge into JSON tags when possible; otherwise append to string tags).

**Load** `references/metrics.md` and present the metric selection prompt.

**If user chooses "Create custom metric" (option 6):**
Load `references/custom_metrics.md` with context:
- Preserve function name: `{function_name}`
- Preserve test table: `{test_table}`
- Preserve column mappings: `{input_columns}`, `{label_column}`
- Note: *"After creating your custom metric, we'll return here to run the evaluation."*

After custom metric creation completes, return to this step with the new metric name.

**Custom Metric Loading:** Custom metrics are implemented as Python UDFs in Snowflake. Pass the fully qualified UDF name as the `CUSTOM_METRIC_UDF` parameter. The SPROC calls the UDF directly via SQL.

**Otherwise**, execute with the selected metric:

#### Execution Mode Selection

For large test datasets or complex metrics (especially `llm_judge`), evaluations can take several minutes. Ask the user:

```
How would you like to run the evaluation?

1. **Sync** (default)
2. **Async** (recommended for large datasets) - Run in background, track with run_id
```

**Sync execution** (default): Runs directly and returns results. 

**Async execution**: Uses a Snowflake Task to run the evaluation in the background. This prevents Cortex Code from timing out on long-running evaluations.

#### Sync Evaluation

```sql
CALL EVALUATE_AI_FUNCTION(
    '{function_name}',                    -- Fully qualified AI function name (DB.SCHEMA.FUNC)
    '{test_table}',                       -- Fully qualified test data table
    ARRAY_CONSTRUCT('{input_col1}', '{input_col2}'),  -- Input columns passed to function (in order)
    '{label_column}',                     -- Column containing expected outputs
    '{metric_name}',                      -- 'exact_match', 'fuzzy_match', 'contains_match', 'llm_judge', or custom
    '{function_model}',                   -- Model used by function (extracted from DDL in Step 1)
    NULL,                                 -- sample_size: rows to evaluate (NULL = all)
    '{results_table}',                    -- results_table: where to save detailed results (NULL = don't save)
    NULL,                                 -- metric_options: OBJECT_CONSTRUCT('threshold', 0.9) for fuzzy_match, etc.
    500,                                  -- max_length: truncation limit for fields (default 500)
    NULL,                                 -- custom_metric_udf: fully qualified UDF name if using custom metric
    NULL                                  -- run_id: external ID for tracking (auto-generated if NULL)
);
```

#### Async Evaluation

For async execution, use `EVALUATE_AI_FUNCTION_ASYNC` which creates and executes a Snowflake Task:

```sql
CALL EVALUATE_AI_FUNCTION_ASYNC(
    '{function_name}',                    -- Fully qualified AI function name (DB.SCHEMA.FUNC)
    '{test_table}',                       -- Fully qualified test data table
    ARRAY_CONSTRUCT('{input_col1}', '{input_col2}'),  -- Input columns passed to function (in order)
    '{label_column}',                  -- Column containing expected outputs
    '{metric_name}',                      -- 'exact_match', 'fuzzy_match', 'contains_match', 'llm_judge', or custom
    '{function_model}',                   -- Model used by function (extracted from DDL in Step 1)
    NULL,                                 -- sample_size: rows to evaluate (NULL = all)
    '{results_table}',                    -- results_table: REQUIRED for async - results go here
    NULL,                                 -- metric_options: OBJECT_CONSTRUCT('threshold', 0.9) for fuzzy_match, etc.
    500,                                  -- max_length: truncation limit for fields (default 500)
    NULL,                                 -- custom_metric_udf: fully qualified UDF name if using custom metric
    NULL,                                 -- warehouse_name: warehouse to run task (NULL = current)
    NULL                                  -- run_id: custom run ID (auto-generated if NULL)
);
```

The SPROC returns immediately with a **run_id** like `ai_func_eval_MY_AI_FUNCTION_1709234567890`.

**⚠️ WAREHOUSE NOTE**: If the SPROC returns a string starting with `ERROR:` instead of a run_id, it means the current role lacks a direct USAGE grant on the target warehouse. Snowflake Tasks require an explicit grant — session-level access via role hierarchy is not sufficient. Display the full error to the user. It includes the exact `GRANT` command needed and instructions for finding usable warehouses.

**⚠️ IMPORTANT**: Display the run_id prominently to the user:

```
Evaluation started in background!

╔═══════════════════════════════════════════════════════════╗
║  RUN_ID: ai_func_eval_MY_AI_FUNCTION_1709234567890                ║
╚═══════════════════════════════════════════════════════════╝

Save this run_id to track your evaluation.

Check status:  See references/async_status.md
View results:  SELECT * FROM {results_table} WHERE RUN_ID = '{run_id}';
```

You can close this session and return later. To resume, load the custom AI function skill and say "check status of {run_id}" — it will pick up where you left off and present your results.

**Load** `references/async_status.md` if user wants to check status.

**Results Table Schema:**

When saving results, the SPROC automatically:
1. Creates the results table if it doesn't exist
2. Generates a unique `run_id` (e.g., `ai_func_eval_MY_FUNC_1739919133000`) for the evaluation run
3. Records `METRIC_NAME` and `MODEL_NAME` with each row

| Column | Type | Description |
|--------|------|-------------|
| RUN_ID | VARCHAR | Unique identifier for this evaluation run |
| ROW_ID | INTEGER | Row number from test data |
| INPUT_TEXT | VARCHAR | Input values summary |
| EXPECTED | VARCHAR | Expected output |
| PREDICTED | VARCHAR | Model's prediction |
| SCORE | FLOAT | Score for this row (0.0-1.0) |
| FEEDBACK | VARCHAR | Metric feedback explaining the score |
| ERROR_MESSAGE | VARCHAR | Error message if evaluation failed |
| METRIC_NAME | VARCHAR | Name of the metric used |
| MODEL_NAME | VARCHAR | Model name used |
| EVAL_TIMESTAMP | TIMESTAMP | When this row was evaluated |

**Metric Options:**

| Metric | Option | Type | Default | Description |
|--------|--------|------|---------|-------------|
| fuzzy_match | threshold | FLOAT | 0.85 | Minimum similarity score |
| llm_judge | task_description | VARCHAR | '' | Task context for the judge |

### Step 6: Present Results

Display the returned score:
```
Evaluation Results
==================

Function: {function_name}
Metric: {metric_name}
Test Size: {n} examples
Eval ID: {run_id}

Average Score: {score:.1%}
```

**If results were saved:**
```
Detailed results saved to: {results_table}
Evaluation ID: {run_id}

Query this evaluation run:
  SELECT * FROM {results_table} WHERE RUN_ID = '{run_id}' ORDER BY ROW_ID;

Analyze failures:
  SELECT * FROM {results_table} WHERE RUN_ID = '{run_id}' AND SCORE < 1 ORDER BY SCORE;

Check errors:
  SELECT * FROM {results_table} WHERE RUN_ID = '{run_id}' AND ERROR_MESSAGE IS NOT NULL;

Score distribution:
  SELECT SCORE, COUNT(*) FROM {results_table} WHERE RUN_ID = '{run_id}' GROUP BY SCORE ORDER BY SCORE DESC;

Compare evaluation runs:
  SELECT RUN_ID, METRIC_NAME, MODEL_NAME, AVG(SCORE) AS AVG_SCORE, COUNT(*) AS ROWS
  FROM {results_table}
  GROUP BY RUN_ID, METRIC_NAME, MODEL_NAME
  ORDER BY EVAL_TIMESTAMP DESC;
```

### Step 7: Next Steps

Present to user:
```
Evaluation complete!

**Recommended next step:** Optimize your function to improve its performance. The optimizer will automatically tune your system prompt and help you find the best model for your cost/quality tradeoffs.

What would you like to do?
1. **Optimize** (recommended) - Improve the function through prompt tuning and model selection
2. **Done** - Exit for now
```

If optimize → Load `optimize/SKILL.md` with context:
- Preserve function name and column mappings
- Pass the test table used: `{test_table}`
- Note: *"For consistent comparison, use this same test table as your held-out test set in the optimize workflow."*

## Stopping Points

Critical confirmations (always stop, even if pre-provided):
- ✋ Step 2: Confirm column mapping (input_columns, label_column)

Optional confirmations:
- ✋ Step 4: Before creating stage and uploading files
- ✋ Step 5: Before creating stored procedure
- ✋ Step 8: After presenting results

## Output

- Stage with metrics.zip uploaded
- Generic evaluation SPROC created in Snowflake
- Average score returned (float between 0.0 and 1.0)
- Detailed results table (optional) for debugging
