---
name: clothing-classification-demo
description: "Interactive demo: Build a multimodal clothing condition classifier using expert-labeled garment images, then evaluate and optimize across models via GEPA optimization."
parent_skill: demos
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Clothing Condition Classification Demo

Build a multimodal AI function that classifies second-hand garment condition from images, then optimize across models via GEPA optimization.

## Overview

Load expert-labeled garment images → build a classification function → evaluate baseline → run GEPA optimization → compare cost/quality trade-offs. This is a **multimodal** demo: the AI function reads images from a Snowflake stage using `TO_FILE()`. **Estimated time:** 30-60 minutes (GEPA optimization accounts for most of this).

## Workflow

### Step 1: Introduction

Explain to user:
```
Welcome to the Clothing Condition Classification Demo!

At the end of this demo, you will witness the Cortex AI Function Studio's ability to:
- Classify garment condition from images using multimodal AI_COMPLETE
- Evaluate classification accuracy across 5 condition grades
- Optimize functions using GEPA to improve smaller, cheaper models
- Compare cost vs. quality trade-offs across models using Pareto analysis

This demo uses expert-labeled data from the Fashion Second-Hand dataset
(CC BY 4.0), a corpus of 31K+ garment images annotated by textile sorting
professionals on a 1-5 condition scale:

  like_new (5)       — ready for direct resale
  good (4)           — minor wear, reusable
  fair (3)           — visible wear, still functional
  poor (2)           — significant damage
  unsalvageable (1)  — not suitable for reuse

This is a multimodal demo — the AI function reads garment images from a
Snowflake stage and classifies them by visual inspection alone.

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

### Step 3: Dataset Consent & Data Loading

Explain to user:
```
This demo requires downloading garment images to your local machine before
uploading them to your Snowflake stage.

Dataset: Fashion Second-Hand (front-only RGB)
Source:  https://huggingface.co/datasets/fnauman/fashion-second-hand-front-only-rgb
Author:  Faizan Nauman et al.
License: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)

The script will:
1. Download a balanced subset of images from HuggingFace to a local temp directory
2. Resize to max 800px (avg ~24KB per image)
3. Upload to an SSE-encrypted Snowflake stage: DEMO_CLOTHING_STAGE
4. Create stratified train/test tables from the stage directory
5. Clean up the local temp directory after upload

Approximate download size: ~12 MB (default: 500 images at ~24KB each)

Do you consent to downloading this dataset? (yes/no)
```

**⚠️ STOP**: The user **must** answer yes or no.

**If no**, show the expected results summary and end the demo:
```
No problem! Here's what you would typically see if you ran this demo to completion:

Baseline accuracy (generic prompt, exact_match): ~15%
After GEPA optimization (light budget, 3 vision models):

| Model                  | Best Optimized | Improvement |
|------------------------|----------------|-------------|
| gemini-2.5-flash-lite  | ~50%           | +35 pp      |
| gemini-2.5-flash       | ~40%           | +25 pp      |
| claude-haiku-4-5       | ~40%           | +25 pp      |

Key insight: GEPA evolves the generic 7-word prompt into a detailed grading
rubric with visual cue definitions (pilling, stains, holes, fabric integrity),
calibration notes, and tie-breaking rules — closing a 35-point quality gap
through GEPA optimization alone.

Condition grading is inherently subjective (even experts disagree on adjacent
grades), so 50% exact-match on a 5-class task represents meaningful improvement.

Ready to try a different demo? Just say "demo" to see available options.
```
**End the demo here.** Do not continue to Step 4.

**If yes**, proceed:
```
How many images per class? [default: 30, total = 5 × this value]
```

**⚠️ STOP**: Wait for user to specify per-class count (or confirm default) before proceeding.

Run the data generation script:
```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/generate_clothing_data.py \
  --connection <CONNECTION_NAME> \
  --database {database} \
  --schema {schema} \
  --per-class {per_class}
