---
name: optimize-ai-function
description: "Optimize an AI function's prompt through automated prompt tuning."
parent_skill: custom-ai-function
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Optimize AI Function

Automatically improves prompts through iterative optimization with Pareto frontier selection.

## Prerequisites

**The target function must be created via `create/SKILL.md`** (or have compatible structure). The optimizer invokes the UDF directly with `MODEL_NAME` and `SYSTEM_PROMPT` override parameters, so functions must have this signature:

```sql
MY_FUNC(input1, input2, ..., MODEL_NAME VARCHAR DEFAULT 'model', SYSTEM_PROMPT VARCHAR DEFAULT NULL)
```

If the function was not created with these parameters, the optimization will fail. Direct the user to recreate the function using the create workflow.

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
| `metric` | Yes | exact_match | No | - |
| `auto_budget` | Yes | medium | No | - |
| `models` | Yes | [llama3.1-70b] | No | - |
| `reflection_model` | Yes | snowflake-llama-3.1-405b | No | - |
| `stage` | Yes | (generated) | No | - |
| `tracking_table` | No | (generated) | No | function_name |

**Critical fields** (always confirm even if pre-provided): `function_structure_confirmed`, `input_columns`, `label_column`

**Simple fields** (accept silently if pre-provided): `function_name`, `training_table`, `test_table`, `metric`, `auto_budget`, `models`, `reflection_model`, `stage`, `tracking_table`

## Pre-Collection

Before prompting, scan the user's initial message and any prior context for already-provided information:

1. **Function name**: Look for fully qualified names like `DB.SCHEMA.FUNCTION_NAME`
2. **Tables**: Look for table references like `DB.SCHEMA.TABLE`, mentions of "training table", "test table"
3. **Column mappings**: Look for phrases like "input column X", "label column Y", "expected column Z"
4. **Metric**: Look for evaluation metrics like "exact_match" or "llm_judge". Note these are not the only names, and users can build custom evaluation metrics. If you aren't sure, ask the user.
5. **Budget**: Look for "light", "medium", "heavy" or phrases like "quick optimization", "thorough search"
6. **Models**: Look for model names like "llama3.1-70b", "llama3.1-8b", "llama3.1-405b"

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

The optimizer extracts the seed prompt from the function DDL by looking for the `COALESCE(SYSTEM_PROMPT, '...')` pattern. This is the prompt that will be optimized.

