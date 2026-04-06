<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Infrastructure Setup

## Inline Mode (Required)

The runner scripts (`run.py evaluate`, `run.py optimize`, `run.py synthetic`) embed Python source code directly into the anonymous SPROC body (`AS $$ ... $$`). **No stage upload is needed** — the scripts are fully self-contained.

**Always use inline mode.** Skip this entire infrastructure setup step and proceed directly to the execution step in the relevant workflow. Stage-based deployment is not supported due to current infrastructure limitations.

---

The sections below document the legacy stage-based deployment path. **Do not use these steps** — they are retained only for reference.

## Deploy Script (Stage-Based — Legacy, Do Not Use)

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
- upload prerequisite files (`custom_ai_function_utils.py`, `metrics_core.py`, `snow_gepa_adapter.py`, `snow_gepa_optimize.py`, `snow_synthetic_data.py`)
```

If `database` or `schema` is unknown, ask for it first. If infrastructure was uploaded previously, ask whether to reuse the same database/schema.

### Upload command

```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/deploy_ai_function_manager.py \
    --database <DATABASE> \
    --schema <SCHEMA> \
    --connection <CONNECTION_NAME> \
    --stage <STAGE_NAME> \
    --warehouse <WAREHOUSE_NAME> \
    --upload-stage-only
```

Re-running is safe (`CREATE STAGE IF NOT EXISTS`, `PUT ... OVERWRITE=TRUE`).

## When to Load

Load when the user is on a **stage-based** path (not the default inline mode). Triggers: "create stage", "upload files", "setup infrastructure", or explicit request to use a stage instead of inlined Python.

## Stage Setup

Create stage if it doesn't exist:

```sql
CREATE STAGE IF NOT EXISTS {database}.{schema}.{stage_name}
    ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
    DIRECTORY = (ENABLE = TRUE);
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
## Workflow-Specific Requirements

### Evaluate Workflow
- Files: `custom_ai_function_utils.py`, `metrics_core.py`

### Optimize Workflow
- Files: `custom_ai_function_utils.py`, `metrics_core.py`, `snow_gepa_adapter.py`, `snow_gepa_optimize.py`

### Synthetic Data Workflow
- Files: `custom_ai_function_utils.py`, `snow_synthetic_data.py`

### Create Workflow (Full Setup)
- Files: All 5 modules
## Troubleshooting

### Stage Path Not Found

**Problem:** `File not found` or `Stage not found` errors

**Cause:** IMPORTS clause in SPROC uses placeholder paths

**Solution:** Ensure fully qualified stage paths in IMPORTS:
```sql
IMPORTS = ('@MY_DATABASE.MY_SCHEMA.AI_FUNCTIONS/metrics_core.py', ...)
```
