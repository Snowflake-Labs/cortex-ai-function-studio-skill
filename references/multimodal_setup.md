<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Multimodal Setup Reference

## When to Load

Load when user wants to create an AI function that processes images, documents, or other files from a Snowflake stage. Example triggers: "image", "photo", "picture", "document", "PDF", "file", "multimodal", "vision", "TO_FILE", "stage files".

## Overview

AI_COMPLETE supports multimodal inputs — images and documents stored in Snowflake stages. Files are loaded using `TO_FILE('@stage_name', 'path')` and passed to the model alongside text prompts.

There are two AI_COMPLETE calling conventions for multimodal:

1. **Single file** — `AI_COMPLETE(model, prompt_text, TO_FILE(...))` — for one file with a fixed prompt
2. **Prompt object** — `AI_COMPLETE(model, PROMPT('template {0} {1}', TO_FILE(...), text_col))` — for multiple files, or mixed file + text inputs

## Stage Requirements

Files must be stored in a Snowflake stage with `ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')`. The default encryption (`SNOWFLAKE_FULL`) is NOT compatible with `TO_FILE()`.

**Use an existing stage** with `ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')`, or create one (default name: `AI_FUNCTIONS`):

```sql
CREATE STAGE IF NOT EXISTS {database}.{schema}.AI_FUNCTIONS
    ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
    DIRECTORY = (ENABLE = TRUE);
```

**Upload image/document files to the stage:**
```sql
PUT file:///path/to/local/files/* @{database}.{schema}.AI_FUNCTIONS
    AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
```

**Refresh the directory table** (required after uploading):
```sql
ALTER STAGE {database}.{schema}.AI_FUNCTIONS REFRESH;
```

**Verify files are accessible:**
```sql
SELECT RELATIVE_PATH, SIZE FROM DIRECTORY(@{database}.{schema}.AI_FUNCTIONS);
```

## Supported File Formats

### Images

| Format | Extensions | Notes |
|--------|-----------|-------|
| JPEG | `.jpg`, `.jpeg` | All vision models |
| PNG | `.png` | All vision models |
| GIF | `.gif` | All vision models |
| WebP | `.webp` | All vision models |
| BMP | `.bmp` | `pixtral-large` and `llama4` models only |

### Documents (Preview)

| Format | Extensions | Notes |
|--------|-----------|-------|
| PDF | `.pdf` | All document models |
| Plain text | `.txt` | All document models |
| Markdown | `.md` | All document models |
| Word | `.doc`, `.docx` | Claude models only |
| Excel | `.xls`, `.xlsx` | Claude models only |
| CSV | `.csv` | Claude models only |
| XHTML | `.xhtml` | Claude models only |

## File Size Limits

| Model Family | Max Image Size | Max Doc Size | Max Images per Prompt |
|-------------|---------------|-------------|----------------------|
| Claude (`claude-sonnet-4-5`, `claude-sonnet-4-6`, etc.) | 3.75 MB | 4.5 MB | 20 |
| OpenAI (`openai-gpt-5.2`, `openai-o4-mini`) | 10 MB | — | 5 |
| Gemini (`gemini-2.5-flash`, `gemini-2.5-flash-lite`) | 10 MB | 10 MB | — |
| Pixtral (`pixtral-large`) | 10 MB | — | 1 |
| Gemini (`gemini-3-pro`) | — | 10 MB | — |

Claude models also require image resolution ≤ 8000×8000 pixels.

## Vision Models (Image Support)

| Model | Context Window | Notes |
|-------|---------------|-------|
| `claude-sonnet-4-6` | 200,000 | Newest Claude Sonnet, up to 20 images |
| `claude-opus-4-6` | 200,000 | Newest Claude Opus, up to 20 images |
| `claude-sonnet-4-5` | 200,000 | Balanced quality/cost, up to 20 images |
| `claude-opus-4-5` | 200,000 | High quality, up to 20 images |
| `openai-gpt-5.2` | 1,047,576 | Latest GPT, up to 5 images |
| `openai-o4-mini` | 1,047,576 | Reasoning model, up to 5 images |
| `gemini-2.5-flash` | 1,048,576 | Fast and affordable |
| `gemini-2.5-flash-lite` | 1,048,576 | Fastest, lowest cost |
| `pixtral-large` | 128,000 | 1 image per prompt |

