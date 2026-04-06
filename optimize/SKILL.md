---
name: optimize-ai-function
description: "Optimize an AI function's prompt through automated prompt tuning."
parent_skill: cortex-ai-function-studio
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Optimize AI Function

Automatically improves prompts through iterative optimization with Pareto frontier selection.

## Prerequisites

**The target function must be created via `create/SKILL.md`** (or have compatible structure). The optimizer reverse-parses the function DDL to extract the baked-in model and system prompt, then creates temporary functions with candidate model/prompt combinations during optimization. The function body must use the standard `AI_COMPLETE(model=>'...', messages=>ARRAY_CONSTRUCT(...))` pattern. The optimizer substitutes only the model and system prompt; the rest of the function body (including multimodal user messages) is left untouched.

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
| `auto_budget` | Yes | medium | No | - |
| `models` | Yes | [claude-sonnet-4-5] | No | - |
| `reflection_model` | Yes | claude-sonnet-4-6 | No | - |
| `tracking_table` | No | (generated) | No | function_name |
| `aggregation_metric` | No | accuracy | No | metric |

**Critical fields** (always confirm even if pre-provided): `function_structure_confirmed`, `input_columns`, `label_column`, `metric`

**Simple fields** (accept silently if pre-provided): `function_name`, `training_table`, `test_table`, `auto_budget`, `models`, `reflection_model`, `tracking_table`, `aggregation_metric`

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

### Step 2: Verify Function Structure (Background)

The optimizer reverse-parses the function DDL to extract:
- The **model** from `model=>'...'` in the `AI_COMPLETE` call
- The **system prompt** from `OBJECT_CONSTRUCT('role', 'system', 'content', '...')` in the messages array

These become the seed model and seed prompt for optimization.

