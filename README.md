<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# cortex-ai-function-studio-skill
Build, evaluate, and optimize custom AI functions powered by Snowflake Cortex Complete. Create LLM-powered UDFs from your data, measure performance with built-in or custom metrics, and automatically optimize function bodies and select models to balance quality and cost.

## License
Copyright (c) Snowflake Inc. All rights reserved.
This repo is source-available and licensed under [these terms](LICENSE).

## Adding the Skill to Cortex Code

```bash
git clone git@github.com:snowflake-eng/cortex-ai-function-studio-skill.git
cortex skill add /path/to/cortex-ai-function-studio-skill
```

## Verify Installation

```bash
cortex skill list
```

You should see `cortex-ai-function-studio` listed under **Discovered skills → [EXTERNAL]**.

## Remove the Skill

```bash
cortex skill remove cortex-ai-function-studio
```

## Usage

Once the skill is installed, start a Cortex Code session and ask it to create, evaluate, or optimize a custom AI function. For example:

- *"Create a custom AI function that classifies support tickets"*
- *"Evaluate my AI function against labeled test data"*
- *"Optimize my AI function"*
- *"Show me a demo of custom AI functions"*

The skill will guide you through the full **create → evaluate → optimize** workflow.