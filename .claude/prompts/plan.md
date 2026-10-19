You are planning Phase 1 of Wallet Manager for LiteLLM (SPEC.md). Explore minimally (do not deep-crawl the filesystem or the web). Do NOT call ExitPlanMode. Return the FULL plan as your final message in PLAN.md format below.

# Important context about SPEC.md
SPEC.md is INCOMPLETE: ~53k characters were truncated in relay between the start of section 3 and ~15.2. Recoverable parts: glossary, goals/non-goals, delivery plan (section 16), open decisions (17), credits (18), UI e2e tests U1–U8 (15.3). Missing: most of LiteLLM behavior (3), domain rules (4), architecture (5), functional requirements detail, LiteLLM write API (10), UI API (11), UI screens (12), NFRs, AD sync, etc. Plan Phase 1 from section 16 exit criteria + the builder decisions below. Put "Obtain complete SPEC.md from Maor" first under Questions for Maor. Still produce a concrete, implementable Phase 1 plan.

# Builder decisions (Maor may override; treat as defaults)
1. Target Phase 1 of section 16 first as a runnable vertical slice. Later phases (2–4) only as a short outline at the end.
2. Real deploy target is airgapped OpenShift + LiteLLM Enterprise + ADFS — we do NOT have that here. Isolate the LiteLLM client in one module (NFR3 spirit) with typed models. For local/dev/tests: an in-repo fake LiteLLM management API (small FastAPI app) implementing only the management endpoints Phase 1 needs (org/team/user/membership/key/budget style from typical LiteLLM management API + whatever SPEC section 10 listed), with in-memory or DB state, so outbox/reconciliation are testable. Contract tests against a real LiteLLM are written but skipped unless LITELLM_BASE_URL is set. No Enterprise license; no paid services/API keys.
3. Auth: implement real OIDC via configuration, plus DEV_AUTH=1 only for a seeded user/role picker. AD/LDAP sync is phase 3; seed data via a fixture script for now.
4. PostgreSQL per SPEC. In this sandbox use `pgembed` (proven: Postgres 17.9, Python 3.13 wheel, unix-socket URI). Docker is unavailable. System postgres packages are not installed (do not apt-install).
5. Backend: Python 3.13 (sandbox has 3.13.5; SPEC asked 3.12 — note the skew), FastAPI, SQLAlchemy async, Alembic, Pydantic v2, uv. Frontend: React+TS+Vite, RTL Hebrew, TanStack Query, Playwright. Public npm/PyPI OK here; airgapped mirrors are a deployment concern.
6. Preview ports on 0.0.0.0: UI 4100, API 4101, fake LiteLLM 4102.
7. Always use explicit models when invoking tools that would call Claude — N/A inside this plan. Hooks and CLAUDE.md already exist; do not edit `.claude/hooks/*` or `.claude/settings.json`.
8. Known LiteLLM OSS vs Enterprise (from docs, quick check): Organizations / org_admin RBAC are Enterprise; Teams, virtual keys, team member `max_budget_in_team` are open-source. Fake server should still expose org-shaped endpoints so our client matches the Enterprise contract; real contract tests stay skipped without LITELLM_BASE_URL + license.

# PLAN.md format (required)
Bite-sized numbered steps for Phase 1. Each step:
- Exact files to create/change
- What to build (signatures / endpoints / tables as appropriate)
- The test that proves it (and how `scripts/test.sh` will run it)
- Sized for ONE Sonnet build session
Follow DRY, YAGNI, TDD, frequent commits.
Then a short "Later phases (outline only)" section.
Then "Questions for Maor" — ONLY true blockers.

Return the complete plan markdown as your final message. Do not write PLAN.md yourself (the builder will save your final message). Explore minimally.
