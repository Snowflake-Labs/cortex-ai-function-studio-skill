---
name: redaction-demo
description: "Interactive demo [Experimental]: Build a PII redaction function using Agent Research mode — the agent searches the web for techniques, proposes SQL UDF architectures with pre/post-processing, and you pick the approach. Then optimize the full function body."
parent_skill: demos
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Agent Research: Pre/Post-Processing Demo [Experimental]

Build a PII redaction function using Agent Research mode — an experimental creation mode where the agent researches implementation approaches and proposes SQL UDF architectures with pre- and post-processing around AI_COMPLETE. Then optimize the entire function body (prompts, model, and SQL logic).

## Overview

This demo walks you through:
1. Loading French-language PII examples as sample data
2. Using **Agent Research mode** to build the function — the agent searches the web for PII redaction techniques, proposes 2-3 SQL UDF architectures, and you select your preferred approach
3. Testing the function with sample inputs
4. Optimizing the entire function body — prompts, model references, and SQL pre/post-processing

This demo showcases two features working together: **Agent Research mode** for creating complex SQL UDFs, and **body-mode optimization** that evolves the full SQL expression (not just the prompt).

**Time:** ~10 minutes
**Status:** Experimental — Agent Research mode uses web search and approach synthesis, which may produce varying results across runs.

## Workflow

### Step 0: Introduction

Explain to user:
```
Welcome to the Agent Research Demo! [Experimental]

This demo showcases an experimental creation mode called Agent Research,
followed by full-body optimization of the resulting SQL UDF.

1. **Research** — Search the web for state-of-the-art PII redaction techniques
2. **Propose** — Synthesize findings into 2-3 concrete SQL UDF architectures,
   each with different pre- and post-processing around the AI_COMPLETE call
3. **Present** — Show you the pros/cons of each approach so you can choose
4. **Build** — Generate the complete SQL UDF based on your selection
5. **Optimize** — Evolve the entire function body (prompts, model, SQL logic)

This is different from Direct mode (used in other demos) where the function
is a straightforward AI_COMPLETE call. Agent Research mode is for tasks where
SQL pre/post-processing can improve quality — e.g., input validation, output
parsing, iterative transformations, or multi-step extraction.

The optimizer can then improve the full SQL body — not just the prompt, but
also the pre/post-processing logic around the AI_COMPLETE call.

**Note:** Because this mode uses web search, results may vary between runs.
The approaches proposed depend on what the agent finds during research.

**Estimated time:** ~10 minutes
**Objects created:** All prefixed with DEMO_ for easy cleanup
```

### Step 1: Setup - Choose Location

Ask user:
```
Where would you like to create the demo objects?

Database: [e.g., TEMP]
Schema: [e.g., PUBLIC]

All objects will be prefixed with DEMO_ for easy cleanup.
```

Store the database and schema for use throughout the demo.

### Step 2: Create Sample Data

Explain to user:
```
First, I'll create sample datasets with French-language text containing PII.

The data comes from the ai4privacy/pii-masking-300k dataset, filtered to French
entries, and includes:
- Names, emails, phone numbers, addresses, IDs, and more
- Both the original text (INPUT_TEXT) and expected redacted output (EXPECTED_OUTPUT)

I'll create training and test tables:
- {database}.{schema}.DEMO_REDACTION_TRAIN (50 rows — for optimization)
- {database}.{schema}.DEMO_REDACTION_TEST (20 rows — for evaluation)
```

Run the data generation script:
```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/generate_redaction_data.py \
  --connection <CONNECTION_NAME> \
  --database {database} \
  --schema {schema} \
  --train 50 \
  --test 20 \
  --language French
```

**Note:** Replace `<SKILL_DIRECTORY>` with the absolute path to the cortex-ai-function-studio skill directory, and `<CONNECTION_NAME>` with the active Snowflake connection.

Verify creation:
```sql
SELECT COUNT(*) FROM {database}.{schema}.DEMO_REDACTION_TRAIN;
SELECT COUNT(*) FROM {database}.{schema}.DEMO_REDACTION_TEST;
```

Show a few sample rows:
```sql
SELECT
    LEFT(INPUT_TEXT, 200) AS INPUT_PREVIEW,
    LEFT(EXPECTED_OUTPUT, 200) AS OUTPUT_PREVIEW,
    PRIVACY_MASK
FROM {database}.{schema}.DEMO_REDACTION_TRAIN
LIMIT 3;
```

### Step 3: Create AI Function with Agent Research Mode

Present the task to the user:
```
Now we'll create the PII redaction function using Agent Research mode.

Instead of jumping straight to a simple AI_COMPLETE call, I'll:
1. Search the web for PII redaction best practices and techniques
2. Propose 2-3 SQL UDF approaches with different pre/post-processing strategies
3. Let you choose the approach that best fits your needs

Task: Redact all PII from French-language text, replacing each instance with [REDACTED].
PII types: names, email addresses, phone numbers, government IDs, credit card/bank
numbers, physical addresses, and dates/times.
```

**⚠️ STOP**: Wait for user confirmation before proceeding.

**Load** `create/SKILL.md` and follow it from **Step 1 (Gather Intention)**, passing:
- `database`, `schema`: from context
- `task_description`: `Redact all personally identifiable information (PII) from French-language text. Replace each PII instance with [REDACTED]. PII includes: names, email addresses, phone numbers, government IDs (passports, licenses, national IDs, tax IDs), credit card/bank numbers, physical addresses (street, city, building, country), and dates/times. The function should preserve all non-PII text exactly. I want to explore different approaches with SQL pre/post-processing.`
- `creation_mode`: `research` (this triggers Agent Research mode — Steps 4-6 in create/SKILL.md)
- `function_name`: `DEMO_REDACT_PII`
- `inputs`: `[{"name": "TEXT", "sql_type": "VARCHAR"}]`
- `outputs`: `[{"name": "redacted_text", "json_type": "string", "description": "Redacted text with all PII replaced by [REDACTED]"}]`

