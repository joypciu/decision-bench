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

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

```bash
pytest
docker compose up --build
```

Put keys in `.env` only. The file is gitignored.

- `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey)
- `OPENROUTER_API_KEY` from [OpenRouter](https://openrouter.ai/). The default model is `openrouter/free`, which routes to a free model. Free model ids change, so set `OPENROUTER_MODEL` if you want a specific one.

On the run form, pick `gemini` or `openrouter`. Leave the model blank to use the default in `.env`. The whole spawn tree uses that provider, so an eval compares like with like.

## What a recruiter can click

1. Open **Change-risk lead**, paste a diff, run it on `demo`.
2. Open the run. The lead spawns the security checker and the migration checker. Each node has a schema result, a score, latency, and tokens.
3. Open **Evals**, run the change-risk pack. Both checked-in cases pass on `demo`.
4. Create a bot, allow it to spawn an existing bot, and require delegation. The engine rejects a finish that skips that step.

`/docs` is the HTTP API. The UI is a separate client of the same service.

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

**Provider.** Implement `complete(model, messages, tools, schema)` and register the class in `src/decision_bench/providers/registry.py`. The engine does not import a vendor SDK.

**Database.** Implement the `RunStore` methods in `src/decision_bench/ports.py`. SQLite is `src/decision_bench/storage/sqlite.py`. The engine receives the store as an argument.

**Task pack.** Add a YAML file under `packs/` with an output schema and gold cases. Restart the app.

**Tool.** Add a name to the allowlist in `tool_specs` and handle it in the engine loop. Bots opt in by version, so an old run stays pinned to the tools it had.

**Bot.** Saving a bot writes a new version. Runs store that version id. Editing instructions does not rewrite history.

## Layout

| Path | Role |
| --- | --- |
| `src/decision_bench/engine.py` | Step loop, delegation, schema gate |
| `src/decision_bench/providers/` | Demo, Gemini, OpenRouter |
| `src/decision_bench/storage/sqlite.py` | System of record |
| `src/decision_bench/web/` | HTTP API and HTML client |
| `web/` | Templates and CSS |
| `packs/` | Checked-in jobs and gold cases |
| `tests/` | Engine, HTTP, and adapter tests |

The demo provider is deterministic so `pytest` and a fresh clone do not need network access. Live models follow the bot instructions; the demo provider follows the case text and the child outputs. That split is why the gold set can prove the engine before a key is configured.
