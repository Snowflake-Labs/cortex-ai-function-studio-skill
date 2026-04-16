<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Async Task Status

This reference explains how to check the status of async evaluation and optimization jobs.

## When to Load

Load when user wants to check status of an async job or asks about a run_id.

## Checking Task Status

Async jobs run as Snowflake Tasks. Use `INFORMATION_SCHEMA.TASK_HISTORY()` to check status.

**⚠️ IMPORTANT:** Always ask the user for the database and schema before running any queries. Use `{database}.INFORMATION_SCHEMA.TASK_HISTORY()` with the user-provided database to ensure the query targets the correct location.

```sql
-- Check status of a specific run (replace {database} with user-provided value)
SELECT 
    NAME AS RUN_ID,
    STATE,
    SCHEDULED_TIME,
    COMPLETED_TIME,
    ERROR_MESSAGE
FROM TABLE({database}.INFORMATION_SCHEMA.TASK_HISTORY(
    TASK_NAME => '{run_id}',
    RESULT_LIMIT => 1
));
```

### Task States

| State | Meaning |
|-------|---------|
| `SCHEDULED` | Task is queued to run |
| `EXECUTING` | Task is currently running |
| `SUCCEEDED` | Task completed successfully |
| `FAILED` | Task failed (check ERROR_MESSAGE) |
| `CANCELLED` | Task was cancelled |

## Checking Results

### Evaluation Results

Once state is `SUCCEEDED`, query the results table:

```sql
-- Get evaluation results by run_id
SELECT 
    RUN_ID,
    AVG(SCORE) AS AVG_SCORE,
    COUNT(*) AS ROWS_EVALUATED,
    SUM(CASE WHEN ERROR_MESSAGE IS NOT NULL THEN 1 ELSE 0 END) AS ERRORS
FROM {results_table}
WHERE RUN_ID = '{run_id}'
GROUP BY RUN_ID;

-- Detailed results
SELECT * FROM {results_table} 
WHERE RUN_ID = '{run_id}' 
ORDER BY ROW_ID;

-- Analyze failures
SELECT * FROM {results_table} 
WHERE RUN_ID = '{run_id}' AND SCORE < 1 
ORDER BY SCORE;
```

### Optimization Results

Once state is `SUCCEEDED`, query the experiment for results:

```sql
-- List all runs and their status
SHOW RUNS IN EXPERIMENT {experiment_name};

-- Get seed and best scores for a specific model
-- Run names use the pattern: {MODEL}_SEED, {MODEL}_ITER_1, ..., {MODEL}_BEST
-- where {MODEL} is the uppercased model name with non-alphanumeric chars replaced by _
-- Examples: llama3.1-8b -> LLAMA3_1_8B, claude-haiku-4-5 -> CLAUDE_HAIKU_4_5
SHOW RUN METRICS IN EXPERIMENT {experiment_name} RUN {MODEL}_SEED;
SHOW RUN METRICS IN EXPERIMENT {experiment_name} RUN {MODEL}_BEST;

-- Get the optimized prompt/body and aggregate stats
SHOW RUN PARAMETERS IN EXPERIMENT {experiment_name} RUN {MODEL}_BEST;
```

For detailed candidate history (diagnostic, optional):

```sql
-- List all iteration runs for a model
SHOW RUNS IN EXPERIMENT {experiment_name};
-- Then query individual iteration run metrics:
SHOW RUN METRICS IN EXPERIMENT {experiment_name} RUN {MODEL}_ITER_1;
```

## Listing Recent Runs

To see all recent async jobs:

```sql
-- List recent evaluation/optimization tasks (use {database} prefix)
SELECT 
    NAME AS RUN_ID,
    STATE,
    SCHEDULED_TIME,
    COMPLETED_TIME,
    TIMESTAMPDIFF('SECOND', SCHEDULED_TIME, COALESCE(COMPLETED_TIME, CURRENT_TIMESTAMP())) AS DURATION_SECONDS,
    ERROR_MESSAGE
FROM TABLE({database}.INFORMATION_SCHEMA.TASK_HISTORY(
    RESULT_LIMIT => 20
))
WHERE NAME LIKE 'ai_func_eval_%' OR NAME LIKE 'ai_func_opt_%'
ORDER BY SCHEDULED_TIME DESC;
```

## Longer History

`INFORMATION_SCHEMA.TASK_HISTORY()` only retains ~7 days of history. For older runs, use Account Usage (requires ACCOUNTADMIN or appropriate privileges):

```sql
-- Query task history up to 365 days
SELECT 
    NAME AS RUN_ID,
    STATE,
    SCHEDULED_TIME,
    COMPLETED_TIME,
    ERROR_MESSAGE
FROM SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY
WHERE NAME = '{run_id}'
ORDER BY SCHEDULED_TIME DESC
LIMIT 1;
```

## Troubleshooting

### Task Not Found

If `TASK_HISTORY()` returns no results:
1. Verify the run_id is correct
2. Check if the task was created in a different database/schema
3. The task may have been cleaned up (tasks are suspended after execution)

### Task Failed

If state is `FAILED`:
1. Check `ERROR_MESSAGE` for details
2. Common issues:
   - **Warehouse privilege error** ("USAGE privilege on the task's warehouse must be granted to owner role"): The async SPROC now detects this before creating the task and returns an actionable error. If you see this in task history from an older SPROC version, it means the current role does not have a direct USAGE grant on the warehouse. Snowflake Tasks run under the owner role and require an explicit grant — session-level warehouse access via role hierarchy is not sufficient. Fix: either grant access (`GRANT USAGE ON WAREHOUSE {wh} TO ROLE {role}`) or re-run with a warehouse the role has access to via the `WAREHOUSE_NAME` parameter. To find usable warehouses: `SHOW GRANTS TO ROLE {role}` and look for USAGE on WAREHOUSE.
   - **Task timed out**: The async task exceeded its timeout limit (`USER_TASK_TIMEOUT_MS`). The default is 4 hours (240 minutes). Re-run with a larger `TIMEOUT_MINUTES` value, or reduce dataset size / optimization budget
   - Warehouse not available
   - Table/function not found
   - Out of memory (try smaller sample_size)