```

**Note:** Replace `<SKILL_DIRECTORY>` with the absolute path to the cortex-ai-function-studio skill directory, and `<CONNECTION_NAME>` with the active Snowflake connection.

Verify creation:
```sql
SELECT 'TRAIN' AS SPLIT, EXPECTED_OUTPUT, COUNT(*) AS CNT
FROM {database}.{schema}.DEMO_CLOTHING_TRAIN GROUP BY 1, 2
UNION ALL
SELECT 'TEST', EXPECTED_OUTPUT, COUNT(*)
FROM {database}.{schema}.DEMO_CLOTHING_TEST GROUP BY 1, 2
ORDER BY SPLIT, EXPECTED_OUTPUT;
```

Confirm the stage has folder-per-class structure:
```sql
SELECT SPLIT_PART(RELATIVE_PATH, '/', 1) AS LABEL, COUNT(*) AS FILE_COUNT
FROM DIRECTORY(@{database}.{schema}.DEMO_CLOTHING_STAGE)
WHERE ARRAY_SIZE(SPLIT(RELATIVE_PATH, '/')) = 2
GROUP BY LABEL ORDER BY FILE_COUNT DESC;
```

### Step 4: Create the Classification AI Function

Present the function configuration:
```
Now we'll create a multimodal AI function that classifies garment condition
from images.

Default model: gemini-2.5-flash-lite
Stage: @{database}.{schema}.DEMO_CLOTHING_STAGE

Function name: DEMO_CLASSIFY_CLOTHING
Input: FILE_PATH (VARCHAR) — relative path to garment image on stage
Output: condition (string) — one of: like_new, good, fair, poor, unsalvageable

System prompt:
"Classify the condition of this garment image."

User prompt template: "{FILE_PATH}"
```

**⚠️ STOP**: Wait for user confirmation before creating the function.

**Load** `create/SKILL.md` and follow it from **Step 7 onward**, passing:
- `database`, `schema`
- `function_name`: `DEMO_CLASSIFY_CLOTHING`
- `function_intention`: `Classify second-hand garment condition from images.`
- `model`: `gemini-2.5-flash-lite`
- `stage_name`: `@{database}.{schema}.DEMO_CLOTHING_STAGE`
- `inputs`: `[{"name": "FILE_PATH", "sql_type": "STAGE_FILE_PATH"}]`
- `outputs`: `[{"name": "condition", "json_type": "string", "description": "One of: like_new, good, fair, poor, unsalvageable"}]`
- `system_prompt`: confirmed prompt
- `user_prompt_template`: `{FILE_PATH}`

Return here after the smoke test succeeds.

**Troubleshooting:** If the smoke test fails, verify the stage has SSE encryption and the model supports vision inputs. Try a different vision model (see `references/multimodal_setup.md` for the full list).

### Step 5: Evaluate the Classification Function

Present the evaluation configuration:
```
Let's evaluate how well the function classifies garments on the held-out test set.

Metric: exact_match
Results table: DEMO_CLASSIFY_CLOTHING_EVAL_RESULTS
```

**⚠️ STOP**: Wait for user confirmation before running evaluation.

**Load** `evaluate/SKILL.md` and follow it from **Step 5 onward**, passing:
- `function_name`: `{database}.{schema}.DEMO_CLASSIFY_CLOTHING`
- `function_model`: `gemini-2.5-flash-lite`
- `test_table`: `{database}.{schema}.DEMO_CLOTHING_TEST`
- `input_columns`: `['FILE_PATH']`
- `label_column`: `EXPECTED_OUTPUT`
- `metric`: `exact_match`
- `results_table`: `{database}.{schema}.DEMO_CLASSIFY_CLOTHING_EVAL_RESULTS`

**Sync by default:** Use sync execution with `timeout_seconds: 14400`. Only fall back to async if the sync execution times out or the user explicitly requests it.

**Skip Step 6 (next steps)** in the evaluate workflow — return here after results are presented.

Once evaluation is done, review the results. Show the scores to the user. Offer to see misclassifications:
```
Would you like to see which garments were misclassified?
```

If yes, query the results table:
```sql
SELECT
    LEFT(INPUT_TEXT, 80) AS FILE,
    EXPECTED AS EXPECTED_LABEL,
    PREDICTED AS PREDICTED_LABEL,
    SCORE