## Document Models (Document Support — Preview)

| Model | Max Document Size |
|-------|------------------|
| `gemini-3-pro` | 10 MB |
| `gemini-2.5-flash` | 10 MB |
| `gemini-2.5-flash-lite` | 10 MB |
| `claude-sonnet-4-6` | 4.5 MB |
| `claude-opus-4-6` | 4.5 MB |
| `claude-sonnet-4-5` | 4.5 MB |
| `claude-opus-4-5` | 4.5 MB |

## Detecting Input Type

Before creating a multimodal function, determine whether the customer's input data uses **FILE** columns or **VARCHAR file-path** columns. The function signature must match the table's column types so `SELECT func(col) FROM customer_table` works without extra wrapping.

### Step 1: Check column types

```sql
DESCRIBE TABLE {source_table};
```

Look at the `type` column for each input:
- **FILE** → the column already holds file references. Use `sql_type: "FILE"`.
- **VARCHAR** (or STRING/TEXT) → might hold file paths. Proceed to Step 2.

### Step 2: Confirm VARCHAR columns contain file paths

If a column is VARCHAR, inspect sample values to confirm it holds stage file paths:

```sql
SELECT {column_name} FROM {source_table} LIMIT 5;
```

File-path indicators:
- Values contain `/` separators (e.g., `images/cat.jpg`, `documents/report.pdf`)
- Values end with known file extensions (`.jpg`, `.png`, `.pdf`, `.docx`, etc.)
- Values look like relative paths within a stage, not free-form text

If confirmed as file paths, use `sql_type: "STAGE_FILE_PATH"`. Also ask the user which stage holds these files (default: `@{database}.{schema}.AI_FUNCTIONS`).

### Step 3: Guard — Single File Column Limit

**⚠️ STOP**: Count how many file-type columns the table has. Include both `FILE`-typed columns and VARCHAR columns confirmed as file paths in Step 2.

**If 0 file columns** → this table has no multimodal inputs. Return to the standard text-only input flow in `create/SKILL.md` Step 3.

**If exactly 1 file column** → proceed to Step 4.

**If 2+ file columns are detected**, the evaluate and optimize workflows only support a **single** multimodal input column per table. The function creation itself can handle multiple file inputs, but downstream evaluation and optimization cannot.

Present the detected file columns and advise the user on how to consolidate:

```
I found {n} file columns in your table: {col1}, {col2}, ...

The evaluate and optimize workflows currently support a single file input column per table.
To proceed, we need to reduce to one file column. Here are your options:

1. **Select one** (recommended) — Pick the primary file column for your AI function.
   I'll create a view that drops the other file columns:
   CREATE OR REPLACE VIEW {database}.{schema}.{table}_SINGLE_FILE AS
   SELECT {text_columns}, {chosen_file_col}, {label_columns}
   FROM {source_table};

2. **Unpivot** — If each file column should be evaluated independently (one row per file):
   CREATE OR REPLACE VIEW {database}.{schema}.{table}_UNPIVOT AS
   SELECT {text_columns}, FILE_PATH, SOURCE_COLUMN, {label_columns}
   FROM {source_table}
   UNPIVOT(FILE_PATH FOR SOURCE_COLUMN IN ({col1}, {col2}, ...));

Which option works for your use case?
```

**⚠️ STOP**: Wait for user to select an option and confirm the plan.

**If user selects an option**: Execute the SQL to create the view, then use that view as the source table going forward. Continue to Step 4.

**If user declines all options**: Inform the user:
```
The evaluate and optimize workflows currently support only a single multimodal
input column per table. You can still create a function with multiple file inputs,
but you won't be able to use the automated evaluate or optimize workflows on it.

Would you like to proceed with function creation only (no evaluate/optimize),
or restructure your data to use a single file column?
```

### Step 4: Note the detected type

Record the detection result — the evaluate and optimize workflows need this context:
- **FILE type detected**: function will use `FILE` parameter, no `TO_FILE()` in body, no `stage_name` needed in function
- **VARCHAR path detected**: function will use `VARCHAR` parameter with `TO_FILE()` cast inside the body, `stage_name` is baked into the function

