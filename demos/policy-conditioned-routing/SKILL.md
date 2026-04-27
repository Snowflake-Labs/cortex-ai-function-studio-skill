---
name: policy-conditioned-routing-demo
description: "Interactive demo: Build a policy-conditioned ticket router where a seed prompt performs poorly, then watch prompt optimization close the accuracy gap through prompt evolution and Pareto cost/quality analysis. The canonical demo for prompt optimization."
parent_skill: demos
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Prompt Optimization Demo

Build an AI function that routes support tickets using company-specific policy context, then use prompt optimization to improve accuracy across cheap models.

## Overview

> **Note:** This demo uses synthetic data. The companies, policies, and tickets are fictional and hand-crafted to showcase prompt optimization. The dataset is designed to be genuinely challenging — tickets contain cross-company vocabulary bleed, competing signals, buried requests, and red herrings that require careful policy reasoning.

Load gold-labeled policy data → build a routing function → run prompt optimization → compare cost/quality with Pareto analysis. **Estimated time:** ~10 minutes.

## Workflow

### Step 1: Introduction

Explain to user:
```
Welcome to the Prompt Optimization Demo!

At the end of this demo, you will witness the Cortex AI Function Studio's ability to:
- Optimize prompts across multiple models with one call
- Improve accuracy on challenging tasks through prompt evolution alone
- Compare cost vs. quality trade-offs using Pareto frontier analysis
- Automatically learn domain-specific vocabulary through reflection on failures

This demo uses *synthetic data* — the companies, policies, and support tickets
are fictional and hand-crafted. The dataset is designed to be genuinely hard:
tickets contain cross-company vocabulary bleed, competing signals, buried
requests, and red herrings that require careful multi-step policy reasoning.

Route labels:
- billing
- account_access
- bug_or_outage
- feature_request
- refund_or_cancel
- security_or_abuse

The dataset includes 4 fictional companies, each with unique routing policies.
Most tickets have a "default" label that gets overridden by company
policy — a model that ignores or misreads policy context will score poorly.
```

### Step 2: Setup - Choose Location

If `{database}` and `{schema}` are already known from the prerequisite flow, accept them silently and skip the prompt.

Otherwise, ask user:
```
Where would you like to create the demo objects?

Database: [e.g., TEMP]
Schema: [e.g., PUBLIC]

All objects will be prefixed with DEMO_ for easy cleanup.
```

Store the database and schema for use throughout the demo.

### Step 3: Load Seed Data

Explain to user:
```
I'll load the hand-crafted synthetic dataset. This creates:

1. A company routing policy table (4 fictional companies with unique policies)
2. A 24-row holdout set for evaluation (23 override rows requiring policy reasoning + 1 reinforce row testing long-context focus)
3. A 96-row training set for optimization (23 override families + 1 reinforce family, 4 phrasing variants each — 92 override + 4 reinforce rows)

An "override" row is one where the company policy changes the correct
route away from the obvious default. The holdout set includes deliberately
tricky patterns: cross-company vocabulary bleed, competing signals in the
same ticket, buried requests, and red herrings.

The data is pre-split — no train/test splitting needed.
```

**⚠️ STOP**: Wait for user confirmation before loading data.

Run the data loading script:
```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/demos/policy-conditioned-routing/load_dataset.py \
  --connection <CONNECTION_NAME> \
  --database {database} \
  --schema {schema}
```

**Note:** Replace `<SKILL_DIRECTORY>` with the absolute path to the cortex-ai-function-studio skill directory, and `<CONNECTION_NAME>` with the active Snowflake connection.

The script renders the Jinja2 SQL template, executes all CREATE statements, and verifies:
- Row counts: policy=4, holdout=24, train=96
- Zero subject overlap between train and holdout

If any verification check fails, the script exits with a non-zero status.

### Step 4: Create the Routing AI Function

Present the function configuration:
```
Now I'll create a policy-aware routing function.

Function name: DEMO_ROUTE_TICKET
Default model: gemini-2.5-flash-lite

Inputs:
  SUBJECT, BODY, CUSTOMER_TIER, COMPANY_NAME,
  POLICY_PROFILE, POLICY_TEXT, ENTITLEMENT_TEXT

Output: route (string)
```

