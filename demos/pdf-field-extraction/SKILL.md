---
name: pdf-field-extraction-demo
description: "Interactive demo: Extract structured fields from SEC 10-K filing PDFs using multimodal AI, create a custom composite metric for per-field scoring, and evaluate extraction accuracy with per-field analysis."
parent_skill: demos
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# SEC 10-K PDF Field Extraction Demo

Build a multimodal AI function that extracts structured metadata from SEC 10-K filing PDFs, then evaluate with a custom composite metric for per-field analysis.

## Overview

Load real SEC EDGAR filings → build an extraction function → create a custom composite metric → evaluate extraction accuracy → analyze per-field results. This is a **multimodal document** demo: the AI function reads PDFs from a Snowflake stage using `TO_FILE()`. **Estimated time:** ~10 minutes.

**Ground truth** is sourced from the EDGAR submissions API (company metadata), NOT from parsing the PDFs — ensuring 100% deterministic accuracy.

## Workflow

### Step 1: Introduction

Explain to user:
```
Welcome to the SEC 10-K PDF Field Extraction Demo!

This demo showcases multimodal document extraction with a custom
composite metric and per-field evaluation:

1. **Load Data** — Upload real SEC 10-K filing cover pages (PDFs) to a stage
2. **Build the Function** — Create a multimodal extraction function
3. **Custom Metric** — Build a weighted composite metric that scores four
   fields independently (company name, date, EIN, state)
4. **Evaluate** — Run the function against test data using the custom metric
5. **Analyze** — Inspect per-field results to understand extraction
   strengths and failure modes

The custom composite metric is key: by scoring each field independently,
you get per-field visibility into extraction quality that a simple
exact_match on the whole output would miss (e.g., "company name fuzzy
mismatch due to abbreviation" vs "all other fields perfect").

Objects created: all prefixed with DEMO_ for easy cleanup.
```

### Step 2: Setup - Choose Location

Ask user:
```
Where would you like to create the demo objects?

Database: [e.g., TEMP]
Schema: [e.g., PUBLIC]
```

Store the database and schema for use throughout the demo.

### Step 3: Dataset & Data Loading

Explain to user:
```
This demo uses pre-bundled PDF files converted from real SEC EDGAR 10-K
filing cover pages. The script uploads them to a Snowflake stage and
creates labeled train/test tables.

Source:  SEC EDGAR (https://www.sec.gov/edgar)
Data:   Public filings from ~80 well-known US companies across industries
License: SEC filings are public domain

The script will:
1. Extract pre-bundled PDFs from the skill's data.zip archive
2. Upload them to an SSE-encrypted Snowflake stage: DEMO_SEC_FILING_STAGE
3. Create stratified train/test tables with ground truth from the manifest
```

**⚠️ STOP**: Wait for user confirmation before proceeding.

Run the data generation script:
```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/generate_sec_filing_data.py \
  --connection <CONNECTION_NAME> \
  --database {database} \
  --schema {schema}
```

**Note:** Replace `<SKILL_DIRECTORY>` with the absolute path to the cortex-ai-function-studio skill directory, and `<CONNECTION_NAME>` with the active Snowflake connection.

Verify creation:
```sql
SELECT 'TRAIN' AS SPLIT, COUNT(*) AS ROW_COUNT
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

### Step 5: Create Custom Composite Metric

Explain to user:
```
Before evaluation, we'll create a custom composite metric that scores
each extracted field independently. This gives you per-field visibility
into extraction quality that a simple exact_match would miss.

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
| `company_name` | fuzzy_match | 0.30 | Use `SequenceMatcher` on lowered strings. Treat score >= 0.9 as "match" in feedback. |
| `report_date` | exact_match | 0.25 | Compare YYYY-MM-DD strings exactly. |
| `irs_ein` | normalized exact match | 0.25 | Strip all non-digit characters before comparing (so "94-2404110" equals "942404110"). |
| `state_of_incorporation` | case-insensitive match with fuzzy fallback | 0.20 | First try case-insensitive exact match. If that fails, use `SequenceMatcher` and treat score >= 0.85 as a match (to handle minor spelling variations). |

