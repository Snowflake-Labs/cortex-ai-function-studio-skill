---
name: insurance-claim-routing-demo
description: "Interactive demo: Build an insurance claim routing AI function using pseudo-label generation, then evaluate and optimize cheaper models against a Claude Opus teacher baseline."
parent_skill: demos
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Insurance Claim Routing Demo

Build an AI function that routes insurance claims using pseudo-labeled training data.

## Overview

Seed input-only data → pseudo-label with a strong teacher → build a cheap student function → evaluate → optimize. Evaluation and optimization run async by default. **Estimated time:** 10-20 minutes.

## Workflow

### Step 1: Introduction

Explain to user:
```
Welcome to the Insurance Claim Routing Demo!

At the end of this demo, you will witness the Cortex AI Function Studio's ability to:
- Generate pseudo-labels using a strong teacher model
- Produce structured JSON outputs with multiple fields (route, citation, confidence)
- Use a custom composite evaluation metric with weighted field scoring
- Optimize prompts across multiple models using GEPA with the custom metric
- Compare cost vs. quality trade-offs across cheaper student models using a Pareto analysis

This demo starts with unlabeled claim intake records. We will use a strong
teacher model to create routing labels, then measure how closely cheaper models
can reproduce those decisions before and after optimization.

Claim routes:
- fast_track_auto
- property_damage
- injury_review
- fraud_review
- documentation_needed
- manual_specialist_review

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

### Step 3: Create Input-Only Sample Data

Explain to user:
```
I'll load a pre-generated set of 500 realistic insurance claim intake records:
auto collisions, property damage, theft, injury, fraud, and documentation cases.

Columns:
- CLAIM_SUMMARY  (pre-generated, unique per row)
- INCIDENT_CHANNEL
- CUSTOMER_SEGMENT

I'll create:
- {database}.{schema}.DEMO_CLAIMS_UNLABELED
```

**⚠️ STOP**: Wait for user to confirm before loading seed data.

**Load** `seed_claims_unlabeled.sql` and execute it, substituting `{database}` and `{schema}` with the user's chosen values. This creates and populates the table with 500 pre-generated claim summaries.

Verify creation and confirm summaries are diverse:
```sql
SELECT COUNT(*) FROM {database}.{schema}.DEMO_CLAIMS_UNLABELED;

SELECT LEFT(CLAIM_SUMMARY, 150) AS SUMMARY_PREVIEW, INCIDENT_CHANNEL, CUSTOMER_SEGMENT
FROM {database}.{schema}.DEMO_CLAIMS_UNLABELED
LIMIT 5;
```

### Step 4: Pseudo-Label with Teacher Model

Present the pseudo-label configuration:
```
Now we'll use a strong teacher model to label each claim intake row.

Default teacher model: claude-opus-4-6 (or the strongest available Claude Opus variant)
Fallback: use the strongest available Claude-family model in the account

Source table: {database}.{schema}.DEMO_CLAIMS_UNLABELED
Input columns: CLAIM_SUMMARY, INCIDENT_CHANNEL, CUSTOMER_SEGMENT
Output fields:
  - claim_route (string) — routing category
  - citation (string) — excerpt from the claim summary justifying the route
  - confidence (number) — confidence score 0.0-1.0