## Create Workflow: Multimodal Inputs

When the user explicitly asks to process images, documents, or files from a stage, follow these additional steps within the standard create workflow (`create/SKILL.md`). This replaces the standard "Determine input source" section in Step 3.

### Determine input source (multimodal)

Run the **Detecting Input Type** flow above on the customer's table. This determines whether to create a FILE-parameter or VARCHAR-parameter function.

If the customer does not have a table yet, ask for the stage where files are stored. Default: `@{database}.{schema}.AI_FUNCTIONS`. In this case, default to **VARCHAR path** approach since the customer will likely query with file paths from a directory listing.

List files to confirm accessibility:
```sql
SELECT RELATIVE_PATH, SIZE FROM DIRECTORY(@{stage_name});
```

### Two function patterns

The function signature always matches the customer's table so they can call it directly.

**Pattern A: VARCHAR file paths** (customer table has VARCHAR columns with paths)
- Function takes `VARCHAR`, casts to `FILE` inside the body via `TO_FILE()`
- Stage name is baked into the function body
- Customer calls: `SELECT func(path_col) FROM my_table` — just works

**Pattern B: FILE data type** (customer table has FILE columns)
- Function takes `FILE`, uses it directly — no `TO_FILE()` in body
- No stage name in the function body
- Customer calls: `SELECT func(file_col) FROM my_table` — just works

### Stage

**Pattern A only**: Use the existing `AI_FUNCTIONS` stage (see Stage Requirements above). If the user has files on a different stage, accept that stage name.

**Pattern B**: No stage needed in the function — the FILE value is self-contained.

### Model selection

The model must support file inputs. Prefer these families (in order):

**For images:** `claude-sonnet-4-6` > `claude-sonnet-4-5` > `openai-gpt-5.2` > `gemini-2.5-flash` > `gemini-2.5-flash-lite`

**For documents (preview):** `gemini-2.5-flash` > `gemini-3-pro` > `claude-sonnet-4-5`

If the user's selected model does not support multimodal, warn and suggest a compatible alternative.

### JSON config for `create_udf.py` (Direct mode)

**Pattern A — VARCHAR file paths:**
```json
{
    "database": "DB", "schema": "SCHEMA", "function_name": "FUNC",
    "function_intention": "description",
    "model": "claude-sonnet-4-5",
    "stage_name": "@DB.SCHEMA.AI_FUNCTIONS",
    "inputs": [
        {"name": "FILE_PATH", "sql_type": "STAGE_FILE_PATH"},
        {"name": "QUESTION", "sql_type": "VARCHAR"}
    ],
    "outputs": [{"name": "answer", "json_type": "string", "description": "desc"}],
    "system_prompt": "...",
    "user_prompt_template": "Analyze this file: {FILE_PATH}\nQuestion: {QUESTION}"
}
```

`stage_name` is required when any input has `sql_type: "STAGE_FILE_PATH"`. The script generates `TO_FILE()` calls inside the function body to cast VARCHAR paths to FILE.

**Pattern B — FILE data type:**
```json
{
    "database": "DB", "schema": "SCHEMA", "function_name": "FUNC",
    "function_intention": "description",
    "model": "claude-sonnet-4-5",
    "inputs": [
        {"name": "IMAGE", "sql_type": "FILE"},
        {"name": "QUESTION", "sql_type": "VARCHAR"}
    ],
    "outputs": [{"name": "answer", "json_type": "string", "description": "desc"}],
    "system_prompt": "...",
    "user_prompt_template": "Analyze this file: {IMAGE}\nQuestion: {QUESTION}"
}
```

No `stage_name` needed — FILE inputs are passed directly to `PROMPT()` without `TO_FILE()`.

### User prompt template

Example for multimodal inputs `[IMAGE (file), QUESTION (text)]`:
```
Analyze this image: {IMAGE}

Question: {QUESTION}
```

The template syntax is the same for both patterns. The script handles the difference: VARCHAR file-path placeholders become `TO_FILE()` calls, FILE placeholders become direct column references.

### Constraints