Follow the custom_metrics workflow from **Step 2 onward** (write code, test, create UDF). Return here after the metric UDF is created and smoke-tested.

### Step 6: Evaluate the Extraction Function

Present the evaluation configuration:
```
Now we'll evaluate the extraction function against the test set using
the custom composite metric. This scores every row across all four
fields and saves detailed per-row results for analysis.

Please confirm or modify any settings:

Metric: sec_extraction_metric (custom composite)
Custom metric UDF: {database}.{schema}.DEMO_SEC_EXTRACTION_METRIC
Test table: {database}.{schema}.DEMO_SEC_FILING_TEST
Label column: EXPECTED_OUTPUT
Experiment: auto-generated per evaluation (run_id)

Options:
1. Yes - Run evaluation with these settings
2. Modify - Change settings before running
3. No - Skip to cleanup
```

**⚠️ STOP**: Wait for user confirmation before running evaluation.

If user chooses No, skip to Step 8.

If yes, **load** `evaluate/SKILL.md` and follow it from **Step 4 onward** (Run Evaluation), passing:
- `function_name`: `{database}.{schema}.DEMO_EXTRACT_SEC_FIELDS`
- `function_model`: `gemini-2.5-flash`
- `test_table`: `{database}.{schema}.DEMO_SEC_FILING_TEST`
- `input_columns`: `['FILE_PATH']`
- `label_column`: `EXPECTED_OUTPUT`
- `metric_name`: `sec_extraction_metric`
- `custom_metric_udf`: `{database}.{schema}.DEMO_SEC_EXTRACTION_METRIC`

The evaluation run will auto-create an experiment named after its `run_id`. After it returns, capture `experiment_name` from the JSON output for the queries below.

**Skip Step 6 (next steps)** in the evaluate workflow — return here after results are presented.

### Step 7: Analyze Results

After evaluation completes, walk the user through the per-field results.

**7.1. Show the overall score and score distribution** (per-row details live in the per-evaluation experiment's `eval_detail.json`). First create the JSON file format (required — inline `(TYPE => JSON)` isn't supported on SnowURL paths):

```sql
CREATE OR REPLACE TEMPORARY FILE FORMAT eval_detail_json_fmt
  TYPE = JSON
  STRIP_OUTER_ARRAY = TRUE;

SELECT
    COUNT(*) AS TOTAL_ROWS,
    ROUND(AVG($1:metric_score::FLOAT), 3) AS AVG_SCORE,
    SUM(CASE WHEN $1:metric_score::FLOAT = 1.0 THEN 1 ELSE 0 END) AS PERFECT_ROWS,
    SUM(CASE WHEN $1:metric_score::FLOAT < 1.0 THEN 1 ELSE 0 END) AS IMPERFECT_ROWS
FROM 'snow://experiment/{experiment_name}/versions/EVAL/eval_detail.json'
(FILE_FORMAT => eval_detail_json_fmt);
```

**7.2. Offer to inspect failures:**

```
Would you like to see which filings the function extracted incorrectly?
```

If yes, query the artifact directly:
```sql
SELECT
    LEFT($1:input_text::STRING, 60) AS FILE,
    ROUND($1:metric_score::FLOAT, 3) AS SCORE,
    $1:metric_feedback::STRING AS FEEDBACK
FROM 'snow://experiment/{experiment_name}/versions/EVAL/eval_detail.json'
(FILE_FORMAT => eval_detail_json_fmt)
WHERE $1:metric_score::FLOAT < 1.0
ORDER BY SCORE
LIMIT 15;
```

> Querying `'snow://experiment/...'` requires the server-side parameter `ENABLE_EXPERIMENT_SNOWURL_READ_PATH_RESOLUTION` to be enabled. The path is a string literal (not `@stage`) and must be paired with a **named** FILE FORMAT.

**7.3. Discuss failure patterns:**

