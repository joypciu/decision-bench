# Decision Bench

**Evals → Compare evaluations** compares saved gold-set results case by case,
showing improved/regressed outcomes and links to each underlying run. New evaluations
fingerprint case inputs and scoring criteria; changed cases, different packs,
duplicate IDs, and older evaluations without fingerprints are excluded from
regression/improvement classification. Lead bot versions are pinned for each
evaluation so a mid-run edit cannot mix lead versions. Evaluation reports can
be downloaded as JSON from the Evals page or comparison screen.
Case comparison supports title/ID search and change-status filters, retained in
the URL across reloads. **Export all cases CSV** includes every compared case,
regardless of visible filters, with outcomes, scores, token counts, and run IDs.
CSV preserves Unicode and guards potential spreadsheet formulas in text cells.
`GET /api/evaluation-comparison.csv?left={id}&right={id}` downloads the comparison.
`GET /api/evals/{id}` returns a stored evaluation; `/export` downloads its report.

**Review edited case** on a saved run opens its input, bot, provider, and model
in an editable draft. Submitting creates a fresh run using the bot's current
version and preserves the original. Use it to review a fix and compare the two
decisions. The run form also accepts an optional model override.

Open **Compare** to compare two saved runs side by side: decisions, bot versions,
providers/models, scores, lead-run token usage and timing, plus specialist results.
Changed values are highlighted. Different case inputs and same-run selections
show explicit notices; the comparison URL can be bookmarked.

Browser verification: install `playwright`, run `python -m playwright install chromium`,
then `python e2e/browser_runs.py`. It uses a temporary database and demo inference,
creates reviews through the UI, compares them, checks report download actions and
HTTP payloads, and verifies reload/mobile/theme behavior. GitHub CI runs this test.

The refreshed workspace includes light/dark themes, sample cases, searchable
history (the latest 100 root runs), status filters, and JSON report downloads.
Press `/` to focus run search. A run's **Download report** exports its decision,
input, specialist tree and trace through `GET /api/runs/{id}/export`.
Shipgate's review page uses the same visual style. Interface fonts are local,
so the UI does not depend on Google Fonts.

Shipgate is now maintained in this repository under `integrations/shipgate/`.
Its complete Git history was merged without squashing. Install both applications
from this checkout:

```powershell
pip install -e ".[dev]" -e "integrations/shipgate[dev]"
python -m decision_bench  # workbench, port 8000
python -m shipgate        # GitHub integration, port 8010 (separate terminal)
```

Shipgate locates the task packs in this checkout automatically; no sibling
`E:\shipgate` directory is required. Configure its GitHub settings using
`integrations/shipgate/.env.example`. Environment variables must be loaded into
the shell before launch. Set `SHIPGATE_DATA` to an absolute directory to choose
where Shipgate keeps its state. Existing local state was preserved under
`integrations/shipgate/data/`.

AI Gateway remains a separate service for credentials, provider access and
usage accounting. In **Settings**, enable the `ai-gateway` preset, enter a
gateway user key, and set the model to a registered `<provider>/<model>` ID.
Use the gateway's `/v1` base URL. Select that provider on bots/evaluations; for
Shipgate set `SHIPGATE_PROVIDER=ai-gateway`. A preset alone does not start or
configure AI Gateway.

Run both suites with `pytest` and
`pytest --import-mode=importlib integrations/shipgate/tests`.

Decision Bench is a local workbench for structured decisions. A lead bot spawns specialist bots, each answer has to match a JSON schema, and the same gold cases can be scored on more than one model provider.

The two jobs in the box are a change-risk review (`ship`, `revise`, or `block`) and an incident triage (`sev1`, `sev2`, or `sev3`). The demo provider runs both, including their sub-agents, with no API key. Gemini and any OpenAI-compatible endpoint are the live adapters.

## Try the demo

Install and start the app (Python 3.11+):

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
py -m decision_bench
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Leave the bot on **Change-risk lead** and the provider on **demo**. Paste this diff and click **Review**:

```text
diff --git a/auth.py b/auth.py
--- a/auth.py
+++ b/auth.py
@@ -4,7 +4,7 @@ def allow(user):
-    if user.is_authenticated:
+    if True:  # bypass auth
         return True
     return False
```

The run page should show **block** in a few hundred milliseconds. Under it, one risk:

| Severity | File | Reason |
| --- | --- | --- |
| high | auth.py | Authentication or secret handling was weakened. |

Three checkers run under that lead. Open each one from the chips on the run page.

| Checker | Result |
| --- | --- |
| Security | `risk_level` high, finding on `auth.py` |
| Migration | `risk_level` none, no schema change |
| Research | no external lookup, empty `sources` |

That is a saved demo run, not a live model. The demo provider is deterministic, so this result does not depend on an API key or a quota. `pytest` uses the same provider.

On a finished run, **Ask a follow-up** starts that bot again. The new run includes your question, the previous structured result, and the original case. A lead can do the same thing by calling `delegate` a second time for a bot that already returned.

## What a run is

```text
task pack (schema, rubric, gold cases)
        |
        v
bot version ---- engine loop ---- provider port ---- demo | gemini | openai-compatible
                    |  delegate / delegate_parallel
                    v
              child bot run
                    |
                    v
              storage port ---- SQLite
```

