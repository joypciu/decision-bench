# Shipgate

Shipgate is a GitHub App that reviews a pull request and posts one comment: **ship**, **revise**, or **block**, with a file and a reason for each risk. The repository is [joypciu/Shipgate](https://github.com/joypciu/Shipgate).

The decision comes from [Decision Bench](https://github.com/joypciu/decision-bench). The change-risk lead spawns the security, migration, and research checkers. Shipgate submits one pull request review and sets a `shipgate` commit status. **Block** requests changes on the risky lines, **revise** leaves a comment, and **ship** approves. The check is pending while the review runs, then success for ship or failure for revise or block. A draft pull request is skipped until it is marked ready. The same commit is not reviewed twice.

This app reads the pull request diff and writes a commit status plus an issue comment. Create the GitHub App with those permissions:

- Pull requests: read and write
- Contents: read
- Commit statuses: write

Copy `.env.example` to `.env` and fill in the webhook secret, app id, and private key.

This checkout can live next to Decision Bench. GitHub Actions checks out both repositories before running `pytest`.

## Run a review without GitHub

Install Decision Bench from the sibling checkout, then Shipgate:

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -e E:\decision-bench
pip install -e ".[dev]"
$env:DECISION_BENCH_ROOT = "E:\decision-bench"
pytest
```

`pytest` runs the auth-bypass diff through the demo provider and expects **block** on `auth.py`.

Review one diff file and print the comment. The command exits 0 for ship and 1 for revise or block:

```powershell
py -m shipgate review auth.diff
```

## Webhook

`POST /github/webhook` accepts `pull_request` events whose action is `opened`, `synchronize`, or `reopened`. When `GITHUB_APP_ID` and a private key are set, Shipgate exchanges a short-lived installation token for that delivery. Otherwise it uses `GITHUB_TOKEN`.

- `GITHUB_WEBHOOK_SECRET` verifies `X-Hub-Signature-256`
- `GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY` (PEM text, or `GITHUB_APP_PRIVATE_KEY_PATH`) identify the GitHub App
- `GITHUB_TOKEN` is the fallback when the app key is not set
- `DECISION_BENCH_ROOT` is the Decision Bench checkout that contains `packs/`
- `SHIPGATE_PROVIDER` (`demo` by default)

Open [http://127.0.0.1:8010](http://127.0.0.1:8010), paste a diff, and click **Review**. The page uses the demo provider and does not post to GitHub. Load the auth-bypass sample to see **block** on `auth.py`.

```powershell
$env:DECISION_BENCH_ROOT = "E:\decision-bench"
py -m shipgate
```