FROM {database}.{schema}.DEMO_CLASSIFY_CLOTHING_EVAL_RESULTS
WHERE SCORE < 1.0
ORDER BY SCORE
LIMIT 10;
```

Discuss common failure patterns: adjacent classes are easily confused (e.g., `good` vs `fair`), condition grading is inherently subjective from a single image, and a generic function body doesn't tell the model what visual cues distinguish each grade. This is exactly what GEPA can improve.

After reviewing results, continue to Step 6.

### Step 6: Optimize with GEPA

Present the optimization configuration:
```
Now we'll use GEPA to optimize the classification prompt.

GEPA (Genetic-Pareto Algorithm) evolves the system prompt through multiple
generations:
1. Scores prompt variations against training examples
2. Reflects on failures — analyzing WHY specific classifications went wrong
   (e.g., "garments with pilling were misclassified as fair instead of poor")
3. Generates new prompt variations informed by those reflections
4. Keeps only Pareto-optimal performers (best quality at each cost level)

Please confirm or modify any settings:

Auto budget: demo (~5 minutes)
Metric: exact_match
Label column: EXPECTED_OUTPUT
Experiment: DEMO_CLASSIFY_CLOTHING_OPT_EXP
Models: ['gemini-2.5-flash-lite', 'gemini-2.5-flash', 'claude-haiku-4-5']

Options:
1. Yes - Run GEPA optimization with these settings
2. Modify - Change settings before running
3. No - Skip to cleanup
```

**⚠️ STOP**: Wait for user confirmation before starting optimization.

If user chooses No, skip to Step 8.

If yes, **load** `optimize/SKILL.md` and follow it from **Step 6 onward**, passing:
- `function_name`: `{database}.{schema}.DEMO_CLASSIFY_CLOTHING`
- `training_table`: `{database}.{schema}.DEMO_CLOTHING_TRAIN`
- `test_table`: `{database}.{schema}.DEMO_CLOTHING_TEST`
- `input_columns`: `['FILE_PATH']`
- `label_column`: `EXPECTED_OUTPUT`
- `metric`: `exact_match`
- `models`: `['gemini-2.5-flash-lite', 'gemini-2.5-flash', 'claude-haiku-4-5']`
- `reflection_model`: `claude-opus-4-6` (or strongest available Claude-family model)
- `auto_budget`: `demo`
- `experiment_name`: `{database}.{schema}.DEMO_CLASSIFY_CLOTHING_OPT_EXP`

**Sync by default:** Use sync execution with `timeout_seconds: 14400`. Only fall back to async if the user explicitly requests it or if the sync execution times out.

**Skip Step 8 (next steps)** in the optimize workflow — return here after results are presented and the user has decided whether to apply the optimized function.

### Step 7: Summarize GEPA Results

After optimization completes, present results and compare before/after.

**7.1. Show the optimization journey:**

```sql
-- List all runs (SEED, ITER_1..N, BEST per model)
SHOW RUNS IN EXPERIMENT {database}.{schema}.DEMO_CLASSIFY_CLOTHING_OPT_EXP;

-- View metrics for each run to see how scores evolved
-- Run names follow the pattern: {MODEL}_SEED, {MODEL}_ITER_1, ..., {MODEL}_BEST
-- where {MODEL} is the uppercased model name with non-alphanumeric chars replaced by _
-- e.g. gemini-2.5-flash-lite -> GEMINI_2_5_FLASH_LITE
SHOW RUN METRICS IN EXPERIMENT {database}.{schema}.DEMO_CLASSIFY_CLOTHING_OPT_EXP;
```

Highlight how prompts evolve — early candidates use generic language ("classify this garment"), later candidates include specific visual cues (pilling, stains, holes, fabric integrity, color fading) that GEPA learned from misclassification feedback.

**7.2. Compare baseline vs. optimized:**

```sql
-- Compare seed vs best for each model
SHOW RUN METRICS IN EXPERIMENT {database}.{schema}.DEMO_CLASSIFY_CLOTHING_OPT_EXP;
-- Look for {MODEL}_SEED and {MODEL}_BEST rows and compare valset_score
```

**7.3. Pareto analysis for cost/quality:**

Note: for multimodal functions, actual cost is dominated by image token count, not text chars. The Pareto filter gives a relative cost ordering which is still directionally useful.

```bash
PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/filter_pareto.py \
    --json '[{"model": "model1", "score": 0.85}, ...]' \
    --prompt-chars {prompt_chars} --avg-output-chars 5 \
    --seed-score {baseline_score} --format table