A bot version pins the instructions, the provider, the model, the output schema, the tool allowlist, the child allowlist, and the budgets. A run stores that version id. Editing a bot writes a new version and leaves old runs alone.

The engine loops until the model calls `finish`, the step budget ends, or the token budget ends. `delegate` starts another run of an allowlisted bot and waits for it. Several `delegate` calls in one turn, or one `delegate_parallel` call, run at the same time. Child depth is capped by the bot version, with an absolute ceiling of 8. There is no shell tool. A bot cannot execute code on the machine.

Tools a version may allow: `read_case`, `delegate`, `delegate_parallel`, `web_search`, `fetch_url`, and `finish`. Only the research checker is given search and fetch. `web_search` uses Wikipedia, DuckDuckGo, and the [OSV](https://osv.dev) advisory API for a pinned package version. `fetch_url` reads one public page and refuses local or private addresses. Search and fetch are each limited to one call per run.

**Succeeded** means the JSON matched the output schema. **Passed** means the gold checks matched too: expected fields, phrases that must appear, and the minimum number of child runs. A run can succeed and still fail the rubric.

If one checker fails after the others have been called, the lead finishes from the checkers that succeeded and does not spend another model call. If the lead model itself becomes unavailable after the checkers have returned, the same assembly is used.

## Design choices

The provider port is `complete(model, messages, tools, schema)`. The engine never imports a vendor SDK. An OpenAI-compatible API, including OpenRouter, Groq, Ollama, and LM Studio, is one adapter. Gemini is another, because its tool-call protocol is different. A third protocol means a new class registered from `build_providers` in `src/decision_bench/providers/registry.py`.

The storage port is `RunStore` in `src/decision_bench/ports.py`. SQLite is the implementation in `src/decision_bench/storage/sqlite.py`. Another database is a new class that implements those methods, not a connection-string swap. The engine receives the store as an argument.

Task packs live in `packs/*.yaml`. A pack is the contract: output schema, gold cases, and the phrases a passing answer must mention. Bots are the workers. Swapping the provider on an eval run applies that provider to the whole tree, so two models can be compared on the same cases. When a run uses the bot's saved provider, each child keeps its own.

The demo provider does not call the network. It reads the case text and the child outputs and returns schema-valid JSON. Live models follow the bot instructions. The gold set can therefore prove delegation and scoring before any key is configured.

## Run the tests

```powershell
pytest
```

GitHub Actions runs the same suite on Python 3.12. `docker compose up --build` serves the app on port 8000.

## Live providers

Keys are optional. Put them in `.env` before the first launch, or add them later under **Settings**. After the first boot, the database copy is what the app uses. `.env` and `data/` are gitignored. The UI and the API show only the last four characters of a key.

- `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey). Default model: `gemini-3.6-flash`.
- `OPENROUTER_API_KEY` from [OpenRouter](https://openrouter.ai/). Default model: `openrouter/free`.

Settings already lists Groq, Cerebras, Mistral, Together, Fireworks, DeepInfra, Hugging Face, SambaNova, Ollama, and LM Studio. Each uses that vendor's OpenAI-compatible chat API. Paste a key, enable the row, and save. Ollama (`http://127.0.0.1:11434/v1`) and LM Studio (`http://127.0.0.1:1234/v1`) do not need a key when the local server is running. Tavily, Brave, and Exa are search rows. They are not chat models.

## Documents

**Documents** turns an upload into text you can send to a bot. Markdown, text, HTML, CSV, JSON, XML, PDF, and Office files go through [MarkItDown](https://github.com/microsoft/markitdown). Images, and PDF crops, use Windows OCR when `winocr` is installed, or Tesseract when it is on the path. Crop left, top, right, and bottom limit extraction to one region. This is separate from the decision loop.

## Where to extend it

**Provider.** Add an OpenAI-compatible or Gemini row in Settings. A new protocol implements `complete` and is registered from `build_providers`.

**Database.** Implement `RunStore`. Point the app at that class. The engine stays unchanged.

**Task pack.** Add a YAML file under `packs/` with an output schema and gold cases. Restart the app.

**Tool.** Add the name in `tool_specs`, handle it in the engine loop, and opt a bot version into it. Older runs stay pinned to the tools they had.

**Bot.** Create one in the UI, or edit a built-in checker by saving a new version. A version chooses its children from the allowlist.

## Layout

| Path | Role |
| --- | --- |
| `src/decision_bench/engine.py` | Step loop, delegation, follow-ups, schema gate |
| `src/decision_bench/providers/` | Demo, Gemini, and OpenAI-compatible adapters |
| `src/decision_bench/provider_admin.py` | Saved provider keys and reload |
| `src/decision_bench/storage/sqlite.py` | SQLite `RunStore` |
| `src/decision_bench/ports.py` | `ModelProvider` and `RunStore` |
| `src/decision_bench/web/` | HTTP API and HTML client |
| `web/` | Templates and CSS |
| `packs/` | Change-risk and incident-triage contracts |
| `tests/` | Engine, HTTP, search, documents, and adapter tests |
