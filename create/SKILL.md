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
| `inputs` | Yes | - | **Research only** | input_source |
| `outputs` | Yes | - | **Research only** | - |
| `creation_mode` | Yes (direct, research) | - | No | clarifications, inputs, outputs |
| `selected_approach` | If research | - | **Yes** | creation_mode |
| `system_prompt` | If direct | (generated) | **Yes** | task_description |
| `user_prompt_template` | If direct | (generated) | **Yes** | inputs |
| `database` | Yes | (from prerequisites) | No | - |
| `schema` | Yes | (from prerequisites) | No | - |
| `function_name` | Yes | (generated) | No | - |
| `model` | Yes | claude-sonnet-4-6 | No | - |
| `udf_body_sql` | If research | (generated) | **Yes** | selected_approach, inputs, outputs, model |

**Critical fields** (always confirm even if pre-provided): `selected_approach` + `udf_body_sql` (only if research mode), `system_prompt` + `user_prompt_template` (direct mode). In Direct mode, `inputs` and `outputs` are shown in the Step 8 review block and do not need separate confirmation.

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

**Skip this step unless the task is genuinely ambiguous** — i.e., you cannot generate a reasonable system prompt without more information. Most well-described tasks ("classify sentiment", "extract entities from contracts", "score review quality") do not need clarification. Any minor details (edge cases, formatting preferences) can be adjusted when the user reviews the generated prompt in Step 8.

If clarification is truly needed (e.g., the user said "process my data" with no specifics), ask at most 2-3 targeted questions batched together. Do not ask one at a time.

If you need to ask, wait for user answers before proceeding. Do not gate this as a mandatory stop — only pause if questions were actually asked.

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

**Input type notes:** Semi-structured inputs (`ARRAY`, `VARIANT`, `OBJECT`) are surfaced into the prompt template as JSON strings — `ARRAY` via `ARRAY_TO_STRING(<col>, ', ')`, `VARIANT` and `OBJECT` via `TO_VARCHAR(<col>)`. The evaluate/optimize pipelines do the same conversion, so the prompt sees the JSON serialization rather than native structured data — that is functionally correct, just be aware of it when designing the prompt. Preserve the user's declared SQL types verbatim in the function signature: pass `sql_type: "ARRAY"`, `"VARIANT"`, or `"OBJECT"` to `create_udf.py` exactly as the user described the column; do not silently downcast to `VARCHAR`. For single-output functions, the return type maps from JSON type: `string` -> VARCHAR, `number` -> FLOAT, `integer` -> NUMBER, `boolean` -> BOOLEAN, `array`/`object` -> VARIANT. Multi-output functions always use `RETURNS VARIANT`.

**If creation mode will be Research** (user already indicated research, custom SQL, or pre/post-processing):
- **⚠️ STOP**: Confirm inputs/outputs before proceeding — research depends on accurate I/O definitions and changing them later wastes work.

**If creation mode will be Direct** (the common path):
- Do **not** stop here. Inputs/outputs will be shown in the Step 8 review block where the user confirms everything together. Proceed to Step 4.

#### Step 4: Select Creation Mode

**If any input has a multimodal type (`FILE` or `STAGE_FILE_PATH`)**, Agent Research mode is NOT available — always use Direct mode. Do not offer or mention the research option. Skip to Step 7.

**Default to Direct mode** unless the user explicitly requests research, custom SQL, or pre/post-processing. Do not ask the user to choose a mode — infer it:

- If the user said "research", "explore approaches", "I want pre-processing", or "custom SQL" → Agent Research mode. Continue to Step 5.
- Otherwise → Direct mode. Skip to Create phase (Step 7).

If you are unsure and the task seems complex enough to warrant research, you may briefly offer:
```
This looks like a straightforward task — I'll create a Direct AI_COMPLETE function.
If you'd prefer I research implementation approaches with SQL pre/post-processing, let me know.
```
Then proceed with Direct mode without waiting for a response unless the user intervenes.

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

