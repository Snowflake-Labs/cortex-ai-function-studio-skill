---
name: legal-doc-extraction-demo
description: "Interactive demo: Build a legal contract field extraction AI function using expert-labeled CUAD data, then evaluate and optimize across models via GEPA prompt optimization with a custom composite metric."
parent_skill: demos
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Legal Document Field Extraction Demo

Build an AI function that extracts structured fields from commercial legal contracts, then optimize across models via GEPA prompt optimization.

## Overview

Load expert-labeled contract data → build an extraction function → evaluate baseline → create a composite metric → run GEPA optimization → compare cost/quality trade-offs. Evaluation and optimization run async by default. **Estimated time:** 30-45 minutes (GEPA optimization accounts for most of this).

## Workflow

### Step 1: Introduction

Explain to user:
```
Welcome to the Legal Document Field Extraction Demo!

At the end of this demo, you will witness the Cortex AI Function Studio's ability to:
- Extract structured fields from real commercial legal contracts
- Evaluate extraction quality with a custom composite metric across 4 fields
- Optimize prompts using GEPA to dramatically improve smaller models
- Compare cost vs. quality trade-offs across models using a Pareto analysis
- Achieve strong-model accuracy at a fraction of the cost

This demo uses expert-labeled data from the CUAD (Contract Understanding Atticus
Dataset), a corpus of 510 commercial contracts annotated by practicing lawyers
for 41 clause categories. We focus on four high-value extraction targets:

Fields to extract:
- parties — the entities who signed the contract
- governing_law — which jurisdiction governs the contract
- effective_date — when the contract takes effect
- expiration_date — when the contract expires (or "Perpetual")

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

3. GEPA optimization results (typical, may vary):
   - claude-haiku-4-5: ~75-85% composite score (up from ~55-65% baseline)
   - gemini-2.5-flash: ~80-90% composite score
   - gemini-2.5-flash-lite: ~70-80% composite score
   - llama3.1-70b: ~65-75% composite score

4. Key insight: GEPA learns contract-specific extraction patterns through
   reflection — e.g., normalizing date formats, finding parties in preambles
   vs. signature blocks, handling "State of X" vs "X" for governing law.
   Optimized smaller models can approach strong-model accuracy at a fraction
   of the cost.

Ready to build your own AI function with your own data?
Just say "create an AI function" to get started.
```

End the demo here. Do not continue to Step 4.

**If yes:** Continue below.

Present the data configuration:
```
The data includes:
- CONTRACT_TEXT: Legal contract text (aggregated clause sections)
- EXPECTED_GOV_LAW: The governing law jurisdiction (for quick baseline eval)
- EXPECTED_OUTPUT: JSON with all four extracted fields

Each contract is a real agreement (NDA, license, service, joint venture, etc.)
with diverse formatting, legal language, and clause structures.

How many rows would you like in each table?

Training rows: [default: 25] - Used for optimization
Test rows: [default: 25] - Used for evaluation

Note: The CUAD dataset yields ~390 qualifying contracts (with >= 3 of 4 target
fields populated). The script adjusts proportionally if the total exceeds
what's available. 25 rows is enough for a compelling demo; increase to
50-100 for more statistically robust results.

I'll create these tables:
- {database}.{schema}.DEMO_CONTRACT_TRAIN
- {database}.{schema}.DEMO_CONTRACT_TEST
```

**⚠️ STOP**: Wait for user to specify row counts (or confirm defaults) before proceeding.

Run the data generation script with the specified row counts:
```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/generate_cuad_data.py \
  --connection <CONNECTION_NAME> \
  --database {database} \
  --schema {schema} \
  --train {train_rows} \
  --test {test_rows} \
  --seed 42
```

**Note:** Replace `<SKILL_DIRECTORY>` with the absolute path to the cortex-ai-function-studio skill directory, and `<CONNECTION_NAME>` with the active Snowflake connection.

Verify creation:
```sql
SELECT COUNT(*) FROM {database}.{schema}.DEMO_CONTRACT_TRAIN;
SELECT COUNT(*) FROM {database}.{schema}.DEMO_CONTRACT_TEST;
```

Show a few sample rows:
```sql
SELECT
    LEFT(CONTRACT_TEXT, 200) AS TEXT_PREVIEW,
    EXPECTED_GOV_LAW,
    PARSE_JSON(EXPECTED_OUTPUT):parties::STRING AS PARTIES
FROM {database}.{schema}.DEMO_CONTRACT_TEST
LIMIT 3;
```

### Step 4: Create the Extraction AI Function

