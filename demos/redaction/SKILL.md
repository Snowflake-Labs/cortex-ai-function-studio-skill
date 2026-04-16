---
name: redaction-demo
description: "Interactive demo: Build a PII redaction AI function that masks sensitive information."
parent_skill: demos
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# PII Redaction Demo

Build an AI function that redacts personally identifiable information (PII) from text.

## Overview

This demo walks you through:
1. Creating sample data with PII examples
2. Building a custom AI function for PII redaction
3. Evaluating its accuracy
4. Optimizing it

**Time:** 10-20 minutes

## Workflow

### Step 0: Introduction

Explain to user:
```
Welcome to the PII Redaction Demo!

This hands-on demo will guide you through the complete lifecycle of a custom AI function:

1. **Create Sample Data** - Load French-language PII examples from the ai4privacy dataset
2. **Build the Function** - Create a DEMO_REDACT_PII function using Snowflake AI_COMPLETE
3. **Evaluate Performance** - Measure accuracy against expected outputs
4. **Optimize the Function** - Use GEPA optimization to improve results

By the end, you'll have a working PII redaction function and understand how to evaluate and optimize any custom AI function.

**Estimated time:** 10-20 minutes
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
First, I'll create a sample dataset with French-language text containing PII that needs to be redacted.

The data comes from the ai4privacy/pii-masking-300k dataset, filtered to French entries, and includes:
- Names, emails, phone numbers, addresses, IDs, and more
- Both the original text (INPUT_TEXT) and expected redacted output (EXPECTED_OUTPUT)
- A PRIVACY_MASK column showing exactly which PII entities were redacted

I'll create 50 training rows and 30 test rows in these tables:
- {database}.{schema}.DEMO_REDACTION_TRAIN
- {database}.{schema}.DEMO_REDACTION_TEST
```

Run the data generation script with 50 training and 30 test rows:
```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/generate_redaction_data.py \
  --connection <CONNECTION_NAME> \
  --database {database} \
  --schema {schema} \
  --train {train_rows} \
  --test {test_rows} \
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

### Step 3: Create the AI Function

Present the function configuration to the user:

```
Now we'll create an AI function that redacts PII from text.

For now I'll use these settings. Please confirm or modify any you'd like to change:

Function name: DEMO_REDACT_PII
Model: claude-sonnet-4-5
Input: TEXT (VARCHAR) - "Text that may contain personally identifiable information (PII)"
Output: redacted_text (string) - "Redacted text with all PII replaced by [REDACTED]"
System prompt:
  "Identify and redact all PII from the text below, replacing each with [REDACTED]. 
   PII includes: names, email addresses, phone numbers, government IDs (passports, 
   licenses, national IDs, tax IDs), credit card/bank numbers, physical addresses 
   (street, city, building, country), and dates/times. Add no additional text, and 
   immediately start writing the original text (with redactions) without adding any
   preamble. Stop immediately when the input text stops."
User prompt template: "{TEXT}"
```

Store the confirmed settings (function_name, model, system_prompt, user_prompt_template) for use in function creation.

**⚠️ STOP**: Wait for user confirmation or modifications before creating the function.

Create the function using `create_udf.py` with the confirmed settings:

```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/create_udf.py \
    --database {database} \
    --schema {schema} \
    --function-name {function_name} \
    --function-intention 'Redact all PII from input text.' \
    --model {model} \
    --system-prompt '{system_prompt}' \
    --user-prompt-template '{user_prompt_template}' \
    --inputs '[{"name": "TEXT", "sql_type": "VARCHAR"}]' \
    --outputs '[{"name": "redacted_text", "json_type": "string", "description": "Redacted text with all PII replaced by [REDACTED]"}]' \
    --execute --connection <CONNECTION_NAME>
```

This generates a function with the model and system prompt hardcoded in the body:
```sql
CREATE OR REPLACE FUNCTION {database}.{schema}.{function_name}(TEXT VARCHAR)
RETURNS VARCHAR
...
AS
$$
    SNOWFLAKE.CORTEX.AI_COMPLETE(
        model=>'{model}',
        messages=>[
            {'role': 'system', 'content': '{system_prompt}'},
            {'role': 'user', 'content': TEXT}
        ],
        response_format=>{'type': 'json', 'schema': {...}}
    ).redacted_text::VARCHAR
$$;
```

Execute the generated SQL to create the function.

Present the test example to the user:

```
Now let's test the function with a quick example to see it in action.

I'll run a simple query that passes French text containing PII (a name, email, and phone number) 
to the function. The function should return the same text with all PII replaced by [REDACTED].

Example input:
  "Veuillez contacter Jean Dupont à jean.dupont@email.com ou au 01 23 45 67 89"

