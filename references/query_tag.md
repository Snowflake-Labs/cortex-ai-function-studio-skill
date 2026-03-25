---
name: query-tag-wrapper
description: "Agent-oriented SQL pattern to set a temporary QUERY_TAG for custom AI function SPROC calls. Always loaded automatically by evaluate/optimize workflows; not user-invocable."
parent_skill: cortex-ai-function-studio
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Query Tag Wrapper (Agent-Oriented SQL)

**MANDATORY:** Use this wrapper for **every** Cortex AI Function Studio SPROC `CALL ...`.

This ensures every SPROC call is consistently tagged for internal tracking by temporarily setting `QUERY_TAG` (and restoring it afterward).

## When to Load

Always load and apply this wrapper immediately before executing any Cortex AI Function Studio SPROC `CALL ...`.

## Instructions

### 1) Read the current tag

```sql
SELECT CURRENT_QUERY_TAG() AS CURRENT_QUERY_TAG;
```

### 2) Set a temporary tag, call the SPROC, then restore

**Hard agent requirement**: The agent MUST render the SQL with a literal session id string before sending it to Snowflake
    by injecting its local `CORTEX_SESSION_ID` value into the SQL below (replace `<CORTEX_SESSION_ID>`).
    Do not leave placeholders like <CORTEX_SESSION_ID> and do not output ${CORTEX_SESSION_ID} / &{var} for the user to substitute later.
    If the SQL shown still contains a placeholder, it is not compliant with this wrapper.

This pattern:
- saves the original tag
- **if the original tag is a JSON string**, merges `__CUSTOM_AI_FUNCTION_CORTEX_SESSION_ID`
- otherwise appends `__CUSTOM_AI_FUNCTION_CORTEX_SESSION_ID=<CORTEX_SESSION_ID>` to the string tag
- restores the original tag after the call

```sql
-- Agent must substitute <CORTEX_SESSION_ID> with the actual local session id value by inlining it as a string literal
SET cortex_session_id = '<CORTEX_SESSION_ID>';

-- Save original query tag (string)
SET orig_query_tag = (SELECT CURRENT_QUERY_TAG());

-- If original tag is JSON, merge in the session id; else append a suffix.
SET orig_query_tag_json = TRY_PARSE_JSON($orig_query_tag);

SET new_query_tag = IFF(
  $orig_query_tag_json IS NOT NULL AND IS_OBJECT($orig_query_tag_json),
  -- JSON tag: merge key (prefer merge over overwrite)
  TO_JSON(
    OBJECT_INSERT(
      $orig_query_tag_json,
      '__CUSTOM_AI_FUNCTION_CORTEX_SESSION_ID',
      $cortex_session_id,
      TRUE
    )
  ),
  -- String tag: append
  IFF(
    $orig_query_tag IS NULL OR $orig_query_tag = '',
    '__CUSTOM_AI_FUNCTION_CORTEX_SESSION_ID=' || $cortex_session_id,
    $orig_query_tag || '|__CUSTOM_AI_FUNCTION_CORTEX_SESSION_ID=' || $cortex_session_id
  )
);

ALTER SESSION SET QUERY_TAG = $new_query_tag;

-- Your SPROC call
CALL <DB>.<SCHEMA>.<SPROC_NAME>(...);

-- Always restore
ALTER SESSION SET QUERY_TAG = $orig_query_tag;
```

## Notes / Gotchas

- If the `CALL` fails, the final `ALTER SESSION ...` may not run. If that happens, restore manually (or reconnect):
  ```sql
  ALTER SESSION SET QUERY_TAG = $orig_query_tag;
  ```
- `CURRENT_QUERY_TAG()` returns the tag for the current session; `QUERY_TAG` is a session parameter.