Destination table: {database}.{schema}.DEMO_CLAIMS_LABELED
Preview rows: 20
```

If the default teacher model is unavailable, **load** `references/model_selection.md` and choose the strongest Claude-family option.

**MANDATORY:** Do not synthesize labels ad hoc in this demo. **Load** `synthetic-data/SKILL.md` and follow the **Pseudo-Label Input-Only Tables** workflow.

Pass this context into that workflow:
- `source_table`: `{database}.{schema}.DEMO_CLAIMS_UNLABELED`
- `input_columns`: `['CLAIM_SUMMARY', 'INCIDENT_CHANNEL', 'CUSTOMER_SEGMENT']`
- `output_table`: `{database}.{schema}.DEMO_CLAIMS_LABELED`
- `model`: `{teacher_model}`
- `task_description`: `Classify each claim intake row into exactly one routing category: fast_track_auto, property_damage, injury_review, fraud_review, documentation_needed, or manual_specialist_review. Also provide a short citation (an excerpt from the claim summary that justifies your routing decision) and a confidence score between 0.0 and 1.0.`
- `output_schema`: `{"properties": {"claim_route": {"type": "string", "description": "One of: fast_track_auto, property_damage, injury_review, fraud_review, documentation_needed, manual_specialist_review"}, "citation": {"type": "string", "description": "Short excerpt from the claim summary justifying the route"}, "confidence": {"type": "number", "description": "Confidence score between 0.0 and 1.0"}}}`
- `preview_rows`: `20`

The label-synthesis workflow must:
- preview labels first
- show `EXPECTED` values for review
- allow revise/regenerate if needed
- run the full overwrite labeling pass only after explicit approval
- continue with `EXPECTED` as the default label column

**⚠️ STOP**: Wait for the label-synthesis preview and explicit user approval before the full labeling run.

Show the label distribution and a sample of structured outputs:
```sql
SELECT EXPECTED:claim_route::STRING AS CLAIM_ROUTE, COUNT(*) AS ROWS
FROM {database}.{schema}.DEMO_CLAIMS_LABELED
GROUP BY 1
ORDER BY ROWS DESC;

SELECT
    EXPECTED:claim_route::STRING AS ROUTE,
    LEFT(EXPECTED:citation::STRING, 80) AS CITATION_PREVIEW,
    EXPECTED:confidence::FLOAT AS CONFIDENCE
FROM {database}.{schema}.DEMO_CLAIMS_LABELED
LIMIT 5;
```

Flatten and split the labeled data. We keep two label columns:
- `EXPECTED_OUTPUT` — just the route string, used for exact_match evaluation in Step 6
- `EXPECTED_JSON` — full JSON with all 3 fields, used for composite metric optimization in Step 7

```sql
CREATE OR REPLACE TEMPORARY TABLE DEMO_CLAIMS_SPLIT_TEMP AS
SELECT
    CLAIM_SUMMARY,
    INCIDENT_CHANNEL,
    CUSTOMER_SEGMENT,
    EXPECTED:claim_route::STRING AS EXPECTED_OUTPUT,
    EXPECTED::STRING AS EXPECTED_JSON,
    ROW_NUMBER() OVER (ORDER BY RANDOM(42)) AS _split_rn
FROM {database}.{schema}.DEMO_CLAIMS_LABELED;

SET split_point = (
    SELECT FLOOR(COUNT(*) * 0.6)
    FROM DEMO_CLAIMS_SPLIT_TEMP
);

CREATE OR REPLACE TABLE {database}.{schema}.DEMO_CLAIMS_TRAIN AS
SELECT * EXCLUDE (_split_rn)
FROM DEMO_CLAIMS_SPLIT_TEMP
WHERE _split_rn <= $split_point;

CREATE OR REPLACE TABLE {database}.{schema}.DEMO_CLAIMS_TEST AS
SELECT * EXCLUDE (_split_rn)
FROM DEMO_CLAIMS_SPLIT_TEMP
WHERE _split_rn > $split_point;

DROP TABLE IF EXISTS DEMO_CLAIMS_SPLIT_TEMP;
```

Verify the split:
```sql
SELECT 'TRAIN' AS SPLIT, COUNT(*) AS ROWS
FROM {database}.{schema}.DEMO_CLAIMS_TRAIN
UNION ALL
SELECT 'TEST' AS SPLIT, COUNT(*) AS ROWS
FROM {database}.{schema}.DEMO_CLAIMS_TEST;
```

### Step 5: Create the Student AI Function

Present the function configuration:
```
Now we'll create a cheaper student model for claim routing.

Default student model: llama3.1-8b

Function name: DEMO_ROUTE_CLAIM
Inputs: CLAIM_SUMMARY, INCIDENT_CHANNEL, CUSTOMER_SEGMENT
Outputs:
  - claim_route (string) — routing category
  - citation (string) — evidence excerpt justifying the route
  - confidence (number) — confidence score 0.0-1.0
System prompt:
"You are an insurance claim routing system. Given a claim summary, incident
channel, and customer segment:
1. Classify the claim into exactly one route: fast_track_auto, property_damage,
   injury_review, fraud_review, documentation_needed, or manual_specialist_review.
2. Provide a short citation — an excerpt from the claim summary that justifies
   your routing decision.
