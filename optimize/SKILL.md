---
name: optimize-ai-function
description: "Optimize an AI function through automated function body optimization, including prompts, model references, and SQL pre/post-processing."
parent_skill: cortex-ai-function-studio
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Optimize AI Function

Automatically improves AI functions through iterative function body optimization with Pareto frontier selection. The optimizer can modify the entire SQL function body -- prompts, model references, SQL pre/post-processing -- not just the system prompt.

## Prerequisites

**The target function must be created via `create/SKILL.md`** (or have compatible structure). The optimizer reverse-parses the function DDL to extract the full function body as the seed for optimization, then creates temporary functions with candidate body variations during optimization. The function body must contain at least one `AI_COMPLETE` call. The optimizer can modify the entire body -- prompts, model references, SQL pre/post-processing -- while preserving the function signature.

**Supported multimodal patterns:**
- **VARCHAR file paths**: function takes VARCHAR inputs with `TO_FILE()` in the body — auto-detected from DDL.
- **FILE data type**: function takes FILE parameters directly — auto-detected from DDL signature. `stage_name` **must** be provided in metric options.

For FILE-type functions, ask the user for `stage_name` during data collection. The optimizer validates stage access and file existence automatically before starting — see `references/multimodal_setup.md` "Validating File Access".

If the function was not created with the expected structure, the optimization will fail. Direct the user to recreate the function using the create workflow.

## When to Load

Load from main skill when user intent matches OPTIMIZE: "optimize", "tune", "improve".

## Information Model

