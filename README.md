# Decision Bench

Decision Bench is a local workbench for structured decisions. A bot you create can spawn specialist bots, every model call is checked against a JSON schema, and the same gold cases can be scored on more than one provider.

The first jobs are a change-risk review and an incident triage. The demo provider runs both, including their sub-agents, without an API key. Gemini and OpenRouter are the live adapters.

## Run it

From this directory, on Python 3.11+:

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
py -m decision_bench
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Documents accepts markdown, text, HTML, CSV, JSON, XML, PDF, images, Word, PowerPoint, and Excel. PDF and image crops limit extraction to one region. Image and cropped PDF text uses Windows OCR when `winocr` is installed, or Tesseract when it is on the path. Conversion of the other formats uses [MarkItDown](https://github.com/microsoft/markitdown).

```bash
pytest
docker compose up --build
```

Put a key in `.env` before the first launch, or add it afterward under **Settings**. The database copy is what the app uses after that. `.env` and the database are gitignored.

- `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey). Default model: `gemini-3.6-flash`.
- `OPENROUTER_API_KEY` from [OpenRouter](https://openrouter.ai/). Default model: `openrouter/free`.

To add another model, open Settings. Groq, Cerebras, Mistral, Together, Fireworks, DeepInfra, Hugging Face, SambaNova, Ollama, and LM Studio are already listed, using the same OpenAI-compatible chat API documented by each vendor. Tavily, Brave, and Exa are listed as search providers. Paste the API key, enable the row, and save. Ollama (`http://127.0.0.1:11434/v1`) and LM Studio (`http://127.0.0.1:1234/v1`) do not need a key when they are running locally.

Bots can call `web_search` without a key. That uses Wikipedia and DuckDuckGo. If a Tavily, Brave, or Exa key is enabled, those results are included too. `fetch_url` reads one public page. Several `delegate` calls in the same turn, or one `delegate_parallel` call, run the child bots at the same time.

## How a run moves

```text
task pack (schema, rubric, gold cases)
        |
        v
bot version ---- engine loop ---- provider port ---- demo | gemini | openrouter
                    |  delegate
                    v
              child bot run
                    |
                    v
              storage port ---- SQLite
```

The engine loads one bot version and loops until the model calls `finish`, the step budget ends, or the token budget ends. `delegate` starts another run of an allowlisted bot and waits for it. Depth, steps, and tokens are hard limits. A bot cannot call a shell. The tool list is `read_case`, `delegate`, and `finish`.

A run can succeed and still fail the rubric. Success means the JSON matched the schema. Passed means the gold checks matched too: expected fields, required evidence, and the minimum number of child runs.

## Where to extend it

**Provider.** Add an OpenAI-compatible endpoint or a Gemini key from Settings. A new Python adapter is only needed for a protocol that is not one of those two. Implement `complete(model, messages, tools, schema)` and register it from `build_providers` in `src/decision_bench/providers/registry.py`.

**Database.** Implement the `RunStore` methods in `src/decision_bench/ports.py`. SQLite is `src/decision_bench/storage/sqlite.py`. The engine receives the store as an argument.

**Task pack.** Add a YAML file under `packs/` with an output schema and gold cases. Restart the app.

**Tool.** Add a name to the allowlist in `tool_specs` and handle it in the engine loop. Bots opt in by version, so an old run stays pinned to the tools it had.

**Bot.** Saving a bot writes a new version. Runs store that version id. Editing instructions does not rewrite history.

## Layout

| Path | Role |
| --- | --- |
| `src/decision_bench/engine.py` | Step loop, delegation, schema gate |
| `src/decision_bench/providers/` | Demo, Gemini, and OpenAI-compatible adapters |
| `src/decision_bench/provider_admin.py` | Saved provider keys and reload |
| `src/decision_bench/storage/sqlite.py` | System of record |
| `src/decision_bench/web/` | HTTP API and HTML client |
| `web/` | Templates and CSS |
| `packs/` | Checked-in jobs and gold cases |
| `tests/` | Engine, HTTP, and adapter tests |

The demo provider is deterministic so `pytest` and a fresh clone do not need network access. Live models follow the bot instructions; the demo provider follows the case text and the child outputs. That split is why the gold set can prove the engine before a key is configured.
