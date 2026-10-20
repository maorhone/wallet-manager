# Wallet Manager for LiteLLM — agent rules (Codex)
Allocates model budgets, per-model allowances and concurrency down an org hierarchy on top of a LiteLLM proxy.
Read as needed: SPEC.md (source of truth), PLAN.md (steps), PROGRESS.md (state, owner questions), PRD.md (living product doc). CLAUDE.md holds Claude-only notes.

## Commands
- Install: `uv sync`; `npm ci` (repo root, npm workspaces)
- Dev: API `uv run uvicorn app.main:app --port 4101`, UI `cd frontend && npm run dev -- --port 4100`, fake LiteLLM port 4102 (ports 4100–4199 only)
- Format/lint (no hooks for you): `uv run ruff format <files>`, `uv run ruff check --fix <files>`, `npm run -s lint`
- Tests (must be green before you finish): `./scripts/test.sh`

## Rules
- Work only inside this repo. No sudo, tunnels, tailscale, paid APIs or API keys; never set any OpenAI or Anthropic API-key variable.
- Do NOT commit or push: the builder commits after its own test run.
- One PLAN.md step per run. The spec wins over the plan. Update PROGRESS.md (done / next / blockers) and PRD.md when behavior changes.
- Don't delete, skip or weaken tests; fix the root cause. TDD for new behavior.
- Don't touch `.claude/hooks/*`, `.claude/settings*.json` or `.codex/`.
- Ambiguous? Add it to PROGRESS.md "Questions for owner", pick the safest option, continue. Truly blocked: final message `BLOCKED: <reason>`.
- Scheduler jobs take the injectable clock; tests move a fake clock, never sleep.

## Code Review Rules
- Money and credits are `Decimal` end to end (DB `numeric`, Pydantic, services); never `float` arithmetic. Floats appear only at the LiteLLM/JSON boundary.
- Conservation: an allocation never makes the children's sum exceed the parent (`PARENT_OVERALLOCATED`); shares never exceed the grant remainder; closing a share or grant returns exactly the unused part to the pool. Check sign, rounding and idempotency.
- Every write validates under the per-parent advisory lock, and writes domain rows, one audit row (actor, role, reason, before/after) and outbox rows in ONE transaction.
- Authz: every query and mutation is scoped to the caller's role and scope (own record / group / department / grants); no endpoint trusts a client-supplied scope id without a check. Manual member actions need a reason and an Idempotency-Key.
- LiteLLM is reached only through `app/litellm_client/` (NFR3).
- Public repo hygiene (`tests/test_repo_hygiene.py`): no secrets, tokens, real hostnames or internal corporate names; placeholders like `corp.example` and generated dummy values only.
- Severity: blocker = wrong money, security or data loss; major = spec violation or real bug; minor = style.

## Architecture
- Backend: FastAPI + SQLAlchemy async + Alembic + Pydantic v2 + PostgreSQL (pgembed locally; its `psql` segfaults here, use asyncpg).
- Frontend: React + TypeScript + Vite, RTL Hebrew, TanStack Query, Playwright. New API prefixes go in the `frontend/vite.config.ts` proxy regex.
- Scheduler: separate process `python -m app.scheduler`, one advisory lock per job.
- Auth: OIDC; `DEV_AUTH=1` for seeded users. No automatic AD sync: onboarding API + manual member actions.
