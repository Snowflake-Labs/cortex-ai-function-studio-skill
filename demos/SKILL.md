---
name: demos
description: "Interactive demos showcasing custom AI function capabilities with concrete example use cases."
parent_skill: cortex-ai-function-studio
---
<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# AI Function Demos

Interactive walkthroughs that demonstrate the full create → evaluate → optimize workflow using real example use cases.

## When to Load

Load from main skill when user intent matches DEMO: "demo", "example", "walkthrough", "show me", "how does this work".

## Workflow

### Step 1: Select Demo

Ask user:
```
Which demo would you like to try?

1. **PII Redaction** - Build a function that redacts sensitive information (names, emails, SSNs)
2. **Content Moderation** - Detect toxic content across 55 languages
3. **Insurance Claim Routing** - Build and optimize a claim routing AI function with async task execution
4. **Policy conditioned routing** - dynamically route ticket according to company's policies
5. **Clothing Condition Classification** - Classify garment condition from images using multimodal AI
6. **Image Summarization** - Summarize image content as text (coming soon)
7. **Sentiment Analysis** - Analyze text sentiment (coming soon)
```

### Step 2: Route

**If PII Redaction:** Load `redaction/SKILL.md`

**If Content Moderation:** Load `classification/SKILL.md`

**If Insurance Claim Routing:** Load `insurance-claim-routing/SKILL.md`

**If Policy conditioned routing:** Load `policy-conditioned-routing/SKILL.md`

**If Clothing Condition Classification:** Load `clothing-classification/SKILL.md`

**If Image Summarization:** Inform user this demo is coming soon, offer to try another demo instead.

**If Sentiment Analysis:** Inform user this demo is coming soon, offer to try another demo instead.

## What to Expect

Each demo will:
1. Create sample data in your account (with `DEMO_` prefix)
2. Walk through creating an AI function step-by-step
3. Evaluate the function's performance
4. Optionally optimize the function's prompt
5. Offer to clean up demo objects when finished

**Note:** Demos execute real SQL and create real objects in your Snowflake account. All demo objects use the `DEMO_` prefix for easy identification and cleanup.

## Stopping Points

- ✋ Step 1: After presenting demo options, wait for user selection

## Output

Routed to specific demo sub-skill (e.g., `redaction/SKILL.md`, `classification/SKILL.md`)