Present the function configuration:
```
Now we'll create an AI function that extracts key fields from legal contracts.

Default model: claude-haiku-4-5

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

**⚠️ STOP**: Wait for user confirmation before creating the function.

**Load** `create/SKILL.md` and follow it from **Step 7 onward**, passing:
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

Present the evaluation configuration:
```
Let's evaluate how well the function extracts fields on the held-out test set.

Since our function returns structured output with 4 fields, we'll start with a
quick baseline using exact_match on the governing_law field. This gives us a
fast read on extraction accuracy before we build the full composite metric.

Default metric: exact_match
Output field: governing_law (extracted from VARIANT)
Results table: DEMO_EXTRACT_CONTRACT_EVAL_RESULTS
```

**⚠️ STOP**: Wait for user confirmation before running evaluation.

**Load** `evaluate/SKILL.md` and follow it from **Step 5 onward**, passing:
- `function_name`: `{database}.{schema}.DEMO_EXTRACT_CONTRACT`
- `function_model`: `claude-haiku-4-5`
- `test_table`: `{database}.{schema}.DEMO_CONTRACT_TEST`
- `input_columns`: `['CONTRACT_TEXT']`
- `label_column`: `EXPECTED_GOV_LAW`
- `metric`: `exact_match`
- `metric_options`: `OBJECT_CONSTRUCT('output_field', 'governing_law')`
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

Explain to user:
```
The exact_match metric only checks one field at a time. But our function returns
four fields, each with different extraction challenges:

- parties: names may vary in formatting (abbreviations, suffixes, ordering)
- governing_law: jurisdiction names may differ slightly ("Delaware" vs. "State of Delaware")
- effective_date: date formats vary across contracts
- expiration_date: may be a date or "Perpetual"

We'll create a custom composite metric that scores all four fields, giving GEPA
richer feedback to evolve better prompts.

Custom metric: DEMO_CONTRACT_EXTRACTION_METRIC
Fields and weights:
  - governing_law: case-insensitive match (weight 0.30)
  - parties: fuzzy token overlap (weight 0.30)
  - effective_date: normalized date comparison (weight 0.20)
  - expiration_date: normalized date or "Perpetual" (weight 0.20)
```

**⚠️ STOP**: Wait for user confirmation before creating the custom metric.

**Load** `references/custom_metrics.md` and follow the **composite metric** workflow, passing this field configuration:

- `metric_name`: `DEMO_CONTRACT_EXTRACTION_METRIC`
- `database`, `schema`: from context
- `metric_description`: Composite metric that scores legal contract field extraction across four fields. Both expected and predicted are JSON strings with keys: `parties`, `governing_law`, `effective_date`, `expiration_date`.

**Field configuration to pass:**

| Field | Check | Weight | Scoring details |
|-------|-------|--------|-----------------|
| `governing_law` | case-insensitive match with partial credit | 0.30 | 1.0 if exact case-insensitive match, 0.5 if one string contains the other (e.g., "Delaware" vs "State of Delaware"), 0.0 otherwise |
| `parties` | fuzzy token overlap | 0.30 | Split both values on commas, semicolons, ampersands, and whitespace. Score = fraction of expected tokens found in predicted tokens. Give 0.8 if one string fully contains the other. 1.0 if exact match. |
| `effective_date` | normalized date comparison | 0.20 | Dates appear in varied formats across contracts (e.g., "01/15/2020", "January 15, 2020", "2020-01-15"). Parse common date formats into a canonical form before comparing. Handle special values like "N/A" or empty strings. Score 1.0 if normalized dates match, 0.0 otherwise. |
| `expiration_date` | normalized date comparison | 0.20 | Same as effective_date, but also handle "Perpetual" as a valid value that should match exactly (case-insensitive). |

**Important:** The date normalization is critical for fair scoring. Without it, semantically identical dates in different formats (e.g., "01/15/2020" vs "January 15, 2020") would be scored as mismatches, unfairly penalizing correct extractions.

After the custom metric workflow completes (code review, test cases, UDF creation, smoke test), return here and continue to Step 7.

### Step 7: Optimize with GEPA

Present the optimization configuration:
```
Now we'll use GEPA to optimize the extraction prompt.

