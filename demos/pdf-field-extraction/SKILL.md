---
name: pdf-field-extraction-demo
description: "Interactive demo: Extract structured fields from SEC 10-K filing PDFs using multimodal AI, then evaluate and optimize across document models via GEPA prompt optimization."
parent_skill: demos
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# SEC 10-K PDF Field Extraction Demo

Build a multimodal AI function that extracts structured metadata from SEC 10-K filing PDFs, then evaluate accuracy and optimize prompts across document-capable models.

## Overview

Download real SEC EDGAR filings → convert cover pages to PDF → build an extraction function → evaluate baseline → create a custom composite metric → run GEPA optimization → compare cost/quality trade-offs. This is a **multimodal document** demo: the AI function reads PDFs from a Snowflake stage using `TO_FILE()`.

**Estimated time:** 20-40 minutes (GEPA optimization accounts for most of this).

**Ground truth** is sourced from the EDGAR submissions API (company metadata), NOT from parsing the PDFs — ensuring 100% deterministic accuracy.

## Workflow

### Step 1: Introduction

Explain to user:
```
Welcome to the SEC 10-K PDF Field Extraction Demo!

At the end of this demo, you will witness the Cortex AI Function Studio's ability to:
- Extract structured fields from real SEC filing PDFs using multimodal AI_COMPLETE
- Measure extraction accuracy with a custom composite metric
- Optimize prompts using GEPA to improve cheaper document models
- Compare cost vs. quality trade-offs across models using Pareto analysis

This demo downloads real 10-K annual report filings from the SEC EDGAR
database, converts them to PDF, and challenges AI models to extract
four key fields from each cover page:

  company_name            — legal registrant name
  report_date             — fiscal year end date (YYYY-MM-DD)
  irs_ein                 — IRS Employer Identification Number (XX-XXXXXXX)
  state_of_incorporation  — full state name (e.g. "Delaware")

Ground truth comes from the EDGAR submissions API — deterministic and
always correct. The model must read the PDF to find these same fields.

Objects created: all prefixed with DEMO_ for easy cleanup.
```

**⚠️ STOP**: Ask user if they want to proceed before continuing.

### Step 2: Setup - Choose Location

Ask user:
```
Where would you like to create the demo objects?

Database: [e.g., TEMP]
Schema: [e.g., PUBLIC]
```

Store the database and schema for use throughout the demo.

### Step 3: Dataset Consent & Data Loading

Explain to user:
```
This demo uses pre-bundled PDF files converted from real SEC EDGAR 10-K
filing cover pages. The script uploads them to a Snowflake stage and
creates labeled train/test tables.

Source:  SEC EDGAR (https://www.sec.gov/edgar)
Data:   Public filings from ~80 well-known US companies across industries
        (technology, finance, healthcare, consumer, energy, industrial, defense)
License: SEC filings are public domain

The script will:
1. Extract pre-bundled PDFs from the skill's data.zip archive
2. Upload them to an SSE-encrypted Snowflake stage: DEMO_SEC_FILING_STAGE
3. Create stratified train/test tables with ground truth from the manifest
```

**⚠️ STOP**: Wait for user confirmation before proceeding.

Run the data generation script:
```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/generate_sec_filing_data.py \
  --connection <CONNECTION_NAME> \
  --database {database} \
  --schema {schema}
```

**Note:** Replace `<SKILL_DIRECTORY>` with the absolute path to the cortex-ai-function-studio skill directory, and `<CONNECTION_NAME>` with the active Snowflake connection.

Verify creation:
```sql
SELECT 'TRAIN' AS SPLIT, COUNT(*) AS ROWS
FROM {database}.{schema}.DEMO_SEC_FILING_TRAIN
UNION ALL
SELECT 'TEST', COUNT(*)
FROM {database}.{schema}.DEMO_SEC_FILING_TEST;
```

Confirm the stage has PDF files:
```sql
SELECT RELATIVE_PATH, ROUND(SIZE / 1024, 1) AS SIZE_KB
FROM DIRECTORY(@{database}.{schema}.DEMO_SEC_FILING_STAGE)
ORDER BY RELATIVE_PATH
LIMIT 10;
```

