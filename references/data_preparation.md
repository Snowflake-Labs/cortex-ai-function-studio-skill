<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Data Preparation Reference

Shared workflow for acquiring and validating data for evaluate and optimize workflows.

## When to Load

Load from evaluate or optimize skill when user needs data preparation: "split data", "need train/test", "create data", "generate data", "prepare data", "input-only table", "pseudo labels".

## Information Model

| Field | Required | Default | Confirm | Dependencies |
|-------|----------|---------|---------|--------------|
| `source_table` | Yes | - | No | - |
| `base_name` | Yes | (from source) | No | source_table |
| `split_ratio` | Yes | 60/40 | No | - |
| `database` | Yes | (from context) | No | - |
| `schema` | Yes | (from context) | No | - |
| `stratified` | No | false | No | label_column |
| `label_column` | If stratified | - | No | - |

**Critical fields**: None (all fields are operational/simple)

**Simple fields** (accept silently if pre-provided): `source_table`, `base_name`, `split_ratio`, `database`, `schema`, `stratified`, `label_column`

## Pre-Collection

Before prompting, scan the user's initial message and any prior context for already-provided information:

1. **Source table**: Look for table references like `DB.SCHEMA.TABLE`
2. **Split ratio**: Look for phrases like "60/40", "70/30", "80/20"
3. **Output naming**: Look for base names for output tables
4. **Database/schema**: Usually inherited from parent workflow context

For each piece found:
- Accept silently, proceed without re-asking (no critical fields in this workflow)

## Overview

Proper data splitting ensures:
- **Training data**: Used by optimizer to tune the function body (internally split into train/dev during optimization)
- **Test data**: Held-out data for final evaluation (never seen during optimization)

Note: The optimization step internally splits training data into train/dev sets for its genetic algorithm. You only need to provide train and test splits.

## Recommended Data Sizes

| Workflow | Table | Recommended Rows | Minimum |
|----------|-------|-----------------|---------|
| Evaluate | Test | 20–50 rows | 20 |
| Optimize | Training | 30–50 rows | 20 |

> These sizes are enough for fast iteration. Larger datasets (200+ rows) improve statistical signal but are not required to get started.

## Data Acquisition Flow

### Step 1: Determine Data Situation

**Default to option 1** — most users already have a table. Only present the full menu if the user says they don't have data or asks about data generation.

If the user has already provided a table name, skip this step entirely and proceed to validation (Step 3).

Otherwise, ask:
```
Do you have a labeled table ready, or do you need help preparing data?

1. **I have a table ready** - Provide your table name
2. **I need to generate or prepare data** - Synthetic data, pseudo labels, or table splitting
```

If the user selects option 2, expand with details:
```
What kind of data preparation do you need?

1. **Split an existing table** - Create train/test splits from one table
2. **Generate synthetic data** - Create labeled examples from scratch
3. **Label input-only data** - Generate pseudo labels using a strong model
```

### Step 2: Acquire Data

#### Option 1: Use Existing Tables

Ask user for table name(s) based on workflow context:
- **Evaluate workflow**: "Provide your test data table: `DB.SCHEMA.TABLE`"
- **Optimize workflow**: "Provide your training table (required) and test table (optional)"

#### Option 2: Split Existing Table

