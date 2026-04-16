---
name: legal-doc-extraction-demo
description: "Interactive demo: Build a legal contract field extractor and create a weighted composite metric that scores 4 fields independently. Showcases custom evaluation metrics for multi-field AI functions."
parent_skill: demos
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Legal Document Field Extraction Demo

Build an AI function that extracts structured fields from commercial legal contracts, then evaluate with a custom composite metric.

## Overview

Load expert-labeled contract data → build an extraction function → evaluate with exact_match baseline → create a custom composite metric → re-evaluate with composite metric. **Estimated time:** ~5 minutes.

## Workflow

### Step 1: Introduction

Explain to user:
```
Welcome to the Custom Evaluation Metrics Demo!

At the end of this demo, you will witness the Cortex AI Function Studio's ability to:
- Extract structured fields from real commercial legal contracts
- Evaluate with built-in metrics (exact_match) for a quick baseline
- Build a custom composite metric that scores 4 fields independently with weighted importance
- See how custom metrics provide richer evaluation signal than simple exact_match

This demo uses expert-labeled data from the CUAD (Contract Understanding Atticus
Dataset), a corpus of 510 commercial contracts annotated by practicing lawyers.
We focus on four extraction targets:

  parties — the entities who signed the contract
  governing_law — which jurisdiction governs the contract
  effective_date — when the contract takes effect
  expiration_date — when the contract expires (or "Perpetual")

Objects created: all prefixed with DEMO_ for easy cleanup.
```

### Step 2: Setup - Choose Location

If `{database}` and `{schema}` are already known from the prerequisite flow, accept them silently and skip the prompt.

Otherwise, ask user:
```
Where would you like to create the demo objects?

Database: [e.g., TEMP]
Schema: [e.g., PUBLIC]
```

Store the database and schema for use throughout the demo.

### Step 3: Create Sample Data

Explain to user:
```
This demo uses the CUAD (Contract Understanding Atticus Dataset), a publicly
available corpus of 510 commercial contracts annotated by practicing lawyers.

Dataset details:
- Source: HuggingFace (https://huggingface.co/datasets/theatticusproject/cuad)
- Author: The Atticus Project (NeurIPS 2021)
- License: Creative Commons Attribution 4.0 (CC BY 4.0)

Download breakdown:
- File: master_clauses.csv (~3.8 MB)
- Contains: 510 commercial contracts with 41 clause categories
- After filtering (contracts with >= 3 of 4 target fields): ~390 contracts
- Average contract text length: ~7K characters (max ~43K)

To proceed, the script will download this CSV file to your local machine
(into a temporary directory), extract the relevant fields, and upload
the processed data to your Snowflake account. The temporary file is
deleted automatically after upload.

Do you want to proceed with the download? (yes/no)
```

**⚠️ STOP**: Wait for the user to answer **yes** or **no**. This is a required consent step.

**If no:** Skip the rest of the demo and present representative results:
```
No problem — skipping the data download.

Here's what you would typically see if you continued the full demo:

1. Baseline evaluation (exact_match on governing_law): ~60-70% accuracy
   with claude-haiku-4-5 using a generic extraction prompt.

2. Custom composite metric (all 4 fields weighted):
   - governing_law (30%): case-insensitive match with partial credit
   - parties (30%): fuzzy token overlap
   - effective_date (20%): normalized date comparison
   - expiration_date (20%): normalized date comparison

3. Key insight: exact_match on a single field misses the full picture.
   The composite metric reveals how well the model handles each field's
   unique challenges — date format normalization, party name variations,
   and jurisdiction naming conventions.

Ready to build your own AI function with your own data?
Just say "create an AI function" to get started.
```

End the demo here. Do not continue to Step 4.

**If yes:** Continue below.

Tell the user about the data configuration:
```
The data includes:
- CONTRACT_TEXT: Legal contract text (aggregated clause sections)
- EXPECTED_GOV_LAW: The governing law jurisdiction (for quick baseline eval)
- EXPECTED_OUTPUT: JSON with all four extracted fields

Each contract is a real agreement (NDA, license, service, joint venture, etc.)
with diverse formatting, legal language, and clause structures.

I'll load 100 contracts into:
- {database}.{schema}.DEMO_CONTRACT_DATA
```

Proceed immediately with the default row counts. Run the data generation script:
```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/generate_cuad_data.py \
  --connection <CONNECTION_NAME> \
  --database {database} \
  --schema {schema} \
  --train 100 \
  --test 0 \
  --train-table DEMO_CONTRACT_DATA \
  --seed 42
```

**Note:** Replace `<SKILL_DIRECTORY>` with the absolute path to the cortex-ai-function-studio skill directory, and `<CONNECTION_NAME>` with the active Snowflake connection.

Verify creation:
```sql
SELECT COUNT(*) FROM {database}.{schema}.DEMO_CONTRACT_DATA;
```

