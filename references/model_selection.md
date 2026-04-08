<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Model Selection Reference

## When to Load

**Always load this reference when model selection is needed.** This is the standard workflow for choosing a model — it dynamically queries the account's available models and presents smart recommendations.

Triggers: model selection step in any workflow, "see more models", "list models", "available models", "which models", "recommend a model".

## Workflow

### Step 1: Check Account Allowlist

```sql
SHOW PARAMETERS LIKE 'cortex_models_allowlist' IN ACCOUNT
```

**Based on the `value` column:**

- **`ALL`**: Proceed to Step 2 to query all available models
- **Comma-separated list** (e.g., `claude-haiku-4-5, claude-sonnet-4-5`): Parse and use only those specific models as the available set
- **`NONE`**: Inform user: "No Cortex models are available for this account. Please contact your administrator to enable model access." Then exit the Cortex AI Function Studio.

### Step 2: Query Available Models

```sql
SHOW MODELS IN SNOWFLAKE.MODELS
```

Intersect the results with the models defined in `src/models.json`. Only models that appear in **both** the `SHOW MODELS` output **and** `models.json` are considered available.

**IF `SHOW MODELS` fails or returns empty:**

1. Use the default family names from Step 3 as fallback options
2. In fallback mode, treat **all families as available** using their preferred picks from the table
3. **Present the first 4 families from the Step 3 table in order**: Claude (`claude-sonnet-4-6`), GPT (`openai-gpt-5.2`), Gemini (`gemini-3-pro`), Llama (`llama3.1-405b`)
4. Continue to Step 4 with these 4 options

⚠️ **SAY THIS TO THE USER** (do not paraphrase):
> The `SNOWFLAKE.MODELS` registry doesn't exist in this account. I'll show you the standard model families. An ACCOUNTADMIN can run `CALL SNOWFLAKE.MODELS.CORTEX_BASE_MODELS_REFRESH();` to populate the full model list.

**IF user asks** "where is model X?" or "why can't I see certain models?":

⚠️ **SAY THIS TO THE USER** (do not paraphrase):
> An ACCOUNTADMIN can run `CALL SNOWFLAKE.MODELS.CORTEX_BASE_MODELS_REFRESH();` to populate the model registry with the latest available Cortex models.

### Step 3: Select Top Recommendations by Family

From the available models, identify the **most popular/recommended model from each major family**. Use this ranking to pick one representative per family:

**Family ranking (pick the first available from each family):**

| Family | Preferred pick (in order) | Fallback |
|--------|--------------------------|----------|
| **Claude** (Anthropic) | `claude-sonnet-4-6` > `claude-sonnet-4-5` > `claude-4-sonnet` | Any available Claude |
| **GPT** (OpenAI) | `openai-gpt-5.2` > `openai-gpt-5.1` > `openai-gpt-5` | Any available GPT |
| **Gemini** (Google) | `gemini-3-pro` > `gemini-2.5-flash` | Any available Gemini |
| **Llama** (Meta) | `llama3.1-405b` > `llama3.3-70b` > `llama3.1-70b` | Any available Llama |
| **Mistral** | `mistral-large2` > `mixtral-8x7b` | Any available Mistral |

Fallback for any family: any available model in that family.

Select the top 3-4 families that have available models (in the order listed above). These become the quick-pick options.

**CRITICAL: When displaying the initial list of models, use ONLY one model per family.** Users who want a specific size variant can select "See all models" to browse the full list.

**Note:** If the calling workflow specifies that a strong/high-quality model is preferred (e.g., for synthetic data generation or reflection models), default to suggesting `claude-opus-4-6` and bias toward the largest and most capable model in each family when presenting options.

### Step 4: Present Options

Present using `ask_user_question` with 3-4 recommended models (one per family) plus a "See all models" option.

**IMPORTANT:** Present options in the same order as the family ranking table in Step 3 (Claude → GPT → Gemini → Llama → Mistral). Skip any families that don't have available models, but maintain the relative order.

**Format each option as:** `model-name` with a brief description noting the family and character (e.g., "Balanced quality/cost", "Fast and affordable", "Highest quality").

Add a final option: **"See all models"** — "View the complete list of available models grouped by family."

**After presenting options, inform the user:**

> Not all models may be available in every region or cloud provider. I can run a quick test after you choose to make sure the model works in your account.

#### If user selects "See all models":

Display **all** available chat models organized by family in a list format. For each family, show all available models with a brief note (size/speed/quality). Let the user pick from the full list.

### Step 5: Verify Model Availability

After the user selects a model (from Step 4, "See all models", or free-text input), run a lightweight test call to confirm the model is actually callable in this account's region and cloud provider:

```sql
SELECT AI_COMPLETE('<selected_model>', 'test') AS test_response
```

**IF the call succeeds:** The model is confirmed available. Proceed with the selected model.

**IF the call fails** (e.g., model not deployed in this region, permission error, unsupported cloud provider):

⚠️ **SAY THIS TO THE USER** (do not paraphrase):
> The model `<selected_model>` is not available in your account's region or cloud provider. Let's pick a different model.

Then return to **Step 4** and re-present the model options, **excluding the failed model** from the list. Continue this loop until the user selects a model that passes verification.

**Note:** Keep a running list of failed models for the duration of the selection workflow so they are excluded from all subsequent presentations (Steps 3, 4, and "See all models").

## Model Validation & Auto-Correction

When user provides a model name (including via "Something else" free-text input):

1. **Validate**: Check if the model exists in the available models list (case-insensitive match)
2. **Auto-correct** common issues:
   - Case normalization: `LLAMA3.1-70B` → `llama3.1-70b`
   - Hyphen variations: `claude-3.5-sonnet` → `claude-3-5-sonnet`
   - Close matches: Suggest the closest valid model if user input is similar
3. **Confirm**: Show the user the resolved model name and get confirmation before proceeding