Show a few sample rows with ground truth:
```sql
SELECT
    FILE_PATH,
    PARSE_JSON(EXPECTED_OUTPUT):company_name::STRING AS COMPANY,
    PARSE_JSON(EXPECTED_OUTPUT):report_date::STRING AS REPORT_DATE,
    PARSE_JSON(EXPECTED_OUTPUT):state_of_incorporation::STRING AS STATE
FROM {database}.{schema}.DEMO_SEC_FILING_TEST
LIMIT 5;
```

### Step 4: Create the Extraction AI Function

Present the function configuration:
```
Now we'll create a multimodal AI function that extracts fields from
SEC 10-K filing PDFs.

Default model: gemini-2.5-flash
Stage: @{database}.{schema}.DEMO_SEC_FILING_STAGE

Function name: DEMO_EXTRACT_SEC_FIELDS
Input: FILE_PATH (VARCHAR) — relative path to PDF on stage
Output: VARIANT with four fields:
  - company_name (string)
  - report_date (string, YYYY-MM-DD)
  - irs_ein (string, XX-XXXXXXX)
  - state_of_incorporation (string)

System prompt:
"You are an expert analyst of SEC financial filings. Given the cover page
of a 10-K annual report, extract these four fields exactly:

1. company_name: The legal name of the registrant as stated on the cover page.
2. report_date: The fiscal year end date from the phrase 'fiscal year ended ...',
   formatted as YYYY-MM-DD.
3. irs_ein: The I.R.S. Employer Identification Number, formatted as XX-XXXXXXX.
4. state_of_incorporation: The full name of the state or jurisdiction of
   incorporation (e.g. 'Delaware', not 'DE')."

User prompt template: "{FILE_PATH}"
```

**⚠️ STOP**: Wait for user confirmation before creating the function.

**Load** `create/SKILL.md` and follow it from **Step 7 onward**, passing:
- `database`, `schema`
- `function_name`: `DEMO_EXTRACT_SEC_FIELDS`
- `function_intention`: `Extract structured metadata fields from SEC 10-K filing PDFs.`
- `model`: `gemini-2.5-flash`
- `stage_name`: `@{database}.{schema}.DEMO_SEC_FILING_STAGE`
- `inputs`: `[{"name": "FILE_PATH", "sql_type": "STAGE_FILE_PATH"}]`
- `outputs`: `[{"name": "company_name", "json_type": "string", "description": "Legal registrant name"}, {"name": "report_date", "json_type": "string", "description": "Fiscal year end date YYYY-MM-DD"}, {"name": "irs_ein", "json_type": "string", "description": "IRS EIN in XX-XXXXXXX format"}, {"name": "state_of_incorporation", "json_type": "string", "description": "Full state name of incorporation"}]`
- `system_prompt`: confirmed prompt
- `user_prompt_template`: `{FILE_PATH}`

Return here after the smoke test succeeds.

**Troubleshooting:** If the smoke test fails, verify the stage has SSE encryption and the model supports document inputs. Try `claude-sonnet-4-5` as fallback. See `references/multimodal_setup.md` for the full list of document-capable models.

### Step 5: Create Custom Composite Metric

Explain to user:
```
Before evaluation, we'll create a custom composite metric that scores
each extracted field independently. This gives the optimizer fine-grained
feedback to improve field-specific extraction.

Custom metric: DEMO_SEC_EXTRACTION_METRIC
Fields and weights:
  - company_name:           fuzzy match  (weight 0.30)
  - report_date:            exact match  (weight 0.25)
  - irs_ein:                normalized exact match (weight 0.25)
  - state_of_incorporation: case-insensitive match (weight 0.20)

The EIN comparison strips non-digit characters so "94-2404110" and
"942404110" are treated as equivalent.
```

**⚠️ STOP**: Wait for user confirmation before creating the custom metric.

**Load** `references/custom_metrics.md` and follow the composite metric workflow, passing:
- `metric_name`: `DEMO_SEC_EXTRACTION_METRIC`
- `metric_description`: `Composite metric scoring four extracted fields from SEC 10-K filings with independent weighted checks.`
- `database`, `schema`: from context