Follow the [Splitting Workflow](#splitting-workflow) below.

After splitting, use:
- `{base_name}_TRAIN` → Training table
- `{base_name}_TEST` → Test table

#### Option 3: Generate Synthetic Data

**Load** `synthetic-data/SKILL.md` for comprehensive guidelines.

After generation, return here to validate the created table.

#### Option 4: Generate Pseudo Labels for Input-Only Data

Use this when the user has inputs but no expected labels.

**Load** `synthetic-data/SKILL.md` and follow the pseudo-label workflow in pseudo-label mode.

Requirements:
- Preserve parent workflow context (`evaluate` or `optimize`) and function context if available.
- Collect output shape via `OUTPUT_SCHEMA` or `FUNCTION_NAME` per `synthetic-data/SKILL.md`.
- Use preview/revise before full-run labeling.

After pseudo-label generation:
- Default expected/label column to `EXPECTED`.
- **Evaluate workflow**: Use the labeled table as the test table.
- **Optimize workflow**: If only one labeled table is created, split it into train/test via this reference's splitting workflow.

Then return to Step 3 to validate the resulting table(s).

### Step 3: Validate Tables

For each table, run:
```sql
DESCRIBE TABLE {table_name};
SELECT COUNT(*) AS row_count FROM {table_name};
```

Check:
- Table exists and has expected columns
- Row count meets minimum requirements
- Input columns match function parameters

### Step 4: Map Columns

**⚠️ STOP**: Always confirm column mapping (critical fields).

Present to user:
```
Map your table columns:

Input columns (passed to function in order):
- [INPUT_COL1]
- [INPUT_COL2]

Label column (expected outputs):
- [LABEL_COL]
```

For optimize workflow, these input columns are referenced in prompts as `{COLUMN_NAME}` placeholders.

If the table came from pseudo-labeling, default `label_column` to `EXPECTED`.

Confirm: "I'll use input columns `{input_columns}` and label column `{label_column}` — confirm?"

### Step 5: Validate Columns

After user confirms column mapping, verify all mapped columns exist in the relevant tables. Column matching is case-insensitive.

**For evaluate workflow** (test table only):
- Validate `input_columns` and `label_column` exist in `test_table`

**For optimize workflow** (training + optional test):
- Validate `input_columns` and `label_column` exist in `training_table`
- If `test_table` provided, validate same columns exist there too

**If any column is missing:**
Present the mismatch to the user using `ask_user_question` with remediation options:
```
Column mismatch detected:
- Table ({table_name}) columns: [list]
- Missing column(s): [columns]

Options:
1. Re-map to different column names that exist in the table(s)
2. Rename columns in the table to match
3. Use the same table for both training and test (optimize only)
```

**⚠️ STOP**: Do NOT proceed if columns don't match — the workflow will fail.

### Multi-Column Truth Aggregation

When the user's table has multiple truth columns that correspond to keys in a multi-key function output (e.g., separate `SENTIMENT`, `CONFIDENCE`, `RATIONALE` columns), these must be combined into a single VARIANT `label_column` before passing data to evaluate or optimize.

Help the user create a view that aggregates the truth columns using `OBJECT_CONSTRUCT`:

```sql
CREATE VIEW {database}.{schema}.{table_name}_WITH_EXPECTED AS
SELECT *,
    OBJECT_CONSTRUCT(
        'SENTIMENT', "SENTIMENT",
        'CONFIDENCE', "CONFIDENCE",
        'RATIONALE', "RATIONALE"
    ) AS EXPECTED
FROM {database}.{schema}.{original_table};
```

Then use the view as the table and `EXPECTED` as the `label_column`.

Individual keys can be verified with VARIANT syntax:
```sql
SELECT EXPECTED:SENTIMENT, EXPECTED:CONFIDENCE FROM {view_name} LIMIT 5;
```

**Note on column type:** The `OBJECT_CONSTRUCT` expression produces a VARIANT column. This is compatible with the evaluate and optimize pipelines, which convert label values to strings internally. The EXPECTED column will contain a JSON object like `{"SENTIMENT": "positive", "CONFIDENCE": 0.95}` — the same format as synthetic data's EXPECTED column. No manual casting is needed.

---

## Splitting Workflow

For users who have a single table that needs splitting into train/test.

### Confirm Split Ratio

```
How would you like to split your data?

1. **Standard (60/40)** - 60% train, 40% test (recommended)
2. **Custom** - Specify your own ratio
```

### Create Split Tables

**IMPORTANT**: Always use `RANDOM(42)` for splitting. Do NOT use hash-based approaches.

```sql
-- Step 1: Create temp table with random ordering
CREATE OR REPLACE TEMPORARY TABLE {base_name}_SPLIT_TEMP AS
SELECT *, ROW_NUMBER() OVER (ORDER BY RANDOM(42)) AS _split_rn
FROM {source_table};

-- Step 2: Calculate split point
SET split_point = (SELECT FLOOR(COUNT(*) * {train_pct} / 100) FROM {base_name}_SPLIT_TEMP);

-- Step 3: Create train table
CREATE TABLE {database}.{schema}.{base_name}_TRAIN AS
SELECT * EXCLUDE (_split_rn) FROM {base_name}_SPLIT_TEMP WHERE _split_rn <= $split_point;

-- Step 4: Create test table
CREATE TABLE {database}.{schema}.{base_name}_TEST AS
SELECT * EXCLUDE (_split_rn) FROM {base_name}_SPLIT_TEMP WHERE _split_rn > $split_point;

-- Step 5: Clean up
DROP TABLE IF EXISTS {base_name}_SPLIT_TEMP;
```

### Verify Splits

```sql
SELECT 'TRAIN' AS split, COUNT(*) AS row_count FROM {base_name}_TRAIN
UNION ALL
SELECT 'TEST' AS split, COUNT(*) AS row_count FROM {base_name}_TEST;
```

Present:
```
Data split complete:

| Split | Rows | Percentage |
|-------|------|------------|
| TRAIN | {n}  | {pct}%     |
| TEST  | {n}  | {pct}%     |

Tables created:
- {database}.{schema}.{base_name}_TRAIN
- {database}.{schema}.{base_name}_TEST
```

---

## Stratified Splitting (Advanced)

For classification tasks with imbalanced classes:

```sql
CREATE OR REPLACE TEMPORARY TABLE {base_name}_SPLIT_TEMP AS
SELECT *,
       ROW_NUMBER() OVER (PARTITION BY {label_column} ORDER BY RANDOM(42)) AS _split_rn,
       COUNT(*) OVER (PARTITION BY {label_column}) AS _class_total
FROM {source_table};

CREATE TABLE {database}.{schema}.{base_name}_TRAIN AS
SELECT * EXCLUDE (_split_rn, _class_total)
FROM {base_name}_SPLIT_TEMP
WHERE _split_rn <= FLOOR(_class_total * {train_pct} / 100);

CREATE TABLE {database}.{schema}.{base_name}_TEST AS
SELECT * EXCLUDE (_split_rn, _class_total)
FROM {base_name}_SPLIT_TEMP
WHERE _split_rn > FLOOR(_class_total * {train_pct} / 100);

DROP TABLE IF EXISTS {base_name}_SPLIT_TEMP;
```

Ask user when label column has categorical values:
```
Your label column '{label_column}' has {n_classes} unique values.
Would you like stratified splitting to maintain class distribution?
```