Would you like to:
1. Run with this example
2. Try a different example (provide your own text)
```

**⚠️ STOP**: Wait for user confirmation or custom example before running.

Run the test:
```sql
SELECT {database}.{schema}.{function_name}(
  '{example_input}'
) AS redacted_text;
```

Show the result and explain what the function did.

### Step 4: Evaluate the Function

Present the evaluation plan to the user:

```
Let's evaluate how well the function performs on our test data (30 rows).

Metric: redaction_match
Results table: DEMO_REDACTION_EVAL_RESULTS

Note: redaction_match compares text structure while ignoring content inside brackets,
so [NAME] and [REDACTED] are treated as equivalent placeholders.
```

Follow the evaluate workflow (`evaluate/SKILL.md`) with these values:
- Function: `{database}.{schema}.DEMO_REDACT_PII`
- Test table: `{database}.{schema}.DEMO_REDACTION_TEST`
- Input column: `INPUT_TEXT`
- Expected column: `EXPECTED_OUTPUT`
- Metric: `redaction_match`
- Results table: `{database}.{schema}.DEMO_REDACTION_EVAL_RESULTS`

Present the evaluation results.

### Step 5: Review Failures

If accuracy < 100%, automatically query and present failures:
```sql
SELECT 
    INPUT_TEXT,
    EXPECTED_OUTPUT AS EXPECTED,
    PREDICTED,
    SCORE
FROM {database}.{schema}.DEMO_REDACTION_EVAL_RESULTS
WHERE SCORE < 1.0
ORDER BY SCORE;
```

Discuss common failure patterns (e.g., different redaction formats, missed PII types).

### Step 6: Optimize (Optional)

Ask user if they want to optimize, then present configuration:

```
Would you like to try optimizing the function?

This will evolve the prompt through multiple generations to find variations 
that improve redaction accuracy. Takes ~2-10 minutes to run. 

For now I'll use these settings. Please confirm or modify any you'd like to change:

Auto budget: demo
Experiment: DEMO_REDACT_PII_OPT_EXP

Options:
1. Yes - Run GEPA optimization with these settings
2. Modify - Change settings before running
3. No - Skip to cleanup
```

**⚠️ STOP**: Wait for user confirmation or modifications before running optimization.

If yes, follow the optimize workflow (`optimize/SKILL.md`) with these values:
- Function: `{database}.{schema}.DEMO_REDACT_PII`
- Training table: `{database}.{schema}.DEMO_REDACTION_TRAIN`
- Test table: `{database}.{schema}.DEMO_REDACTION_TEST`
- Input column: `INPUT_TEXT`
- Label column: `EXPECTED_OUTPUT`
- Metric: `redaction_match`
- Auto budget: demo
- Experiment: `{database}.{schema}.DEMO_REDACT_PII_OPT_EXP`

Present results and compare before/after scores.

### Step 7: Cleanup

Ask user:
```
Demo complete! Would you like to clean up the demo objects?

This will drop:
- {database}.{schema}.DEMO_REDACTION_TRAIN
- {database}.{schema}.DEMO_REDACTION_TEST
- {database}.{schema}.DEMO_REDACT_PII
- {database}.{schema}.DEMO_REDACTION_EVAL_RESULTS (if created)
- {database}.{schema}.DEMO_REDACT_PII_OPT_EXP (if created)

Options:
1. Yes - Clean up all demo objects
2. No - Keep objects for further exploration
```

If yes, execute:
```sql
DROP TABLE IF EXISTS {database}.{schema}.DEMO_REDACTION_TRAIN;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_REDACTION_TEST;
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_REDACT_PII(VARCHAR);
DROP TABLE IF EXISTS {database}.{schema}.DEMO_REDACTION_EVAL_RESULTS;
DROP EXPERIMENT IF EXISTS {database}.{schema}.DEMO_REDACT_PII_OPT_EXP;
```

### Step 8: Next Steps

```
Thanks for trying the PII Redaction demo!

You've learned how to:
- Create an AI function for text processing
- Evaluate function performance with metrics
- Optimize functions using GEPA

Ready to build your own AI function? Just say "create an AI function" to get started.
```

## Stopping Points

- ✋ Step 0: After introduction, before proceeding
- ✋ Step 1: After location selection
- ✋ Step 2: Before loading data (confirm row counts)
- ✋ Step 3: Before creating function (confirm settings)
- ✋ Step 3: Before testing function (confirm example input)
- ✋ Step 4: Before running evaluation (confirm settings)
- ✋ Step 6: Before running optimization (confirm settings)
- ✋ Step 7: Before cleanup