- **Single file column for evaluate/optimize**: The evaluate and optimize workflows support at most one file-type input column. Functions with multiple file inputs can be created but cannot be evaluated or optimized through the automated workflows. See **Detecting Input Type → Step 3** for the guard and consolidation options.
- Return type follows the same rules as text-only UDFs: single output uses its mapped SQL type (e.g., `VARCHAR`, `NUMBER`), multiple outputs use `VARIANT`
- `response_format` with JSON schema is supported for structured output (use `PARSE_JSON('{"type":"json","schema":{...}}')`)
- Uses `messages=>ARRAY_CONSTRUCT(...)` with separate system and user messages, same as text-only UDFs
- User message content uses `PROMPT()` with `TO_FILE()` for VARCHAR paths or direct column references for FILE inputs

## Key Limitations

1. **Single file column for evaluate/optimize**: The automated evaluate and optimize workflows support at most one file-type input column per data table. If the customer's table has multiple file columns, consolidate to one before proceeding (see **Detecting Input Type → Step 3**).

2. **Stage encryption**: Only server-side encrypted stages are supported. Client-side encryption is not compatible.

3. **Custom network policies**: AI_COMPLETE with files does not support custom network policies.

4. **Video/audio**: Not supported. Only images and documents are accepted.

5. **Case sensitivity**: Stage names are case-insensitive, but file paths within stages are case-sensitive.

## UDF Patterns

### Pattern A: VARCHAR file paths (TO_FILE inside body)

Use when the customer's table has VARCHAR columns containing stage-relative file paths.

#### Single image classification
```sql
CREATE OR REPLACE FUNCTION DB.SCHEMA.CLASSIFY_IMAGE(FILE_PATH VARCHAR)
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
    AI_COMPLETE(
        model=>'claude-sonnet-4-5',
        messages=>ARRAY_CONSTRUCT(
            OBJECT_CONSTRUCT('role', 'system', 'content', 'You are an image classifier.'),
            OBJECT_CONSTRUCT('role', 'user', 'content', PROMPT(
                '{0} Classify this image.',
                TO_FILE('@DB.SCHEMA.AI_FUNCTIONS', FILE_PATH)
            ))
        ),
        response_format=>PARSE_JSON('{"type":"json","schema":{"type":"object","properties":{"category":{"type":"string"}},"required":["category"]}}')
    ):category::VARCHAR
$$;

-- Customer calls: SELECT CLASSIFY_IMAGE(FILE_PATH) FROM my_table;
```

#### Image + text question
```sql
CREATE OR REPLACE FUNCTION DB.SCHEMA.ANALYZE_IMAGE(FILE_PATH VARCHAR, QUESTION VARCHAR)
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
    AI_COMPLETE(
        model=>'claude-sonnet-4-5',
        messages=>ARRAY_CONSTRUCT(
            OBJECT_CONSTRUCT('role', 'system', 'content', 'You are a visual analyst.'),
            OBJECT_CONSTRUCT('role', 'user', 'content', PROMPT(
                'Analyze this image {0} and answer: {1}',
                TO_FILE('@DB.SCHEMA.AI_FUNCTIONS', FILE_PATH),
                QUESTION
            ))
        ),
        response_format=>PARSE_JSON('{"type":"json","schema":{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"]}}')
    ):answer::VARCHAR
$$;
```

#### Document Q&A
```sql
CREATE OR REPLACE FUNCTION DB.SCHEMA.DOC_QA(DOC_PATH VARCHAR, QUESTION VARCHAR)
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
    AI_COMPLETE(
        model=>'gemini-3-pro',
        messages=>ARRAY_CONSTRUCT(
            OBJECT_CONSTRUCT('role', 'system', 'content', 'You are a document analyst.'),
            OBJECT_CONSTRUCT('role', 'user', 'content', PROMPT(
                'Given this document {0}, answer: {1}.',
                TO_FILE('@DB.SCHEMA.AI_FUNCTIONS', DOC_PATH),
                QUESTION
            ))
        ),
        response_format=>PARSE_JSON('{"type":"json","schema":{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"]}}')
    ):answer::VARCHAR
$$;
```

### Pattern B: FILE data type (direct reference)

Use when the customer's table has FILE columns. No `TO_FILE()` in the body.