If extraction fails (function doesn't use the expected `AI_COMPLETE` pattern), inform user they need to recreate the function using `create/SKILL.md`.

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

**If no test table provided after data preparation:**
```
No test table provided. Training table will be used for final evaluation.
Note: For more reliable results, consider a separate held-out test set.
```

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

1. light - ~6 iterations (quick exploration, ~5-10 minutes)
2. medium - ~12 iterations (balanced, recommended, ~10-20 minutes)
3. heavy - ~18 iterations (thorough search, ~20-30 minutes)
```

Store as `auto_budget`. Default: `medium`

#### Step 4.3: Select Models to Optimize

**If `models` already collected**, skip. Otherwise:

The optimizer will run independently for each selected model, allowing you to compare results across different cost/quality tradeoffs.

**⚠️ STOP**: Ask the user which models to optimize for (can select multiple):

**Load** `references/model_selection.md` and follow its full workflow to dynamically query available models and present smart recommendations. Use `multiSelect: true` since the optimizer runs independently for each model. Present the same one-per-family options from model_selection.md — do not expand any family into multiple size variants. Users who want a specific size variant (e.g., claude-haiku-4-5 instead of claude-opus-4-5) can select "See all models".

Store as `models` (array). Default: `['claude-sonnet-4-5']`

#### Step 4.4: Select Reflection Model

**If `reflection_model` already collected**, skip. Otherwise:

The reflection model analyzes failures during optimization to guide prompt evolution. A more capable model generally produces better reflections.

**Load** `references/model_selection.md` and follow its full workflow, biasing toward the strongest available model. Note to the user that a more capable model is recommended for reflections.

Store as `reflection_model`. Default: `claude-sonnet-4-6`

#### Step 4.5: Advanced Configuration

The following have sensible defaults. Only ask if user wants to customize:

- **Tracking table**: Where to save optimization candidates. Default: `{FUNCTION_NAME}_OPT_TRACKING`
- **Validation fraction**: Fraction of training data for validation vs reflection. Recommended default from the Step 3 training row count: `0.5`; if the training table has more than 200 rows, use `0.2`

Proceed with defaults unless user requests changes. 

### Step 5: Run Optimization

Explain to the user what the procedure does:
```
OPTIMIZE_AI_FUNCTION iteratively improves your prompt by:
1. Scoring prompt variations against training examples
2. Keeping Pareto-optimal performers (best quality/cost tradeoffs)
3. Generating new variations from successful prompts
```

**⚠️ STOP**: Get user confirmation before proceeding with optimization.

**MANDATORY**: Wrap the SPROC `CALL ...` in the query tag wrapper from `references/query_tag.md` (set/restore `QUERY_TAG`). The agent MUST inject its local `CORTEX_SESSION_ID` into the wrapper and record it under the canonical key `__CUSTOM_AI_FUNCTION_CORTEX_SESSION_ID` (merge into JSON tags when possible; otherwise append to string tags). **You MUST reproduce the full wrapper SQL from `references/query_tag.md` verbatim** — including `CURRENT_QUERY_TAG()`, `TRY_PARSE_JSON`, `OBJECT_INSERT`, and both `ALTER SESSION SET QUERY_TAG` statements (set and restore). Do NOT simplify or abbreviate the wrapper. The agent MUST also inline the actual `CORTEX_SESSION_ID` value as a string literal — do NOT leave ANY placeholder such as `<CORTEX_SESSION_ID>`, `${CORTEX_SESSION_ID}`, `YOUR_SESSION_ID_HERE`, `YOUR_ACTUAL_SESSION_ID_HERE`, or similar. Look up the real session ID and substitute it directly into the SQL.

Pass all selected models in a single SPROC call. The SPROC runs all models **concurrently** internally using parallel threads, so there is no need to call it once per model. Results from all models will be compared in Step 6 to find pareto-optimal options.

#### Execution Mode Selection

Optimization can take 10-30+ minutes depending on budget and model count. Ask the user:

```
How would you like to run the optimization?

1. **Sync** (default) - Wait for results (use timeout_seconds: 14400)
2. **Async** (recommended) - Run in background, track with run_id
```

**If user selects Async**, ask about timeout:
```
The default async timeout is 4 hours (240 minutes).
Would you like to use a different timeout?
```
If user provides a custom value, store as `timeout_minutes`. Otherwise use the default (240).

**Sync execution** (default): Runs directly with extended timeout via an anonymous stored procedure (no persistent objects). Python optimizer code is inlined into the SPROC body. Risk of Cortex Code timeout on heavy budgets.

**Async execution**: Uses a Snowflake Task to run optimization in the background. The Task body contains the anonymous SPROC with inlined Python, so no named procedures are created and no stage upload is required. Recommended for `medium` or `heavy` budgets.

#### Running the Optimization Script

**Important: SQL timeout** — For sync execution, the optimization SPROC can run for 10+ minutes. The script runs in its own subprocess, so there is no Cortex Code UI timeout concern. However, Snowflake statement timeout still applies. Use `timeout_seconds: 14400` (4 hours) to prevent the query from timing out before completion.

Run the optimization script. It renders the anonymous SPROC, appends the CALL, and executes everything in a single Snowpark session. **Always pass every flag** — use `none` for unused optional parameters:

```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/run.py optimize \
    --database {database} --schema {schema} --connection <CONNECTION_NAME> \
    --function-name {function_name} --training-table {training_table} \
    --label-column {label_column} --input-columns {input_col1} {input_col2} \
    --metric-name {metric_name} --models {model1} {model2} --reflection-model {reflection_model} \
    --test-table {test_table or none} --auto-budget {auto_budget} \
    --tracking-table {tracking_table or none} --results-table {results_table or none} \
    --validation-fraction {validation_fraction} --temperature 0.0 --max-tokens 8192 \
    --metric-options none --custom-metric-udf none --enable-detailed-tracking false \
    --run-id none --aggregation-metric {aggregation_metric or none}
    # For async: append --async --warehouse {warehouse} --timeout-minutes {timeout_minutes}
```

Run `run.py optimize --help` to see all flags and their descriptions.

#### Sync Output

The script prints a JSON result to stdout:
```json
{"status": "success", "result": {"run_id": "...", "best_prompt": "...", ...}, "function": "DB.SCHEMA.MY_FUNC"}
```

Collect and store results from each model run for comparison in Step 6.
The single call returns results for all models. Each model gets the same budget and runs independently in parallel.

**Timeout self-correction:** If the script fails due to a SQL timeout, the query was killed (client-side cancellation), not completed. Inform the user, then check the tracking table for partial results (`SELECT MODEL_NAME, COUNT(*) AS CANDIDATES_FOUND, MAX(METRIC_SCORE) AS BEST_SCORE FROM {tracking_table} GROUP BY MODEL_NAME`). Offer to **skip the timed-out model** or **reduce budget** (e.g., `medium` → `light`). Do NOT silently move on or conflate partial results from different models.

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
View results:  SELECT * FROM {tracking_table} WHERE RUN_ID = '{run_id}';
```

**⚠️ IMPORTANT**: For async optimization, `--tracking-table` is required since the return value isn't directly accessible. Results are written to the tracking table with `RUN_ID = run_id`.

**Load** `references/async_status.md` if user wants to check status.

**Cleanup after async completes:** See `references/async_status.md` Cleanup section for task status verification and cleanup SQL (drop the Task after it reaches `SUCCEEDED`, `FAILED`, or `CANCELLED`).

### Step 6: Present Results (with Pareto Filtering)

**⚠️ MANDATORY**: You MUST complete ALL substeps below (6.1 through 6.5) before presenting any results to the user or asking what to do next. Do NOT skip the pareto filter.

**6.1. Collect raw results:**

The procedure returns JSON with: `run_id`, `seed_prompt`, `best_prompt`, `seed_val_score`, `best_val_score`, `seed_test_score`, `best_test_score`, `total_candidates`, `model`, `metric`.

**Important**: Capture `run_id` — needed for version management.

If tracking table used, query candidate history:
```sql
SELECT ROW_NUMBER() OVER (PARTITION BY MODEL_NAME ORDER BY CREATED_AT) AS IDX, MODEL_NAME, METRIC_SCORE, LEFT(PROMPT_TEXT, 100) FROM {tracking_table} WHERE METRIC_SCORE IS NOT NULL ORDER BY METRIC_SCORE DESC;
```

**6.2. Get character length statistics:**

For each input column, compute average length separately. If there are multiple input columns, sum their averages (e.g., `AVG(LENGTH(COL_A)) + AVG(LENGTH(COL_B))`):
```sql
SELECT
    {sum_of_AVG_LENGTH_per_input_column} AS avg_input_chars,
    AVG(LENGTH({label_column})) AS avg_output_chars
FROM {test_table};
```

**6.3. Filter to pareto-optimal options:**
```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/filter_pareto.py \
    --json '[{"model": "model1", "score": 0.85}, ...]' \
    --prompt-chars {prompt_chars} --avg-input-chars {avg_input_chars} --avg-output-chars {avg_output_chars} \
    --seed-score {seed_test_score} --format table
```

**6.4. Present pareto-optimal results:**

Present the `filter_pareto.py` table output to the user. The table shows columns: `#`, `Model`, `Score`, `Improvement`, `Relative Cost`. Dominated options (where another model has both lower cost AND higher score) are automatically filtered out. Cost uses model-specific token costs from `models.json`.

**6.5. Get user selection:**

**⚠️ STOP**: Use `ask_user_question` to let user select the result to apply.

### Step 7: Apply Optimized Prompt

Ask user:
```
Apply the optimized prompt?

1. Yes - Recreate function with optimized prompt
2. Save as new version - Create a new function with version suffix (e.g., MY_FUNC_V2)
3. No - Keep original
```

**If "Yes" (replace original):**

Recreate the function using the create skill script with:
- The optimized system prompt from `best_prompt`
- The original user prompt template (preserved)
- The selected model
- The original response_format schema (preserved)

Extract the original function metadata from Step 2, then build the JSON config:

```json
{
    "database": "{database}",
    "schema": "{schema}",
    "function_name": "{function_name}",
    "function_intention": "{original_intention} [Optimized: {metric_name}={best_score:.1%}]",
    "model": "{selected_model}",
    "inputs": {original_inputs_array},
    "outputs": {original_outputs_array},
    "system_prompt": "{optimized_system_prompt}",
    "user_prompt_template": "{original_user_prompt_template}"
}
```

**Multimodal:** Preserve the original input types from the function DDL:
- For VARCHAR path inputs: use `"sql_type": "STAGE_FILE_PATH"` with `"stage_name"`
- For FILE type inputs: use `"sql_type": "FILE"` (no `stage_name` needed in the function)

Generate and execute the SQL:

```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/create_udf.py \
    --json '<JSON_CONFIG>'
```

**⚠️ STOP**: Show generated SQL DDL to user for review. Once confirmed, run the script once again in execute mode to create the udf

```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/create_udf.py \
    --execute --json '<JSON_CONFIG>' \
    --connection <CONNECTION_NAME> \
    --warehouse <WAREHOUSE_NAME>
```

**If "Save as new version":**

**Load** `references/version_management.md` and follow the "Save New Version" workflow.

Pass this context:
- `{database}`, `{schema}`: Target location
- `{function_base}`: Base function name (without DB.SCHEMA prefix)
- `{selected_model}`: The model selected in Step 7
- `{best_score}`: The optimization score
- `{metric_name}`: The metric used for optimization (from Step 4)
- `{run_id}`: The optimization experiment ID (from Step 5 results)
- `{optimized_system_prompt}`: The optimized prompt from results
- `{original_inputs_array}`, `{original_outputs_array}`: From Step 2
- `{original_user_prompt_template}`: From Step 2

### Step 8: Next Steps

Ask user:
```
Optimization complete. What would you like to do?

1. **Evaluate** - Test optimized function on held-out data
2. **Re-optimize** - Run again with different settings
3. **Manage Versions** - List, compare, promote, or delete versions
4. **Done** - Exit
```

If evaluate → Load `evaluate/SKILL.md` with context:
- Preserve function name
- Pass the test table used: `{test_table}`
- Note: *"The evaluate workflow will use your test table: {test_table} for consistent results."*

If manage versions → **Load** `references/version_management.md` with context:
- `{database}`, `{schema}`: Current location
- `{function_base}`: Base function name

If the customer wants to re-optimize or is unsatisfied with the current improvement:
**⚠️ STOP**: Use `ask_user_question` to let the user select the result to apply.

Ask the user:
```
Do you want to change the initial `AI_COMPLETE` prompts or pre- and post-processing?
Most of the cases, changing in abstraction can give significantly different results.

1. Yes - Go back to recreate new function with different initial prompts/ pre/ post processing. 
2. No - Re-optimize with different optimization effort level.
3. Analyze errors and help me decide.
```

If the user select 1 -> Load `create/SKILL.md` with context:
- Preserve `{database}`, `{schema}`, `{function_name}`, `{inputs}`, `{outputs}`, `{model}`
- Pass the current seed prompt and error analysis so the create workflow can inform approach selection
- Note: *"The customer is re-creating this function with a different approach. Skip to Step 4 (Select Creation Mode) — task description, clarifications, inputs, and outputs are already known. Default to Agent Research mode for the re-creation."*

If the user select 2 -> Ask the customer for a new `auto_budget` and optionally new `models`. Then re-run optimization from Step 5 (Run Optimization) with the updated settings.

If the user select 3:
Try to run evaluation on the optimized version and see what the error types are. Then, critically reason through the following:
1. Can this be solved with different pre-/post-processing?
2. Inspect prompt optimization progression to see whether it's in the correct direction. Would a different seed prompt solve this problem?
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
| `Could not extract system prompt from DDL` | Function does not use the expected `AI_COMPLETE(model=>'...', messages=>...)` pattern. Recreate via `create/SKILL.md`. |
| `Could not extract model name from DDL` | Function body does not contain `model=>'...'`. Recreate via `create/SKILL.md`. |

## Output

- VARIANT result with best prompts per model
- Tracking table with optimization history
- Updated AI function with optimized prompt (if applied)
- Version metadata saved in AI_FUNCTION_VERSIONS table (if versioned)
- No persistent artifacts — Python code is inlined into the anonymous SPROC