### Re-running a Failed Job

To re-run a failed job, simply call the async SPROC again. It will create a new task with a new run_id.

## Cancelling a Running Job

To cancel an async job that is currently `EXECUTING`:

```sql
-- Suspend the task to stop execution
ALTER TASK {database}.{schema}.{run_id} SUSPEND;

-- Then drop the task to clean up
DROP TASK IF EXISTS {database}.{schema}.{run_id};
```

Any partial results already written to the experiment or results table will remain available.

## Cleanup

Async tasks are automatically dropped after successful completion. No manual cleanup is needed for successful runs.

For failed runs where automatic cleanup did not execute, you can clean up manually:

```sql
-- Drop a specific task
DROP TASK IF EXISTS {database}.{schema}.{run_id};

-- List any remaining eval/opt tasks
SHOW TASKS LIKE 'ai_func_eval_%' IN SCHEMA {database}.{schema};
SHOW TASKS LIKE 'ai_func_opt_%' IN SCHEMA {database}.{schema};
```

## Resume Workflow

When a user returns in a new session to check on an async job, follow this workflow after determining the task state.

### If run_id not provided

Ask the user for it, or list recent runs using the "Listing Recent Runs" SQL above and let them pick.

### ⚠️ MANDATORY: Ask for database and schema

**Before running any status or results queries**, ask the user which database and schema the job was created in. Do NOT attempt broad account-level searches like `SHOW TASKS ... IN ACCOUNT` — these are slow and may fail due to permissions.

Ask:
```
Which database and schema was this job created in?
For example: MY_DATABASE.MY_SCHEMA
```

Use the provided database and schema for all subsequent `TASK_HISTORY()`, results table, and experiment queries. For example:
```sql
SELECT *
FROM TABLE({database}.INFORMATION_SCHEMA.TASK_HISTORY(
    TASK_NAME => '{run_id}',
    RESULT_LIMIT => 1
));
```

### Inferring table names from run_id

The run_id encodes the function name and timestamp:
- `ai_func_eval_FUNC_NAME_1709234567890` → function is `FUNC_NAME`, results table is `FUNC_NAME_EVAL_RESULTS`
- `ai_func_opt_FUNC_NAME_1709234567890` → function is `FUNC_NAME`, experiment name is `FUNC_NAME_OPT_EXP`

To recover the function name: strip the `ai_func_eval_` or `ai_func_opt_` prefix and the trailing `_<13-digit-timestamp>` suffix. Use the database and schema provided by the user (from the mandatory step above) to fully qualify all references.

### If state is EXECUTING

Inform the user the job is still running. Show elapsed time from `SCHEDULED_TIME`. Offer to check again later:

```
Your job is still running ({elapsed} so far).

You can check again anytime by loading the Cortex AI Function Studio and saying:
"check status of {run_id}"
```

### If state is FAILED

Show the `ERROR_MESSAGE` from task history. Offer to re-run:

```
Your job failed with error: {error_message}

Would you like to re-run it? Simply start a new evaluation or optimization workflow.
```

### If state is SUCCEEDED — Evaluation (run_id starts with `ai_func_eval_`)

1. Query the results table using the inferred name and run_id:
   ```sql
   SELECT AVG(SCORE) AS AVG_SCORE, COUNT(*) AS ROWS_EVALUATED,
          SUM(CASE WHEN ERROR_MESSAGE IS NOT NULL THEN 1 ELSE 0 END) AS ERRORS
   FROM {database}.{schema}.{results_table}
   WHERE RUN_ID = '{run_id}';
   ```

2. Present results using the same format as `evaluate/SKILL.md` Step 5:
   ```
   Evaluation Results
   ==================

   Function: {function_name}
   Metric: {metric_name}  (query from results table: SELECT DISTINCT METRIC_NAME)
   Test Size: {n} examples
   Eval ID: {run_id}

   Average Score: {score:.1%}
   ```

3. Show the helpful queries (failures, score distribution, etc.) from `evaluate/SKILL.md` Step 5.

4. Present the `evaluate/SKILL.md` Step 6 next-steps menu:
   ```
   What would you like to do?
   1. Optimize (recommended) - Improve the function through function body optimization and model selection
   2. Done - Exit for now
   ```

   If optimize → Load `optimize/SKILL.md` with context from the results (function name, test table).

### If state is SUCCEEDED — Optimization (run_id starts with `ai_func_opt_`)

1. Query the experiment for results:
   ```sql
   -- List all runs
   SHOW RUNS IN EXPERIMENT {database}.{schema}.{experiment_name};

   -- Get best scores per model (query each MODEL_BEST run)
   SHOW RUN METRICS IN EXPERIMENT {database}.{schema}.{experiment_name} RUN {MODEL}_BEST;

   -- Get the optimized prompt and aggregate stats
   SHOW RUN PARAMETERS IN EXPERIMENT {database}.{schema}.{experiment_name} RUN {MODEL}_BEST;
   ```

2. Present results per `optimize/SKILL.md` Step 6 format: seed vs best scores, improvement, candidate count, and prompts.

3. Proceed to `optimize/SKILL.md` Step 7 (Select Best Result) to let the user choose a pareto-optimal option and apply it.
