---
name: create-ai-function
description: "Create a new custom AI function. Supports table-based or manual input specification, single or variant outputs. Direct AI_COMPLETE calls or additional pre- and post-processing."
parent_skill: cortex-ai-function-studio
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
| `task_description` | Yes | - | No | - |
| `clarifications` | Yes | (gathered) | No | task_description |
| `input_source` | Yes | - | No | - |
| `source_table` | If from_table | - | No | input_source |
| `source_stage` | If multimodal | @{db}.{schema}.AI_FUNCTIONS | No | input_source |
| `inputs` | Yes | - | **Yes** | input_source |
| `outputs` | Yes | - | **Yes** | - |
| `creation_mode` | Yes (direct, research) | - | No | clarifications, inputs, outputs |
| `selected_approach` | If research | - | **Yes** | creation_mode |
| `system_prompt` | If direct | (generated) | **Yes** | task_description |
| `user_prompt_template` | If direct | (generated) | **Yes** | inputs |
| `database` | Yes | (from prerequisites) | No | - |
| `schema` | Yes | (from prerequisites) | No | - |
| `function_name` | Yes | (generated) | No | - |
| `model` | Yes | claude-sonnet-4-5 | No | - |
| `udf_body_sql` | If research | (generated) | **Yes** | selected_approach, inputs, outputs, model |

**Critical fields** (always confirm even if pre-provided): `selected_approach` + `udf_body_sql` (only if use research mode), `system_prompt` + `user_prompt_template` (direct mode), `inputs`, `outputs`

**Simple fields** (accept silently if pre-provided): `creation_mode`, `input_source`, `source_table`, `database`, `schema`, `function_name`, `model`, `task_description`

## Pre-Collection

At workflow start, before prompting:

1. **Parse the user's initial message** for any pre-provided information:
   - Task description (e.g., "create a function that classifies sentiment...")
   - Creation mode hints (e.g., "simple function" → direct, "research the best approach" / "I want to add post-processing" → research)
   - Input/output definitions (e.g., "...takes a TEXT input and returns sentiment and confidence")
   - Target location (e.g., "...in MY_DB.MY_SCHEMA")
   - Function name (e.g., "...called CLASSIFY_SENTIMENT")
   - Model preference (e.g., "...using claude-sonnet-4-5")

2. **Check conversation context** for inherited values:
   - Database/schema from prerequisites
   - Function context if coming from another skill

3. **Mark collected fields** and note their source (user message, context, or default)

4. **Identify missing required fields** to determine which steps need prompting

---

## Workflow

### Planning

#### Step 1: Gather Intention

**If `task_description` already collected** (user described what the function should do):
- Accept silently and proceed to Step 2.

**If `task_description` not collected**, ask:
```
What should this AI function do? Describe the task in 1-2 sentences.

For example:
- "Classify customer support tickets into categories"
- "Extract named entities from legal documents"
- "Score product review quality on a 0-1 scale"
```

#### Step 2: Clarifying Questions

Based on the `task_description`, ask targeted clarifying questions. **Only ask questions whose answers are not already apparent from the user's message.** Batch related questions together — do not ask one at a time.

