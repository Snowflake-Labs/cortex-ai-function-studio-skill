<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Prerequisites

This document covers all prerequisites needed for the Custom AI Function skill.

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

### uv (for demo scripts)

Some workflows use Python scripts managed with [uv](https://docs.astral.sh/uv/).

**Check if installed:**
```bash
uv --version
```

**Install if missing:**
```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with Homebrew
brew install uv
```

After installation, restart your terminal or run `source ~/.bashrc` (or equivalent).
