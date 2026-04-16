<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Prerequisites

This document covers all prerequisites needed for the Cortex AI Function Studio.

## When to Load

Load from main SKILL.md Step 0 (always). Also load when: "setup", "install", "prerequisites", "requirements", "getting started".

## Environment Detection

Detect whether the session is running inside **Snowsight** (the Snowflake web UI) or a **CLI** environment. Store the result as state variable `environment` (`snowsight` | `cli`) — subsequent workflows branch on this value.

**Detection heuristic:** Run **`bash ls /workspace/`** to check if a Workspace context is active. If it succeeds (returns a directory listing), the session is running inside Snowsight with Workspaces — set `environment = snowsight`. If it fails (e.g., "No such file or directory") or is unavailable, set `environment = cli`. Do **not** ask the user which environment they are in — detect it automatically. **⚠️ There is no `list_files` tool — always use `bash ls` for file listing.**

**If `environment == snowsight`:** Load `references/snowsight_copilot.md` and complete the Snowsight-specific setup before proceeding to the remaining prerequisites below.

**If `environment == cli`:** Skip Snowsight setup and proceed directly.

## Required

### Silent Prerequisite Checks

**IMPORTANT — Quiet on success**: Run all prerequisite checks (connection, AI_COMPLETE, uv) in a **single parallel batch**. Do NOT narrate what you are checking beforehand — just run the checks. If everything passes, do NOT display individual results. Only mention prerequisites if something **fails**. The user should see zero prerequisite output on the happy path.

Run these two checks **in parallel** (single tool-call batch):

1. **Snowflake connection + AI_COMPLETE + session defaults** (single SQL):
```sql
SELECT AI_COMPLETE('llama3.1-8b','ping') AS ai_test, CURRENT_DATABASE() AS db, CURRENT_SCHEMA() AS sch, CURRENT_ROLE() AS role;
```

2. **uv installed**:
```bash
uv --version
```

**If any check fails**, stop and report only the failure:
- Connection/AI_COMPLETE fails → tell user to verify their Snowflake connection
- `uv` not found → install it:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
  Then restart terminal and retry. **⚠️ STOP**: Do NOT proceed until `uv --version` succeeds.

**If all checks pass**, proceed silently to the target database/schema step.

### Target Database and Schema

All workflows require a target database and schema where AI function objects will be created. Collect these now so that privilege checks and all subsequent steps use the same location.

**If `database` and `schema` are already known** (from the user's initial message or conversation context), accept silently.

**If not known**, use the session defaults from the prerequisite check above (the `db` and `sch` columns). If they are set, ask offering them as an option:
```
Which database and schema should the AI function be created in?

1. Use current session: {current_database}.{current_schema}
2. Specify a different database and schema
```

If the session has no database/schema set (NULL), omit option 1 and ask directly:
```
Which database and schema should the AI function be created in?

Database: [e.g., MY_DB]
Schema: [e.g., MY_SCHEMA]
```

Store as `{database}` and `{schema}` — these are reused by create, evaluate, and optimize workflows.

## Snowflake Privileges

The skill creates several Snowflake objects. Run the privilege checks below **silently** — same pattern as prerequisite checks. Only surface results to the user if something is **missing**.

### Privilege Check

Run these two queries **in parallel** (single tool-call batch):

```sql
SHOW GRANTS ON DATABASE {database};
```

```sql
SHOW GRANTS ON SCHEMA {database}.{schema};
```

The current role is already known from the prerequisite check above. Check whether the role (or a role it inherits) has the following grants. If the role has **ALL** or **OWNERSHIP** on the database and schema, all checks pass — proceed silently.

**Required privileges:**

| Privilege | Check In | Needed For |
|-----------|----------|------------|
| USAGE | `GRANTS ON DATABASE` | Accessing the database |
| USAGE | `GRANTS ON SCHEMA` | Accessing the schema |
| CREATE FUNCTION | `GRANTS ON SCHEMA` | Creating AI function UDFs |
| CREATE TAG | `GRANTS ON SCHEMA` | Tagging UDFs for tracking |
| CREATE STAGE | `GRANTS ON SCHEMA` | Creating the AI_FUNCTIONS stage |
| CREATE PROCEDURE | `GRANTS ON SCHEMA` | Creating evaluation/optimization stored procedures |

**Optional privileges (async execution):**

These are only needed if the user explicitly requests async execution. Do not check these by default — only verify if the user asks for async.

| Privilege | Needed For |
|-----------|------------|
| CREATE TASK | Creating background tasks |
| EXECUTE TASK (account-level) | Running background tasks |
| USAGE on warehouse (direct grant) | Tasks require explicit warehouse USAGE |

### Reporting Results

**If all required privileges pass**: Proceed silently. Do NOT display a summary message — just move on to the next workflow step.

**If any required privilege is missing:**

**⚠️ STOP**: Display each missing privilege with its remediation GRANT command. Do not proceed.

```
Missing privileges for role {role} on {database}.{schema}:

✗ {PRIVILEGE} on {OBJECT}
  Needed for: {description}
  Fix: GRANT {PRIVILEGE} ON {OBJECT_TYPE} {object_name} TO ROLE {role};

Ask your account administrator to run the GRANT commands above,
or choose a different database/schema where your role has sufficient privileges.
```
