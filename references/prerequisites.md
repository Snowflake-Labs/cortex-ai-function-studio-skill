<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Prerequisites

This document covers all prerequisites needed for the Cortex AI Function Studio.

## When to Load

Load from main SKILL.md Step 0 (always). Also load when: "setup", "install", "prerequisites", "requirements", "getting started".

## Required

### Snowflake Connection with AI_COMPLETE Access

You need an active Snowflake connection with access to AI_COMPLETE.

**Verify connection:**
```bash
cortex connections list
```

**Test AI_COMPLETE access:**
```sql
SELECT AI_COMPLETE('llama3.1-8b','Hello, world!');
```

### uv (Required)

Most workflows use Python scripts managed with [uv](https://docs.astral.sh/uv/).

**⚠️ HARD GATE**: Check if installed — do not proceed without `uv`:
```bash
uv --version
```

**If `uv` is not installed**, inform the user and install:
```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with Homebrew
brew install uv
```

After installation, restart the terminal or run `source ~/.bashrc` (or equivalent).

**⚠️ STOP**: Do NOT proceed to any workflow until `uv --version` succeeds. The create, evaluate, optimize, and demo workflows all depend on `uv`-managed scripts.

### Target Database and Schema

All workflows require a target database and schema where AI function objects will be created. Collect these now so that privilege checks and all subsequent steps use the same location.

**If `database` and `schema` are already known** (from the user's initial message or conversation context), accept silently.

**If not known**, first query the current session defaults:
```sql
SELECT CURRENT_DATABASE(), CURRENT_SCHEMA();
```

Then ask, offering the session defaults as an option (if they are set):
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

The skill creates several Snowflake objects. Run the checks below to verify the current role has the necessary grants.

### Privilege Check

Run these three queries in parallel to check all required privileges at once:

```sql
SELECT CURRENT_ROLE() AS CURRENT_ROLE;
```

```sql
SHOW GRANTS ON DATABASE {database};
```

```sql
SHOW GRANTS ON SCHEMA {database}.{schema};
```

From the results, check whether the current role (or a role it inherits) has the following grants. If the role has **ALL** or **OWNERSHIP** on the database and schema, all checks pass — skip to the summary.

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

These are only needed for running evaluations or optimizations in the background via Snowflake Tasks. Missing async privileges are **not blockers** — the user can still use synchronous execution.

| Privilege | Check In | Needed For |
|-----------|----------|------------|
| CREATE TASK | `GRANTS ON SCHEMA` | Creating background tasks |
| EXECUTE TASK | Account-level grant | Running background tasks |
| USAGE on warehouse (direct grant) | `GRANTS ON WAREHOUSE` | Tasks require explicit warehouse USAGE — role hierarchy is not sufficient |

To check EXECUTE TASK, run:
```sql
SHOW GRANTS ON ACCOUNT;
```
Filter for `"privilege" = 'EXECUTE TASK'` granted to the current role.

To check warehouse USAGE (only if async is needed), run:
```sql
SHOW GRANTS ON WAREHOUSE {warehouse};
```

**Note:** Warehouse USAGE for tasks is also validated at runtime by the async SPROCs. This pre-check gives early visibility.

### Reporting Results

**If all required privileges pass**, display a brief summary and proceed:
```
Privileges verified for {role} on {database}.{schema}. Ready to proceed.
```

Append one of:
- `Async execution available.` — if all three optional privileges pass
- `Async execution unavailable (sync only). Missing: {list}` — if any optional are missing

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
