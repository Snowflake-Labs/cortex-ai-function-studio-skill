<!-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
     Licensed under the Snowflake Skills License. See LICENSE file. -->

# Snowsight Co-Pilot UI

Loaded when `environment == snowsight`. Adds notebook-based visual surfaces to the studio. **CLI behavior is unchanged — everything below is gated on `environment == snowsight`.**

## Fail fast: required Snowsight tools

This skill depends on the following Snowsight-specific tools. If **any** tool is unavailable, errors out, or behaves unexpectedly, **stop immediately** — do not attempt workarounds, do not fall back to chat-based alternatives, and do not continue the workflow. Tell the user which tool failed and why.

**Required tools:** `write` (for creating .ipynb files), `notebook_action` (for `add_cells`, `get_cell_source_codes`, `get_notebook_state`, etc.), `bash` (for shell commands like `ls`), `read_active_pane`, `ask_user_question`

If a tool call returns an error, report it to the user in plain language and halt:

```
⚠️ Snowsight tool error — cannot continue.

Tool: {tool_name}
Error: {error_message}

This workflow requires Snowsight Workspace tools. Please verify you are in a Workspace
and that your role has the necessary privileges, then try again.
```

## UX: One notebook per function

Each AI function gets **one notebook** named `{function_name}.ipynb`. The notebook is the function's living document — each workflow stage (Create, Evaluate, Optimize) **appends** a markdown section header followed by its content cells. The result is a chronological record of the function's lifecycle, all in one place.

**Notebook path convention:** `NOTEBOOK_PATH={function_name}.ipynb` — store this when the function name is decided and reuse it across all workflow stages.

### Creating the notebook (first stage only)

The notebook is created during the **first workflow stage** that runs (usually Create). Use the `write` tool to create a minimal `.ipynb` file:

```json
{
  "metadata": {
    "kernelspec": {
      "display_name": "Jupyter Notebook",
      "name": "jupyter"
    }
  },
  "nbformat": 4,
  "nbformat_minor": 5,
  "cells": []
}
```

If the notebook already exists from a previous session, **do not try to reuse it**. Write a fresh `.ipynb` file (it will overwrite). This avoids "notebook has not been loaded yet" errors from trying to open stale files.

### Appending cells (all stages)

Each workflow stage adds cells to the notebook using `notebook_action(action="add_cells", ...)`. New cells are always appended at the end. **Always start each stage with a markdown cell** as a section divider (e.g., `# 📋 Create`, `# 📊 Evaluation`, `# 🔧 Optimization`).

**After adding cells, run them** (unless the stage is preview-only — see Create Workflow below). The `add_cells` response returns the cell IDs of the newly created cells. Use the **first** new cell's ID with `run_type: "after"` to run it and everything after it:

```
notebook_action(action="run_notebook", params='{"notebook_path": "{function_name}.ipynb", "run_type": "after", "cell_id": "<first_new_cell_id>"}')
```

This is important for **all** cell types — markdown cells must be run to render as formatted text (otherwise they show raw markdown source), and SQL/Python cells must be run to produce results and charts. Running from the first new cell avoids re-executing earlier sections.

**Exception — Create stage:** Do **not** auto-run the Create cells. The DDL SQL cell is for preview and editing only — the actual function creation is handled by the deployment script. Running the SQL cell would execute the DDL prematurely. Only run the markdown cells in this stage (use `run_type: "single"` on each markdown cell ID) so they render properly.

After running, **tell the user** where to find the notebook (e.g., "I've updated `{function_name}.ipynb` — click on it in the file explorer to view"). On the first stage, say "created"; on subsequent stages, say "updated".

**⚠️ There is no `open_notebook` tool.** Do not attempt to programmatically open notebooks. After writing and populating the notebook, inform the user and let them click on it in the Workspace file explorer.

**⚠️ `notebook_action` parameter naming:** The notebook file parameter is always `notebook_path` (not `file_path`). Example:
```
notebook_action(action="add_cells", params='{"notebook_path": "{function_name}.ipynb", "cells": [...]}')
```

## UX: Use `read_active_pane` for context

