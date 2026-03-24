---
name: cortex-ai-function-studio
description: "Create, evaluate, and optimize custom AI functions using Snowflake Cortex AI Complete. Use when: building LLM-powered functions, evaluating AI function performance, tuning prompts, selecting models, checking async job status. Triggers: ai function builder, custom ai function, user defined ai function, build my own llm function, evaluate ai function, tune ai function, optimize ai function, demo ai function, resume ai function job."
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
with SQL pre/post-processing — you can also specify your own strategy). After building, evaluate against labeled data, then optimize with automated prompt tuning and model selection.
If you're new to custom AI functions, you can start with a **demo** to see a worked example end-to-end.
```

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

**If CREATE:** Load `create/SKILL.md`

**If EVALUATE:** Load `evaluate/SKILL.md`

**If OPTIMIZE:** Load `optimize/SKILL.md`

**If DEMO:** Load `demos/SKILL.md`

**If CHECK_STATUS:** Load `references/async_status.md` with the run_id from the user's message (if provided).

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
- **Optimize**: Improve functions using prompt optimization and perform cost/quality model comparison 
- **Demo**: Interactive walkthroughs with example use cases
- **Data Preparation**: Prepare train/test data (`references/data_preparation.md`)
- **Synthetic Data**: Generate training/test data with multi-hop reasoning (`references/synthetic_data.md`)
- **Pseudo Labels**: Label input-only tables using strong-model inference and reuse for evaluate/optimize (`references/synthetic_data.md`)
- **Version Management**: Track and manage function versions (`references/version_management.md`)

## Data Suggestions

| Workflow | Training Rows | Test Rows | Notes |
|----------|---------------|-----------|-------|
| Evaluate | - | ~200 (min 50) | Test data only |
| Optimize | ~300 (min 50) | ~200 (min 50) | Training internally split into train/dev |

## Stopping Points

- ✋ Step 0: After prerequisite check fails
- ✋ Step 1-2: If intent unclear, ask user to select workflow

Each sub-skill has its own stopping points documented within.

## Output

Depends on workflow selected:
- **Create**: AI function DDL created in Snowflake
- **Evaluate**: Performance score and detailed results table
- **Optimize**: Optimized prompt with improved performance
- **Demo**: Completed walkthrough with demo objects (cleanable)
