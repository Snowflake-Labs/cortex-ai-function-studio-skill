---
name: classification-demo
description: "Quick Start demo: Build a toxicity classifier and evaluate it — the fastest way to experience the core create → evaluate workflow."
parent_skill: demos
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Content Moderation Demo

Build an AI function that detects toxic content using the toxi-text-3M dataset.

## Overview

This is a quick ~5 minute demo focused on the core create → evaluate workflow. It walks you through:
1. Loading real-world English toxicity examples from the toxi-text-3M dataset
2. Building a custom AI function for binary toxicity detection
3. Evaluating the function's accuracy with built-in exact_match

## Workflow

### Step 1: Introduction

Explain to user:
```
Welcome to the Content Moderation Quick Start!

This ~5 minute demo covers the core create → evaluate workflow:

1. **Create Sample Data** - Sample English toxic/not_toxic examples from the toxi-text-3M dataset
2. **Build the Function** - Create a DEMO_DETECT_TOXICITY function using Cortex AI_COMPLETE
3. **Evaluate Performance** - Measure detection accuracy against ground truth labels

By the end, you'll have a working content moderation classifier and understand how to build
and evaluate any custom AI function.

**Objects created:** All prefixed with DEMO_ for easy cleanup
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

### Step 3: Create Sample Data

Explain to user:
```
I'll load real-world examples from the toxi-text-3M dataset, filtered to English text
under 500 characters for cleaner labels and more consistent evaluation.

The data includes:
- TEXT: The original text sample
- EXPECTED_OUTPUT: Either "toxic" or "not_toxic"

The dataset is balanced 50/50 between toxic and non-toxic examples to ensure
fair evaluation.

I'll load 150 examples into this table:
- {database}.{schema}.DEMO_TOXICITY_DATA
```

Run the data generation script:
```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/generate_toxicity_data.py \
  --connection <CONNECTION_NAME> \
  --database {database} \
  --schema {schema} \
  --train 150 \
  --test 0 \
  --train-table DEMO_TOXICITY_DATA \
  --language en \
  --max-length 500
```

**Note:** Replace `<SKILL_DIRECTORY>` with the absolute path to the cortex-ai-function-studio skill directory, and `<CONNECTION_NAME>` with the active Snowflake connection.

Verify creation:
```sql
SELECT COUNT(*) FROM {database}.{schema}.DEMO_TOXICITY_DATA;
```

Show a few sample rows:
```sql
SELECT 
    LEFT(TEXT, 200) AS TEXT_PREVIEW,
    EXPECTED_OUTPUT AS LABEL
FROM {database}.{schema}.DEMO_TOXICITY_DATA 
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
  "Classify the given text as either toxic or not toxic.
  Return exactly one label: toxic or not_toxic."
User prompt template: "{TEXT}"
```

**⚠️ STOP**: Wait for user confirmation or modifications before creating the function.

**Load `create/SKILL.md`** and follow it from **Step 9 onward** (Create UDF), passing all confirmed values as context:
- `database`, `schema`: From Step 2
- `function_name`: `DEMO_DETECT_TOXICITY`
- `function_intention`: `Detect toxic content in English text.`
- `model`: `llama3.1-70b` (or user's choice)
- `inputs`: `[{"name": "TEXT", "sql_type": "VARCHAR"}]`
- `outputs`: `[{"name": "toxicity", "json_type": "string", "description": "Either toxic or not_toxic"}]`
- `system_prompt`: Confirmed system prompt from above
- `user_prompt_template`: `{TEXT}`

The create workflow will generate the SQL, show it for confirmation, execute it, and run a smoke test. **Skip Step 10 (next steps)** in the create workflow -- return here after the function is created and tested.

After the function is confirmed working, continue to Step 5.

### Step 5: Evaluate the AI Function

Present the evaluation configuration to the user:

```
Let's evaluate how well the function detects toxic content on our data.

Since this is a binary classification task (toxic vs not_toxic), the built-in
exact_match metric works perfectly — no custom metric needed.

Please confirm or modify any settings you'd like to change:

Metric: exact_match
Results table: DEMO_DETECT_TOXICITY_EVAL_RESULTS
```

**⚠️ STOP**: Wait for user confirmation or modifications before running evaluation.

**Load `evaluate/SKILL.md`** and follow its workflow from **Step 4 onward** (Run Evaluation), passing all values as context so the user is not re-asked for information already collected:
- `function_name`: `{database}.{schema}.DEMO_DETECT_TOXICITY`
- `function_model`: `llama3.1-70b` (or user's choice from Step 4)
- `test_table`: `{database}.{schema}.DEMO_TOXICITY_DATA`
- `input_columns`: `['TEXT']`
- `label_column`: `EXPECTED_OUTPUT`
- `metric_name`: `exact_match`
- `results_table`: `{database}.{schema}.DEMO_DETECT_TOXICITY_EVAL_RESULTS`

The evaluate workflow will run the evaluation and present results. **Skip Step 6 (next steps)** in the evaluate workflow -- return here after results are presented.

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

Discuss common failure patterns (e.g., borderline cases, sarcasm misclassified, mild profanity in casual context, false positives on quoted speech).

After reviewing results, continue to Step 6.

### Step 6: Cleanup

```
The Content Moderation demo is complete!

Would you like to clean up the demo objects?

This will drop:
- {database}.{schema}.DEMO_TOXICITY_DATA
- {database}.{schema}.DEMO_DETECT_TOXICITY (function)
- {database}.{schema}.DEMO_DETECT_TOXICITY_EVAL_RESULTS (if created)

Options:
1. Yes - Clean up all demo objects
2. No - Keep objects for further exploration
```

**⚠️ STOP**: Wait for user selection before proceeding.

If yes, execute:
```sql
DROP TABLE IF EXISTS {database}.{schema}.DEMO_TOXICITY_DATA;
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_DETECT_TOXICITY(VARCHAR);
DROP TABLE IF EXISTS {database}.{schema}.DEMO_DETECT_TOXICITY_EVAL_RESULTS;
```

### Step 7: Next Steps

```
Thanks for trying the Quick Start demo!

Here's what you learned:
- **Created** an AI function that detects toxic content in English text (binary classification)
- **Evaluated** accuracy using the built-in exact_match metric against real-world labeled data

This is the core create → evaluate loop. From here you can:
- **Optimize** your function's prompt (try the "Prompt Optimization" demo)
- **Build custom metrics** for richer evaluation (try the "Custom Evaluation Metrics" demo)
- **Process images or PDFs** with multimodal AI (try the multimodal demos)

Ready to build your own AI function? Just say "create an AI function" to get started.
```

## Stopping Points

- ✋ Step 1: After introduction, before proceeding
- ✋ Step 2: After location selection
- ✋ Step 4: Before creating function (confirm settings)
- ✋ Step 5: Before running evaluation (confirm settings)
- ✋ Step 6: Before cleanup (confirm choice)