If extraction fails (function doesn't have `MODEL_NAME`/`SYSTEM_PROMPT` parameters), inform user they need to recreate the function using `create/SKILL.md`.

### Step 3: Get Training & Test Data Tables

**If `training_table` already collected** (user provided table name upfront):
- Skip to table validation, acknowledge: "I'll use `{training_table}` for training"

**If coming from evaluate workflow with split data:** Pre-populate:
```
Using data splits from evaluation:
- Training: {train_table_from_evaluate}
- Test: {test_table_from_evaluate}

Press Enter to confirm, or provide different tables.
```

**Otherwise:** **Load** `references/data_preparation.md` with context:
- `workflow`: "optimize"
- Keep function context from Step 1 for synthetic or pseudo-label routes.

**Note on optimize training data:** The optimizer internally splits training into train/dev sets (2/3 scoring, 1/3 reflection).

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

After confirmation, validate columns per `references/data_preparation.md` Step 5.

### Step 4: Configure Optimization

#### Step 4.1: Select Metric

**If `metric` already collected** (user provided metric upfront):
Accept silently (simple field) — skip this substep

**If not collected:**

**Load** `references/metrics.md` and present the metric selection prompt.

**If user chooses "Create custom metric" (option 6):**
Load `references/custom_metrics.md` with context:
- Preserve function name: `{function_name}`
- Preserve training table: `{training_table}`
- Preserve column mappings: `{input_columns}`, `{label_column}`
- Note: *"After creating your custom metric, we'll return here to continue optimization setup."*

After custom metric creation completes, return to this step with the new metric name.

Store as `metric_name`.

#### Step 4.2: Select Budget

**If `auto_budget` already collected** (user provided budget upfront):
Accept silently (simple field) — skip this substep

**If not collected**, ask the user which optimization budget to use:

```
How thorough should the optimization search be?

1. light - ~6 iterations (quick exploration, ~5-10 minutes)
2. medium - ~12 iterations (balanced, recommended, ~10-20 minutes)
3. heavy - ~18 iterations (thorough search, ~20-30 minutes)
```

Store as `auto_budget`. Default: `medium`

#### Step 4.3: Select Models to Optimize

**If `models` already collected** (user provided model list upfront):
Accept silently (simple field) — skip this substep

**If not collected:**

The optimizer will run independently for each selected model, allowing you to compare results across different cost/quality tradeoffs.

Ask the user which models to optimize for (can select multiple):

**Load** `references/model_selection.md` and follow its full workflow to dynamically query available models and present smart recommendations. Use `multiSelect: true` since the optimizer runs independently for each model. Present the same one-per-family options from model_selection.md — do not expand any family into multiple size variants. Users who want a specific size variant (e.g., llama3.1-8b instead of llama3.1-405b) can select "See all models".

Store as `models` (array). Default: `['llama3.1-70b']`

#### Step 4.4: Select Reflection Model

**If `reflection_model` already collected** (user provided reflection model upfront):
Accept silently (simple field) — skip this substep

**If not collected:**

The reflection model analyzes failures during optimization to guide prompt evolution. A more capable model generally produces better reflections.

**Load** `references/model_selection.md` and follow its full workflow, biasing toward the strongest available model. Note to the user that a more capable model is recommended for reflections.

Store as `reflection_model`. Default: `claude-sonnet-4-6`

#### Step 4.5: Advanced Configuration

The following have sensible defaults. Only ask if user wants to customize:

- **Stage**: Where to upload optimizer code. Default: `{database}.{schema}.AI_FUNCTIONS`
- **Tracking table**: Where to save optimization candidates. Default: `{FUNCTION_NAME}_OPT_TRACKING`
- **Validation fraction**: Fraction of training data for validation vs reflection. Default: `0.667` (2/3 validation, 1/3 reflection)

Proceed with defaults unless user requests changes.

### Step 5: Setup Infrastructure

**Load** `references/infrastructure_setup.md` and run the deploy script shortcut to provision stage, modules, and procedures before optimization.

Explain to the user what the procedure does:
```
OPTIMIZE_AI_FUNCTION iteratively improves your prompt by:
1. Scoring prompt variations against training examples
2. Keeping Pareto-optimal performers (best quality/cost tradeoffs)
3. Generating new variations from successful prompts
```

**⚠️ STOP**: Get user confirmation before creating the stored procedure.

### Step 6: Run Optimization

**MANDATORY**: Wrap the SPROC `CALL ...` in the query tag wrapper from `references/query_tag.md` (set/restore `QUERY_TAG`). The agent MUST inject its local `CORTEX_SESSION_ID` into the wrapper and record it under the canonical key `__CUSTOM_AI_FUNCTION_CORTEX_SESSION_ID` (merge into JSON tags when possible; otherwise append to string tags).

Pass all selected models in a single SPROC call. The SPROC runs all models **concurrently** internally using parallel threads, so there is no need to call it once per model. Results from all models will be compared in Step 8 to find pareto-optimal options.

#### Execution Mode Selection

Optimization can take 10-30+ minutes depending on budget and model count. Ask the user:

```
How would you like to run the optimization?

1. **Sync** (default) - Wait for results (use timeout_seconds: 1200)
2. **Async** (recommended) - Run in background, track with run_id
```

**Sync execution** (default): Runs directly with extended timeout. Risk of Cortex Code timeout on heavy budgets.

**Async execution**: Uses a Snowflake Task to run optimization in the background. Recommended for `medium` or `heavy` budgets.

#### Sync Optimization

**Important: SQL timeout** — The optimization SPROC can run for 10+ minutes depending on budget and model count. When executing the `CALL OPTIMIZE_AI_FUNCTION(...)` statement, use `timeout_seconds: 1200` (20 minutes) to prevent the query from timing out before completion.

```sql
CALL OPTIMIZE_AI_FUNCTION(
    '{function_name}',                             -- Fully qualified function name (DB.SCHEMA.FUNC)
    '{training_table}',                            -- Training table (auto-split into val/train)
    '{label_column}',                              -- Label column
    ARRAY_CONSTRUCT('{input_col1}', ...),          -- Input columns
    '{metric_name}',                               -- Metric from Step 4.1
    ARRAY_CONSTRUCT('{model1}', '{model2}', ...),  -- All models to optimize (run concurrently)
    '{reflection_model}',                          -- Reflection model from Step 4.4 (required)
    '{test_table}',                                -- Held-out test table (NULL = use training table)
    '{auto_budget}',                               -- Budget from Step 4.2
    '{tracking_table}',                            -- Tracking table (or NULL)
    '{results_table}',                             -- Table to save optimization results (NULL = don't save)
    {validation_fraction},                         -- Fraction for validation (default 0.667)
    {temperature},                                 -- LLM temperature (default 0.0)
    {max_tokens},                                  -- Max tokens (default 8192)
    NULL,                                          -- Metric options (or OBJECT_CONSTRUCT(...))
    '{custom_metric_udf}',                         -- Custom metric UDF name (or NULL)
    FALSE,                                         -- enable_detailed_tracking: verbose logging (default FALSE)
    NULL                                           -- run_id: external ID for tracking (auto-generated if NULL)
);
```

Collect and store results from each model run for comparison in Step 7.
The single call returns results for all models. Each model gets the same budget and runs independently in parallel.

**Timeout self-correction:** If the CALL statement fails due to a SQL timeout (e.g., the query exceeds `timeout_seconds`), do NOT attempt to check query history or treat it as an unknown failure. The timeout is a client-side cancellation — the query was killed, not completed. Self-correct as follows:
1. Inform the user the optimization timed out.
2. If a tracking table was configured, check it for any partial results from the timed-out run (some models may have completed before the timeout):
   ```sql
   SELECT MODEL_NAME, COUNT(*) AS CANDIDATES_FOUND, MAX(METRIC_SCORE) AS BEST_SCORE
   FROM {tracking_table}
   GROUP BY MODEL_NAME;
   ```
3. Ask the user how to proceed:
   - **Retry with longer timeout** — re-run the same CALL with a higher `timeout_seconds` (e.g., double the previous value).
   - **Skip this model** — move on to the next model in the list, using any partial tracking data already captured.
   - **Reduce budget** — re-run with a lighter budget setting (e.g., switch from `medium` to `light`).
4. Do NOT silently move on or conflate partial results from a prior/different model's run with the timed-out model's results.

**Required params:** FUNCTION_NAME, TRAINING_TABLE, LABEL_COLUMN, INPUT_COLUMNS, METRIC_NAME, MODELS, REFLECTION_MODEL

**Optional params:** TEST_TABLE, AUTO_BUDGET ('light'), TRACKING_TABLE, VALIDATION_FRACTION (0.667), TEMPERATURE (0.0), MAX_TOKENS (8192), METRIC_OPTIONS, CUSTOM_METRIC_UDF, ENABLE_DETAILED_TRACKING, RUN_ID

#### Async Optimization

For async execution, use `OPTIMIZE_AI_FUNCTION_ASYNC` which creates and executes a Snowflake Task:

```sql
CALL OPTIMIZE_AI_FUNCTION_ASYNC(
    '{function_name}',                             -- Fully qualified function name (DB.SCHEMA.FUNC)
    '{training_table}',                            -- Training table (auto-split into val/train)
    '{label_column}',                              -- Label column
    ARRAY_CONSTRUCT('{input_col1}', ...),          -- Input columns
    '{metric_name}',                               -- Metric from Step 4.1
    ARRAY_CONSTRUCT('{model1}', '{model2}', ...),  -- All models to optimize (run concurrently)
    '{reflection_model}',                          -- Reflection model from Step 4.4 (required)
    '{test_table}',                                -- Held-out test table (NULL = use training table)
    '{auto_budget}',                               -- Budget from Step 4.2
    '{tracking_table}',                            -- Tracking table (or NULL)
    '{results_table}',                             -- Table to save optimization results (NULL = don't save)
    {validation_fraction},                         -- Fraction for validation (default 0.667)
    {temperature},                                 -- LLM temperature (default 0.0)
    {max_tokens},                                  -- Max tokens (default 8192)
    NULL,                                          -- Metric options (or OBJECT_CONSTRUCT(...))
    '{custom_metric_udf}',                         -- Custom metric UDF name (or NULL)
    FALSE,                                         -- enable_detailed_tracking: verbose logging (default FALSE)
    NULL                                           -- run_id: external ID for tracking (auto-generated if NULL)
    NULL                                           -- warehouse_name: warehouse to run task (NULL = current)
);
```

**Required params:** FUNCTION_NAME, TRAINING_TABLE, LABEL_COLUMN, INPUT_COLUMNS, METRIC_NAME, MODELS, REFLECTION_MODEL

**Optional params:** TEST_TABLE, AUTO_BUDGET ('light'), TRACKING_TABLE, VALIDATION_FRACTION (0.667), TEMPERATURE (0.0), MAX_TOKENS (8192), METRIC_OPTIONS, CUSTOM_METRIC_UDF, ENABLE_DETAILED_TRACKING, RUN_ID

The SPROC returns immediately with a **run_id** like `ai_func_opt_MY_AI_FUNCTION_1709234567890`.

**⚠️ WAREHOUSE NOTE**: If the SPROC returns a string starting with `ERROR:` instead of a run_id, it means the current role lacks a direct USAGE grant on the target warehouse. Snowflake Tasks require an explicit grant — session-level access via role hierarchy is not sufficient. Display the full error to the user. It includes the exact `GRANT` command needed and instructions for finding usable warehouses.

**⚠️ IMPORTANT**: Display the run_id prominently to the user:

```
╔═══════════════════════════════════════════════════════════╗
║  RUN_ID: ai_func_opt_MY_AI_FUNCTION_1709234567890                 ║
╚═══════════════════════════════════════════════════════════╝

Save this run_id to track your optimization.

Check status:  See references/async_status.md
View results:  SELECT * FROM {tracking_table} WHERE RUN_ID = '{run_id}';
```

You can close this session and return later. To resume, load the custom AI function skill and say "check status of {run_id}" — it will pick up where you left off and present your results.

**Load** `references/async_status.md` if user wants to check status.

**Note**: For async optimization, `TRACKING_TABLE` is required since the SPROC return value isn't directly accessible. Results are written to the tracking table with `RUN_ID = run_id`.

### Step 7: Present Results (with Pareto Filtering)

**⚠️ MANDATORY: You MUST complete ALL substeps below (7.1 through 7.5) before presenting any results to the user or asking what to do next. Do NOT skip the pareto filter.**

**7.1. Collect raw results:**

The procedure returns JSON with: `run_id`, `seed_prompt`, `best_prompt`, `seed_val_score`, `best_val_score`, `seed_test_score`, `best_test_score`, `total_candidates`, `model`, `metric`.

**Important**: Capture `run_id` — needed for version management.

If tracking table used, query candidate history:
```sql
SELECT ROW_NUMBER() OVER (PARTITION BY MODEL_NAME ORDER BY CREATED_AT) AS IDX, MODEL_NAME, METRIC_SCORE, LEFT(PROMPT_TEXT, 100) FROM {tracking_table} WHERE METRIC_SCORE IS NOT NULL ORDER BY METRIC_SCORE DESC;
```

**7.2. Get character length statistics:**
```sql
SELECT AVG(LENGTH({input_columns})) AS avg_input_chars, AVG(LENGTH({label_column})) AS avg_output_chars FROM {test_table};
```

**7.3. Filter to pareto-optimal options:**
```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/filter_pareto.py \
    --json '[{"model": "model1", "score": 0.85}, ...]' \
    --prompt-chars {prompt_chars} --avg-input-chars {avg_input_chars} --avg-output-chars {avg_output_chars} \
    --seed-score {seed_test_score} --format table
```

**7.4. Present pareto-optimal results:**

```
Select the best result to apply:

(Showing pareto-optimal options only - dominated options filtered out)

| # | Model | Score | Improvement | Relative Cost |
|---|-------|-------|-------------|---------------|
| 1 | llama3.1-8b | 82.0% | +17.0% | 1.0x (cheapest) |
| 2 | llama3.1-70b | 85.0% | +20.0% | 3.0x |

Enter the number of your choice (or 0 to skip):
```

**Note**: Options where another model has both lower cost AND higher score are automatically filtered out. Cost is calculated using model-specific input/output token costs from `models.json`.

**7.5. Get user selection:**

**⚠️ STOP**: Use `ask_user_question` to let user select the result to apply.

### Step 8: Apply Optimized Prompt

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
- The original user template (preserved)
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

Generate and execute the SQL:
```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/create_udf.py \
    --json '<JSON_CONFIG>'
```

**⚠️ STOP**: Show generated SQL DDL to user for review before execution.

**If "Save as new version":**

**Load** `references/version_management.md` and follow the "Save New Version" workflow.

Pass this context:
- `{database}`, `{schema}`: Target location
- `{function_base}`: Base function name (without DB.SCHEMA prefix)
- `{selected_model}`: The model selected in Step 8
- `{best_score}`: The optimization score
- `{metric_name}`: The metric used for optimization (from Step 4)
- `{run_id}`: The optimization experiment ID (from Step 6 results)
- `{optimized_system_prompt}`: The optimized prompt from results
- `{original_inputs_array}`, `{original_outputs_array}`: From Step 2
- `{original_user_prompt_template}`: From Step 2

### Step 9: Next Steps

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

## Stopping Points

Critical confirmations (always stop, even if pre-provided):
- ✋ Step 3: Confirm column mapping (input_columns, label_column)

Optional confirmations:
- ✋ Step 7: After presenting pareto-optimal results (get user selection)
- ✋ Step 8: Before applying changes

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Function not found` | Use fully qualified name: `DB.SCHEMA.MY_FUNC`. Verify: `SHOW FUNCTIONS LIKE 'MY_FUNC' IN SCHEMA DB.SCHEMA;` |
| `Could not extract prompt definition` | Function missing `MODEL_NAME`/`SYSTEM_PROMPT` params. Recreate via `create/SKILL.md`. |

## Output

- Stage with optimizer code uploaded
- Optimization SPROC created in Snowflake
- VARIANT result with best prompts per model
- Tracking table with optimization history
- Updated AI function with optimized prompt (if applied)
- Version metadata saved in AI_FUNCTION_VERSIONS table (if versioned)