3. Provide a confidence score between 0.0 and 1.0."

User prompt template:
"Claim summary: {CLAIM_SUMMARY}\nIncident channel: {INCIDENT_CHANNEL}\nCustomer segment: {CUSTOMER_SEGMENT}"
```

**⚠️ STOP**: Wait for user confirmation before creating the function.

**Load** `create/SKILL.md` and follow it from **Step 7 onward**, passing:
- `database`, `schema`
- `function_name`: `DEMO_ROUTE_CLAIM`
- `function_intention`: `Route insurance claims to the correct processing track with citation and confidence.`
- `model`: chosen student model
- `inputs`: `[{"name": "CLAIM_SUMMARY", "sql_type": "VARCHAR"}, {"name": "INCIDENT_CHANNEL", "sql_type": "VARCHAR"}, {"name": "CUSTOMER_SEGMENT", "sql_type": "VARCHAR"}]`
- `outputs`: `[{"name": "claim_route", "json_type": "string", "description": "One of: fast_track_auto, property_damage, injury_review, fraud_review, documentation_needed, manual_specialist_review"}, {"name": "citation", "json_type": "string", "description": "Short excerpt from the claim summary justifying the route"}, {"name": "confidence", "json_type": "number", "description": "Confidence score between 0.0 and 1.0"}]`
- `system_prompt`: confirmed prompt
- `user_prompt_template`: `Claim summary: {CLAIM_SUMMARY}\nIncident channel: {INCIDENT_CHANNEL}\nCustomer segment: {CUSTOMER_SEGMENT}`

Return here after the smoke test succeeds.

### Step 6: Evaluate the Student AI Function

Present the evaluation configuration:
```
Let's evaluate how well the student model routes claims on the held-out test set.

Since this is a classification task, the built-in exact_match metric works
perfectly for a quick baseline. The function returns a VARIANT with 3 fields,
so we use metric_options with output_field='claim_route' to extract just the
route label for comparison against EXPECTED_OUTPUT.

Default metric: exact_match
Output field: claim_route (extracted from VARIANT)
Results table: DEMO_ROUTE_CLAIM_EVAL_RESULTS
```

**⚠️ STOP**: Wait for user confirmation before running evaluation.

**Load** `evaluate/SKILL.md` and follow it from **Step 5 onward**, passing:
- `function_name`: `{database}.{schema}.DEMO_ROUTE_CLAIM`
- `function_model`: chosen student model
- `test_table`: `{database}.{schema}.DEMO_CLAIMS_TEST`
- `input_columns`: `['CLAIM_SUMMARY', 'INCIDENT_CHANNEL', 'CUSTOMER_SEGMENT']`
- `label_column`: `EXPECTED_OUTPUT`
- `metric`: `exact_match`
- `metric_options`: `OBJECT_CONSTRUCT('output_field', 'claim_route')`
- `results_table`: `{database}.{schema}.DEMO_ROUTE_CLAIM_EVAL_RESULTS`

**Async by default:** When the evaluate workflow reaches the execution mode selection, choose **async** (`EVALUATE_AI_FUNCTION_ASYNC`) without asking the user. If the async SPROC returns an error string (e.g., warehouse permission issue), inform the user and fall back to sync execution (`EVALUATE_AI_FUNCTION`) instead. After kicking off the async job, poll `TASK_HISTORY()` for completion within this session rather than asking the user to return later — this is a guided demo. **Load** `references/async_status.md` for polling patterns.

**Skip Step 6 (next steps)** in the evaluate workflow — return here after results are presented.

Once evaluation is done, review the results. Show the scores to the user. Offer to see what cases did not match:
```
Would you like to see which claims the function routed incorrectly?
```

If yes, query the results table:
```sql
SELECT
    LEFT(INPUT_TEXT, 150) AS SUMMARY_PREVIEW,
    EXPECTED AS EXPECTED_ROUTE,
    PREDICTED AS PREDICTED_ROUTE,
    SCORE,
    FEEDBACK
