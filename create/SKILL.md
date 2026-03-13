---
name: create-ai-function
description: "Create a new custom AI function. Supports table-based or manual input specification, single or variant outputs."
parent_skill: custom-ai-function
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Create AI Function

## When to Load

Load from main skill when user intent matches CREATE: "create", "build", "new" + ai/llm function.

## Information Model

Before starting the workflow, scan the user's message and conversation context for pre-provided information. Track what's collected and only prompt for missing required fields.

| Field | Required | Default | Confirm | Dependencies |
|-------|----------|---------|---------|--------------|
| `input_source` | Yes | - | No | - |
| `source_table` | If from_table | - | No | input_source |
| `inputs` | Yes | - | **Yes** | input_source |
| `outputs` | Yes | - | **Yes** | - |
| `database` | Yes | - | No | - |
| `schema` | Yes | - | No | - |
| `function_name` | Yes | (generated) | No | - |
| `model` | Yes | llama3.1-70b | No | - |
| `task_description` | Yes | - | No | - |
| `function_intention` | Yes | (inferred) | No | task_description |
| `system_prompt` | Yes | (generated) | **Yes** | task_description |
| `user_prompt_template` | Yes | (generated) | **Yes** | inputs |

**Critical fields** (always confirm even if pre-provided): `inputs`, `outputs`, `system_prompt`, `user_prompt_template`

**Simple fields** (accept silently if pre-provided): `input_source`, `source_table`, `database`, `schema`, `function_name`, `model`, `task_description`, `function_intention`

## Pre-Collection

At workflow start, before prompting:

1. **Parse the user's initial message** for any pre-provided information:
   - Task description or system prompt (e.g., "create a function that classifies sentiment...")
   - Input/output definitions (e.g., "...takes a TEXT input and returns sentiment and confidence")
   - Target location (e.g., "...in MY_DB.MY_SCHEMA")
   - Function name (e.g., "...called CLASSIFY_SENTIMENT")
   - Model preference (e.g., "...using llama3.1-405b")

2. **Check conversation context** for inherited values:
   - Database/schema from prior workflow or active connection
   - Function context if coming from another skill

3. **Mark collected fields** and note their source (user message, context, or default)

4. **Identify missing required fields** to determine which steps need prompting

## Workflow

### Step 1: Determine Input Source

**If `inputs` already collected** (user provided input definitions upfront):
- Skip this step entirely — input source is implicitly "manual"
- Mark `input_source` as "manual"

**If `source_table` already collected** (user mentioned a table):
- Skip this step — input source is implicitly "from_table"
- Mark `input_source` as "from_table"
- Proceed to Step 2A

**If neither collected**, ask user:
```
How would you like to define your AI function inputs?

1. **From Table** - Extract schema from a Snowflake table
2. **Manual Spec** - Define inputs manually (names, types)
```

**If From Table:** Go to Step 2A

**If Manual Spec:** Go to Step 2B

### Step 2A: Extract from Table

**If `source_table` not collected**, ask:
```
Provide the fully qualified table name (e.g., DB.SCHEMA.TABLE):
```

Once table name is available, run:
```sql
DESCRIBE TABLE <table_name>
```

**If `inputs` already collected**, show for confirmation:
```
I understood these as your function inputs:
{inputs}

Is this correct? (confirm or edit)
```

**If `inputs` not collected**, show columns and ask user to select:
- Which columns are **inputs** to the function
- What **output fields** the function should return

**⚠️ STOP**: Always confirm inputs/outputs before proceeding (critical field).

Go to Step 3.

### Step 2B: Manual Specification

**If `inputs` already collected**, show for confirmation:
```
I understood these as your function inputs:
{inputs}

Is this correct? (confirm or edit)
```

**If `inputs` not collected**, collect input parameters:
```
Define your function inputs:

Input 1:
  - Name: (e.g., customer_message)
  - SQL Type: [VARCHAR] (or NUMBER, FLOAT, BOOLEAN, VARIANT)

Input 2: (or empty to finish)
  ...
```

**If `outputs` already collected**, show for confirmation:
```
I understood these as your function outputs:
{outputs}

Is this correct? (confirm or edit)
```

