---
name: cortex-ai-function-studio
description: "Create, evaluate, and optimize custom AI functions using Snowflake Cortex AI Complete. Supports text, image, and document inputs. Use when: building LLM-powered functions, evaluating AI function performance, tuning prompts, selecting models, checking async job status. Triggers: ai function builder, custom ai function, user defined ai function, build my own llm function, evaluate ai function, tune ai function, optimize ai function, demo ai function, resume ai function job, image classification, document analysis, multimodal ai function."
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Cortex AI Function Studio

Build, evaluate, and optimize AI functions powered by Snowflake Cortex AI Complete.

## When to Load

Load when user wants to work with LLM-powered functions: "custom ai function", "build llm function", "evaluate ai function", "optimize prompt", "tune ai function".

Display to the user:
```
The Cortex AI Function Studio guides you through the full lifecycle of custom AI functions. The intended workflow is **create → evaluate → optimize**.
During creation, you choose how to build: **Direct** (simple AI_COMPLETE call) or **Agent Research** (I research and propose approaches 
with SQL pre/post-processing — you can also specify your own strategy). After building, evaluate against labeled data, then optimize with automated function body optimization and model selection.
If you're new to custom AI functions, you can start with a **demo** to see a worked example end-to-end.
```

## Agent Execution Rules

**After a `⚠️ STOP` point is cleared by the user, execute all subsequent tool calls (uv scripts, SQL) WITHOUT re-asking for confirmation until the next `⚠️ STOP` point.** The skill-level confirmation IS the authorization to proceed.

Do NOT:
- Ask "shall I proceed?" immediately before running a uv script the user just approved
- Ask "OK to run this SQL?" after the user confirmed the evaluation/optimization settings
- Re-confirm tool calls that are direct consequences of an already-approved action
- Ask the user to choose between sync and async execution — default to sync

These rules apply to all sub-skills (create, evaluate, optimize, demos).

**Handling "Object already exists" errors:** All `CREATE` statements (FUNCTION, TABLE, VIEW, STAGE) use plain `CREATE` (not `CREATE OR REPLACE`). If any `CREATE` fails with `SQL compilation error: Object '{name}' already exists`, prompt the user:
```
That object name already exists. Would you like to:
1. **Choose a different name** — e.g., {OBJECT_NAME}_{YYYYMMDD_HHMMSS} to avoid clashes
2. **Drop and recreate** — Drop the existing object first, then create the new one
```
If option 1, suggest a timestamped variant and re-run. If option 2, run the appropriate `DROP ... IF EXISTS` then retry.

## Workflow

### Step 0: Check Prerequisites

**⚠️ STOP**: Before proceeding, verify all prerequisites by loading `references/prerequisites.md`. This checks the Snowflake connection, tool installation, collects the target database/schema, and verifies the user's role has the necessary privileges.

If any prerequisites or privileges are missing, follow the instructions in the prerequisites file. Do not proceed until all checks pass.

### Step 1: Detect Intent

| Intent | Triggers | Route |
|--------|----------|-------|
| CREATE | "create", "build", "new" + ai/llm function | `create/SKILL.md` |
| EVALUATE | "evaluate", "test", "measure", "score" | `evaluate/SKILL.md` |
| OPTIMIZE | "optimize", "tune", "improve" | `optimize/SKILL.md` |
| DEMO | "demo", "example", "walkthrough", "show me", "how does this work" | `demos/SKILL.md` |
| CHECK_STATUS | "check status", "run_id", "ai_func_eval_", "ai_func_opt_", "is my job done", "resume", "pick up" | `references/async_status.md` |

### Step 2: Route

**⚠️ MANDATORY**: You MUST read the sub-skill file before responding. The sub-skill contains the actual command syntax, parameter formats, and execution details. Do NOT generate commands, SQL, or CLI invocations from memory — always read the sub-skill first.

**If CREATE:** Read `create/SKILL.md` before responding.

**If EVALUATE:** Read `evaluate/SKILL.md` before responding.

**If OPTIMIZE:** Read `optimize/SKILL.md` before responding.

**If DEMO:** Read `demos/SKILL.md` before responding.

**If CHECK_STATUS:** Read `references/async_status.md` with the run_id from the user's message (if provided).

**If unclear**, ask:
```
What would you like to do?
1. Create - Build a new AI function
2. Evaluate - Test AI function performance
3. Optimize - Optimize an AI function
4. Check Status - Check on an async evaluation or optimization job
5. Demo - Interactive walkthrough with example use cases
```

## Capabilities

- **Create**: Two modes — Direct (simple AI_COMPLETE) or Agent Research (research + propose SQL UDF structures, with option to specify your own)
- **Evaluate**: Measure with pre-built or custom metrics via SQL
- **Optimize**: Improve functions using function body optimization (modifies prompts, model references, and SQL pre/post-processing) and perform cost/quality model comparison. Pass ALL models in a single call — the optimizer runs them concurrently. Do NOT make separate calls per model.
- **Demo**: Interactive walkthroughs with example use cases
- **Data Preparation**: Prepare train/test data (`references/data_preparation.md`)
- **Synthetic Data**: Generate data for evaluation and optimization (`synthetic-data/SKILL.md`)
- **Pseudo Labels**: Label input-only tables using strong-model inference and reuse for evaluate/optimize (`synthetic-data/SKILL.md`)

## Data Suggestions

| Workflow | Recommended Rows |
|----------|------------------|
| Evaluate | 20–50 rows       |
| Optimize | 20–50 rows       |

> These sizes are enough for fast iteration. Larger datasets (200+ rows) improve statistical signal but are not required to get started.

## Stopping Points

- ✋ Step 0: After prerequisite check fails
- ✋ Step 1-2: If intent unclear, ask user to select workflow

Each sub-skill has its own stopping points documented within.

## Output

Depends on workflow selected:
- **Create**: AI function DDL created in Snowflake
- **Evaluate**: Performance score and detailed results table
- **Optimize**: Optimized function with improved performance
- **Demo**: Completed walkthrough with demo objects (cleanable)
