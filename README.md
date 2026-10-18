# LogFix AI V2

Multi-cloud log investigation console (AWS CloudWatch, Azure Log Analytics, GCP Cloud Logging) plus a repository coding assistant.
Tool-using AI agents, execution traces, runbook search, scheduled collection, incident detection, persistent history, authenticated admin.

## Run it (2 minutes, no cloud account, no API keys)
    cd backend
    python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    python -m app                                       # http://127.0.0.1:8000
Or `./run.sh` (macOS/Linux) / `run.bat` (Windows). The UI is prebuilt in `frontend/dist`, so Node is not needed to run it.

1. Open http://127.0.0.1:8000. The first-run admin login is in `backend/data/initial_admin_password.txt` (you must change it).
2. **Sources** > the *Demo cloud* source > inject **connection_pool**. Three incidents appear.
3. **Incidents** > open one > **Investigate**. Read the evidence, the "x of y verified" badge and the execution trace.
4. **Chat**: "why is payment-service failing?". **Code**: ask about `sample-payment-service` (the bug the demo points to is in `db.py`).

## Turn on real AI
Admin > LLM: choose **bedrock** (Claude via Converse; use an instance role or keys) or **azure_openai**, then **Test**.
Until you do, the app uses a *heuristic provider* (fixed tool plan, no reasoning). The UI labels it "heuristic (no LLM)".

## The 9-agent incident pipeline
Runs on every new incident (switch in Administration > Detection, hourly cap applies) or on demand with **Run pipeline** on an incident.
| # | Agent | What it really does | Uses AI model |
|---|---|---|---|
| 1 | Log Monitor | Measures the error spike vs the last hour and names the anomaly | yes (fallback: rule-based title) |
| 2 | Data Sanitizer | Re-scans evidence for PII/secrets, SHA-256 fingerprint | no |
| 3 | Dedup Engine | Same fingerprint within 30 min across sources = duplicate; duplicates skip alerts and tickets | no |
| 4 | Classifier | 0-100 score from severity, volume, blast radius, trend, business path; P1-P4; category. Every point is explained | no |
| 5 | RCA Correlator | Tool-using agent, evidence-checked root cause | yes |
| 6 | Recommender | Fix steps, auto-remediation plans (never executed), verification, rollback, resolution estimate (from your own resolved incidents; labelled baseline until 3+ exist) | yes (fallback: your runbook, then built-in playbooks) |
| 7 | Notifier | Dashboard, Slack/Teams, PagerDuty, email, each with its own minimum priority; one failing channel does not block others | no |
| 8 | Documenter | Resolution guide: PDF, TXT, MD download | yes (summaries only) |
| 9 | ServiceNow | Creates one ticket per incident (impact/urgency from priority) | no |

The **Agents** page shows all 14 agents (9 above + collector, scheduler, chat, code assistant, PR reviewer): running / idle / error / not configured, last result, 24h counts, average time.
A failed stage never stops the later ones. Alerts and the guide still go out if the AI model is down.

## GitHub
Code assistant > Link a GitHub repository: URL (+ token for private repos). LogFix clones it (shallow) and shows a **webhook URL and secret** once. In GitHub add the webhook (content type JSON; Pushes + Pull requests):
- **push** to the tracked branch: re-sync automatically
- **pull_request** opened/updated: the PR Reviewer runs (rule checks + AI if configured); comment is posted only if you enable it
- **Open pull request** on an approved proposal: creates a branch, commits, pushes, opens the PR (needs a write token and "Allow opening pull requests")
Webhooks need HMAC signature, duplicate deliveries are ignored. Set `LOGFIX_PUBLIC_URL` so the URL shown is the public one. The server needs `git` (the Dockerfile installs it).

## Tests
    cd backend && pip install -r requirements-dev.txt && python -m pytest -q      # 136 tests

## Honest limitations (read before relying on it)
- **Connectors were tested against mocks/stubs only**, not live AWS/Azure/GCP. Expect to fix small things on first real contact.
- **PagerDuty, SMTP, ServiceNow and the GitHub REST/push paths were tested against stub servers and a local git repo, never against the real services.**
- **Auto-remediation is advice, not automation.** Nothing is executed.
- **Resolution estimate** is learned from your resolved incidents. Until 3+ similar ones exist it is a generic baseline, and says so.
- **The Classifier is a rule-based score**, not a trained model. The factors are shown so you can disagree with it.
- **Without an AI model** the Monitor, RCA, Recommender, Documenter and PR reviewer fall back to rules and runbook text. The UI labels this "heuristic (no LLM)".
- **PR review sees the diff only** (not the whole project at the PR commit). It can miss problems and be wrong.
- **Retrieval is BM25 keyword search, not embeddings.**
- **SQLite, single instance.** On Render free tier the disk is wiped on redeploy/sleep: use a persistent disk or you lose logs, users, linked repos and clones.
- **Encryption key lives on the same disk as the DB by default.** Set LOGFIX_SECRET_KEY.
- **The code assistant cannot run code or tests.** Review every diff.
- **No TLS in the app**: the platform proxy terminates it.
- **Docker/compose files were not executed** in the build environment.
- **Fingerprint algorithm changed to SHA-256**: incidents from an older database will not merge with new ones.
- Redaction is regex-based; it will miss unusual secret formats.

## Layout
    backend/app/{api,agents,connectors,llm,kb,services}  backend/tests  frontend/src  deploy/  sample_repo/
