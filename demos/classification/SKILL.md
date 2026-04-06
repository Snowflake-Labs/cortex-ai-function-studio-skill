---
name: classification-demo
description: "Interactive demo: Build a content moderation AI function that detects toxic text across 55 languages."
parent_skill: demos
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Content Moderation Demo

Build an AI function that detects toxic content using the toxi-text-3M dataset.

## Overview

This demo walks you through:
1. Loading real-world multilingual toxicity examples from the toxi-text-3M dataset
2. Building a custom AI function for binary toxicity detection
3. Evaluating the function's accuracy with built-in exact_match
4. Optimizing the prompt to improve results

## Workflow

### Step 1: Introduction

Explain to user:
```
Welcome to the Content Moderation Demo!

This hands-on demo will guide you through the complete lifecycle of a custom AI function:

1. **Create Sample Data** - Sample multilingual toxic/not_toxic examples from the toxi-text-3M dataset
2. **Build the Function** - Create a DEMO_DETECT_TOXICITY function using Cortex AI_COMPLETE
3. **Evaluate Performance** - Measure detection accuracy against ground truth labels
4. **Optimize the Prompt** - Use GEPA optimization to improve accuracy

By the end, you'll have a working content moderation classifier and understand how to build,
evaluate, and optimize any custom AI function.

**Objects created:** All prefixed with DEMO_ for easy cleanup
```

**⚠️ STOP**: Ask user if they want to proceed before continuing.

### Step 2: Setup - Choose Location

Ask user:
```
Where would you like to create the demo objects?

Database: [e.g., TEMP]
Schema: [e.g., PUBLIC]

All objects will be prefixed with DEMO_ for easy cleanup.
```

Store the database and schema for use throughout the demo.

### Step 3: Create Sample Data

Explain to user:
```
I'll load real-world examples from the toxi-text-3M dataset — a multilingual collection of
text samples spanning 55 languages, labeled for toxicity.

The data includes:
- TEXT: The original text sample
- EXPECTED_OUTPUT: Either "toxic" or "not_toxic"

The dataset is balanced 50/50 between toxic and non-toxic examples to ensure
fair evaluation.

How many total examples would you like?

Training rows: [default: 300] - Used for optimization
Test rows: [default: 200] - Used for evaluation

I'll create these tables:
- {database}.{schema}.DEMO_TOXICITY_TRAIN
- {database}.{schema}.DEMO_TOXICITY_TEST
```

**⚠️ STOP**: Wait for user to specify row counts (or confirm defaults) before proceeding.

Run the data generation script with the specified row counts:
```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/generate_toxicity_data.py \
  --connection <CONNECTION_NAME> \
  --database {database} \
  --schema {schema} \
  --train {train_rows} \
  --test {test_rows}
```

**Note:** Replace `<SKILL_DIRECTORY>` with the absolute path to the cortex-ai-function-studio skill directory, and `<CONNECTION_NAME>` with the active Snowflake connection.

Verify creation:
```sql
SELECT COUNT(*) FROM {database}.{schema}.DEMO_TOXICITY_TRAIN;
SELECT COUNT(*) FROM {database}.{schema}.DEMO_TOXICITY_TEST;
```

Show a few sample rows:
```sql
SELECT 
    LEFT(TEXT, 200) AS TEXT_PREVIEW,
    EXPECTED_OUTPUT AS LABEL
FROM {database}.{schema}.DEMO_TOXICITY_TRAIN 
LIMIT 5;
```

### Step 4: Create the AI Function

Present the function configuration to the user:

```
Now we'll create an AI function that detects toxic content.

Please confirm or modify any settings you'd like to change:

Function name: DEMO_DETECT_TOXICITY
Model: llama3.1-70b
Input: TEXT (VARCHAR) - The text to classify
Output: toxicity (string) - Either "toxic" or "not_toxic"
System prompt:
  "You are an expert content moderation system trained to detect toxic, harmful, or abusive language across many languages.
  Classify the given text as either toxic or not toxic. Toxic content includes hate speech, slurs, derogatory language, 
  threats, insults, bullying, harassment, obscene language, and discrimination.
  The text may be in any of 55+ languages. Apply the same toxicity standards regardless of language.
  Return exactly one label: toxic or not_toxic."
User prompt template: "{TEXT}"
```