Use this pre-approved field configuration (skip the field-by-field prompting in custom_metrics Step 1b):

| Field | Check | Weight | Notes |
|-------|-------|--------|-------|
| `company_name` | fuzzy_match | 0.30 | Use `SequenceMatcher` on lowered strings. Treat score ≥ 0.9 as "match" in feedback. |
| `report_date` | exact_match | 0.25 | Compare YYYY-MM-DD strings exactly. |
| `irs_ein` | normalized exact match | 0.25 | Strip all non-digit characters before comparing (so "94-2404110" equals "942404110"). |
| `state_of_incorporation` | case-insensitive match with fuzzy fallback | 0.20 | First try case-insensitive exact match. If that fails, use `SequenceMatcher` and treat score ≥ 0.85 as a match (to handle minor spelling variations). |

Follow the custom_metrics workflow from **Step 2 onward** (write code → test → create UDF). Return here after the metric UDF is created and smoke-tested.

### Step 6: Evaluate the Extraction Function

Present the evaluation configuration:
```
Let's evaluate how well the function extracts fields from the test PDFs.

We'll use the custom composite metric which scores all four fields with
independent checks and weighted scoring.

Metric: sec_extraction_metric (custom composite)
Custom metric UDF: {database}.{schema}.DEMO_SEC_EXTRACTION_METRIC
Results table: DEMO_EXTRACT_SEC_FIELDS_EVAL_RESULTS
```

**⚠️ STOP**: Wait for user confirmation before running evaluation.

**Load** `evaluate/SKILL.md` and follow it from **Step 5 onward**, passing:
- `function_name`: `{database}.{schema}.DEMO_EXTRACT_SEC_FIELDS`
- `function_model`: `gemini-2.5-flash`
- `test_table`: `{database}.{schema}.DEMO_SEC_FILING_TEST`
- `input_columns`: `['FILE_PATH']`
- `label_column`: `EXPECTED_OUTPUT`
- `metric`: `sec_extraction_metric`
- `custom_metric_udf`: `{database}.{schema}.DEMO_SEC_EXTRACTION_METRIC`
- `results_table`: `{database}.{schema}.DEMO_EXTRACT_SEC_FIELDS_EVAL_RESULTS`

**Async by default:** When the evaluate workflow reaches the execution mode selection, choose **async** (`EVALUATE_AI_FUNCTION_ASYNC`) without asking the user. If the async SPROC returns an error, fall back to sync execution. After kicking off the async job, poll `TASK_HISTORY()` for completion within this session. **Load** `references/async_status.md` for polling patterns.

**Skip Step 7 (next steps)** in the evaluate workflow — return here after results are presented.

Once evaluation is done, review the results. Show the scores to the user. Offer to see extraction errors:
```
Would you like to see which filings had extraction errors?
```

If yes, query the results table:
```sql
SELECT
    LEFT(INPUT_TEXT, 60) AS FILE,
    SCORE,
    FEEDBACK
FROM {database}.{schema}.DEMO_EXTRACT_SEC_FIELDS_EVAL_RESULTS
WHERE SCORE < 1.0
ORDER BY SCORE
LIMIT 15;
```

Discuss common failure patterns: date format mismatches (model returns "September 28, 2024" instead of "2024-09-28"), EIN formatting differences, company name suffix variations ("Inc." vs "Inc" vs "Incorporated"), state abbreviations instead of full names.

After reviewing results, continue to Step 7.

### Step 7: Optimize with GEPA

Present the optimization configuration:
```
Now we'll use GEPA to optimize the extraction prompt across
document-capable models.

GEPA (Genetic-Pareto Algorithm) evolves the system prompt through
multiple generations:
1. Scores prompt variations against training examples
2. Reflects on extraction failures — analyzing WHY specific fields
   were extracted incorrectly (e.g., "model returned state abbreviation
   instead of full name", "date format was not normalized to YYYY-MM-DD")
3. Generates new prompt variations informed by those reflections
4. Keeps only Pareto-optimal performers (best quality at each cost level)

Please confirm or modify any settings:

Auto budget: medium (~10-20 minutes)
Metric: sec_extraction_metric (custom composite)
Label column: EXPECTED_OUTPUT
Experiment: DEMO_EXTRACT_SEC_FIELDS_OPT_EXP
Models: ['gemini-2.5-flash-lite', 'gemini-2.5-flash', 'claude-haiku-4-5']

Options:
1. Yes - Run GEPA optimization with these settings
2. Modify - Change settings before running
3. No - Skip to cleanup
```