Before asking the user for context (function name, table name, database/schema), call **`read_active_pane`** first. It returns the content currently visible in the user's active Snowsight pane — typically a SQL worksheet, query results, or object explorer. If the active pane contains relevant SQL, table references, or query results, use that context instead of prompting. This avoids redundant questions when the answer is already on screen.

## UX: Prefer `ask_user_question` for confirmations

In Snowsight, use the **`ask_user_question`** tool instead of asking the user to type replies in chat. It renders clickable option buttons for a much better UX. The tool accepts:

```json
{
  "questions": [
    {
      "header": "Short title",
      "question": "Your question text",
      "type": "options",
      "options": [
        {"label": "Option A", "description": "optional detail"},
        {"label": "Option B"}
      ]
    }
  ]
}
```

- `type: "options"` (default) — renders clickable buttons. "Other" and "Cancel" options are appended automatically.
- `type: "text"` — renders a free-form text input. **Always** provide `"defaultValue"` to pre-fill.
- Multiple questions can be asked in a single call.

### Long content pattern

The `ask_user_question` UI does not render long text well. When a stopping point involves presenting detailed content (configuration summaries, column lists, JSON schemas, DDL, research approaches, multi-line explanations, etc.), **do not put the full content into the `question` field**. Instead:

1. **Print the content first** in a chat message, using the best formatting for the content type:
   - **Code block** (` ``` `) for JSON configs, SQL, DDL, or structured data
   - **Bold** key values and labels in prose summaries (e.g., **Function:** `DB.SCHEMA.MY_FUNC`)
   - **Tables** for column mappings, model comparisons, or parameter lists
   - **Numbered lists** with **bold headers** for multi-option presentations (e.g., research approaches)

2. **Then call `ask_user_question`** with a short question that references the printed content. Examples:
   - `"Does the above configuration look good?"` → options: `"Yes, proceed"` / `"I want to edit"` / `"Cancel"`
   - `"Which option above would you like?"` → one short label per option
   - `"Confirm the settings above?"` → `"Yes"` / `"Edit"` options

**Rule of thumb:** If the question text or any option description would exceed ~2 short sentences, print the detail in chat first and keep the `ask_user_question` call concise.

Use this tool at every stopping point below **and** at these interaction points in the sub-skills (instead of plain-text numbered menus):

- **SKILL.md Step 1-2**: Workflow selection (Create / Evaluate / Optimize / Check Status / Demo)
- **create/SKILL.md Step 3**: Input/output source (From Table / Manual Spec)
- **create/SKILL.md Step 4**: Creation mode (Direct / Agent Research)
- **create/SKILL.md Step 5**: Research approach selection (one option per approach + Custom)
- **create/SKILL.md Step 10**: Next steps (Evaluate / Generate Data / Test / Done)
- **evaluate/SKILL.md Step 2**: Test table source (generate data / use existing table)
- **evaluate/SKILL.md Step 4**: Metric selection (exact_match / fuzzy_match / llm_judge / custom)
- **custom_metrics.md Step 2–5**: Custom metric creation review (Ready to create metric) — Snowsight only
- **evaluate/SKILL.md Step 4**: Execution mode (Sync / Async)
- **evaluate/SKILL.md Step 6**: Next steps (Optimize / Done)
- **optimize/SKILL.md Step 4.1**: Metric selection
- **optimize/SKILL.md Step 4.2**: Budget (light / medium / heavy)
- **optimize/SKILL.md Step 4.3**: Model selection — use `multiSelect: true`
- **optimize/SKILL.md Step 5**: Execution mode (Sync / Async)
- **optimize/SKILL.md Step 8**: Next steps (Evaluate / Re-optimize / Manage versions / Done)
- **demos/SKILL.md Step 1**: Demo selection
- **references/data_preparation.md Step 1**: Data situation (tables ready / need split / synthetic / pseudo-labels)

## Setup: Verify Workspace Context

**Do not ask the user** whether they are in Workspaces. Detect it automatically by running **`bash ls /workspace/`**. If it succeeds (returns a directory listing), Workspace context is active — proceed silently.

If the command fails (e.g., "No such file or directory"), **then** ask the user to navigate:

```
Before we get started, please switch to **Workspaces** in Snowsight.

Why? The AI Function Studio uses Notebooks as a visual workspace — you'll see SQL definitions
rendered in notebook cells before execution, and interactive charts after optimization.
I can only create and manage notebooks when we're inside Workspaces, so I need you
to navigate there first.

**To switch:** In the left navigation menu, select **Projects » Workspaces**.
```

Use `ask_user_question` to confirm only if the automatic check failed. After they confirm, retry `bash ls /workspace/` to verify.

**⚠️ There is no `list_files` tool.** Always use `bash ls` for file listing.

## Setup: `uv` Environment Variables

The skill directory lives on a **read-only** Snowflake stage (`/snowflake/stages/...`). Running `uv` against it directly fails because `uv` tries to create a virtual environment and write lock files inside the project directory. The fix is to redirect all writable paths **out** of the project tree — no need to copy the skill folder.

Additionally, the Snowflake filesystem does not support symlinks and `/tmp` has `noexec` restrictions, so the venv must go in `/var/tmp/` with copy mode.

**Prepend these environment variables to ALL `uv run` commands** in Snowsight:

```bash
UV_PROJECT_ENVIRONMENT=/var/tmp/<venv_name> UV_LINK_MODE=copy PYTHONPATH=<SKILL_DIRECTORY>/src uv run --project <SKILL_DIRECTORY> ...
```

- `UV_PROJECT_ENVIRONMENT=/var/tmp/<venv_name>` — places the venv in a writable, executable location outside the read-only stage
- `UV_LINK_MODE=copy` — avoids symlink errors (`Function not implemented, os error 38`)
- `PYTHONPATH=<SKILL_DIRECTORY>/src` — enables sibling module imports (e.g., `custom_ai_function_utils`)

The `<venv_name>` can be any descriptive name (e.g., `ai_func_studio_venv`). Reuse the same venv across commands within a session to avoid redundant installs.

## Create Workflow: Notebook Preview (Step 8–9)

**Replaces Steps 8 and 9 entirely.** Instead of confirming via chat (Step 8) and immediately deploying (Step 9), place the generated DDL in a notebook cell for review. **Do NOT run the deployment script (`create_udf.py`) until the user has reviewed the notebook cell and explicitly confirmed.** The sequence is: generate DDL → show in notebook → wait for confirmation → re-read cell → deploy.

After generating the DDL in Step 8 (either Direct or Agent Research mode), **skip the chat-based confirmation and do NOT execute Step 9 yet.** Instead:

1. **Create the notebook** — write a minimal `.ipynb` file named `{function_name}.ipynb` (see the notebook creation section above).
2. **Add a markdown section header** + **SQL cell** with a single `notebook_action(action="add_cells", ...)` call. The markdown cell marks the start of the Create section. The SQL cell contains the DDL — define prompts as `SET` variables at the top, then reference them via `$system_prompt` / `$user_prompt_template` in the function body. Do NOT duplicate prompts as comments. Example cells:

   **Markdown cell:**
   ```markdown
   # 📋 Create: {function_name}
   Review and edit the DDL below. When ready, confirm to deploy.
   ```

   **SQL cell:**
   ```sql
   SET system_prompt = 'You are an expert ... Your task is to ...
   Guidelines:
   - ...';

   SET user_prompt_template = 'Evaluate the following:
   Input: ';

   CREATE OR REPLACE FUNCTION DB.SCHEMA.MY_FUNC(INPUT VARCHAR)
   RETURNS VARCHAR
   LANGUAGE SQL
   AS
   $$
       AI_COMPLETE(
           model=>'model-name',
           messages=>ARRAY_CONSTRUCT(
               OBJECT_CONSTRUCT('role', 'system', 'content', $system_prompt),
               OBJECT_CONSTRUCT('role', 'user', 'content', $user_prompt_template || INPUT)
           )
       )
   $$;
   ```
3. **Run only the markdown cell(s)** so they render as formatted text. Do **not** run the SQL cell — it is for preview/editing only. The deployment script handles actual function creation. Use `run_type: "single"` on each markdown cell ID.
4. Tell the user the notebook is ready:

   ```
   I've created {function_name}.ipynb with your function DDL — click on it in the
   file explorer to review. You can edit the system prompt, user prompt template,
   and any pre/post-processing logic directly in the notebook cell.

   When you're done reviewing, let me know.
   ```

   Then use `ask_user_question` with one option: **"Ready to deploy"** (description: "The DDL looks good or I've finished editing — proceed to create the function").

**⚠️ STOP**: Wait for confirmation. Then re-read the SQL cell via `notebook_action(action="get_cell_source_codes", ...)` — the user may have edited it. Use the cell's current content as the final DDL and proceed to Step 9 (`create_udf.py`).

## Custom Metric Creation: Notebook Preview (Steps 2–5 of `custom_metrics.md`)

**Replaces Steps 2–5 entirely.** Instead of writing to `/tmp`, testing locally, and confirming DDL in chat, show the metric code in a notebook cell for review. The sequence is: generate code → show in notebook with smoke test → wait for confirmation → re-read cell → create UDF.

After generating the metric code in Step 2, **skip Steps 3–4** (local testing) and do this instead:

1. **Add cells** to `{function_name}.ipynb` with a single `notebook_action(action="add_cells", ...)` call:

   **Markdown cell:**
   ```markdown
   # 📏 Custom Metric: {metric_name}
   Review the scoring logic below — you can edit weights, thresholds, or field
   checks directly in the cell.
   ```

   **Python cell** — the metric code. Format for readability:
   - Add a top-level docstring summarizing what the metric measures
   - For composite metrics, add a comment before each field block (e.g., `# --- category: exact_match (weight: 0.3) ---`)
   - For weighted combinations, add a weights summary near the top:
     ```python
     # Field weights:
     #   category    0.3  (exact_match)
     #   summary     0.5  (fuzzy_match)
     #   entities    0.2  (keyword_overlap)
     ```
   - Break long expressions across multiple lines

   **SQL cell** — smoke test with representative examples (perfect match, mismatch, partial match):
   ```sql
   SELECT
       expected_str,
       predicted_str,
       {database}.{schema}.{metric_name}(expected_str, predicted_str) AS metric_result
   FROM (VALUES
       ('{perfect_match_expected}', '{perfect_match_predicted}'),
       ('{mismatch_expected}', '{mismatch_predicted}'),
       ('{partial_expected}', '{partial_predicted}')
   ) AS t(expected_str, predicted_str);
   ```

2. **Run only the markdown cell** so it renders. Do not run the Python or SQL cells. Use `run_type: "single"` on the markdown cell ID.

3. Tell the user the metric is in the notebook:

   ```
   📏 I've added your custom metric code to {function_name}.ipynb — click on it
   in the file explorer to review. You can edit weights, thresholds, or field
   logic directly in the Python cell.

   When you're done reviewing, let me know.
   ```

   Then use `ask_user_question` with one option: **"Ready to create metric"** (description: "The metric looks good or I've finished editing — proceed to create the UDF").

**⚠️ STOP**: Wait for confirmation. Then re-read the Python cell via `notebook_action(action="get_cell_source_codes", ...)` — the user may have edited it. Use the cell's current content to create the UDF (Step 5 of `custom_metrics.md`). After the UDF is created, run the SQL smoke-test cell so the user can see results.

## Evaluate Workflow: Results Notebook (Step 5)

After evaluation completes and the score is returned, **append results to the function's notebook** instead of dumping example queries in chat. If the notebook doesn't exist yet (e.g., user jumped straight to Evaluate), create `{function_name}.ipynb` first using the creation workflow above.

Use a single `notebook_action(action="add_cells", ...)` call to append all of the following cells:

1. **Markdown** — section header + summary:
   ```markdown
   # 📊 Evaluation: {function_name}
   **Metric:** {metric_name} | **Test Size:** {n} examples | **Score:** {score:.1%} | **Run ID:** {run_id}
   ```

2. **Markdown** — detailed results description:
   ```markdown
   ## Detailed Results
   Every row from the test set with its expected vs predicted output, sorted by score (worst first). Look for patterns in the low-scoring rows — do failures cluster around a specific input type or edge case?
   ```

3. **SQL** — detailed results query:
   ```sql
   SELECT ROW_ID, INPUT_TEXT, EXPECTED, PREDICTED, SCORE, FEEDBACK
   FROM {results_table}
   WHERE RUN_ID = '{run_id}'
   ORDER BY SCORE;
   ```

4. **Markdown** — failure analysis description:
   ```markdown
   ## Failure Analysis
   Only rows where the function scored below 1.0. Review these to understand *why* the function failed — is the prompt unclear for these cases? Are the expected labels ambiguous? This is the most actionable section for improving your function.
   ```

5. **SQL** — failure analysis:
   ```sql
   SELECT ROW_ID, INPUT_TEXT, EXPECTED, PREDICTED, SCORE, FEEDBACK
   FROM {results_table}
   WHERE RUN_ID = '{run_id}' AND SCORE < 1
   ORDER BY SCORE;
   ```

Run the newly added cells (see "Appending cells" above), then tell the user the notebook has been updated:

```
📊 I've added evaluation results to your notebook and ran them:

📓 Notebook: {function_name}.ipynb — click on it in the file explorer to view.
  • Detailed results (all rows, sorted by score)
  • Failure analysis (only rows with score < 1)
```

Then use `ask_user_question` for next steps (Optimize / Done).

## Synthetic Data Workflow: Preview Notebook (Step 6)

After synthetic data generation completes (Step 5), **append a data preview and label distribution chart to the function's notebook** instead of just printing SQL to chat. If the notebook doesn't exist yet, create `{function_name}.ipynb` first.

Use a single `notebook_action(action="add_cells", ...)` call to append all of the following cells:

1. **Markdown** — section header:
   ```markdown
   # 🧪 Synthetic Data: {function_name}
   **Output table:** `{output_table}` | **Examples generated:** {total_generated} | **Model:** {model}
   ```

2. **SQL** — data preview:
   ```sql
   SELECT * FROM {output_table} LIMIT 10;
   ```

3. **Python** — label distribution pie chart. Use the primary output key from the output schema (e.g., `EXPECTED:LABEL`, `EXPECTED:CATEGORY`). The agent knows the output schema from Step 2 — pick the key that represents the classification label:
   ```python
   import matplotlib.pyplot as plt

   labels = {label_counts}  # dict populated from: SELECT EXPECTED:{label_key}::STRING AS LABEL, COUNT(*) AS CNT FROM {output_table} GROUP BY LABEL ORDER BY CNT DESC

   fig, ax = plt.subplots(figsize=(8, 6))
   wedges, texts, autotexts = ax.pie(
       labels.values(), labels=labels.keys(), autopct='%1.0f%%',
       colors=['#29B5E8', '#1A3E5C', '#FF6B6B', '#B0BEC5', '#4CAF50', '#FF9800', '#9C27B0', '#00BCD4'],
       textprops={'fontsize': 11}
   )
   ax.set_title('Label Distribution')
   plt.tight_layout()
   plt.show()
   ```

   To populate `{label_counts}`, first run this query (do NOT add it as a notebook cell — run it via SQL tool and use the results to build the Python dict):
   ```sql
   SELECT EXPECTED:{label_key}::STRING AS LABEL, COUNT(*) AS CNT FROM {output_table} GROUP BY LABEL ORDER BY CNT DESC;
   ```

Run the newly added cells (see "Appending cells" above), then tell the user the notebook has been updated:

```
🧪 I've added synthetic data results to your notebook and ran them:

📓 Notebook: {function_name}.ipynb — click on it in the file explorer to view.
  • Data preview (first 10 rows)
  • Label distribution chart
```

Then use `ask_user_question` for next steps (Evaluate / Optimize / Generate more / Done).

## Optimize Workflow: Progress Bar (Step 5)

Show an approximate progress bar in the notebook while optimization runs. Progress is derived from two signals:

1. **QUERY_HISTORY** — the optimization SPROC executes child queries (CREATE TEMPORARY FUNCTION, SELECT, AI_COMPLETE) that appear in `INFORMATION_SCHEMA.QUERY_HISTORY()` tagged with `SPROC_OPTIMIZATION`. Each GEPA iteration produces ~3-5 queries, so counting tagged queries and dividing by the expected total gives approximate progress.
2. **Experiment completion** — when a model finishes, its `{MODEL}_BEST` run appears in the experiment. This gives a definitive "done" signal.

### Budget to iteration mapping

The budget preset determines N (max proposal iterations). Compute N in the progress cell using the same formula as the backend (`resolve_budget` in `snow_gepa_optimize.py`):

| Preset | n (candidates) | N (max iterations) | Queries (Q=4) | Queries (Q=6) | Queries (Q=7) |
|--------|---------------|-------------------|---------------|---------------|---------------|
| demo   | 2             | 4                 | ~16           | ~24           | ~28           |
| light  | 6             | 10                | ~40           | ~60           | ~70           |
| medium | 12            | 18                | ~72           | ~108          | ~126          |
| heavy  | 18            | 27                | ~108          | ~162          | ~189          |

**Estimated queries per model** = `N * Q` where Q depends on the metric:

- **Deterministic metrics** (exact_match, fuzzy_match, contains_match, redaction_match): Q = 4 (1 reflection + 1 compile + 2 evaluation). Scoring runs in Python — no extra SQL.
- **llm_judge**: Q = 7 (adds 1 AI_COMPLETE judge query per evaluation batch — 2 minibatch + ~1 valset on accepted candidates; actual average is slightly lower since rejected iterations skip valset, but 7 is a conservative upper bound).
- **Custom metric UDF**: Q = 6 (adds 1 UDF call per evaluation batch — similar to llm_judge but slightly cheaper).

For multiple models, multiply by `len(MODELS)` since models run in parallel threads within the same SPROC session.

### Sequence

1. **Generate the run ID early.** Before starting optimization, construct the run ID yourself using the same pattern the script uses: `ai_func_opt_{FUNCTION_SHORT_NAME}_{timestamp_ms}`. Pass it via `--run-id` so both the progress cell and the optimization SPROC use the same ID.

2. **Add a progress cell** to the notebook with a single `notebook_action(action="add_cells", ...)` call:

   **Markdown cell:**
   ```markdown
   # 🔧 Optimization: {function_name}
   **Budget:** {auto_budget} (~{N} iterations per model) | **Metric:** {metric_name} | **Experiment:** {experiment_name}
   ```

   **Python cell** — approximates progress by counting SPROC child queries in QUERY_HISTORY, and detects completion via the experiment's `{MODEL}_BEST` runs. Uses ANSI escape sequences for in-place updates.
   ```python
   import math, sys, time, re
   from snowflake.snowpark.context import get_active_session

   session = get_active_session()
   EXPERIMENT = "{experiment_name}"
   MODELS = {models_list}
   TIMEOUT = {timeout_seconds}
   AUTO_BUDGET = "{auto_budget}"
   METRIC_NAME = "{metric_name}"
   CUSTOM_METRIC_UDF = "{custom_metric_udf}"

   BUDGET_N = {"demo": 2, "light": 6, "medium": 12, "heavy": 18}
   n = BUDGET_N.get(AUTO_BUDGET, 6)
   N = int(max(2 * 2 * math.log2(n), 1.5 * n))

   if METRIC_NAME == "llm_judge":
       QUERIES_PER_ITER = 7
   elif CUSTOM_METRIC_UDF and CUSTOM_METRIC_UDF != "none":
       QUERIES_PER_ITER = 6
   else:
       QUERIES_PER_ITER = 4
   EXPECTED_TOTAL_QUERIES = N * QUERIES_PER_ITER * len(MODELS)

   start_ts = session.sql("SELECT CURRENT_TIMESTAMP()::VARCHAR AS TS").collect()[0]["TS"]

   def model_prefix(model):
       return re.sub(r"[^A-Za-z0-9]", "_", model).upper()

   def bar(pct):
       filled = int(20 * min(pct, 1.0))
       return "█" * filled + "░" * (20 - filled)

   db = session.get_current_database()
   wh = session.get_current_warehouse()

   def count_sproc_queries():
       try:
           rows = session.sql(
               f"SELECT COUNT(*) AS QC FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY("
               f"END_TIME_RANGE_START => '{start_ts}'::TIMESTAMP_LTZ, "
               f"RESULT_LIMIT => 10000)) "
               f"WHERE QUERY_TAG LIKE '%SPROC_OPTIMIZATION%'"
               f"  AND DATABASE_NAME = '{db}'"
               f"  AND WAREHOUSE_NAME = '{wh}'"
           ).collect()
           return rows[0]["QC"] if rows else 0
       except Exception:
           return 0

   def check_done():
       done = {}
       for m in MODELS:
           try:
               best_run = f"{model_prefix(m)}_BEST"
               rows = session.sql(
                   f"SHOW RUN METRICS IN EXPERIMENT {EXPERIMENT} RUN {best_run}"
               ).collect()
               for r in rows:
                   if r["name"] == "valset_score" and r["value"] is not None:
                       done[m] = float(r["value"])
           except Exception:
               pass
       return done

   first_print = True
   start = time.time()
   sys.stdout.write(f"Optimization started — {len(MODELS)} model(s), ~{N} iterations each\n\n")
   sys.stdout.flush()

   while time.time() - start < TIMEOUT:
       done = check_done()
       qc = count_sproc_queries()
       remaining = len(MODELS) - len(done)
       remaining_qc = max(qc - len(done) * N * QUERIES_PER_ITER, 0)
       if remaining > 0:
           pct = min(remaining_qc / max(remaining * N * QUERIES_PER_ITER, 1), 0.99)
       else:
           pct = 1.0
       elapsed = int(time.time() - start)

       if not first_print:
           sys.stdout.write(f"\033[{len(MODELS) + 1}A")

       overall_pct = (len(done) + pct * remaining) / len(MODELS) if MODELS else 0
       sys.stdout.write(f"\r\033[2K  [{bar(overall_pct)}] {overall_pct:.0%} overall  ({elapsed}s)\n")
       for m in MODELS:
           if m in done:
               sys.stdout.write(f"\r\033[2K    ✅ {m} — Complete | Best: {done[m]:.1%}\n")
           else:
               model_pct = min((remaining_qc / max(remaining, 1)) / max(N * QUERIES_PER_ITER, 1), 0.99)
               sys.stdout.write(f"\r\033[2K    [{bar(model_pct)}] {m} ~{model_pct:.0%}\n")
       sys.stdout.flush()
       first_print = False

       if len(done) == len(MODELS):
           sys.stdout.write(f"\n✅ All models complete ({elapsed}s)\n")
           sys.stdout.flush()
           break
       time.sleep(8)
   else:
       sys.stdout.write(f"\n⏱️ Progress tracking timed out after {TIMEOUT}s. Check the following for more info on the optimization: SHOW RUNS IN EXPERIMENT {EXPERIMENT}\n")
       sys.stdout.flush()
   ```

   **How progress is approximated:** The cell counts queries tagged with `SPROC_OPTIMIZATION` in `INFORMATION_SCHEMA.QUERY_HISTORY()` since the optimization started (using `CURRENT_TIMESTAMP()` captured from the Snowflake session to avoid timezone mismatches). Results are scoped to the current user (default `QUERY_HISTORY` behavior), database, and warehouse to avoid counting unrelated queries. The queries-per-iteration constant (Q) adapts to the metric: Q=4 for deterministic metrics (scoring is pure Python, no extra SQL), Q=7 for `llm_judge` (adds AI_COMPLETE judge calls — valset judge only fires when a candidate is accepted, so actual Q averages slightly lower), and Q=6 for custom metric UDFs (detected via `{custom_metric_udf}` — adds UDF calls per eval batch). Overall progress = `query_count / (N * Q * num_models)`, capped at 99% until the experiment's `{MODEL}_BEST` run confirms completion. `QUERY_HISTORY` can have a short delay (seconds), so the bar may update in small jumps rather than smoothly.

   For multi-model runs, per-model progress divides the total query count by the number of models (`qc / len(MODELS)`) since all model threads share one SPROC session and query tag — individual model queries can't be distinguished.

   The `METRIC_NAME` and `CUSTOM_METRIC_UDF` values come from Step 4.1 of `optimize/SKILL.md` and the `--metric-name` / `--custom-metric-udf` flags respectively.

   The `MODELS` list comes from the user's model selection in Step 4.3 of `optimize/SKILL.md`. For a single model, the cell renders one progress bar; for multiple models, one bar per model plus an overall bar. Each model's bar transitions to a completion marker when its `{MODEL}_BEST` run appears in the experiment.

   **⚠️ Concurrent optimizations:** The query tag filter (`LIKE '%SPROC_OPTIMIZATION%'`) is not scoped to a specific run. If two optimizations run simultaneously (e.g., different functions), their queries are counted together, inflating progress. This is an inherent limitation of the frontend-only approach — in practice, concurrent optimizations from the same user are rare.

3. **Run the progress cell** immediately after adding it — use `notebook_action(action="run_notebook", run_type="after", cell_id=<first_new_cell_id>)`. The cell starts its polling loop in the notebook kernel, which runs independently from the agent.

4. **Start optimization** via `bash` as normal (Step 5 in `optimize/SKILL.md`). The bash call blocks until the SPROC finishes, but the notebook cell is polling concurrently from its own Snowpark session. When the experiment's `{MODEL}_BEST` run appears, the progress cell shows completion.

**⚠️ The progress cell must be added and run BEFORE starting the bash optimization command.** The notebook kernel and bash run concurrently — if you start bash first, there's no progress cell to show updates.

## Optimize Workflow: Result Charts (Step 6.4–6.5)

After presenting the Pareto-optimal table in Step 6.4, **append charts to the function's notebook** before asking for user selection. If the notebook doesn't exist yet, create `{function_name}.ipynb` first.

Use a single `notebook_action(action="add_cells", ...)` call to append all of the following cells:

1. **Markdown** — section header + summary:
   ```markdown
   # 🔧 Optimization: {function_name}
   **Metric:** {metric_name} | **Budget:** {auto_budget} | **Run ID:** {run_id}
   ```

2. **Python** — bar chart comparing seed vs optimized scores per model:
   ```python
   import matplotlib.pyplot as plt

   models = ["{model_1}", "{model_2}", ...]
   seed_scores = [{seed_test_score_1}, ...]
   best_scores = [{best_test_score_1}, ...]

   x = range(len(models))
   width = 0.35
   fig, ax = plt.subplots(figsize=(10, 6))
   ax.bar([i - width/2 for i in x], seed_scores, width, label='Seed (Before)', color='#B0BEC5')
   ax.bar([i + width/2 for i in x], best_scores, width, label='Optimized (After)', color='#29B5E8')
   ax.set_ylabel('{metric_name} Score')
   ax.set_title('Optimization Improvement by Model')
   ax.set_xticks(list(x))
   ax.set_xticklabels(models, rotation=45, ha='right')
   ax.legend()
   ax.set_ylim(0, 1.05)
   plt.tight_layout()
   plt.show()
   ```

3. **Python** — Pareto frontier graph (accuracy vs relative cost):
   ```python
   import matplotlib.pyplot as plt

   pareto_models = ["{model_1}", "{model_2}", ...]
   pareto_scores = [{score_1}, {score_2}, ...]
   pareto_costs = [{relative_cost_1}, {relative_cost_2}, ...]

   fig, ax = plt.subplots(figsize=(10, 6))
   ax.scatter(pareto_costs, pareto_scores, s=120, c='#29B5E8', edgecolors='#1A3E5C', linewidths=1.5, zorder=5)
   sorted_pairs = sorted(zip(pareto_costs, pareto_scores))
   ax.plot([p[0] for p in sorted_pairs], [p[1] for p in sorted_pairs], '--', color='#29B5E8', alpha=0.5, zorder=3)
   for model, cost, score in zip(pareto_models, pareto_costs, pareto_scores):
       ax.annotate(model, (cost, score), textcoords='offset points', xytext=(8, 8), fontsize=9)
   ax.axhline(y={seed_test_score}, color='#B0BEC5', linestyle=':', label=f'Seed score ({seed_test_score:.1%})')
   ax.set_xlabel('Relative Cost (1.0 = seed model)')
   ax.set_ylabel('{metric_name} Score')
   ax.set_title('Pareto Frontier: Accuracy vs Cost')
   ax.legend()
   plt.tight_layout()
   plt.show()
   ```

Run the newly added cells (see "Appending cells" above). Tell the user the notebook has been updated with optimization charts. Use `ask_user_question` to let them pick which optimized configuration to apply — one option per Pareto-optimal result (showing model name, score, and relative cost), plus a "Let me review the charts first" option.