Show a few sample rows:
```sql
SELECT
    LEFT(CONTRACT_TEXT, 200) AS TEXT_PREVIEW,
    EXPECTED_GOV_LAW,
    PARSE_JSON(EXPECTED_OUTPUT):parties::STRING AS PARTIES
FROM {database}.{schema}.DEMO_CONTRACT_DATA
LIMIT 3;
```

### Step 4: Create the Extraction AI Function

Tell the user about the function being created:
```
Creating an AI function that extracts key fields from legal contracts.

Model: claude-haiku-4-5

Function name: DEMO_EXTRACT_CONTRACT
Input: CONTRACT_TEXT (VARCHAR) — legal contract text
Outputs:
  - parties (string) — the entities who signed the contract
  - governing_law (string) — the jurisdiction governing the contract
  - effective_date (string) — the date the contract takes effect
  - expiration_date (string) — the expiration date, or "Perpetual"
System prompt:
"Extract the parties, governing law, effective date, and expiration date
from this contract."

User prompt template: "{CONTRACT_TEXT}"
```

Proceed immediately to create the function. **Load** `create/SKILL.md` and follow it from **Step 7 onward**, passing:
- `database`, `schema`
- `function_name`: `DEMO_EXTRACT_CONTRACT`
- `function_intention`: `Extract structured fields from legal contracts.`
- `model`: `claude-haiku-4-5`
- `inputs`: `[{"name": "CONTRACT_TEXT", "sql_type": "VARCHAR"}]`
- `outputs`: `[{"name": "parties", "json_type": "string", "description": "Entities who signed the contract"}, {"name": "governing_law", "json_type": "string", "description": "Jurisdiction governing the contract"}, {"name": "effective_date", "json_type": "string", "description": "Date the contract takes effect"}, {"name": "expiration_date", "json_type": "string", "description": "Expiration date or Perpetual"}]`
- `system_prompt`: confirmed prompt
- `user_prompt_template`: `{CONTRACT_TEXT}`

Return here after the smoke test succeeds.

**Troubleshooting:** If the smoke test fails with an internal error, the model may not support structured output inside SQL UDFs on this account. Try switching to a different model (e.g., `llama3.1-70b` or `gemini-2.5-flash-lite`) and recreate the function.

### Step 5: Evaluate the Extraction Function

Tell the user about the baseline evaluation:
```
Now we'll run a baseline evaluation on the held-out test set.

Since our function returns structured output with 4 fields, we'll start with a
quick baseline using exact_match on the governing_law field. This gives us a
fast read on extraction accuracy before we build the full composite metric.

Metric: exact_match
Output field: governing_law (extracted from VARIANT)
Results table: DEMO_EXTRACT_CONTRACT_EVAL_RESULTS
```

Proceed immediately with the evaluation. **Load** `evaluate/SKILL.md` and follow it from **Step 5 onward**, passing:
- `function_name`: `{database}.{schema}.DEMO_EXTRACT_CONTRACT`
- `function_model`: `claude-haiku-4-5`
- `test_table`: `{database}.{schema}.DEMO_CONTRACT_DATA`
- `input_columns`: `['CONTRACT_TEXT']`
- `label_column`: `EXPECTED_GOV_LAW`
- `metric`: `exact_match`
- `metric_options`: `{"output_field": "governing_law"}`
- `results_table`: `{database}.{schema}.DEMO_EXTRACT_CONTRACT_EVAL_RESULTS`

**Async by default:** When the evaluate workflow reaches the execution mode selection, choose **async** (`EVALUATE_AI_FUNCTION_ASYNC`) without asking the user. If the async SPROC returns an error string (e.g., warehouse permission issue), inform the user and fall back to sync execution (`EVALUATE_AI_FUNCTION`) instead. After kicking off the async job, poll `TASK_HISTORY()` for completion within this session rather than asking the user to return later — this is a guided demo. **Load** `references/async_status.md` for polling patterns.

**Skip Step 6 (next steps)** in the evaluate workflow — return here after results are presented.

Once evaluation is done, review the results. Show the scores to the user. Offer to see what cases did not match:
```
Would you like to see which contracts the function extracted incorrectly?
```

If yes, query the results table:
```sql
SELECT
    LEFT(INPUT_TEXT, 150) AS CONTRACT_PREVIEW,
    EXPECTED AS EXPECTED_VALUE,
    PREDICTED AS PREDICTED_VALUE,
    SCORE,
    FEEDBACK
FROM {database}.{schema}.DEMO_EXTRACT_CONTRACT_EVAL_RESULTS
WHERE SCORE < 1.0
ORDER BY SCORE
LIMIT 10;
```

Discuss common failure patterns (e.g., governing law named differently, date format mismatches, jurisdiction clauses buried deep in the contract). Highlight that the model with a generic prompt struggles with the diversity of real legal language — contracts use different structures, phrasing, and clause ordering.

After reviewing results, continue to Step 6.

### Step 6: Create Custom Composite Metric