**If not collected**: Generate a name (SCREAMING_SNAKE_CASE) based on task description or outputs. Do not ask the user to confirm — the name will be shown in the Step 8 review block where they can edit it along with everything else.

**If `model` already collected**, accept silently — unless the user explicitly asked to see model options (e.g., "show me available models"), in which case load `references/model_selection.md` and present the options.

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

  **If `user_prompt_template` not collected**, auto-generate from input parameter names:
  - Example for inputs `[TEXT, LANGUAGE]`:
    ```
    Analyze the following text:

    Text: {TEXT}
    Language: {LANGUAGE}
    ```

  **⚠️ STOP**: Present everything as a single review block. This is the only confirmation point for Direct mode — inputs, outputs, function name, and prompts are all shown here:
  ```
  Here's what I'll create:

  Function: {database}.{schema}.{function_name}
  Model: {model}

  Inputs: {input_name} ({sql_type}), ...
  Outputs: {output_name} ({json_type}), ...
  Returns: {return_type}

  System prompt:
    "{system_prompt}"

  User prompt template:
    "{user_prompt_template}"

  Confirm or edit any of the above?
  ```
  Wait for user to confirm or request edits before proceeding.

**Agent Research mode:**

	Based on the confirmed approach, inputs, outputs, and model, write the complete `CREATE FUNCTION` DDL. Preserve the user's declared SQL types verbatim in the function signature — never silently convert semi-structured inputs (`ARRAY`, `VARIANT`, `OBJECT`) to `VARCHAR`.

	**The UDF body is NOT limited to a single AI_COMPLETE call.** It should follow the structural pattern selected in the Planning phase, which may include:
	- SQL pre-processing on **scalar inputs only** (CASE WHEN, CONCAT, IFF, OBJECT_CONSTRUCT) — do NOT pre-process ARRAY or VARIANT inputs as they have indefinite length; pass them directly to AI_COMPLETE
	- AI_COMPLETE call (exactly one)
	- SQL post-processing (TRY_PARSE_JSON, COALESCE, type casting, CASE WHEN)
	- Scalar subqueries for intermediate variables: `(SELECT expr FROM (SELECT AI_COMPLETE(...) AS r))`
	- REDUCE for iterative transformations on array results
	- Any other valid SQL expressions

	**Show the complete DDL to the user:**
	```sql
	CREATE FUNCTION DB.SCHEMA.FUNCTION_NAME(inputs...)
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

  Pass the confirmed configuration as individual CLI flags:
  ```bash
  PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/create_udf.py \
      --execute \
      --connection <CONNECTION_NAME> \
      --database <DATABASE> \
      --schema <SCHEMA> \
      --function-name <FUNCTION_NAME> \
      --function-intention '<FUNCTION_INTENTION>' \
      --model <MODEL> \
      --system-prompt '<SYSTEM_PROMPT>' \
      --user-prompt-template '<USER_PROMPT_TEMPLATE>' \
      --inputs '<INPUTS_JSON_ARRAY>' \
      --outputs '<OUTPUTS_JSON_ARRAY>'
  ```
  If any input uses `sql_type: "STAGE_FILE_PATH"`, also pass `--stage-name <STAGE_NAME>`. Not needed for native `FILE` column inputs.

**Agent Research mode** (arbitrary SQL UDF body):

	Pass the confirmed DDL directly:
	```bash
	PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/src/create_udf.py \
	    --execute \
	    --sql-body '<COMPLETE_CREATE_FUNCTION_DDL>' \
	    --connection <CONNECTION_NAME>
	```

Both modes handle object tagging and query tag logging automatically.

After creation, test with a sample query:
```sql
SELECT <function_name>(<sample_input>) AS result;
```

**Important — return type awareness for smoke tests:**
- **Single-output functions** (one entry in `outputs`): The UDF body already extracts the field (e.g., `:route::VARCHAR`) and `RETURNS` the scalar type (VARCHAR, FLOAT, etc.). Call the function directly — do **NOT** add a `:field_name` accessor on the outside. Example: `SELECT my_func(input) AS result` (correct), NOT `SELECT my_func(input):route` (error: GET on VARCHAR).
- **Multi-output functions** (multiple entries in `outputs`): The function `RETURNS VARIANT` containing all fields. Access individual fields with `:field_name::TYPE`. Example: `SELECT my_func(input):sentiment::VARCHAR AS sentiment, my_func(input):confidence::FLOAT AS confidence`.

If there are multiple pre- and post-processing steps, we should test edge cases to confirm that the function behaves correctly.

Verify execution, format, types, and edge cases.

#### Step 10: Next Steps

Present to user:
```
Your AI function is ready!