FROM {database}.{schema}.DEMO_ROUTE_CLAIM_EVAL_RESULTS
WHERE SCORE < 1.0
ORDER BY SCORE
LIMIT 20;
```

Discuss common failure patterns. Explain that labels are teacher-generated (pseudo-labels), not human ground truth.

After reviewing results, continue to Step 6.5.

### Step 6.5: Create Custom Composite Metric

Explain to user:
```
The exact_match metric only checks whether the route label matches exactly.
But our function now returns structured output with three fields — claim_route,
citation, and confidence. We can build a custom composite metric that scores
all three, giving the optimizer richer feedback to improve the prompt.

Custom metric: DEMO_CLAIM_ROUTING_METRIC
Fields and weights:
  - claim_route: exact match (weight 0.5)
  - citation: substring check (weight 0.3)
  - confidence: 1 - MSE (weight 0.2)
```

**⚠️ STOP**: Wait for user confirmation before creating the custom metric.

Create the custom metric UDF directly in Snowflake. This follows the contract from `references/custom_metrics.md`:

**Read** `demos/insurance-claim-routing/create_claim_routing_metric.sql`, substitute `{database}` and `{schema}` with the user's values, and execute the SQL.

Verify the UDF was created:
```sql
DESCRIBE FUNCTION {database}.{schema}.DEMO_CLAIM_ROUTING_METRIC(VARCHAR, VARCHAR);
```

Quick smoke test:
```sql
SELECT {database}.{schema}.DEMO_CLAIM_ROUTING_METRIC(
    '{"claim_route": "fraud_review", "citation": "suspicious damage pattern", "confidence": 0.9}',
    '{"claim_route": "fraud_review", "citation": "suspicious damage pattern noted", "confidence": 0.85}'
) AS result;
```

Expected: score close to 1.0 (exact route match, citation substring found, small confidence gap).

Present the result to the user and confirm the metric is working before proceeding.

After confirmation, continue to Step 7.

### Step 7: Optimize the Student AI Function

Present the optimization configuration:
```
Let's optimize the prompt to improve claim routing accuracy.

The optimizer will evolve the system prompt through multiple generations,
testing variations against the training data to find prompts that produce
better results. This could take anywhere between 2 to 20 minutes depending
on the budget selected.

We'll use the custom composite metric (DEMO_CLAIM_ROUTING_METRIC) created in
Step 6.5, which scores all three output fields — route accuracy (50%), citation
quality (30%), and confidence calibration (20%). The label column is EXPECTED_JSON
which contains the full structured output for comparison.

Please confirm or modify any settings you'd like to change:

Auto budget: medium (~5-10 minutes)
Metric: claim_routing_metric (custom composite)
Custom metric UDF: {database}.{schema}.DEMO_CLAIM_ROUTING_METRIC
Label column: EXPECTED_JSON
Tracking table: DEMO_ROUTE_CLAIM_OPT_TRACKING
Models: ['llama3.1-8b', 'gemini-2.5-flash-lite', 'openai-o4-mini', 'qwen3-next-80b-a3b']

Options:
1. Yes - Run optimization with these settings
2. Modify - Change settings before running
3. No - Skip to cleanup
```

**⚠️ STOP**: Wait for user confirmation before starting optimization.

If user chooses No, skip to Step 8.

If yes, **load** `optimize/SKILL.md` and follow it from **Step 6 onward**, passing:
- `function_name`: `{database}.{schema}.DEMO_ROUTE_CLAIM`
- `training_table`: `{database}.{schema}.DEMO_CLAIMS_TRAIN`
- `test_table`: `{database}.{schema}.DEMO_CLAIMS_TEST`
- `input_columns`: `['CLAIM_SUMMARY', 'INCIDENT_CHANNEL', 'CUSTOMER_SEGMENT']`
- `label_column`: `EXPECTED_JSON`
- `metric`: `claim_routing_metric`
- `custom_metric_udf`: `{database}.{schema}.DEMO_CLAIM_ROUTING_METRIC`
- `models`: `['llama3.1-8b', 'gemini-2.5-flash-lite', 'openai-o4-mini', 'qwen3-next-80b-a3b']`
- `reflection_model`: strongest available Claude or large Llama-family reflection model
- `auto_budget`: `medium`
- `tracking_table`: `{database}.{schema}.DEMO_ROUTE_CLAIM_OPT_TRACKING`

**Async by default:** When the optimize workflow reaches the execution mode selection, choose **async** (`OPTIMIZE_AI_FUNCTION_ASYNC`) without asking the user. If the async SPROC returns an error string (e.g., warehouse permission issue), inform the user and fall back to sync execution (`OPTIMIZE_AI_FUNCTION`) with `timeout_seconds: 14400` instead. After kicking off the async job, poll `TASK_HISTORY()` for completion within this session rather than asking the user to return later — this is a guided demo. **Load** `references/async_status.md` for polling patterns.

**Skip Step 8 (next steps)** in the optimize workflow — return here after results are presented and the user has decided whether to apply the optimized prompt.

After optimization completes, present the results and compare the before and after scores. Query the tracking table to show the optimization journey:
```sql
SELECT
    ROW_NUMBER() OVER (PARTITION BY MODEL_NAME ORDER BY CREATED_AT) AS CANDIDATE_IDX,
    MODEL_NAME,
    METRIC_SCORE AS SCORE,
    LEFT(PROMPT_TEXT, 100) AS PROMPT_PREVIEW