The create workflow will:
1. **Step 5 (Research)**: Search the web for PII redaction techniques and propose 2-3 SQL UDF approaches with different pre/post-processing strategies
2. **Step 6 (Confirm)**: Present approaches for user selection
3. **Steps 7-9**: Generate and create the function

**Skip Step 10 (next steps)** in the create workflow — return here after the function is created and smoke-tested.

### Step 4: Test the Function

After the function is created, test it with a sample from the dataset:

```sql
SELECT
    LEFT(INPUT_TEXT, 200) AS ORIGINAL,
    LEFT({database}.{schema}.DEMO_REDACT_PII(INPUT_TEXT), 200) AS REDACTED
FROM {database}.{schema}.DEMO_REDACTION_TEST
LIMIT 3;
```

Discuss with the user:
- How the pre/post-processing in the selected approach affected the output
- Whether the redaction patterns match expectations
- How this differs from a simple Direct mode function (which would just be a bare AI_COMPLETE call)

### Step 5: Optimize the Function

Present the optimization configuration:

```
Now we'll optimize the function using body-mode optimization.

Unlike prompt-only optimization, this can modify the entire SQL function body —
the system prompt, user prompt template, model reference, and any SQL
pre/post-processing logic from the Agent Research approach.

Please confirm or modify any settings you'd like to change:

Model: claude-sonnet-4-5
Auto budget: demo
Experiment: DEMO_REDACT_PII_OPT_EXP
```

**⚠️ STOP**: Wait for user confirmation or modifications before starting optimization.

**Load** `optimize/SKILL.md` and follow it from **Step 5 onward** (Run Optimization), passing:
- `function_name`: `{database}.{schema}.DEMO_REDACT_PII`
- `training_table`: `{database}.{schema}.DEMO_REDACTION_TRAIN`
- `test_table`: `{database}.{schema}.DEMO_REDACTION_TEST`
- `input_columns`: `['INPUT_TEXT']`
- `label_column`: `EXPECTED_OUTPUT`
- `metric`: `redaction_match`
- `models`: `['claude-sonnet-4-5']`
- `reflection_model`: `claude-sonnet-4-6`
- `auto_budget`: `demo`
- `experiment_name`: `{database}.{schema}.DEMO_REDACT_PII_OPT_EXP`

Return here after optimization results are presented.

After the optimizer returns, discuss:
- Whether the optimized body changed the SQL pre/post-processing logic (not just the prompt)
- The score improvement over the baseline
- How body-mode optimization differs from prompt-only optimization

### Step 6: Cleanup

Ask user:
```
The Agent Research demo is complete!

Would you like to clean up the demo objects?

This will drop:
- {database}.{schema}.DEMO_REDACTION_TRAIN
- {database}.{schema}.DEMO_REDACTION_TEST
- {database}.{schema}.DEMO_REDACT_PII (function)
- {database}.{schema}.DEMO_REDACT_PII_OPT_EXP (if created)

Options:
1. Yes - Clean up all demo objects
2. No - Keep objects for further exploration
```

**⚠️ STOP**: Wait for user selection before proceeding.

If yes, execute:
```sql
DROP TABLE IF EXISTS {database}.{schema}.DEMO_REDACTION_TRAIN;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_REDACTION_TEST;
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_REDACT_PII(VARCHAR);
DROP EXPERIMENT IF EXISTS {database}.{schema}.DEMO_REDACT_PII_OPT_EXP;
```

### Step 7: Next Steps

```
Thanks for trying the Agent Research demo!

Here's what you experienced:
- **Agent Research mode** — the agent searched the web for PII redaction
  techniques and proposed multiple SQL UDF architectures
- **Pre/post-processing** — the function includes SQL logic before and/or
  after the AI_COMPLETE call, not just a bare LLM invocation
- **Approach selection** — you chose between concrete implementation
  alternatives with different quality/complexity trade-offs
- **Body-mode optimization** — the optimizer evolved the entire SQL function
  body, not just the prompt — including pre/post-processing logic

This experimental mode is useful when:
- Your task benefits from SQL validation or transformation around the LLM call
- You want the agent to research domain-specific techniques
- You want to compare multiple implementation strategies before committing
- You want optimization to consider the full SQL structure, not just prompts

For simpler tasks, Direct mode (used in the Quick Start demo) is faster.
For prompt-only optimization, try the "Prompt Optimization" demo.

Ready to build your own AI function? Just say "create an AI function" and
mention that you want to explore implementation approaches.
```

## Key Cautions

- Agent Research mode uses web search — results may vary between runs
- The proposed approaches depend on what the agent finds during research
- Pre/post-processing adds SQL complexity; only use when it adds value
- This is an experimental feature and may change in future releases
- Body-mode optimization can modify any part of the SQL body, which may simplify or restructure the pre/post-processing
- PII redaction via LLM is not guaranteed to be comprehensive — do not use as a production privacy filter without human review
- The ai4privacy dataset is filtered to French text; results may differ for other languages
- Redaction patterns depend on the model's training data — novel PII formats may be missed
- Some model names may not be available in every account or region

## Stopping Points

- ✋ Step 0: After introduction, before proceeding
- ✋ Step 1: After location selection
- ✋ Step 3: Before triggering Agent Research mode
- ✋ Step 5: Before running optimization
- ✋ Step 6: Before cleanup