**⚠️ STOP**: Wait for user confirmation before starting optimization.

If user chooses No, skip to Step 9.

If yes, **load** `optimize/SKILL.md` and follow it from **Step 6 onward**, passing:
- `function_name`: `{database}.{schema}.DEMO_EXTRACT_SEC_FIELDS`
- `training_table`: `{database}.{schema}.DEMO_SEC_FILING_TRAIN`
- `test_table`: `{database}.{schema}.DEMO_SEC_FILING_TEST`
- `input_columns`: `['FILE_PATH']`
- `label_column`: `EXPECTED_OUTPUT`
- `metric`: `sec_extraction_metric`
- `custom_metric_udf`: `{database}.{schema}.DEMO_SEC_EXTRACTION_METRIC`
- `models`: `['gemini-2.5-flash-lite', 'gemini-2.5-flash', 'claude-haiku-4-5']`
- `reflection_model`: `claude-opus-4-6` (or strongest available Claude-family model)
- `auto_budget`: `medium`
- `experiment_name`: `{database}.{schema}.DEMO_EXTRACT_SEC_FIELDS_OPT_EXP`

**Async by default:** When the optimize workflow reaches the execution mode selection, choose **async** (`OPTIMIZE_AI_FUNCTION_ASYNC`) without asking the user. If the async SPROC returns an error, fall back to sync with `timeout_seconds: 14400`. After kicking off the async job, poll `TASK_HISTORY()` for completion. **Load** `references/async_status.md` for polling patterns.

**Skip Step 9 (next steps)** in the optimize workflow — return here after results are presented and the user has decided whether to apply the optimized prompt.

### Step 8: Summarize GEPA Results

After optimization completes, present results and compare before/after.

**8.1. Show the optimization journey:**

```sql
-- List all runs (SEED, ITER_1..N, BEST per model)
SHOW RUNS IN EXPERIMENT {database}.{schema}.DEMO_EXTRACT_SEC_FIELDS_OPT_EXP;

-- View metrics for each run to see how scores evolved
-- Run names follow the pattern: {MODEL}_SEED, {MODEL}_ITER_1, ..., {MODEL}_BEST
-- where {MODEL} is the uppercased model name with non-alphanumeric chars replaced by _
SHOW RUN METRICS IN EXPERIMENT {database}.{schema}.DEMO_EXTRACT_SEC_FIELDS_OPT_EXP;
```

Highlight how prompts evolve — early candidates use generic instructions ("extract fields from this document"), later candidates include specific formatting rules (YYYY-MM-DD dates, XX-XXXXXXX EIN format, full state name vs abbreviation) that GEPA learned from extraction error feedback.

**8.2. Compare baseline vs. optimized:**

```sql
-- Compare seed vs best for each model
SHOW RUN METRICS IN EXPERIMENT {database}.{schema}.DEMO_EXTRACT_SEC_FIELDS_OPT_EXP;
-- Look for {MODEL}_SEED and {MODEL}_BEST rows and compare valset_score
```

**8.3. Pareto analysis for cost/quality:**

Calculate character lengths from the test table:
```sql
SELECT
    120 AS avg_output_chars
FROM {database}.{schema}.DEMO_SEC_FILING_TEST;
```

Note: for document functions, actual cost is dominated by document token count, not text chars. The Pareto filter gives a relative cost ordering which is still directionally useful.

```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/filter_pareto.py \
    --json '[{"model": "model1", "score": 0.85}, ...]' \
    --prompt-chars {prompt_chars} --avg-output-chars {avg_output_chars} \
    --seed-score {baseline_score} --format table
```

**8.4. Summarize key findings:**