Questions to consider (ask only what's missing):
- **Domain/context**: What industry or domain? (e.g., customer support, legal, medical, finance)
- **Input characteristics**: What does the input data look like? (free text, structured fields, mixed, multiple columns)
- **Output expectations**: What does "good output" look like? (exact categories, free-form text, numeric scores, structured JSON)
- **Edge cases**: Any special handling needed? (nulls, empty inputs, multilingual content, ambiguous cases)
- **Quality vs. speed tradeoffs**: Is accuracy or latency more important?

**⚠️ STOP**: Wait for user answers before proceeding.

#### Step 3: Define Inputs and Outputs

**If the user explicitly asks to process images, documents, or files from a stage**, load `references/multimodal_setup.md` and follow its workflow for multimodal input handling, model selection, and UDF generation. The default is text-only — do not suggest multimodal unprompted.

**Determine input source:**

**If `inputs` already collected** (user provided input definitions):
- Skip input source determination
- Show for confirmation

**If `source_table` already collected** (user mentioned a table):
- Input source is implicitly "from_table"
- Run `DESCRIBE TABLE <table_name>` and ask user to select input/output columns

**If neither collected**, ask:
```
How would you like to define your AI function inputs?

1. **From Table** - Extract schema from a Snowflake table
2. **Manual Spec** - Define inputs manually (names, types)
```

**If From Table:**
```sql
DESCRIBE TABLE <table_name>
```
Show columns and ask user to select which are **inputs** and what **outputs** the function should return.

**Detecting file inputs from table schema:** When running `DESCRIBE TABLE`, check each selected input column's data type:
- If type is **FILE** → use `sql_type: "FILE"` in the config. The function will accept FILE directly.
- If type is **VARCHAR** and sample values look like stage file paths (contain `/`, end with file extensions like `.jpg`, `.pdf`, etc.) → use `sql_type: "STAGE_FILE_PATH"`. The function will cast to FILE internally via `TO_FILE()`. Ask user for the stage name.
- Otherwise → regular text/scalar input.

The function signature must match the table's column types so `SELECT func(col) FROM table` works without any extra wrapping. Load `references/multimodal_setup.md` for full detection flow and model selection when file inputs are detected.

**If Manual Spec:** Collect input parameters and output fields:
```
Input 1:
  - Name: (e.g., customer_message)
  - SQL Type: [VARCHAR] (or NUMBER, FLOAT, BOOLEAN, VARIANT, FILE)

Output 1:
  - Name: (e.g., sentiment)
  - JSON Type: [string] (or number, boolean, array, object)
  - Description: (e.g., "The detected sentiment")
```

**⚠️ STOP**: Always confirm inputs/outputs before proceeding (critical field).

#### Step 4: Select Creation Mode

Based on the task description and clarifications, present the creation modes:
```
Now that I understand your task, how would you like to build the function?

1. **Direct** — I'll create a straightforward AI_COMPLETE function with a system prompt
   and user prompt template. Best for simple tasks where the LLM output is used as-is.
2. **Agent Research** (experimental) — I'll research state-of-the-art techniques and
   propose implementation approaches with SQL pre- and post-processing strategies.
   You'll pick the approach that fits best, or describe your own.
```

**⚠️ STOP**: Wait for customer to select a mode.

**If Direct** → skip to Create phase (Step 7).

**If Agent Research** → continue to Step 5.

#### Step 5: Research and Present Approaches

With a clear understanding of the task, research how to best implement it as a Snowflake SQL UDF.

**Web search** — Search for state-of-the-art techniques relevant to the customer's specific task. Example queries:
   - "best approach for [task] with LLMs"
   - "[task] prompt engineering techniques"
   - "[task] structured output best practices"

   Focus on findings that translate to a SQL UDF implementation: prompting strategies, output structuring, validation techniques, and error handling. 
   A realistic combination would be SQL pre and post processings steps. Note, we cannot finetune or directly change model weights. Treat the model as a black box, but we can do Turing complete computation with SQL before and after calling AI_COMPLETE.

Synthesize web research and pre-built patterns to identify ~1-3 concrete approaches.

Present the approaches to the customer. Each approach must vary in **how the SQL UDF body is structured** — not just in prompt wording. For each approach, show:

1. **Name** and one-line description
2. **SQL UDF body sketch** — the complete `$$ ... $$` body showing where AI_COMPLETE sits relative to other SQL expressions
3. **Pros and cons** — quality, complexity, cost, latency, maintainability
4. **Best for** — when this approach is the right choice
5. **Recommended** - pick one of the approaches and tell the customer that this is the recommended approach

If there is high variance in solutions, order them from simplest to most complex.

**Always include a final option** after the researched approaches:
```
N+1. **Custom** — None of these fit? Describe your own pre- and post-processing strategy
     and I'll build the SQL UDF accordingly.
```

If the customer selects Custom, ask them to describe what they have in mind:
```
Describe the approach you'd like to take — what should happen before or after the
AI_COMPLETE call? I'll translate your idea into a concrete SQL UDF.
```

**⚠️ STOP**: Wait for customer to select an approach or describe their own. If they want modifications to any option, iterate.

#### Step 6: Confirm Approach

**Agent Research mode only.** Direct mode skips this step.

Confirm the selected or described approach. Summarize what was chosen:
```
Selected approach: [name]

I'll build a SQL UDF that:
- [key structural characteristic]
- [key behavioral characteristic]

Proceeding to generate the full function.
```

---

### Create

#### Step 7: Select Target Location and Model

Use the target `database` and `schema` values that were already collected during prerequisites.

**If `function_name` already collected**, accept silently.

**If not collected**: Generate a suggested name (SCREAMING_SNAKE_CASE) based on task description or outputs. Ask user to accept or provide their own.

**If `model` already collected**, accept silently.

**If not collected**: It is MANDATORY to **load** `references/model_selection.md` and follow its full workflow.

#### Step 8: Generate UDF

**Direct mode:**

	Generate the system prompt and user prompt template from collected information.

	**If `system_prompt` not collected**, auto-generate from task description and clarifications:
	```
	You are an expert [domain] assistant. Your task is to [task_description].

	Guidelines:
	- [specific instruction based on clarifications]
	- [edge case handling]
	- Always respond in the specified JSON format
	```
	Show for confirmation: "I'll use this system prompt:\n\n{system_prompt}\n\nConfirm or edit?"

	**If `user_prompt_template` not collected**, auto-generate from input parameter names:
	- Example for inputs `[TEXT, LANGUAGE]`:
	  ```
	  Analyze the following text:

	  Text: {TEXT}
	  Language: {LANGUAGE}
	  ```
	Show for confirmation: "I'll use this user prompt template:\n\n{user_prompt_template}\n\nConfirm or edit?"

	**⚠️ STOP**: Always confirm system prompt and user prompt template before proceeding (critical fields for direct mode).

**Agent Research mode:**

	Based on the confirmed approach, inputs, outputs, and model, write the complete `CREATE OR REPLACE FUNCTION` DDL.

	**The UDF body is NOT limited to a single AI_COMPLETE call.** It should follow the structural pattern selected in the Planning phase, which may include:
	- SQL pre-processing on **scalar inputs only** (CASE WHEN, CONCAT, IFF, OBJECT_CONSTRUCT) — do NOT pre-process ARRAY or VARIANT inputs as they have indefinite length; pass them directly to AI_COMPLETE
	- AI_COMPLETE call (exactly one)
	- SQL post-processing (TRY_PARSE_JSON, COALESCE, type casting, CASE WHEN)
	- Scalar subqueries for intermediate variables: `(SELECT expr FROM (SELECT AI_COMPLETE(...) AS r))`
	- REDUCE for iterative transformations on array results
	- Any other valid SQL expressions

	**Show the complete DDL to the user:**
	```sql
	CREATE OR REPLACE FUNCTION DB.SCHEMA.FUNCTION_NAME(inputs...)
	RETURNS <return_type>
	LANGUAGE SQL
	COMMENT = '<description>'
	AS
	$$
	    <complete UDF body following the selected approach>
	$$;
	```

	**⚠️ STOP**: Always confirm the complete DDL before executing (critical field). The user may want to adjust the prompt, add edge case handling, change the SQL logic, etc.

#### Step 9: Create UDF

After confirmation, execute the DDL.

**Direct mode** (AI_COMPLETE with structured output, matching the JSON config schema):

	Build the JSON configuration from confirmed system prompt, user prompt template, inputs, and outputs:
	```bash
	uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/create_udf.py \
	    --execute \
	    --json '<JSON_CONFIG>' \
	    --connection <CONNECTION_NAME> \
	    --warehouse <WAREHOUSE_NAME>
	```

**Agent Research mode** (arbitrary SQL UDF body):

	Pass the confirmed DDL directly:
	```bash
	uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/create_udf.py \
	    --execute \
	    --sql-body '<COMPLETE_CREATE_FUNCTION_DDL>' \
	    --connection <CONNECTION_NAME> \
	    --warehouse <WAREHOUSE_NAME>
	```

Both modes handle object tagging and query tag logging automatically.

After creation, test with a sample query:
```sql
SELECT <function_name>(<sample_input>) AS result;
```

If there are multiple pre- and post-processing steps, we should test edge cases to confirm that the function behaves correctly.

Verify execution, format, types, and edge cases.

#### Step 10: Next Steps

Present to user:
```
Your AI function is ready!

**Recommended next step:** Evaluate your function against labeled test data to establish a performance baseline.

What would you like to do?
1. **Evaluate** (recommended) - Measure function performance against a labeled dataset
2. **Generate Data** - Generate synthetic training/test data if you don't have labeled examples
3. **Test** - Run a quick test with sample inputs via SQL
4. **Done** - Exit for now
```

If evaluate → Load `evaluate/SKILL.md` with context:
- Preserve function name, database, schema
- Note: *"You'll need a test table with input columns and expected outputs to evaluate."*

---

## Best Practices

**SQL UDF Bodies:** Custom AI functions are `LANGUAGE SQL` UDFs. The body is a single SQL expression (not a statement block). Use scalar subqueries `(SELECT expr FROM (SELECT AI_COMPLETE(...) AS r))` when the AI_COMPLETE result is referenced multiple times. Inside UDF bodies, use `ARRAY_CONSTRUCT()` (not `[...]`), `OBJECT_CONSTRUCT()` (not `{...}`), and `PARSE_JSON('...')` for the response_format value. Named parameters (`model=>`, `messages=>`, `response_format=>`) are valid. AI_COMPLETE is called exactly once per invocation. Do not apply input enrichment to ARRAY or VARIANT inputs — their indefinite length makes them unsuitable for SQL-level transformation; pass them directly to AI_COMPLETE. When the body uses `OBJECT_CONSTRUCT(...)`, declare `RETURNS OBJECT` (not `RETURNS VARIANT`) — Snowflake enforces strict return type compatibility. Do not use PostgreSQL `E'...'` escape string syntax; use regular `'\n'` or `CHAR(10)` for newlines.

**System Prompts:** Be specific, define edge cases, use structure, add examples for complex tasks, keep focused.

**JSON Schema:** Use correct types (`string`, `number`, `boolean`, `array`, `object`), mark required fields, define `items` for arrays.

**Output Types:** VARCHAR (text), FLOAT/NUMBER (scores), BOOLEAN (flags), ARRAY (lists), OBJECT (multiple fields via OBJECT_CONSTRUCT), VARIANT (raw AI_COMPLETE output with multiple fields).

**Testing:** Test typical inputs, edge cases (NULL, empty, long), verify types and structure.

**Performance:** Choose appropriate model, keep prompts concise, use structured output.

## Common Patterns

- **Classification:** Single string output (category)
- **Extraction:** Multiple fields (VARIANT)
- **Sentiment:** sentiment (string) + confidence (number)
- **Transformation:** Single string output (transformed text)
- **Validation:** is_valid (boolean) + errors (array)
- **Scoring:** Numeric output with range validation (Output Transformation pattern)
- **Conditional Processing:** Different prompts for different input types (Input Enrichment pattern)
- **Structured Extraction:** LLM extracts raw fields, SQL reshapes into precise output (Output Transformation - heavy)
- **Find-and-Replace:** LLM identifies items, SQL applies REPLACE iteratively (Iterative Transformation pattern)

## Stopping Points

Planning:
- ✋ Step 2: After clarifying questions — wait for answers
- ✋ Step 3: Confirm inputs/outputs
- ✋ Step 4: After presenting creation modes — wait for selection
- ✋ Step 5: (research) After presenting approaches — wait for selection or custom description
- ✋ Step 6: (research) Confirm selected approach

Create:
- ✋ Step 8: (direct) Confirm system prompt and user prompt template
- ✋ Step 8: (research) Confirm complete DDL before execution
- ✋ Step 10: Before setting up infrastructure