GEPA (Genetic-Pareto Algorithm) evolves the system prompt through multiple
generations:
1. Scores prompt variations against training examples
2. Reflects on failures — analyzing WHY specific extractions went wrong
   (e.g., "date format varies across contracts", "parties are listed in
   the preamble in some contracts and the signature block in others")
3. Generates new prompt variations informed by those reflections
4. Keeps only Pareto-optimal performers (best quality at each cost level)

This reflection-based evolution is what distinguishes GEPA from random search.
The optimizer learns from extraction failures and produces increasingly specific
instructions for handling the diversity of real legal contract formats.

Please confirm or modify any settings you'd like to change:

Auto budget: medium (~10-20 minutes)
Metric: contract_extraction_metric (custom composite)
Custom metric UDF: {database}.{schema}.DEMO_CONTRACT_EXTRACTION_METRIC
Label column: EXPECTED_OUTPUT
Tracking table: DEMO_EXTRACT_CONTRACT_OPT_TRACKING
Models: ['claude-haiku-4-5', 'llama3.1-70b', 'gemini-2.5-flash-lite', 'gemini-2.5-flash']

Options:
1. Yes - Run GEPA optimization with these settings
2. Modify - Change settings before running
3. No - Skip to cleanup
```

**⚠️ STOP**: Wait for user confirmation before starting optimization.

If user chooses No, skip to Step 9.

If yes, **load** `optimize/SKILL.md` and follow it from **Step 6 onward**, passing:
- `function_name`: `{database}.{schema}.DEMO_EXTRACT_CONTRACT`
- `training_table`: `{database}.{schema}.DEMO_CONTRACT_TRAIN`
- `test_table`: `{database}.{schema}.DEMO_CONTRACT_TEST`
- `input_columns`: `['CONTRACT_TEXT']`
- `label_column`: `EXPECTED_OUTPUT`
- `metric`: `contract_extraction_metric`
- `custom_metric_udf`: `{database}.{schema}.DEMO_CONTRACT_EXTRACTION_METRIC`
- `models`: `['claude-haiku-4-5', 'llama3.1-70b', 'gemini-2.5-flash-lite', 'gemini-2.5-flash']`
- `reflection_model`: `claude-opus-4-6` (or strongest available Claude-family model)
- `auto_budget`: `medium`
- `tracking_table`: `{database}.{schema}.DEMO_EXTRACT_CONTRACT_OPT_TRACKING`

**Async by default:** When the optimize workflow reaches the execution mode selection, choose **async** (`OPTIMIZE_AI_FUNCTION_ASYNC`) without asking the user. If the async SPROC returns an error string (e.g., warehouse permission issue), inform the user and fall back to sync execution (`OPTIMIZE_AI_FUNCTION`) with `timeout_seconds: 14400` instead. After kicking off the async job, poll `TASK_HISTORY()` for completion within this session rather than asking the user to return later — this is a guided demo. **Load** `references/async_status.md` for polling patterns.

**Skip Step 8 (next steps)** in the optimize workflow — return here after results are presented and the user has decided whether to apply the optimized prompt.

### Step 8: Summarize GEPA Results

After optimization completes, present the results and compare the before and after scores.

**8.1. Show the optimization journey:**

Query the tracking table to show how GEPA evolved the prompt:
```sql
SELECT
    ROW_NUMBER() OVER (PARTITION BY MODEL_NAME ORDER BY CREATED_AT) AS CANDIDATE_IDX,
    MODEL_NAME,
    METRIC_SCORE AS SCORE,
    LEFT(PROMPT_TEXT, 120) AS PROMPT_PREVIEW
FROM {database}.{schema}.DEMO_EXTRACT_CONTRACT_OPT_TRACKING
WHERE METRIC_SCORE IS NOT NULL
ORDER BY MODEL_NAME, CREATED_AT;
```

Highlight how the scores improve across candidates. Point out specific prompt evolution — early candidates use generic extraction language, later candidates include specific instructions for handling date formats, party name variations, and contract structure nuances. This is GEPA's reflection at work: the optimizer observed extraction failures and proposed targeted fixes.

**8.2. Compare baseline vs. optimized:**

Show each model's best optimized score against the baseline:
```sql
SELECT MODEL_NAME, MAX(METRIC_SCORE) AS BEST_SCORE
FROM {database}.{schema}.DEMO_EXTRACT_CONTRACT_OPT_TRACKING
WHERE METRIC_SCORE IS NOT NULL
GROUP BY MODEL_NAME
ORDER BY BEST_SCORE DESC;
```

**8.3. Pareto analysis for cost/quality:**

Calculate relative cost using the Pareto filter script. Compute character lengths from the test table:

```sql
SELECT
    AVG(LENGTH(CONTRACT_TEXT)) AS avg_input_chars,
    AVG(LENGTH(EXPECTED_OUTPUT)) AS avg_output_chars
FROM {database}.{schema}.DEMO_CONTRACT_TEST;
```

```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/filter_pareto.py \
    --json '[{"model": "model1", "score": 0.85}, ...]' \
    --prompt-chars {prompt_chars} --avg-input-chars {avg_input_chars} --avg-output-chars {avg_output_chars} \
    --seed-score {baseline_score} --format table
```

**8.4. Summarize key findings:**

Present the headline result:

If the best optimized model matches or beats a strong reference baseline, highlight:
```
Key result: {best_model} achieves {optimized_score}% composite extraction accuracy
at ~{cost_ratio}x cheaper than {strong_reference}. GEPA closed the quality gap
through prompt optimization alone — no model change, no fine-tuning, no additional data.
```

Explain what GEPA learned:
```
What GEPA did:
- Reflected on extraction failures across {total_candidates} prompt variations
- Learned contract-specific patterns (e.g., date formats need explicit
  normalization instructions, parties are best found in the preamble
  and signature block, governing law clauses use jurisdiction-specific language)
- Evolved the prompt from generic extraction language to targeted instructions
  that handle the formatting diversity of real commercial contracts
```

If the most expensive model is dominated on the Pareto frontier (a cheaper model has equal or higher score), note that the expensive model is no longer the best option at any price point. If no cost-effective model beats the strong reference, note the remaining gap and suggest heavier optimization budgets or different models.

Continue to Step 9.

### Step 9: Cleanup

Ask user:
```
The Legal Document Field Extraction demo is complete!

Would you like to clean up the demo objects?

This will drop:
- {database}.{schema}.DEMO_CONTRACT_TRAIN
- {database}.{schema}.DEMO_CONTRACT_TEST
- {database}.{schema}.DEMO_EXTRACT_CONTRACT
- {database}.{schema}.DEMO_CONTRACT_EXTRACTION_METRIC
- {database}.{schema}.DEMO_EXTRACT_CONTRACT_EVAL_RESULTS
- {database}.{schema}.DEMO_EXTRACT_CONTRACT_OPT_TRACKING
```

**⚠️ STOP**: Wait for user confirmation before cleanup.

If yes, execute:
```sql
DROP TABLE IF EXISTS {database}.{schema}.DEMO_CONTRACT_TRAIN;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_CONTRACT_TEST;
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_EXTRACT_CONTRACT(VARCHAR);
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_CONTRACT_EXTRACTION_METRIC(VARCHAR, VARCHAR);
DROP TABLE IF EXISTS {database}.{schema}.DEMO_EXTRACT_CONTRACT_EVAL_RESULTS;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_EXTRACT_CONTRACT_OPT_TRACKING;
```

### Step 10: Next Steps

Summarize the workflow: expert-labeled data → extraction function → baseline evaluation → custom composite metric → GEPA optimization → cost/quality Pareto analysis.

If the optimized model beat baseline, reiterate cost savings: `{best_model}` achieves `{optimized_score}%` at ~`{cost_ratio}x` cheaper than `{strong_reference}`.

Explain to user:
```
Thanks for trying the Legal Document Field Extraction demo!

Here's what you learned:
- **Created** an AI function that extracts structured fields from real legal contracts
- **Evaluated** extraction accuracy using exact_match for a quick baseline
- **Built** a custom composite metric that scores all 4 fields with weighted importance
- **Optimized** the prompt using GEPA to improve extraction quality across smaller models

Key takeaways about GEPA optimization:

  Reflection-based evolution: GEPA doesn't search randomly. It analyzes WHY
  specific extractions failed and proposes targeted prompt improvements. For
  legal documents, it learned to handle date format variations, party name
  conventions, and jurisdiction clause patterns that a generic prompt misses.

  Cost savings: The Pareto frontier shows which models offer the best
  quality-per-dollar. When an optimized smaller model matches the strong
  reference, switching to it saves cost with no quality penalty.

  Custom metrics matter: The composite metric gave GEPA rich, multi-field
  feedback. Optimizing against a single field would miss improvements on
  the others — the composite metric lets GEPA balance all extraction targets.

  When to use GEPA: Whenever baseline accuracy is disappointing, especially
  with smaller models. GEPA can close the gap through prompt optimization
  alone — no fine-tuning required.

The full create → evaluate → optimize workflow works the same way for any
extraction, classification, or transformation task. Custom metrics let you
tailor evaluation to your specific needs.

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
- ✋ Step 3: Before loading data (confirm row counts)
- ✋ Step 4: Before creating the extraction function
- ✋ Step 5: Before evaluation
- ✋ Step 6: Before creating custom metric
- ✋ Step 7: Before GEPA optimization
- ✋ Step 9: Before cleanup
