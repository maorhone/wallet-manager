# Wallet Manager for LiteLLM
Allocates model budgets, per-model allowances and concurrency down an org hierarchy on top of a LiteLLM proxy. Spec: SPEC.md. Plan: PLAN.md. Progress: PROGRESS.md.

## Commands
- Backend install: `uv sync` (or `python -m venv .venv && uv pip install -e ".[dev]"`)
- Frontend install: `npm ci` (repo root, npm workspaces)
- Dev API: `uv run uvicorn app.main:app --host 0.0.0.0 --port 4101 --reload`
- Dev UI: `cd frontend && npm run dev -- --port 4100 --host 0.0.0.0`
- Fake LiteLLM: `uv run uvicorn fake_litellm.main:app --host 0.0.0.0 --port 4102`
- Test (must pass before you finish): `./scripts/test.sh`
- Lint/format run automatically after every edit (hook); fix what they report.

## Rules
- Work only inside this directory. Never use sudo, tunnels, tailscale, or ANTHROPIC_* variables.
- Ports: UI 4100, API 4101, fake LiteLLM 4102 — bind 0.0.0.0. Never commit secrets; .env (gitignored) + .env.example.
- One PLAN.md step per session. Before stopping, update PROGRESS.md (done / next / blockers).
- Small commits; push only to origin; never force-push.
- Don't delete, skip, or weaken tests. Fix the root cause.
- Write the least code that works; reuse what exists.
- Always pass an explicit LiteLLM client module boundary (NFR3). No paid APIs or Enterprise license in this sandbox.
- If ambiguous, add the question to PROGRESS.md under "Questions for owner" and pick the safest option.

## Architecture
- Backend: Python FastAPI + SQLAlchemy async + Alembic + Pydantic v2 + PostgreSQL (pgembed for local/tests).
- Frontend: React + TypeScript + Vite, RTL Hebrew, TanStack Query, Playwright.
- `app/litellm_client/`: typed client to LiteLLM management API. Local fake server in `fake_litellm/` implements section-10 endpoints.
- Auth: real OIDC via config; `DEV_AUTH=1` enables seeded-user login. AD sync is later (phase 3).

## Compact instructions
Preserve: current PLAN.md step, modified files, failing test names, test command.