Tell the user about the custom metric being created:
```
The exact_match metric only checks one field at a time. But our function returns
four fields, each with different extraction challenges:

- parties: names may vary in formatting (abbreviations, suffixes, ordering)
- governing_law: jurisdiction names may differ slightly ("Delaware" vs. "State of Delaware")
- effective_date: date formats vary across contracts
- expiration_date: may be a date or "Perpetual"

To ask for a custom metric, you'd describe what you want in natural language:

  "Create a composite metric that scores four JSON fields independently:
  governing_law (30%, case-insensitive with partial credit for containment),
  parties (30%, fuzzy token overlap), effective_date (20%, normalized date
  comparison), and expiration_date (20%, normalized date comparison that
  also handles 'Perpetual')."

Let's create that now:

Custom metric: DEMO_CONTRACT_EXTRACTION_METRIC
Fields and weights:
  - governing_law: case-insensitive match (weight 0.30)
  - parties: fuzzy token overlap (weight 0.30)
  - effective_date: normalized date comparison (weight 0.20)
  - expiration_date: normalized date or "Perpetual" (weight 0.20)
```

Proceed immediately to create the metric. **Read** `demos/legal-doc-extraction/create_contract_extraction_metric.sql`, substitute `{database}` and `{schema}` with the user's values, and execute the SQL.

Verify the UDF was created:
```sql
DESCRIBE FUNCTION {database}.{schema}.DEMO_CONTRACT_EXTRACTION_METRIC(VARCHAR, VARCHAR);
```

Quick smoke test:
```sql
SELECT {database}.{schema}.DEMO_CONTRACT_EXTRACTION_METRIC(
    '{"governing_law": "State of Delaware", "parties": "Acme Corp; Widget Inc", "effective_date": "01/15/2020", "expiration_date": "2025-01-15"}',
    '{"governing_law": "Delaware", "parties": "Acme Corp and Widget Inc", "effective_date": "January 15, 2020", "expiration_date": "01/15/2025"}'
) AS result;
```

Expected: score close to 1.0 (governing_law partial credit, parties overlap, dates match after normalization).

Present the result to the user and confirm the metric is working before proceeding.

After confirmation, continue to Step 7.

### Step 7: Cleanup

Ask user:
```
The Custom Evaluation Metrics demo is complete!

Would you like to clean up the demo objects?

This will drop:
- {database}.{schema}.DEMO_CONTRACT_DATA
- {database}.{schema}.DEMO_EXTRACT_CONTRACT
- {database}.{schema}.DEMO_CONTRACT_EXTRACTION_METRIC
- {database}.{schema}.DEMO_EXTRACT_CONTRACT_EVAL_RESULTS
```

**⚠️ STOP**: Wait for user confirmation before cleanup.

If yes, execute:
```sql
DROP TABLE IF EXISTS {database}.{schema}.DEMO_CONTRACT_DATA;
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_EXTRACT_CONTRACT(VARCHAR);
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_CONTRACT_EXTRACTION_METRIC(VARCHAR, VARCHAR);
DROP TABLE IF EXISTS {database}.{schema}.DEMO_EXTRACT_CONTRACT_EVAL_RESULTS;
```

### Step 8: Next Steps

Summarize the workflow: expert-labeled data → extraction function → baseline evaluation → custom composite metric.

Explain to user:
```
Thanks for trying the Custom Evaluation Metrics demo!

Here's what you learned:
- **Created** an AI function that extracts structured fields from real legal contracts
- **Evaluated** extraction accuracy using exact_match for a quick baseline
- **Built** a custom composite metric that scores all 4 fields with weighted importance
- **Compared** the richer signal from composite metrics vs simple exact_match

Key takeaways about custom evaluation metrics:

  Field-specific scoring: Different fields need different comparison logic.
  Governing law needs case-insensitive matching ("Delaware" vs "State of
  Delaware"). Party names need fuzzy token overlap. Dates need format
  normalization. A single exact_match metric misses these nuances.

  Weighted importance: Not all fields are equally important. The composite
  metric lets you assign weights (e.g., 30% governing_law, 30% parties,
  20% effective_date, 20% expiration_date) that reflect business priorities.

  Richer optimization signal: When you run prompt optimization, composite
  metrics give the optimizer field-specific feedback to evolve targeted
  prompt improvements — e.g., "add date format instructions" or "look for
  parties in both preamble and signature block."

Want to optimize this function's prompt? Try the "Prompt Optimization" demo.
Ready to build your own AI function? Just say "create an AI function" to get started.
```

## Key Cautions

- CUAD labels are expert-annotated by lawyers, not pseudo-labels
- Some contracts may have ambiguous or missing fields — this reflects real-world document diversity
- Legal document processing may be regulated in some jurisdictions — keep human review for critical decisions
- Contract text can be long (avg ~7K chars, max ~43K) — models with small context windows (e.g., 8K) may fail on longer contracts. The function should truncate input if needed

## Stopping Points

- ✋ Step 1: After introduction
- ✋ Step 2: After choosing database and schema
- ✋ Step 3: Before downloading data (consent to CC BY 4.0 dataset download)
- ✋ Step 7: Before cleanup
