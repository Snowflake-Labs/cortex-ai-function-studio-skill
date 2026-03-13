<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Version Management Reference

## When to Load

Load from optimize/SKILL.md Step 10 when user selects "Save as new version". Also load for: "list versions", "compare versions", "rollback", "promote version", "delete version".

This reference covers how to manage versions of AI functions.

## Overview

Version management allows you to:
- Track multiple iterations of an AI function
- Compare performance across versions
- Easily rollback to previous versions
- Deploy specific versions to production

## Version Metadata Table

The `AI_FUNCTION_VERSIONS` table stores metadata for all versions:

```sql
CREATE TABLE IF NOT EXISTS {database}.{schema}.AI_FUNCTION_VERSIONS (
    VERSION_ID VARCHAR,
    FUNCTION_NAME VARCHAR,
    BASE_FUNCTION_NAME VARCHAR,
    MODEL_NAME VARCHAR,
    METRIC_SCORE FLOAT,
    METRIC_NAME VARCHAR,
    RUN_ID VARCHAR,
    PROMPT_TEXT VARCHAR(16777216),
    NOTES VARCHAR,
    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
);
```

**Columns:**
- `METRIC_NAME`: The metric used for optimization (e.g., 'exact_match', 'llm_judge', custom metric name)
- `RUN_ID`: Links to the optimization experiment in the GEPA tracking table (e.g., 'ai_func_opt_MY_FUNC_1771615368000')

**Note:** Model costs are looked up from `models.json` at display time based on `MODEL_NAME`, not stored in the table. This keeps costs in sync with the source of truth.

## Save New Version

Use this workflow when saving an optimized function as a new version (called from `optimize/SKILL.md` Step 10).

**Expected context from optimize workflow:**
- `{database}`, `{schema}`: Target location
- `{function_base}`: Base function name (without DB.SCHEMA prefix)
- `{selected_model}`: The model used
- `{best_score}`: The optimization score
- `{metric_name}`: The metric used for optimization (e.g., 'exact_match', 'llm_judge')
- `{run_id}`: The optimization experiment ID (e.g., 'ai_func_opt_MY_FUNC_1771615368000')
- `{optimized_system_prompt}`: The optimized prompt
- `{original_inputs_array}`, `{original_outputs_array}`: Function signature
- `{original_user_prompt_template}`: User prompt template

### Step 1: Get Version Suffix

Ask user:
```
Enter version suffix (e.g., V2, OPTIMIZED, PROD):
```

### Step 2: Create Metadata Table

Create the version metadata table if it doesn't exist:
```sql
CREATE TABLE IF NOT EXISTS {database}.{schema}.AI_FUNCTION_VERSIONS (
    VERSION_ID VARCHAR,
    FUNCTION_NAME VARCHAR,
    BASE_FUNCTION_NAME VARCHAR,
    MODEL_NAME VARCHAR,
    METRIC_SCORE FLOAT,
    METRIC_NAME VARCHAR,
    RUN_ID VARCHAR,
    PROMPT_TEXT VARCHAR(16777216),
    NOTES VARCHAR,
    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
);
```

### Step 3: Create Versioned Function

Build the JSON configuration:
```json
{
    "database": "{database}",
    "schema": "{schema}",
    "function_name": "{function_base}_{version_suffix}",
    "function_intention": "Optimized version of {function_base}. Score: {best_score:.1%} with {selected_model}",
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

### Step 4: Save Version Metadata

Optionally ask for notes:
```
Add notes for this version (or press Enter to skip):
```

Insert the metadata:
```sql
INSERT INTO {database}.{schema}.AI_FUNCTION_VERSIONS (
    VERSION_ID,
    FUNCTION_NAME,
    BASE_FUNCTION_NAME,
    MODEL_NAME,
    METRIC_SCORE,
    METRIC_NAME,
    RUN_ID,
    PROMPT_TEXT,
    NOTES
)
VALUES (
    'ver_' || SUBSTR(UUID_STRING(), 1, 12),
    '{database}.{schema}.{function_base}_{version_suffix}',
    '{function_base}',
    '{selected_model}',
    {best_score},
    '{metric_name}',
    '{run_id}',
    $${optimized_system_prompt}$$,
    '{user_notes}'
);
```

### Step 5: Confirm Creation

```
✅ New version created: {database}.{schema}.{function_base}_{version_suffix}
   Model: {selected_model}
   Score: {best_score:.1%}
   