**⚠️ STOP**: Wait for user confirmation or modifications before creating the function.

**Load** `create/SKILL.md` and follow it from **Step 7 onward**, passing:
- `database`, `schema`
- `function_name`: `DEMO_ROUTE_TICKET`
- `function_intention`: `Route policy-aware support tickets using company context.`
- `model`: `gemini-2.5-flash-lite`
- `inputs`: `[{"name": "SUBJECT", "sql_type": "VARCHAR"}, {"name": "BODY", "sql_type": "VARCHAR"}, {"name": "CUSTOMER_TIER", "sql_type": "VARCHAR"}, {"name": "COMPANY_NAME", "sql_type": "VARCHAR"}, {"name": "POLICY_PROFILE", "sql_type": "VARCHAR"}, {"name": "POLICY_TEXT", "sql_type": "VARCHAR"}, {"name": "ENTITLEMENT_TEXT", "sql_type": "VARCHAR"}]`
- `outputs`: `[{"name": "route", "json_type": "string", "description": "Support ticket route"}]`
- `system_prompt`: `You are a support ticket router. Given a ticket subject, body, customer tier, company name, policy profile, company policy text, and entitlement notes, classify it into exactly one category: billing, account_access, bug_or_outage, feature_request, refund_or_cancel, or security_or_abuse. The company policy is written in internal handling language rather than the route labels themselves. Infer the best route from the ticket and company policy, and only use company context when it changes the default interpretation. Return only the label in the route field.`
- `user_prompt_template`: `Subject: {SUBJECT}\nBody: {BODY}\nCustomer tier: {CUSTOMER_TIER}\nCompany name: {COMPANY_NAME}\nPolicy profile: {POLICY_PROFILE}\nCompany policy: {POLICY_TEXT}\nEntitlement notes: {ENTITLEMENT_TEXT}`

Return here after the smoke test succeeds.

### Step 5: Optimize Prompts

Present the optimization configuration:
```
Now we'll run prompt optimization across multiple cheap models.
The optimizer evolves the system prompt through multiple generations, testing
variations against the 96-row training set. The optimizer reports both
the seed score (baseline with your original prompt) and the best
optimized score for each model.

Default models to optimize:
- mistral-7b
- gemini-2.5-flash
- gemini-2.5-flash-lite
- claude-haiku-4-5

Auto budget: demo (~5 minutes)
Experiment: DEMO_ROUTE_TICKET_OPT_EXP
```

**⚠️ STOP**: Wait for user confirmation or modifications before starting optimization.

**Load** `optimize/SKILL.md` and follow it from **Step 5 onward**, passing:
- `function_name`: `{database}.{schema}.DEMO_ROUTE_TICKET`
- `training_table`: `{database}.{schema}.DEMO_TICKETS_POLICY_TRAIN_V6_LARGE`
- `test_table`: `{database}.{schema}.DEMO_TICKETS_HARD_GOLD_V6_SMALL`
- `input_columns`: `['SUBJECT', 'BODY', 'CUSTOMER_TIER', 'COMPANY_NAME', 'POLICY_PROFILE', 'POLICY_TEXT', 'ENTITLEMENT_TEXT']`
- `label_column`: `EXPECTED_OUTPUT`
- `metric_name`: `exact_match`
- `models`: the confirmed cheap-model list
- `reflection_model`: `claude-sonnet-4-5`
- `auto_budget`: `demo`
- `experiment_name`: `{database}.{schema}.DEMO_ROUTE_TICKET_OPT_EXP`

Return here after optimization results are presented.

### Step 6: Summarize Results

**6.1.** Show seed vs optimized scores from the experiment:
```sql
SHOW RUN METRICS IN EXPERIMENT {database}.{schema}.DEMO_ROUTE_TICKET_OPT_EXP;
```

Present the seed (baseline) score and best optimized score for each model side by side.