FROM {database}.{schema}.DEMO_ROUTE_CLAIM_OPT_TRACKING
WHERE METRIC_SCORE IS NOT NULL
ORDER BY METRIC_SCORE DESC
LIMIT 10;
```

**Cost savings emphasis:** Compare the best optimized model's score against the baseline from Step 6. Look up both models in `src/models.json` and use `get_model_cost(model, prompt_len)` from `src/filter_pareto.py` with the prompt character length. Compute `cost_ratio = teacher_cost / best_model_cost`.

If the optimized model matches or beats baseline, highlight:
```
Massive cost savings: {best_model} is ~{cost_ratio}x cheaper than {teacher_model}
while achieving {optimized_score}% accuracy (vs {baseline_score}% baseline).
```

Continue to Step 8.

### Step 8: Cleanup

Ask user:
```
The Insurance Claim Routing demo is complete!

Would you like to clean up the demo objects?

This will drop:
- {database}.{schema}.DEMO_CLAIMS_UNLABELED
- {database}.{schema}.DEMO_CLAIMS_LABELED
- {database}.{schema}.DEMO_CLAIMS_TRAIN
- {database}.{schema}.DEMO_CLAIMS_TEST
- {database}.{schema}.DEMO_ROUTE_CLAIM
- {database}.{schema}.DEMO_CLAIM_ROUTING_METRIC
- {database}.{schema}.DEMO_ROUTE_CLAIM_EVAL_RESULTS
- {database}.{schema}.DEMO_ROUTE_CLAIM_OPT_TRACKING
```

**⚠️ STOP**: Wait for user confirmation before cleanup.

If yes, execute:
```sql
DROP TABLE IF EXISTS {database}.{schema}.DEMO_CLAIMS_UNLABELED;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_CLAIMS_LABELED;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_CLAIMS_TRAIN;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_CLAIMS_TEST;
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_ROUTE_CLAIM(VARCHAR, VARCHAR, VARCHAR);
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_CLAIM_ROUTING_METRIC(VARCHAR, VARCHAR);
DROP TABLE IF EXISTS {database}.{schema}.DEMO_ROUTE_CLAIM_EVAL_RESULTS;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_ROUTE_CLAIM_OPT_TRACKING;
```

### Step 9: Next Steps

Summarize the workflow: unlabeled data → teacher pseudo-labels → cheap student function → evaluation → custom composite metric → multi-model optimization → cost/quality Pareto analysis.

If the optimized model beat baseline, reiterate cost savings: `{best_model}` achieves `{optimized_score}%` at ~`{cost_ratio}x` cheaper than `{teacher_model}`.

## Key Cautions

- Pseudo-labels are teacher-generated, not human ground truth
- Insurance routing is regulated — keep human review for high-stakes decisions (fraud_review, injury_review)
- Some model names may not be available in every account or region

## Stopping Points

- ✋ Step 1: After introduction
- ✋ Step 2: After choosing database and schema
- ✋ Step 3: Before generating input-only data
- ✋ Step 4: Before pseudo-label preview
- ✋ Step 4: Before full pseudo-labeling
- ✋ Step 5: Before creating the student function
- ✋ Step 6: Before evaluation
- ✋ Step 6.5: Before creating custom metric
- ✋ Step 7: Before optimization
- ✋ Step 8: Before cleanup