```
Key result: {best_model} achieves {optimized_score:.0%} composite extraction
accuracy at ~{cost_ratio}x cheaper than {strong_reference}. GEPA closed the
quality gap through prompt optimization alone.

What GEPA learned:
- Reflected on extraction failures across {total_candidates} prompt variations
- Learned format-specific instructions (YYYY-MM-DD dates, XX-XXXXXXX EIN
  format, full state names) that a generic prompt misses
- Evolved from "extract fields from this document" to detailed extraction
  rubrics with field-specific formatting rules and common error guards
```

### Step 9: Cleanup

Ask user:
```
The SEC 10-K PDF Field Extraction demo is complete!

Would you like to clean up the demo objects?

This will drop:
- {database}.{schema}.DEMO_SEC_FILING_STAGE (stage + PDFs)
- {database}.{schema}.DEMO_SEC_FILING_TRAIN
- {database}.{schema}.DEMO_SEC_FILING_TEST
- {database}.{schema}.DEMO_EXTRACT_SEC_FIELDS (function)
- {database}.{schema}.DEMO_SEC_EXTRACTION_METRIC (metric)
- {database}.{schema}.DEMO_EXTRACT_SEC_FIELDS_EVAL_RESULTS
- {database}.{schema}.DEMO_EXTRACT_SEC_FIELDS_OPT_EXP
```

**⚠️ STOP**: Wait for user confirmation before cleanup.

If yes, execute:
```sql
DROP STAGE IF EXISTS {database}.{schema}.DEMO_SEC_FILING_STAGE;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_SEC_FILING_TRAIN;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_SEC_FILING_TEST;
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_EXTRACT_SEC_FIELDS(VARCHAR);
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_SEC_EXTRACTION_METRIC(VARCHAR, VARCHAR);
DROP TABLE IF EXISTS {database}.{schema}.DEMO_EXTRACT_SEC_FIELDS_EVAL_RESULTS;
DROP EXPERIMENT IF EXISTS {database}.{schema}.DEMO_EXTRACT_SEC_FIELDS_OPT_EXP;
```

### Step 10: Next Steps

Summarize the workflow: public SEC filings → PDF conversion → multimodal extraction function → custom composite metric → baseline evaluation → GEPA optimization → cost/quality Pareto analysis.

Explain to user:
```
Thanks for trying the SEC 10-K PDF Field Extraction demo!

Here's what you learned:
- **Created** a multimodal AI function that extracts structured fields from PDFs
- **Built** a custom composite metric that scores four fields independently
- **Evaluated** extraction accuracy against deterministic ground truth
- **Optimized** the prompt using GEPA to improve accuracy across document models

Key takeaways about document extraction with GEPA:

  Format precision matters: Generic prompts produce inconsistent formats
  (date as "September 28, 2024" vs "2024-09-28", state as "DE" vs
  "Delaware"). GEPA learns to embed explicit format rules by reflecting
  on per-field error feedback.

  Composite metrics unlock targeted optimization: By scoring each field
  separately, GEPA can identify which fields need the most attention
  and evolve prompts that address field-specific failure modes.

  Cost savings: Cheaper document models (gemini-2.5-flash-lite) with
  optimized prompts can approach the accuracy of expensive models with
  generic prompts.

Ready to build your own document extraction function? Just say
"create an AI function" and mention that you want to process PDFs.
```

## Key Cautions

- SEC filings are public domain; no license restrictions
- Ground truth is from the EDGAR API, not from the PDFs — any discrepancy between the API metadata and the document content (e.g., company name change between filing date and current API data) may cause false negatives
- Stage requires `ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')` for multimodal `TO_FILE()` access
- PDF rendering quality depends on the original HTML structure; some older filings may render with missing styles
- To regenerate the bundled dataset, run `make build-sec-data` (requires `playwright`); this produces `data.zip`

## Stopping Points

- ✋ Step 1: After introduction
- ✋ Step 2: After choosing database and schema
- ✋ Step 3: Before data loading (confirm filing count)
- ✋ Step 4: Before creating the extraction function
- ✋ Step 5: Before creating custom metric
- ✋ Step 6: Before evaluation
- ✋ Step 7: Before GEPA optimization
- ✋ Step 9: Before cleanup