```

**7.4. Summarize key findings:**

```
Key result: {best_model} achieves {optimized_score}% classification accuracy
at ~{cost_ratio}x cheaper than {strong_reference}. GEPA closed the quality gap
through GEPA optimization alone.

What GEPA learned:
- Reflected on misclassifications across {total_candidates} prompt variations
- Learned condition-specific visual cues (pilling severity, stain coverage,
  hole size, fabric stretch, color fading) that a generic prompt misses
- Evolved from "classify this garment" to detailed grading rubrics that
  align with expert textile sorting standards
```

### Step 8: Cleanup

Ask user:
```
The Clothing Condition Classification demo is complete!

Would you like to clean up the demo objects?

This will drop:
- {database}.{schema}.DEMO_CLOTHING_STAGE (stage + images)
- {database}.{schema}.DEMO_CLOTHING_TRAIN
- {database}.{schema}.DEMO_CLOTHING_TEST
- {database}.{schema}.DEMO_CLASSIFY_CLOTHING (function)
- {database}.{schema}.DEMO_CLASSIFY_CLOTHING_EVAL_RESULTS
- {database}.{schema}.DEMO_CLASSIFY_CLOTHING_OPT_EXP
```

**⚠️ STOP**: Wait for user confirmation before cleanup.

If yes, execute:
```sql
DROP STAGE IF EXISTS {database}.{schema}.DEMO_CLOTHING_STAGE;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_CLOTHING_TRAIN;
DROP TABLE IF EXISTS {database}.{schema}.DEMO_CLOTHING_TEST;
DROP FUNCTION IF EXISTS {database}.{schema}.DEMO_CLASSIFY_CLOTHING(VARCHAR);
DROP TABLE IF EXISTS {database}.{schema}.DEMO_CLASSIFY_CLOTHING_EVAL_RESULTS;
DROP EXPERIMENT IF EXISTS {database}.{schema}.DEMO_CLASSIFY_CLOTHING_OPT_EXP;
```

### Step 9: Next Steps

Summarize the workflow: expert-labeled images → multimodal classification function → baseline evaluation → GEPA optimization → cost/quality Pareto analysis.

Explain to user:
```
Thanks for trying the Clothing Condition Classification demo!

Here's what you learned:
- **Created** a multimodal AI function that classifies garments from images
- **Evaluated** classification accuracy using exact_match
- **Optimized** the function using GEPA to improve accuracy across cheaper models

Key takeaways about multimodal GEPA optimization:

  Visual grading is subjective: Expert annotators themselves disagree on
  adjacent grades. GEPA learns to encode the visual criteria that matter
  (pilling, stains, holes, fabric integrity) directly into the prompt.

  Prompt specificity matters more for vision: Generic prompts like "classify
  this image" leave the model guessing about what visual features to focus on.
  GEPA's reflection discovers the discriminative cues automatically.

  Cost savings: The Pareto frontier shows which vision models offer the best
  quality-per-dollar for this task. Smaller models with optimized functions
  often match larger models with generic implementations.

Ready to build your own multimodal AI function? Just say "create an AI function"
and mention that you want to process images.
```

## Key Cautions

- Dataset is CC BY 4.0 — attribute Faizan Nauman et al. if redistributing
- Condition grading is inherently subjective; expert inter-annotator agreement is imperfect
- Image quality and lighting affect classification — real-world deployment should standardize capture conditions
- Stage requires `ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')` for multimodal `TO_FILE()` access

## Stopping Points

- ✋ Step 1: After introduction
- ✋ Step 2: After choosing database and schema
- ✋ Step 3: Dataset consent (yes/no — if no, show expected results and end demo)
- ✋ Step 3: Before loading data (confirm per-class count)
- ✋ Step 4: Before creating the classification function
- ✋ Step 5: Before evaluation
- ✋ Step 6: Before GEPA optimization
- ✋ Step 8: Before cleanup