**⚠️ STOP**: Wait for user confirmation or modifications before creating the function.

**Load `create/SKILL.md`** and follow it from **Step 7 onward** (Generate SQL with Script), passing all confirmed values as context:
- `database`, `schema`: From Step 2
- `function_name`: `DEMO_DETECT_TOXICITY`
- `function_intention`: `Detect toxic content in text across multiple languages.`
- `model`: `llama3.1-70b` (or user's choice)
- `inputs`: `[{"name": "TEXT", "sql_type": "VARCHAR"}]`
- `outputs`: `[{"name": "toxicity", "json_type": "string", "description": "Either toxic or not_toxic"}]`
- `system_prompt`: Confirmed system prompt from above
- `user_prompt_template`: `{TEXT}`

The create workflow will generate the SQL, show it for confirmation, execute it, run a smoke test, and set up the infrastructure. **Skip Step 10 (next steps)** in the create workflow -- return here after the function is created and tested and the infrastructure is set up.

After the function is confirmed working, continue to Step 5.

### Step 5: Evaluate the AI Function

Present the evaluation configuration to the user:

```
Let's evaluate how well the function detects toxic content on our test data.

Since this is a binary classification task (toxic vs not_toxic), the built-in
exact_match metric works perfectly — no custom metric needed.

Please confirm or modify any settings you'd like to change:

Metric: exact_match
Results table: DEMO_DETECT_TOXICITY_EVAL_RESULTS
```

**⚠️ STOP**: Wait for user confirmation or modifications before running evaluation.

**Load `evaluate/SKILL.md`** and follow its workflow from **Step 5 onward** (Create Evaluation SPROC), passing all values as context so the user is not re-asked for information already collected:
- `function_name`: `{database}.{schema}.DEMO_DETECT_TOXICITY`
- `function_model`: `llama3.1-70b` (or user's choice from Step 4)
- `test_table`: `{database}.{schema}.DEMO_TOXICITY_TEST`
- `input_columns`: `['TEXT']`
- `label_column`: `EXPECTED_OUTPUT`
- `metric_name`: `exact_match`
- `results_table`: `{database}.{schema}.DEMO_DETECT_TOXICITY_EVAL_RESULTS`
- `stage_name`: `{database}.{schema}.AI_FUNCTIONS`

The create workflow should have set up the stage, created the SPROCs as needed before coming to this evaluate workflow. Then, the evaluate workflow will run the evaluation and present results. **Skip Step 7 (next steps)** in the evaluate workflow -- return here after results are presented.

Once evaluation is done, review the results. Show the scores to the user. Offer to see what cases did not match:
```
Would you like to see which cases the function got wrong?
```

If yes, query the results table:
```sql
SELECT 
    LEFT(INPUT_TEXT, 150) AS TEXT_PREVIEW,
    EXPECTED AS EXPECTED_LABEL,
    PREDICTED AS PREDICTED_LABEL,
    SCORE,
    FEEDBACK
FROM {database}.{schema}.DEMO_DETECT_TOXICITY_EVAL_RESULTS
WHERE SCORE < 1.0
ORDER BY SCORE
LIMIT 20;
```

Discuss common failure patterns (e.g., borderline cases, sarcasm misclassified, language-specific nuances, false positives on quoted speech).

After reviewing results, continue to Step 6.

### Step 6: Optimize the AI Function

Present the optimization configuration:

```
Let's optimize the prompt to improve toxicity detection accuracy.

GEPA (Genetic-Pareto Algorithm) will evolve the system prompt through multiple 
generations, testing variations against the training data to find prompts that 
produce better results. This could take anywhere between 2 to 20 minutes depending 
on the budget selected.

Please confirm or modify any settings you'd like to change:

Auto budget: medium (~5-10 minutes)
Tracking table: DEMO_DETECT_TOXICITY_OPT_TRACKING
Models: ['claude-haiku-4-5', 'llama3.1-70b', 'llama3.1-8b', 'claude-sonnet-4-5']

Options:
1. Yes - Run GEPA optimization with these settings
2. Modify - Change settings before running
3. No - Skip to cleanup
```

**⚠️ STOP**: Wait for user confirmation or modifications before running optimization.

If user chooses No, skip to Step 7.

If yes, **load `optimize/SKILL.md`** and follow its workflow from **Step 6 onward** (Create Optimization SPROC), passing all values as context so the user is not re-asked for information already collected:
- `function_name`: `{database}.{schema}.DEMO_DETECT_TOXICITY`
- `training_table`: `{database}.{schema}.DEMO_TOXICITY_TRAIN`
- `test_table`: `{database}.{schema}.DEMO_TOXICITY_TEST`
- `input_columns`: `['TEXT']`
- `label_column`: `EXPECTED_OUTPUT`
- `metric_name`: `exact_match`
- `models`: `['claude-haiku-4-5', 'llama3.1-70b', 'llama3.1-8b', 'claude-sonnet-4-5']`
- `reflection_model`: `claude-opus-4-5`
- `auto_budget`: `medium`
- `tracking_table`: `{database}.{schema}.DEMO_DETECT_TOXICITY_OPT_TRACKING`
- `stage_name`: `{database}.{schema}.AI_FUNCTIONS`

The create workflow should have uploaded code and created the SPROCs as needed. Then, the optimize workflow will run the optimization and present results. **Skip Step 9 (next steps)** in the optimize workflow -- return here after results are presented and the user has decided whether to apply the optimized prompt.

After optimization completes, present the results and compare the before and after scores. Query the tracking table to show the optimization journey:
```sql
SELECT 
    ROW_NUMBER() OVER (PARTITION BY MODEL_NAME ORDER BY CREATED_AT) AS CANDIDATE_IDX,
    MODEL_NAME,
    METRIC_SCORE AS SCORE,
    LEFT(PROMPT_TEXT, 100) AS PROMPT_PREVIEW
FROM {database}.{schema}.DEMO_DETECT_TOXICITY_OPT_TRACKING
WHERE METRIC_SCORE IS NOT NULL
ORDER BY METRIC_SCORE DESC
LIMIT 10;
```

Continue to Step 7.

### Step 7: Cleanup

```
The Content Moderation demo is complete!

Would you like to clean up the demo objects?

This will drop:
- {database}.{schema}.DEMO_TOXICITY_TRAIN
- {database}.{schema}.DEMO_TOXICITY_TEST
- {database}.{schema}.DEMO_DETECT_TOXICITY (function)
- {database}.{schema}.DEMO_DETECT_TOXICITY_EVAL_RESULTS (if created)
- {database}.{schema}.DEMO_DETECT_TOXICITY_OPT_TRACKING (if created)

Options:
1. Yes - Clean up all demo objects
2. No - Keep objects for further exploration
```

**⚠️ STOP**: Wait for user selection before proceeding.

If yes, execute:
```sql
DROP TABLE IF EXISTS {database}.{schema}.DEMO_TOXICITY_TRAIN;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_TOXICITY_TEST;
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_DETECT_TOXICITY(VARCHAR);
DROP TABLE IF EXISTS {database}.{schema}.DEMO_DETECT_TOXICITY_EVAL_RESULTS;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_DETECT_TOXICITY_OPT_TRACKING;
```

### Step 8: Next Steps

```
Thanks for trying the Content Moderation demo!

Here's what you learned:
- **Created** an AI function that detects toxic content across 55 languages (binary classification)
- **Evaluated** accuracy using the built-in exact_match metric against real-world labeled data
- **Optimized** the prompt using GEPA to improve detection performance

The full create -> evaluate -> optimize workflow works the same way for any 
classification, extraction, or transformation task. Custom metrics let you 
tailor evaluation to your specific needs.

Ready to build your own AI function? Just say "create an AI function" to get started.
```

## Stopping Points

- ✋ Step 1: After introduction, before proceeding
- ✋ Step 2: After location selection
- ✋ Step 3: Before loading data (confirm row counts)
- ✋ Step 4: Before creating function (confirm settings)
- ✋ Step 5: Before running evaluation (confirm settings)
- ✋ Step 6: Before running optimization (confirm settings)
- ✋ Step 7: Before cleanup (confirm choice)
