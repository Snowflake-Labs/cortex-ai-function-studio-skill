---
name: infrastructure-setup
description: "Common infrastructure setup patterns for AI function workflows, including a deploy script shortcut."
parent_skill: cortex-ai-function-studio
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Infrastructure Setup

Shared patterns for setting up Snowflake infrastructure (stages, files, SPROCs) used by create, evaluate, optimize, and synthetic data workflows.

## Deploy Script (Default)

Use the deploy script as the primary path for infrastructure setup. Do not start with manual SQL/PUT/SPROC steps.

Use the manual sections below only as fallback when:
- the deploy script is unavailable
- the deploy script fails and targeted recovery is needed
- the user explicitly requests a manual setup path

### Required pre-run user echo

Immediately before executing `deploy_ai_function_manager.py`, provide a short preview:

```text
About to deploy AI function infrastructure.

Target:
- database: {database}
- schema: {schema}
- stage: {database}.{schema}.{stage_name}

This will:
- create the stage if missing
- upload prerequisite files (`ai_complete_utils.py`, `metrics_core.py`, `snow_gepa_adapter.py`, `snow_gepa_optimize.py`, `snow_synthetic_data.py`)
- create or replace procedures (`EVALUATE_AI_FUNCTION`, `OPTIMIZE_AI_FUNCTION`, `GENERATE_SYNTHETIC_DATA`)
```

If `database` or `schema` is unknown, ask for it first. If infrastructure was uploaded previously, ask whether to reuse the same database/schema.

### Deploy command

Use the deploy manager to provision stage, module uploads, and all procedures in one idempotent call:

```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/deploy_ai_function_manager.py \
    --database <DATABASE> \
    --schema <SCHEMA> \
    --connection <CONNECTION_NAME> \
    --stage <STAGE_NAME> \
    --warehouse <WAREHOUSE_NAME>
```

Run this immediately when infrastructure is missing. Re-running is safe (`CREATE STAGE IF NOT EXISTS`, `PUT ... OVERWRITE=TRUE`, and `CREATE OR REPLACE PROCEDURE`).

## When to Load

Load from create/evaluate/optimize workflows during infrastructure setup steps. Triggers: "create stage", "upload files", "create procedure", "setup infrastructure".

## Stage Setup

Create stage if it doesn't exist:

```sql
CREATE STAGE IF NOT EXISTS {database}.{schema}.{stage_name} DIRECTORY = (ENABLE = TRUE);
```

Default stage name: `AI_FUNCTIONS`

## File Upload

Upload Python modules to stage:

```sql
PUT file://<SKILL_DIRECTORY>/src/{filename}.py @{database}.{schema}.{stage_name} AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
```

**Available modules:**

| Module | Used By | Purpose |
|--------|---------|---------|
| `custom_ai_function_utils.py` | evaluate, optimize, synthetic | Utility functions such as logging wrappers and Robust AI_COMPLETE wrapper (batching, parsing, error-details mode)|
| `metrics_core.py` | evaluate, optimize | Evaluation metrics (exact_match, fuzzy_match, llm_judge) |
| `snow_gepa_adapter.py` | optimize | GEPA adapter for Snowflake |
| `snow_gepa_optimize.py` | optimize | GEPA optimization logic |
| `snow_synthetic_data.py` | synthetic | Synthetic data generation |

Verify upload:
```sql
LIST @{database}.{schema}.{stage_name};
```

## SPROC Setup

Check if procedure exists before creating:

```sql
SHOW PROCEDURES LIKE '{SPROC_NAME}' IN SCHEMA {database}.{schema};
```

**If procedure does not exist**, create it:

```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/create_sproc.py \
    {type} {database} {schema} {stage_name} --execute --connection {connection}
```

**Available SPROCs:**

| Type | SPROC Name | Required Files |
|------|------------|----------------|
| `evaluate` | EVALUATE_AI_FUNCTION | custom_ai_function_utils.py, metrics_core.py |
| `evaluate_async` | EVALUATE_AI_FUNCTION_ASYNC | (none - SQL wrapper) |
| `optimize` | OPTIMIZE_AI_FUNCTION | custom_ai_function_utils.py, metrics_core.py, snow_gepa_adapter.py, snow_gepa_optimize.py |
| `optimize_async` | OPTIMIZE_AI_FUNCTION_ASYNC | (none - SQL wrapper) |
| `synthetic` | GENERATE_SYNTHETIC_DATA | custom_ai_function_utils.py, snow_synthetic_data.py |

**Note:** The `_ASYNC` SPROCs are SQL-only wrappers that create and execute Snowflake Tasks. They don't require Python files but do require their base SPROCs (EVALUATE_AI_FUNCTION, OPTIMIZE_AI_FUNCTION) to exist first.

## Workflow-Specific Requirements

### Evaluate Workflow
- Files: `custom_ai_function_utils.py`, `metrics_core.py`
- SPROCs: `EVALUATE_AI_FUNCTION`
- Optional: `EVALUATE_AI_FUNCTION_ASYNC` (for async execution)

### Optimize Workflow
- Files: `custom_ai_function_utils.py`, `metrics_core.py`, `snow_gepa_adapter.py`, `snow_gepa_optimize.py`
- SPROCs: `OPTIMIZE_AI_FUNCTION`
- Optional: `OPTIMIZE_AI_FUNCTION_ASYNC` (for async execution)

### Synthetic Data Workflow
- Files: `custom_ai_function_utils.py`, `snow_synthetic_data.py`
- SPROCs: `GENERATE_SYNTHETIC_DATA`

### Create Workflow (Full Setup)
- Files: All 5 modules
- SPROCs: All 5 procedures (3 base + 2 async)

## Troubleshooting

### Stage Path Not Found

**Problem:** `File not found` or `Stage not found` errors

**Cause:** IMPORTS clause in SPROC uses placeholder paths

**Solution:** Ensure fully qualified stage paths in IMPORTS:
```sql
IMPORTS = ('@MY_DATABASE.MY_SCHEMA.AI_FUNCTIONS/metrics_core.py', ...)
```