#### Single image classification
```sql
CREATE OR REPLACE FUNCTION DB.SCHEMA.CLASSIFY_IMAGE(IMAGE FILE)
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
    AI_COMPLETE(
        model=>'claude-sonnet-4-5',
        messages=>ARRAY_CONSTRUCT(
            OBJECT_CONSTRUCT('role', 'system', 'content', 'You are an image classifier.'),
            OBJECT_CONSTRUCT('role', 'user', 'content', PROMPT(
                '{0} Classify this image.',
                IMAGE
            ))
        ),
        response_format=>PARSE_JSON('{"type":"json","schema":{"type":"object","properties":{"category":{"type":"string"}},"required":["category"]}}')
    ):category::VARCHAR
$$;

-- Customer calls: SELECT CLASSIFY_IMAGE(IMAGE_COL) FROM my_table;
```

#### Image + text question
```sql
CREATE OR REPLACE FUNCTION DB.SCHEMA.ANALYZE_IMAGE(IMAGE FILE, QUESTION VARCHAR)
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
    AI_COMPLETE(
        model=>'claude-sonnet-4-5',
        messages=>ARRAY_CONSTRUCT(
            OBJECT_CONSTRUCT('role', 'system', 'content', 'You are a visual analyst.'),
            OBJECT_CONSTRUCT('role', 'user', 'content', PROMPT(
                'Analyze this image {0} and answer: {1}',
                IMAGE,
                QUESTION
            ))
        ),
        response_format=>PARSE_JSON('{"type":"json","schema":{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"]}}')
    ):answer::VARCHAR
$$;
```

### Batch processing from a directory
```sql
SELECT AI_COMPLETE(
    'claude-sonnet-4-5',
    ARRAY_CONSTRUCT(
        OBJECT_CONSTRUCT('role', 'system', 'content', 'Classify images.'),
        OBJECT_CONSTRUCT('role', 'user', 'content', PROMPT(
            '{0} Classify this image in 2 words.',
            TO_FILE('@DB.SCHEMA.AI_FUNCTIONS', RELATIVE_PATH)
        ))
    )
)::VARCHAR AS classification
FROM DIRECTORY(@DB.SCHEMA.AI_FUNCTIONS);
```

## Validating File Access

Run these checks when the customer provides the data table, **before** evaluate or optimize.

**1. For FILE-type functions**, `stage_name` is not in the DDL. Ask the user: "Which stage contains your files? (e.g., `@DB.SCHEMA.AI_FUNCTIONS`)"
   For VARCHAR-path functions, `stage_name` is auto-detected from `TO_FILE()` in the DDL.

**2. Verify stage access:**
```sql
SELECT 1 FROM DIRECTORY({stage_name}) LIMIT 1;
```

**3. Spot-check a file from the data table exists on the stage:**
```sql
SELECT {file_column} FROM {data_table} WHERE {file_column} IS NOT NULL LIMIT 1;
SELECT RELATIVE_PATH FROM DIRECTORY({stage_name}) WHERE RELATIVE_PATH = '{sample_path}' LIMIT 1;
```

The evaluate and optimize modules run these checks automatically via `validate_stage_file_access()`. If validation fails, they return an actionable error before any expensive work starts.

## Evaluate / Optimize with Multimodal

**⚠️ Single file column limit**: The evaluate and optimize workflows support **at most one** file-type input column in the data table. If the customer's table has multiple file columns, run the guard in **Detecting Input Type → Step 3** to consolidate before proceeding. Do NOT skip this check — the evaluate/optimize code uses only the first detected file column and silently ignores the rest, which produces incorrect results.

Table column types must match the function's input signature. The evaluate/optimize SPROCs call the UDF via SQL, so type matching is automatic.

When applying optimized results, preserve original input types in the JSON config:
- VARCHAR path functions: use `sql_type: "STAGE_FILE_PATH"` with `stage_name`
- FILE type functions: use `sql_type: "FILE"`

### Optimizer: LLM judge with file inputs

The optimizer auto-detects file inputs from the function DDL. For FILE-type functions, `stage_name` **must** be provided in metric options so the optimizer can reconstruct `TO_FILE()` for the temp function and LLM judge.
