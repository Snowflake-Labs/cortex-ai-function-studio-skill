---
name: demos
description: "Interactive demos for custom AI functions. Use when: demo, example, walkthrough, show me, how does this work, try it, hands-on, tutorial."
parent_skill: cortex-ai-function-studio
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# AI Function Demos

Interactive walkthroughs organized by **feature** — each demo highlights specific studio capabilities so you can jump straight to what interests you. Not every demo runs the full create → evaluate → optimize pipeline; some focus on a single capability.

## When to Load

Load from main skill when user intent matches DEMO: "demo", "example", "walkthrough", "show me", "how does this work".

**Prerequisites:** Before routing to a demo, ensure the user has a database and schema with CREATE TABLE/FUNCTION privileges. If not already established, the demo sub-skill will prompt for location.

## Workflow

### Step 1: Select Demo

**If `environment == snowsight`**, ask user:
```
Which demo would you like to try?

1. **Pseudo-Labeling & Teacher-Student Distillation** (~10 min)
   Start with unlabeled insurance claims, generate ground truth labels
   with a strong teacher model (Claude Opus), then build a cheap student
   model and measure how closely it reproduces teacher decisions.
   Features: Synthetic Data (pseudo-label), Create, Evaluate

2. **Prompt Optimization** (~10 min)
   Take a policy-conditioned ticket router where cheap models fail badly
   on unfamiliar company vocabulary, then watch prompt optimization close
   the accuracy gap through prompt evolution and Pareto cost/quality analysis.
   Features: Create, Optimize, Pareto Analysis

---

The following demos are currently available in the CLI only.
They may be supported in Snowsight in a future update.

3. **Quick Start: Create & Evaluate** (~5 min)
   Build a toxicity classifier and measure its accuracy — the fastest
   way to experience the core workflow. Uses built-in exact_match metric.
   Features: Create (direct mode), Evaluate

4. **Custom Evaluation Metrics** (~5 min)
   Build a legal contract field extractor and create a weighted composite
   metric that scores 4 fields independently — governing law, parties,
   effective date, expiration date. See how custom metrics give richer
   signal than simple exact_match.
   Features: Create, Custom Composite Metrics, Evaluate

5. **Multimodal: Document Extraction** (~10 min)
   Extract structured fields from SEC 10-K filing PDFs, build a custom
   composite metric for multi-field scoring, and evaluate extraction
   accuracy with per-field analysis.
   Features: Multimodal (PDFs), Custom Metrics, Evaluate

6. **Agent Research: Pre/Post-Processing** (~10 min) [Experimental]
   Build a PII redaction function using Agent Research mode — the agent
   searches the web for state-of-the-art techniques, proposes multiple
   SQL UDF architectures with pre- and post-processing around AI_COMPLETE,
   and you pick the approach. Then optimize the entire function body —
   prompts, model, and SQL logic.
   Features: Create (Agent Research mode, web search, SQL pre/post-processing), Optimize (body mode)
```

**If `environment == cli`** (or not set), ask user:
```
Which demo would you like to try?

1. **Quick Start: Create & Evaluate** (~5 min)
   Build a toxicity classifier and measure its accuracy — the fastest
   way to experience the core workflow. Uses built-in exact_match metric.
   Features: Create (direct mode), Evaluate

2. **Pseudo-Labeling & Teacher-Student Distillation** (~10 min)
   Start with unlabeled insurance claims, generate ground truth labels
   with a strong teacher model (Claude Opus), then build a cheap student
   model and measure how closely it reproduces teacher decisions.
   Features: Synthetic Data (pseudo-label), Create, Evaluate

3. **Custom Evaluation Metrics** (~5 min)
   Build a legal contract field extractor and create a weighted composite
   metric that scores 4 fields independently — governing law, parties,
   effective date, expiration date. See how custom metrics give richer
   signal than simple exact_match.
   Features: Create, Custom Composite Metrics, Evaluate

4. **Prompt Optimization** (~10 min)
   Take a policy-conditioned ticket router where cheap models fail badly
   on unfamiliar company vocabulary, then watch prompt optimization close
   the accuracy gap through prompt evolution and Pareto cost/quality analysis.
   Features: Create, Optimize, Pareto Analysis

5. **Multimodal: Document Extraction** (~10 min)
   Extract structured fields from SEC 10-K filing PDFs, build a custom
   composite metric for multi-field scoring, and evaluate extraction
   accuracy with per-field analysis.
   Features: Multimodal (PDFs), Custom Metrics, Evaluate

6. **Agent Research: Pre/Post-Processing** (~10 min) [Experimental]
   Build a PII redaction function using Agent Research mode — the agent
   searches the web for state-of-the-art techniques, proposes multiple
   SQL UDF architectures with pre- and post-processing around AI_COMPLETE,
   and you pick the approach. Then optimize the entire function body —
   prompts, model, and SQL logic.
   Features: Create (Agent Research mode, web search, SQL pre/post-processing), Optimize (body mode)
```

**⚠️ STOP**: Wait for user selection before proceeding.

### Step 2: Route

Route based on the **demo name** the user selected (numbering differs between Snowsight and CLI menus):

**If Quick Start:** Load `classification/SKILL.md`

**If Pseudo-Labeling:** Load `insurance-claim-routing/SKILL.md`

**If Custom Metrics:** Load `legal-doc-extraction/SKILL.md`

**If Prompt Optimization:** Load `policy-conditioned-routing/SKILL.md`

**If Multimodal Documents:** Load `pdf-field-extraction/SKILL.md`

**If Agent Research:** Load `redaction/SKILL.md`

## What to Expect

Each demo will:
1. Create sample data in your account (with `DEMO_` prefix)
2. Walk through specific studio capabilities (see feature tags above)
3. Offer to clean up demo objects when finished

Not every demo runs every step of the create → evaluate → optimize pipeline. Demos are designed to be fast and focused on the feature they showcase.

**Note:** Demos execute real SQL and create real objects in your Snowflake account. All demo objects use the `DEMO_` prefix for easy identification and cleanup.

## Stopping Points

- ✋ Step 1: After presenting demo options, wait for user selection

## Output

Routed to specific demo sub-skill (e.g., `classification/SKILL.md`, `insurance-claim-routing/SKILL.md`)