Analyze the feedback column to identify common themes. Typical patterns for this dataset:
- **Company name abbreviations vs. full names**: Ground truth from EDGAR API uses abbreviated forms ("FEDEX CORP") while the model extracts the full legal name from the cover page ("FedEx Corporation"). The model is often more correct than the ground truth.
- **Multi-registrant filings**: Some filings list parent company + subsidiaries; the model may extract the wrong entity.
- **Ground truth noise**: The EDGAR submissions API metadata doesn't always match what's printed on the PDF cover page — this creates a ceiling on achievable accuracy.

**7.4. Summarize key findings:**

```
Key findings:

1. The model achieves ~{avg_score:.0%} composite extraction accuracy
   across {total_rows} SEC 10-K filings.

2. Most errors are in the company_name field — the model extracts the
   full legal name from the PDF while the EDGAR API ground truth often
   uses abbreviated forms. The report_date, irs_ein, and
   state_of_incorporation fields are nearly perfect.

3. The custom composite metric was essential here: a simple exact_match
   on the full JSON output would score ~{perfect_pct:.0%} (only perfect
   matches), while the composite metric gives partial credit and reveals
   that individual field accuracy is much higher.

Why composite metrics matter for multi-field extraction:
- Different fields need different comparison logic (fuzzy match for
  names, normalized match for EINs, exact match for dates)
- Per-field feedback pinpoints which extractions need improvement
- Weighted scores reflect business priorities (company name matters
  more than EIN format)
```

### Step 8: Cleanup

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
- The per-evaluation experiment ({experiment_name})
```

**⚠️ STOP**: Wait for user confirmation before cleanup.

If yes, execute:
```sql
DROP STAGE IF EXISTS {database}.{schema}.DEMO_SEC_FILING_STAGE;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_SEC_FILING_TRAIN;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_SEC_FILING_TEST;
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_EXTRACT_SEC_FIELDS(VARCHAR);
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_SEC_EXTRACTION_METRIC(VARCHAR, VARCHAR);
DROP EXPERIMENT IF EXISTS {database}.{schema}.{experiment_name};
```

### Step 9: Next Steps

Explain to user:
```
Thanks for trying the SEC 10-K PDF Field Extraction demo!

Here's what you learned:
- **Created** a multimodal AI function that extracts structured fields from PDFs
- **Built** a custom composite metric that scores four fields independently
- **Evaluated** extraction accuracy and analyzed per-field results

Key takeaways about document extraction with custom metrics:

  Composite metrics reveal per-field quality: A simple exact_match on
  the whole JSON output gives a binary pass/fail. The composite metric
  breaks this down — you can see that date and EIN extraction is nearly
  perfect while company name matching is the weak spot.

  Different fields need different comparison logic: Fuzzy matching for
  company names (handles abbreviations), normalized matching for EINs
  (strips formatting), exact matching for dates. One-size-fits-all
  metrics miss these nuances.

  Ground truth quality matters: The EDGAR API metadata doesn't always
  match what's printed on the PDF cover page. Understanding ground
  truth noise is essential for interpreting evaluation results fairly.

Want to improve extraction accuracy? Try refining your prompt, adjusting
the composite metric weights, or experimenting with different models.

Ready to build your own document extraction function? Just say
"create an AI function" and mention that you want to process PDFs.
```

## Key Cautions

- SEC filings are public domain; no license restrictions
- Ground truth is from the EDGAR API, not from the PDFs — any discrepancy between the API metadata and the document content may cause false negatives
- Stage requires `ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')` for multimodal `TO_FILE()` access
- PDF rendering quality depends on the original HTML structure; some older filings may render with missing styles
- To regenerate the bundled dataset, run `make build-sec-data` (requires `playwright`); this produces `data.zip`

## Stopping Points

- ✋ Step 1: After introduction
- ✋ Step 2: After choosing database and schema
- ✋ Step 3: Before data loading
- ✋ Step 4: Before creating the extraction function
- ✋ Step 5: Before creating custom metric
- ✋ Step 6: Before evaluation
- ✋ Step 8: Before cleanup