| Field | Required | Default | Confirm | Dependencies |
|-------|----------|---------|---------|--------------|
| `function_name` | Yes | - | No | - |
| `function_structure_confirmed` | Yes | - | **Yes** | function_name |
| `training_table` | Yes | - | No | - |
| `test_table` | No | training_table | No | - |
| `input_columns` | Yes | - | **Yes** | training_table |
| `label_column` | Yes | - | **Yes** | training_table |
| `metric` | Yes | - | **Yes** | - |
| `auto_budget` | Yes | light | No | - |
| `models` | Yes | [function's model] | No | - |
| `reflection_model` | Yes | claude-sonnet-4-6 | No | - |
| `experiment_name` | No | (generated) | No | function_name |
| `results_table` | No | (generated) | No | function_name |
| `aggregation_metric` | No | accuracy | No | metric |
| `optimize_mode` | No | body | No | - |

**Critical fields** (always confirm even if pre-provided): `function_structure_confirmed`, `input_columns`, `label_column`, `metric`

**Simple fields** (accept silently if pre-provided): `function_name`, `training_table`, `test_table`, `auto_budget`, `models`, `reflection_model`, `experiment_name`, `results_table`, `aggregation_metric`, `optimize_mode`

## Pre-Collection

Before prompting, scan the user's initial message and any prior context for already-provided information:

1. **Function name**: Look for fully qualified names like `DB.SCHEMA.FUNCTION_NAME`
2. **Tables**: Look for table references like `DB.SCHEMA.TABLE`, mentions of "training table", "test table"
3. **Column mappings**: Look for phrases like "input column X", "label column Y", "expected column Z"
4. **Metric**: Look for evaluation metrics like "exact_match" or "llm_judge". Note these are not the only names, and users can build custom evaluation metrics. If you aren't sure, ask the user.
5. **Budget**: Look for "light", "medium", "heavy" or phrases like "quick optimization", "thorough search"
6. **Models**: Look for model names like "claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-5"

For each piece found:
- **Simple fields**: Accept silently, proceed without re-asking
- **Critical fields**: Present for confirmation even if pre-provided

## Workflow

### Step 1: Get AI Function

**If `function_name` already collected** (user provided function name upfront):
- Skip the prompt — proceed directly to validation
- Acknowledge: "I'll optimize `{function_name}`"

**If not collected**, ask the user what function they would like to optimize. Validate function exists and get its DDL.

**Always use the fully qualified name** (`DB.SCHEMA.FUNCTION_NAME`). If the user provides an unqualified name, resolve it using the `{database}` and `{schema}` from prerequisites. All downstream references (review block, script flags, DDL) must use the fully qualified name.

### Step 2: Verify Function Structure (Background)

The optimizer reverse-parses the function DDL to extract the **full function body** (the SQL expression inside the `AS $$ ... $$` block). This becomes the seed body for optimization. The body must contain at least one `AI_COMPLETE` call.

If extraction fails (function DDL cannot be parsed or body doesn't contain `AI_COMPLETE`), inform user they need to recreate the function using `create/SKILL.md`.

### Step 3: Get Training & Test Data Tables

**If `training_table` already collected** (user provided table name upfront):
- Skip to table validation, acknowledge: "I'll use `{training_table}` for training"

**If coming from evaluate workflow with split data:** Pre-populate:
```
Using data splits from evaluation:
- Training: {train_table_from_evaluate}
- Test: {test_table_from_evaluate}

Confirm these tables? (y/n) If no, provide different table names.
```

**Otherwise:** **Load** `references/data_preparation.md` with context:
- `workflow`: "optimize"
- Keep function context from Step 1 for synthetic or pseudo-label routes.

**Note on optimize training data:** The optimizer internally splits training into train/dev sets automatically. Use `0.5` validation by default; if the training table has more than 200 rows, use `0.2` validation instead.

**Test table defaults to training table.** Do not ask the user for a separate test table unless they explicitly provide one or request a split. If no test table is provided, silently use the training table for final evaluation. If the user provides a separate test table (or one was carried over from an evaluate workflow), use it.

When routing to synthetic data generation from this workflow, pass function context and **infer task description automatically** from the function COMMENT (or system prompt fallback). If unsure, you can ask for a quick confirmation/edit, not for a brand-new intention prompt.

Validate training table (and test table if provided):
```sql
DESCRIBE TABLE <training_table>;
SELECT COUNT(*) FROM <training_table>;
```

If test table provided:
```sql
DESCRIBE TABLE <test_table>;
SELECT COUNT(*) FROM <test_table>;
```

Store the column lists from both DESCRIBE outputs — you will need them for column mapping validation below.

**If `input_columns` and `label_column` already collected:**
- Present for confirmation (critical fields): "I'll use input columns `{input_columns}` and label column `{label_column}` — confirm?"

**Otherwise:** Follow column mapping in `references/data_preparation.md` Step 4.

**⚠️ STOP**: Always confirm column mapping before proceeding (critical fields).

After confirmation, **Load** `references/data_preparation.md` Step 5 to validate that all mapped columns exist in the relevant tables. Do NOT proceed if columns don't match.

**⚠️ Column mismatch handling**: If any mapped column does not exist in the training or test table, you MUST present the mismatch to the user via `ask_user_question` (not prose text). Include the mismatched column names, the actual table columns, and remediation options. Do NOT silently remap or proceed — always surface the mismatch through `ask_user_question` so the user can decide.

**Multi-key output handling:** If the user's training/test table has multiple truth columns that correspond to keys in a multi-key function output (e.g., separate `SENTIMENT`, `CONFIDENCE` columns instead of a single VARIANT), help them combine these into a single VARIANT `label_column` using `OBJECT_CONSTRUCT` in a view before optimization. See `references/data_preparation.md` "Multi-Column Truth Aggregation" for the SQL pattern.

### Step 4: Configure Optimization

#### Step 4.1: Select Metric

**Load** `references/metrics.md` and present the metric selection prompt.

**If user chooses "Create custom metric" (option 6):**
**Load** `references/custom_metrics.md` with context:
- Preserve function name: `{function_name}`
- Preserve training table: `{training_table}`
- Preserve column mappings: `{input_columns}`, `{label_column}`
- Note: *"After creating your custom metric, we'll return here to continue optimization setup."*

After custom metric creation completes, return to this step with the new metric name.

Store as `metric_name`.

#### Step 4.1b: Select Aggregation Metric (Classification Tasks Only)

**If `aggregation_metric` already collected**, skip. Otherwise:

**Only ask this if the task/problem appears classification-based (e.g., predicting categories, classes, or labels).** For non-classification tasks, skip and leave `aggregation_metric` as NULL.

```
The optimizer will use batch-level accuracy as the aggregation metric and report
precision, recall, F1, and accuracy as diagnostics for each candidate.

Would you like to change the aggregation metric?

1. **accuracy** (default) - Optimize for overall accuracy
2. **f1-score** - Optimize for F1 score (recommended for imbalanced classes)
```

Store as `aggregation_metric`. Default: `'accuracy'`

When `aggregation_metric` is set, the optimizer computes precision, recall, F1, and accuracy across each evaluation batch. All four are reported as diagnostics; the selected metric is used to score and rank candidates.

#### Step 4.2: Select Budget

**If `auto_budget` already collected**, skip. Otherwise:

```
How thorough should the optimization search be?

1. demo - ~2 iterations (quickest, ~5 minutes, used in demo walkthroughs)
2. light - ~6 iterations (recommended, ~2-5 minutes)
3. medium - ~12 iterations (balanced, ~10-20 minutes)
4. heavy - ~18 iterations (thorough search, ~20-30 minutes)
```

Store as `auto_budget`. Default: `light`

#### Step 4.3: Select Models to Optimize

**If `models` already collected**, skip. Otherwise:

**Default to the model extracted from the function DDL in Step 2.** Do not ask the user to choose models — use the function's baked-in model. If the user explicitly requests multiple models or a different model, accommodate that.

Store as `models` (array). Default: `[<model from function DDL>]`

#### Step 4.4: Select Reflection Model

**If `reflection_model` already collected**, skip. Otherwise:

Default to `claude-sonnet-4-6` (or the strongest available model). Do not ask — accept silently.

Store as `reflection_model`. Default: `claude-sonnet-4-6`

#### Step 4.5: Review All Settings

**⚠️ STOP**: Present all optimization settings together as a single review block:

- **Experiment name**: Snowflake Experiment to persist optimization results. Default: `{FUNCTION_NAME}_OPT_EXP`
- **Results table**: Where to save per-row test-set evaluation results. Default: `{FUNCTION_NAME}_EVAL_RESULTS`. Only used when a test table is provided.
- **Validation fraction**: Fraction of training data for validation vs reflection. Recommended default from the Step 3 training row count: `0.5`; if the training table has more than 200 rows, use `0.2`

```
Here's what I'll optimize:

Function: {database}.{schema}.{function_name}
Training table: {training_table} ({row_count} rows)
Test table: {test_table}
Input columns: {input_columns}
Label column: {label_column}
Metric: {metric_name}
Model: {models}
Reflection model: {reflection_model}
Budget: {auto_budget}
Experiment: {experiment_name}
Results table: {results_table}

Confirm or edit?
```

Proceed with defaults unless user requests changes.

### Step 5: Run Optimization

Explain to the user what the procedure does:
```
OPTIMIZE_AI_FUNCTION iteratively improves your function by:
1. Scoring function body variations against training examples
2. Keeping Pareto-optimal performers (best quality/cost tradeoffs)
3. Generating new variations — modifying prompts, model references, and SQL pre/post-processing
```

Pass all selected models in a single SPROC call. The SPROC runs all models **concurrently** internally using parallel threads, so there is no need to call it once per model. Results from all models will be compared in Step 6 to find pareto-optimal options.

#### Execution Mode

**Default to sync execution.** Use `timeout_seconds: 14400` (4 hours) to prevent the query from timing out. Async execution is available for large datasets (500+ rows) or heavy budgets — only use it if the user explicitly requests it.

**Sync execution** (default): Runs directly and returns results via an anonymous stored procedure (no persistent objects). Python optimizer code is inlined into the SPROC body.

**Async execution** (only when explicitly requested): Uses a Snowflake Task to run optimization in the background. The Task body contains the anonymous SPROC with inlined Python, so no named procedures are created and no stage upload is required.

#### Running the Optimization Script

**Important: SQL timeout** — For sync execution, the optimization SPROC can run for 10+ minutes. The script runs in its own subprocess, so there is no Cortex Code UI timeout concern. However, Snowflake statement timeout still applies. Use `timeout_seconds: 14400` (4 hours) to prevent the query from timing out before completion.

Run the optimization script. It renders the anonymous SPROC, appends the CALL, and executes everything in a single Snowpark session. **Always pass every flag** — use `none` for unused optional parameters:

```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/run.py optimize \
    --database {database} --schema {schema} --connection <CONNECTION_NAME> \
    --function-name {database}.{schema}.{function_name} --training-table {training_table} \
    --label-column {label_column} --input-columns {input_col1} {input_col2} \
    --metric-name {metric_name} --models {model1} {model2} --reflection-model {reflection_model} \
    --test-table {test_table or none} --auto-budget {auto_budget} \
    --experiment-name {experiment_name or none} \
    --results-table {results_table or none} \
    --validation-fraction {validation_fraction} --temperature 0.0 --max-tokens 8192 \
    --metric-options none --custom-metric-udf none \
    --run-id none --aggregation-metric {aggregation_metric or none}
    # For async: append --async --warehouse {warehouse} --timeout-minutes {timeout_minutes}
```

Run `run.py optimize --help` to see all flags and their descriptions.

#### Sync Output

The script prints a JSON result to stdout:
```json
{"status": "success", "result": {"run_id": "...", "best_body": "...", "best_ddl": "...", "seed_body": "...", ...}, "function": "DB.SCHEMA.MY_FUNC"}
```

Collect and store results from each model run for comparison in Step 6.
The single call returns results for all models. Each model gets the same budget and runs independently in parallel.

**Timeout self-correction:** If the script fails due to a SQL timeout, the query was killed (client-side cancellation), not completed. Inform the user, then check the experiment for partial results (`SHOW RUN METRICS IN EXPERIMENT {experiment_name}`). Offer to **skip the timed-out model** or **reduce budget** (e.g., `medium` → `light`). Do NOT silently move on or conflate partial results from different models.

#### Async Output

The script prints a JSON result to stdout with the generated `run_id`:
```json
{"status": "submitted", "run_id": "ai_func_opt_MY_FUNC_1739919133000", "task": "DB.SCHEMA.ai_func_opt_MY_FUNC_1739919133000"}
```

**⚠️ WAREHOUSE NOTE**: If the script returns `{"status": "error", ...}` instead of `{"status": "submitted", "run_id": "..."}`, it likely means the current role lacks a direct USAGE grant on the target warehouse. Snowflake Tasks require an explicit grant — session-level access via role hierarchy is not sufficient. Display the `message` field to the user. It includes the exact `GRANT` command needed and instructions for finding usable warehouses.

**⚠️ IMPORTANT**: Display the run_id prominently to the user:

```
Optimization started in background!

RUN_ID: {run_id}

Save this run_id to track your optimization.

Check status:  See references/async_status.md
View results:  SHOW RUN METRICS IN EXPERIMENT {experiment_name}
```

**⚠️ IMPORTANT**: For async optimization, `--experiment-name` is required since the return value isn't directly accessible. Results are persisted as experiment runs.

**Load** `references/async_status.md` if user wants to check status.

**Cleanup after async completes:** See `references/async_status.md` Cleanup section for task status verification and cleanup SQL (drop the Task after it reaches `SUCCEEDED`, `FAILED`, or `CANCELLED`).

### Step 6: Present Results (with Pareto Filtering)

**⚠️ MANDATORY**: You MUST complete ALL substeps below (6.1 through 6.5) before presenting any results to the user or asking what to do next. **Exception: single-model fast path** — if only one model was optimized, skip steps 6.2–6.4 (pareto filtering is pointless with a single model since there are no cost/quality tradeoffs to compare). Go directly from 6.1 to 6.5, presenting the single model's best result.

**6.1. Collect raw results:**

The procedure returns JSON with: `run_id`, `seed_body`, `best_body`, `best_ddl`, `seed_score`, `best_score`, `score_source`, `total_candidates`, `elapsed_seconds`, `model`, `metric`.

- `seed_score` / `best_score` are the unified scores (from test data if a test table was provided, otherwise from validation data).
- `score_source` indicates which dataset: `"test"` or `"validation"`.
- `best_ddl` is the complete `CREATE OR REPLACE FUNCTION` DDL with the optimized body.

**Important**: Capture `run_id` — needed for version management.

If the optimization was run asynchronously and the JSON return is unavailable, query the experiment for results:
```sql
-- List all runs and their status
SHOW RUNS IN EXPERIMENT {experiment_name};

-- Get metrics for the best run of a model (e.g. LLAMA3_1_8B_BEST)
SHOW RUN METRICS IN EXPERIMENT {experiment_name} RUN {MODEL}_BEST;

-- Get the optimized prompt/body from the best run
SHOW RUN PARAMETERS IN EXPERIMENT {experiment_name} RUN {MODEL}_BEST;
```

**6.2. Get average output length:**

```sql
SELECT AVG(LENGTH({label_column})) AS avg_output_chars FROM {test_table};
```

**6.3. Filter to pareto-optimal options:**
```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/filter_pareto.py \
    --json '[{"model": "model1", "score": 0.85}, ...]' \
    --prompt-chars {prompt_chars} --avg-output-chars {avg_output_chars} \
    --seed-score {seed_score} --format table
```

Where `{prompt_chars}` is the character length of the best body (or best prompt in prompt mode) from the optimization results.

**6.4. Present pareto-optimal results:**

Present the `filter_pareto.py` table output to the user. The table shows columns: `#`, `Model`, `Score`, `Improvement`, `Relative Cost`. Dominated options (where another model has both lower cost AND higher score) are automatically filtered out. Cost uses model-specific token costs from `models.json`.

**6.5. Get user selection:**

**⚠️ STOP**: Use `ask_user_question` to let user select the result to apply.

### Step 7: Apply Optimized Function

Ask user:
```
Apply the optimized function?

1. Yes - Replace the original function
2. Save as new function - Create a new function with suffix (e.g., MY_FUNC_OPTIMIZED)
3. No - Keep original
```

**If "Yes" (replace original):**

The optimizer returns `best_ddl`, a complete `CREATE OR REPLACE FUNCTION` DDL statement with the optimized body. 

**⚠️ STOP**: Show the `best_ddl` to the user for review. Once confirmed, execute the DDL directly:

```sql
{best_ddl}
```

**If "Save as new function":**

Ask the user for a new function suffix (e.g., V2, OPTIMIZED, PROD). Modify the function name in `best_ddl` to use the new function name (`{function_base}_{function_suffix}`) and show the modified DDL to the user for review. Once confirmed, execute it:

```sql
{best_ddl with function name changed to {function_base}_{function_suffix}}
```

### Step 8: Next Steps

Ask user:
```
Optimization complete. What would you like to do?

1. **Evaluate** - Test optimized function on held-out data
2. **Re-optimize** - Run again with different settings
3. **Done** - Exit
```

If evaluate → Load `evaluate/SKILL.md` with context:
- Preserve function name
- Pass the test table used: `{test_table}`
- Note: *"The evaluate workflow will use your test table: {test_table} for consistent results."*

If the customer wants to re-optimize or is unsatisfied with the current improvement:
**⚠️ STOP**: Use `ask_user_question` to let the user select the result to apply.

Ask the user:
```
Do you want to change the initial function structure (prompts, pre/post-processing, model)?
Most of the cases, changing in abstraction can give significantly different results.

1. Yes - Go back to recreate new function with different initial structure.
2. No - Re-optimize with different optimization effort level.
3. Analyze errors and help me decide.
```

If the user selects 1 -> Load `create/SKILL.md` with context:
- Preserve `{database}`, `{schema}`, `{function_name}`, `{inputs}`, `{outputs}`, `{model}`
- Pass the current seed function body and error analysis so the create workflow can inform approach selection
- Note: *"The customer is re-creating this function with a different approach. Skip to Step 5 (Research and Present Approaches) — task description, clarifications, inputs, and outputs are already known. Use Agent Research mode for the re-creation."*

If the user selects 2 -> Ask the customer for a new `auto_budget` and optionally new `models`. Then re-run optimization from Step 5 (Run Optimization) with the updated settings.

If the user selects 3:
Try to run evaluation on the optimized version and see what the error types are. Then, critically reason through the following:
1. Can this be solved with different pre-/post-processing?
2. Inspect optimization progression to see whether it's in the correct direction. Would a different seed function body solve this problem?
3. Is the current model just not strong enough? Should we try a stronger or different type of model?
After doing this analysis, help user identify whether they should run optimization with different effort level or re-create function with different settings.

## Stopping Points

Critical confirmations (always stop, even if pre-provided):
- ✋ Step 3: Confirm column mapping (input_columns, label_column)

Optional confirmations:
- ✋ Step 6: After presenting pareto-optimal results (get user selection)
- ✋ Step 7: Before applying changes

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Function not found` | Use fully qualified name: `DB.SCHEMA.MY_FUNC`. Verify: `SHOW FUNCTIONS LIKE 'MY_FUNC' IN SCHEMA DB.SCHEMA;` |
| `Could not extract function body from DDL` | Function DDL could not be parsed. Recreate via `create/SKILL.md`. |
| `Could not extract model name from DDL` | Function body does not contain `model=>'...'`. Recreate via `create/SKILL.md`. |

## Output

- VARIANT result with best function body and DDL per model
- Experiment object with optimization history (runs, metrics, parameters)
- Updated AI function with optimized body (if applied)
- No persistent artifacts — Python code is inlined into the anonymous SPROC