**Recommended next step:** Evaluate your function against labeled test data to establish a performance baseline.

What would you like to do?
1. **Evaluate** (recommended) - Measure function performance against a labeled dataset
2. **Test** - Run a quick test with sample inputs via SQL
3. **Done** - Exit for now
```

If evaluate → Load `evaluate/SKILL.md` with context:
- Preserve function name, database, schema
- Note: *"You'll need a test table with input columns and expected outputs to evaluate."*

---

## Best Practices

**SQL UDF Bodies:** Custom AI functions are `LANGUAGE SQL` UDFs. The body is a single SQL expression (not a statement block). Use scalar subqueries `(SELECT expr FROM (SELECT AI_COMPLETE(...) AS r))` when the AI_COMPLETE result is referenced multiple times. Inside UDF bodies, use `ARRAY_CONSTRUCT()` (not `[...]`), `OBJECT_CONSTRUCT()` (not `{...}`), and `PARSE_JSON('...')` for the response_format value. AI_COMPLETE is called exactly once per invocation. Do not apply input enrichment to ARRAY or VARIANT inputs — their indefinite length makes them unsuitable for SQL-level transformation; pass them directly to AI_COMPLETE. When the body uses `OBJECT_CONSTRUCT(...)` for multi-output, declare `RETURNS VARIANT` so the function is compatible with the evaluate/optimize pipelines. Do not use PostgreSQL `E'...'` escape string syntax; use regular `'\n'` or `CHAR(10)` for newlines.

**AI_COMPLETE calling convention:** Use the unqualified built-in `AI_COMPLETE(...)` — not the legacy `SNOWFLAKE.CORTEX.COMPLETE(...)`, which is a different (older) function and is not supported by the evaluate/optimize pipelines. Always use the named-parameter messages-array syntax; positional arguments are not supported for chat completions. The canonical pattern is:

```sql
AI_COMPLETE(
    model => 'claude-sonnet-4-6',
    messages => ARRAY_CONSTRUCT(
        OBJECT_CONSTRUCT('role', 'system', 'content', '<system prompt>'),
        OBJECT_CONSTRUCT('role', 'user', 'content', '<user message>')
    )
)::VARCHAR
```

Do NOT use the positional form `AI_COMPLETE('model', 'system', 'user')` — it will fail with `Invalid argument types` for chat-based models.

**System Prompts:** Be specific, define edge cases, use structure, add examples for complex tasks, keep focused.

**JSON Schema:** Use correct types (`string`, `number`, `boolean`, `array`, `object`), mark required fields, define `items` for arrays.

**Output Types:** For single-output functions, the return type maps from JSON type: `string` -> VARCHAR, `number` -> FLOAT, `integer` -> NUMBER, `boolean` -> BOOLEAN, `array`/`object` -> VARIANT. Multi-output functions always use `RETURNS VARIANT`. This applies to both Direct mode and Agent Research mode — always use `RETURNS VARIANT` for multi-output so the function is compatible with the evaluate and optimize pipelines. Do not use `RETURNS OBJECT` — the evaluate/optimize SPROCs expect VARIANT.

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
- ✋ Step 3: (research only) Confirm inputs/outputs — skipped for Direct mode since they appear in Step 8 review
- ✋ Step 5: (research only) After presenting approaches — wait for selection or custom description

Create:
- ✋ Step 8: (direct) Confirm combined review (function name, model, system prompt, user prompt template, inputs, outputs)
- ✋ Step 8: (research) Confirm complete DDL before execution