**If `outputs` not collected**, collect output fields:
```
Define your function outputs:

Output 1:
  - Name: (e.g., sentiment)
  - JSON Type: [string] (or number, boolean, array, object)
  - Description: (e.g., "The detected sentiment")

Output 2: (or empty to finish)
  ...
```

**⚠️ STOP**: Always confirm inputs/outputs before proceeding (critical field).

### Step 3: Select Target Location and Function Name

**If `database` and `schema` already collected**, accept silently and continue.

**If `database` or `schema` not collected**, ask:
```
Where should the function be created?

Database: [e.g., MY_DB]
Schema: [e.g., MY_SCHEMA]
```

**If `function_name` already collected**, accept silently.

**If `function_name` not collected**:
- Generate suggested function name (SCREAMING_SNAKE_CASE) based on task description or outputs
- Ask user to accept or provide their own

### Step 4: Select Model

**If `model` already collected**, accept silently and continue.

**If `model` not collected**, it is MANDATORY to **load** `references/model_selection.md` and follow its full workflow to dynamically query available models and present smart recommendations.

**⚠️ STOP**: Get user confirmation on model selection before proceeding.

### Step 5: Define System Prompt

**If `system_prompt` already collected** (user provided a complete system prompt):
- Show for confirmation: "I'll use this system prompt:\n\n{system_prompt}\n\nConfirm or edit?"
- Infer `function_intention` from the system prompt (one-line summary)

**If `task_description` collected but `system_prompt` not**:
- Infer `function_intention` from task description
- Generate a system prompt incorporating their input that includes:
  - AI role definition
  - Task description
  - Output format expectations
  - Edge case handling
  - Any constraints
- Show for confirmation

**If neither `task_description` nor `system_prompt` collected**, ask:
```
Describe what your AI function should do (1-2 sentences). If you have an existing system prompt, provide it here:
```

After receiving input:
- Infer `function_intention` from the description/prompt (keep internal, do not display)
- Generate system prompt as above
- Show for confirmation

**⚠️ STOP**: Always confirm system prompt before proceeding (critical field).

### Step 6: Define User Prompt Template

**If `user_prompt_template` already collected**:
- Show for confirmation: "I'll use this user prompt template:\n\n{user_prompt_template}\n\nConfirm or edit?"

**If `user_prompt_template` not collected**:
- Auto-generate based on task description and input parameter names
- Example for inputs `[TEXT, LANGUAGE]`:
  ```
  Analyze the following text:

  Text: {TEXT}
  Language: {LANGUAGE}
  ```
- Show for confirmation

**⚠️ STOP**: Always confirm user prompt template before proceeding (critical field).

### Step 7: Generate SQL with Script

Build the JSON configuration from all collected information:

```json
{
    "database": "<collected_database>",
    "schema": "<collected_schema>",
    "function_name": "<collected_function_name>",
    "function_intention": "<inferred_function_intention>",
    "model": "<selected_model>",
    "inputs": [
        {"name": "<input_1_name>", "sql_type": "<input_1_type>"},
        ...
    ],
    "outputs": [
        {"name": "<output_1_name>", "json_type": "<output_1_type>", "description": "<output_1_desc>"},
        ...
    ],
    "system_prompt": "<confirmed_system_prompt>",
    "user_prompt_template": "<confirmed_user_prompt_template>"
}
```

**Immediately run the script** (do not ask for permission):
```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/create_udf.py \
    --json '<JSON_CONFIG>'
```

Then execute the generated SQL in Snowflake to create the function.

### Step 8: Execute and Test
```sql
SELECT {function_name}({sample_input}) AS result;
-- For VARIANT outputs: result:field_1, result:field_2
```

Verify execution, format, types, and edge cases.

### Step 9: Continue (Optional)

Explain to user:
```
I'll now set up the infrastructure for evaluating and optimizing your AI function:
1. Create a stage for Python code
2. Upload optimizer modules
3. Create evaluation & optimization procedures
```

**⚠️ STOP**: Get user confirmation before proceeding.

**Load** `references/infrastructure_setup.md` and run the deploy script shortcut to provision stage, modules, and all 3 procedures in one call.

### Step 10: Next Steps