You can now use either:
- Original: {database}.{schema}.{function_base}
- Optimized: {database}.{schema}.{function_base}_{version_suffix}
```

## Common Operations

### List All Versions of a Function

```sql
SELECT 
    FUNCTION_NAME,
    MODEL_NAME,
    ROUND(METRIC_SCORE * 100, 1) || '%' AS SCORE,
    METRIC_NAME,
    RUN_ID,
    LEFT(NOTES, 50) AS NOTES,
    CREATED_AT
FROM {database}.{schema}.AI_FUNCTION_VERSIONS
WHERE BASE_FUNCTION_NAME = '{function_base}'
ORDER BY METRIC_SCORE DESC;
```

Present as:
```
Versions of {function_base}:

| Function Name | Model | Score | Metric | Experiment | Notes | Created |
|---------------|-------|-------|--------|------------|-------|---------|
| MY_FUNC_V3 | llama3.1-70b | 88.5% | exact_match | ai_func_opt_MY_FUNC_1771615368000 | Optimized for... | 2024-01-15 |
| MY_FUNC_V2 | llama3.1-8b | 82.0% | llm_judge | ai_func_opt_MY_FUNC_1771601234000 | Added few-shot... | 2024-01-10 |
| MY_FUNC | mistral-7b | 65.0% | - | - | Initial version | 2024-01-05 |
```

### List Pareto-Optimal Versions

To show only versions where no other version is both cheaper AND better, use the pareto filter:

```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/filter_pareto.py \
    --json '[{"model": "llama3.1-8b", "score": 0.82, "name": "MY_FUNC_V2"}, ...]' \
    --prompt-chars 200 \
    --avg-input-chars 500 \
    --avg-output-chars 10 \
    --format table
```

The character count arguments are used to calculate the input/output ratio for cost weighting. See `optimize/SKILL.md` Step 9a for how to get these values from your test data.

This filters out dominated versions (e.g., if V2 with llama3.1-8b has 82% and V3 with llama3.1-70b has only 80%, V3 is filtered out since V2 is both cheaper and better).

### Get Best Performing Version

```sql
SELECT 
    FUNCTION_NAME,
    MODEL_NAME,
    METRIC_SCORE,
    PROMPT_TEXT
FROM {database}.{schema}.AI_FUNCTION_VERSIONS
WHERE BASE_FUNCTION_NAME = '{function_base}'
ORDER BY METRIC_SCORE DESC
LIMIT 1;
```

### Get Version Details

```sql
SELECT *
FROM {database}.{schema}.AI_FUNCTION_VERSIONS
WHERE FUNCTION_NAME = '{full_function_name}';
```

### Compare Two Versions

```sql
SELECT 
    v1.FUNCTION_NAME AS version_1,
    v2.FUNCTION_NAME AS version_2,
    v1.MODEL_NAME AS model_1,
    v2.MODEL_NAME AS model_2,
    ROUND(v1.METRIC_SCORE * 100, 1) AS score_1,
    ROUND(v2.METRIC_SCORE * 100, 1) AS score_2,
    ROUND((v2.METRIC_SCORE - v1.METRIC_SCORE) * 100, 1) AS score_improvement
FROM {database}.{schema}.AI_FUNCTION_VERSIONS v1
JOIN {database}.{schema}.AI_FUNCTION_VERSIONS v2
    ON v1.BASE_FUNCTION_NAME = v2.BASE_FUNCTION_NAME
WHERE v1.FUNCTION_NAME = '{version_1}'
  AND v2.FUNCTION_NAME = '{version_2}';
```

**Note:** For cost comparison, use `filter_pareto.py` with both versions to determine if one dominates the other. Cost is calculated from `models.json` based on model name and the input/output ratio (derived from character counts).

### List Versions by Experiment

Find all versions created from a specific optimization run:

```sql
SELECT 
    FUNCTION_NAME,
    MODEL_NAME,
    ROUND(METRIC_SCORE * 100, 1) || '%' AS SCORE,
    METRIC_NAME,
    CREATED_AT