**6.2.** Calculate relative cost using the Pareto filter script (`src/filter_pareto.py`). Include all models: optimized cheap models at their best optimized score. Use the system prompt character length for `--prompt-chars` and average expected output length from the holdout table for `--avg-output-chars`. Use the seed scores from the experiment for `--seed-score`. Present the Pareto-optimal table to the user.

**6.3.** Show before/after prompt comparison. Pick the best-performing model and display its seed (original) system prompt vs the optimized system prompt side by side. The `seed_body` and `best_body` fields from the optimization result contain the full function bodies — extract the system prompt content string from each. Present as:
```
**Before (seed prompt):**
> {seed system prompt text}

**After (optimized prompt):**
> {optimized system prompt text}
```
Highlight specific differences: new domain vocabulary, added routing heuristics, explicit policy-interpretation instructions, or structural changes the optimizer introduced.

**6.4.** Summarize key findings:
- Which model gained the most accuracy from prompt optimization (seed → best).
- If any optimized cheap model approaches strong-model quality, highlight the cost savings.
- If score differences between models are small, note that the Pareto analysis still reveals cost-efficiency differences — even a few percentage points of accuracy matter when choosing between models at different price points.
- Note that the optimizer learned the unfamiliar policy vocabulary through reflection — the evolved prompts contain domain-specific instructions that the generic seed prompt lacked.
- Recommend heavier optimization budgets for further improvement if gaps remain.

### Step 7: Cleanup

Ask user:
```
The Prompt Optimization demo is complete!

Would you like to clean up the demo objects?

This will drop:
- {database}.{schema}.DEMO_COMPANY_ROUTING_POLICY_V6
- {database}.{schema}.DEMO_TICKETS_HARD_GOLD_V6_SMALL
- {database}.{schema}.DEMO_TICKETS_POLICY_TRAIN_V6_LARGE
- {database}.{schema}.DEMO_ROUTE_TICKET
- {database}.{schema}.DEMO_ROUTE_TICKET_OPT_EXP
```

**⚠️ STOP**: Wait for user confirmation before cleanup.

If yes, execute:
```sql
DROP TABLE IF EXISTS {database}.{schema}.DEMO_COMPANY_ROUTING_POLICY_V6;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_TICKETS_HARD_GOLD_V6_SMALL;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_TICKETS_POLICY_TRAIN_V6_LARGE;
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_ROUTE_TICKET(VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR);
DROP EXPERIMENT IF EXISTS {database}.{schema}.DEMO_ROUTE_TICKET_OPT_EXP;
```

### Step 8: Next Steps

Explain to user:
```
You completed the Prompt Optimization demo:

1. Loaded synthetic labeled data with challenging company routing policies
2. Built a policy-aware routing function
3. Ran prompt optimization — saw seed (baseline) vs optimized scores
4. Compared cost and quality with Pareto frontier analysis

Key takeaways:

  Accuracy: Prompt optimization improved accuracy on challenging
  synthetic tickets. The holdout set includes cross-company vocabulary
  bleed, competing signals, and buried requests that require careful
  policy reasoning.

  Cost: The Pareto frontier shows which models offer the best
  quality-per-dollar. Even small accuracy differences between models
  matter when choosing between price points.

  When to use prompt optimization: Whenever baseline accuracy on
  your task is disappointing, especially with cheaper models.
  Optimization can close the gap without changing models or data.
```

## Key Cautions

- All data is synthetic — companies, policies, and tickets are fictional.
- Gold labels are authored for this specific policy vocabulary. They represent ground truth, not pseudo-labels.
- The holdout set is designed to be hard: cross-company vocabulary bleed, competing signals, buried requests, urgency red herrings, and unverifiable justifications.
- The holdout set is small (24 rows). Each row counts for ~4.2% of accuracy.

## Stopping Points

- ✋ Step 1: After introduction
- ✋ Step 2: After choosing database and schema
- ✋ Step 3: Before loading seed data
- ✋ Step 4: Before creating the routing function
- ✋ Step 5: Before optimization
- ✋ Step 7: Before cleanup