Present to user:
```
Your AI function is ready!

**Recommended next step:** Evaluate your function against labeled test data to establish a performance baseline. This helps you understand how well your function performs before investing in optimization.

What would you like to do?
1. **Evaluate** (recommended) - Measure function performance against a labeled dataset
2. **Generate Data** - Generate synthetic training/test data if you don't have labeled examples
3. **Test** - Run a quick test with sample inputs via SQL
4. **Done** - Exit for now
```

If evaluate → Load `evaluate/SKILL.md` with context:
- Preserve function name, database, schema
- Note: *"You'll need a test table with input columns and expected outputs to evaluate."*

## Example

Entity extraction function (persons, organizations, locations from text):

**Collected Information:**
```json
{
    "database": "DB",
    "schema": "SCHEMA",
    "function_name": "EXTRACT_ENTITIES",
    "function_intention": "Extract persons, organizations, and locations from text.",
    "model": "llama3.1-70b",
    "inputs": [
        {"name": "TEXT", "sql_type": "VARCHAR"}
    ],
    "outputs": [
        {"name": "redacted_text", "json_type": "string", "description": "Text with PII replaced by placeholders"}
    ],
    "system_prompt": "Extract persons, organizations, and locations. Return empty arrays if none found.",
    "user_prompt_template": "'Extract entities:\n\n' || text"
}
```

**Generated SQL:**
```sql
CREATE OR REPLACE FUNCTION DB.SCHEMA.EXTRACT_ENTITIES(TEXT VARCHAR, MODEL_NAME VARCHAR DEFAULT 'llama3.1-70b', SYSTEM_PROMPT VARCHAR DEFAULT NULL)
RETURNS VARIANT
LANGUAGE SQL
COMMENT = 'Extract persons, organizations, and locations from text.'
AS
$$
    SNOWFLAKE.CORTEX.AI_COMPLETE(
        model=>MODEL_NAME,
        prompt=>[
            {
                'role': 'system',
                'content': COALESCE(SYSTEM_PROMPT, 'Extract persons, organizations, and locations. Return empty arrays if none found.')
            },
            {
                'role': 'user',
                'content': 'Extract entities:\n\n' || TEXT
            }
        ],
        response_format=>{
            'type': 'json',
            'schema': {
                'type': 'object',
                'properties': {
                    'redacted_text': {
                        'type': 'string',
                        'description': 'Text with PII replaced by placeholders'
                    }
                },
                'required': [
                    'redacted_text'
                ]
            }
        }
    )
$$;
```

**Usage:**
```sql
-- Use default model and system prompt
SELECT EXTRACT_ENTITIES('John works at Snowflake in San Mateo.');

-- Override model at runtime (positional)
SELECT EXTRACT_ENTITIES('John works at Snowflake in San Mateo.', 'llama3.1-405b');

-- Override model at runtime (named parameter)
SELECT EXTRACT_ENTITIES('John works at Snowflake in San Mateo.', MODEL_NAME => 'claude-3-5-sonnet');

-- Override system prompt at runtime
SELECT EXTRACT_ENTITIES(
    'John works at Snowflake in San Mateo.',
    MODEL_NAME => 'llama3.1-70b',
    SYSTEM_PROMPT => 'Extract only person names. Return as JSON array.'
);
```

## Best Practices

**System Prompt:** Be specific, define edge cases, use structure, add examples for complexity, keep focused.

**JSON Schema:** Use correct types (`string`, `number`, `boolean`, `array`, `object`), mark required fields, define `items` for arrays.

**Output Types:** VARCHAR (text), FLOAT/NUMBER (scores), BOOLEAN (flags), ARRAY (lists), VARIANT (multiple fields).

**Testing:** Test typical inputs, edge cases (NULL, empty, long), verify types and structure.

**Performance:** Choose appropriate model, keep prompts concise, use structured output.

## Common Patterns

- **Classification:** Single string output (category)
- **Extraction:** Multiple fields (VARIANT)
- **Sentiment:** sentiment (string) + confidence (number)
- **Transformation:** Single string output (transformed text)
- **Validation:** is_valid (boolean) + errors (array)

## Stopping Points

Critical confirmations (always stop, even if pre-provided):
- ✋ Step 2A/2B: Confirm inputs/outputs
- ✋ Step 5: Confirm system prompt
- ✋ Step 6: Confirm user prompt template

Optional confirmations:
- ✋ Step 9: Before setting up infrastructure
- ✋ Step 10: Final next steps