FROM {database}.{schema}.AI_FUNCTION_VERSIONS
WHERE RUN_ID = '{run_id}'
ORDER BY METRIC_SCORE DESC;
```

This is useful for reviewing all versions produced by a single GEPA optimization experiment.

### List Versions by Metric

Compare versions optimized for the same metric:

```sql
SELECT 
    FUNCTION_NAME,
    MODEL_NAME,
    ROUND(METRIC_SCORE * 100, 1) || '%' AS SCORE,
    RUN_ID,
    CREATED_AT
FROM {database}.{schema}.AI_FUNCTION_VERSIONS
WHERE BASE_FUNCTION_NAME = '{function_base}'
  AND METRIC_NAME = '{metric_name}'
ORDER BY METRIC_SCORE DESC;
```

This helps compare apples-to-apples when you have versions optimized for different metrics (e.g., `exact_match` vs `llm_judge`).

### Delete a Version

To delete a version:
1. Drop the function
2. Remove the metadata entry

```sql
-- Drop the function
DROP FUNCTION IF EXISTS {database}.{schema}.{function_name}({signature});

-- Remove metadata
DELETE FROM {database}.{schema}.AI_FUNCTION_VERSIONS
WHERE FUNCTION_NAME = '{database}.{schema}.{function_name}';
```

### Promote Version to Production

To promote a versioned function to replace the original:

1. **Retrieve the optimized prompt:**
```sql
SELECT PROMPT_TEXT, MODEL_NAME
FROM {database}.{schema}.AI_FUNCTION_VERSIONS
WHERE FUNCTION_NAME = '{versioned_function_name}';
```

2. **Recreate the original function** using `create_udf.py` with the retrieved prompt:
```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/create_udf.py \
    --json '{
        "database": "{database}",
        "schema": "{schema}",
        "function_name": "{original_function_name}",
        "model": "{model_name}",
        "system_prompt": "{retrieved_prompt_text}",
        ... (other config from original function)
    }'
```

3. **Record the promotion** in metadata:
```sql
INSERT INTO {database}.{schema}.AI_FUNCTION_VERSIONS (
    VERSION_ID,
    FUNCTION_NAME,
    BASE_FUNCTION_NAME,
    MODEL_NAME,
    METRIC_SCORE,
    METRIC_NAME,
    RUN_ID,
    PROMPT_TEXT,
    NOTES
)
SELECT 
    'ver_' || SUBSTR(UUID_STRING(), 1, 12),
    '{database}.{schema}.{original_function_name}',
    BASE_FUNCTION_NAME,
    MODEL_NAME,
    METRIC_SCORE,
    METRIC_NAME,
    RUN_ID,
    PROMPT_TEXT,
    'Promoted from ' || FUNCTION_NAME || ' on ' || CURRENT_DATE()
FROM {database}.{schema}.AI_FUNCTION_VERSIONS
WHERE FUNCTION_NAME = '{versioned_function_name}';
```

**Note:** The actual function DDL is generated by `create_udf.py` to ensure correct SQL syntax for structured outputs, proper escaping, and consistency with the create workflow.

## Version Naming Conventions

Recommended naming patterns:
- `{BASE}_V2`, `{BASE}_V3` - Sequential versions
- `{BASE}_PROD`, `{BASE}_DEV` - Environment-based
- `{BASE}_OPTIMIZED` - After optimization
- `{BASE}_YYYYMMDD` - Date-based snapshots

## Integration with Workflows

### From Optimize Workflow

When optimization completes, offer to save as new version:
1. Create the new versioned function via `create_udf.py`
2. Insert metadata into AI_FUNCTION_VERSIONS
3. Offer to promote to original if performance improves

### From Evaluate Workflow

After evaluation, show version comparison:
```
Current version performance: 72.5%
Best saved version: MY_FUNC_V2 at 85.0% (llama3.1-8b)

Would you like to:
1. Keep current version
2. Rollback to MY_FUNC_V2 (best performing)
3. View all versions
```

When rolling back, use `create_udf.py` with the stored PROMPT_TEXT from the target version.
