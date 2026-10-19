You are re-planning the Wallet Manager for LiteLLM. Read SPEC.md (821 lines, v0.2, complete) and litellm_wallets_erd.mmd FULLY. Otherwise explore minimally: the repo only has scaffolding (CLAUDE.md, hooks, scripts/test.sh placeholder, an old PLAN.md which is SUPERSEDED — ignore its content). Do not use the web. Do NOT call ExitPlanMode and do not try to write any file; return the FULL plan as your final message in markdown starting with the line `# PLAN.md: Wallet Manager for LiteLLM`.

# The plan must follow the spec exactly
- Error codes and HTTP statuses exactly per 11.2 (incl. 422 where the spec says 422); over-allocation checks per 11.3.
- Tables exactly per 8.2; LiteLLM-owned fields per 8.1; LiteLLM calls per section 10 and the ERD.
- API paths exactly per 11.1 (e.g. /me, /me/wallet, /departments/{id}/split with pmo_pool + reserve). Use the spec's paths/prefixes verbatim; do not invent /api/admin variants unless 11.1 has them.
- Write path per 5.3 and failure modes per 5.4 (pending/applied/failed states, idempotency key, required reason, advisory lock per parent), roles/permissions per 6 and 12.2.
- Wallet formulas per 12.3, states/badges per 12.4, screens/flows/messages per 12.5–12.9, Hebrew RTL.
- Stack per NFR2 (section 13) and 12.10. Quote section numbers in each step so the builder can check.
- Phase 1 of section 16 in full detail, covering its exit criteria (FR-D, FR-G, FR-E, FR-V, FR-A; A2, A3, A5; U1, U2, U3, U4, U6, U7, U8). Phases 2–4 as a short outline only.

# Builder decisions for the LOCAL environment only (everything else follows the spec)
1. Real target is airgapped OpenShift + LiteLLM Enterprise + ADFS, unavailable here. For dev/tests build an in-repo fake LiteLLM management API server (FastAPI, port 4102) implementing only the section-10 endpoints Phase 1 uses, with behavior matching section 3 where tests depend on it, plus test-only controls (outage, set spend). The LiteLLM client is isolated in one module (NFR3) with typed Pydantic models.
2. Contract tests for A-scenarios that need a real LiteLLM (15.2) are written but skipped unless LITELLM_BASE_URL is set. Where an A-scenario can also be exercised against the fake, do both.
3. PostgreSQL via `pgembed` for dev/tests (verified here: Postgres 17.9, `from pgembed import PostgresServer; srv = PostgresServer(Path(dir), cleanup_mode="delete"); srv.get_uri()` returns a unix-socket URI like `postgresql://postgres:@/postgres?host=/tmp/...`). No Docker, no system Postgres. Production uses a real Postgres via DATABASE_URL.
4. Auth: real ADFS OIDC (BFF) via configuration, plus a DEV_AUTH=1-only dev login (pick a seeded user). AD sync is phase 3; seed via a fixture script.
5. Ports on 0.0.0.0: UI 4100, API 4101, fake LiteLLM 4102 (Playwright may use other ports in 4100–4199 to avoid clashing with dev servers).
6. Sandbox: Python 3.13.5 (spec says 3.12: keep code 3.12-compatible), uv 0.13, Node 20.19 / npm 11. Public PyPI/npm OK here; airgapped mirrors are a deployment concern. No sudo; Playwright chromium may lack system libs — the plan should say what to do if so (record a blocker, do not skip).
7. Never edit .claude/hooks/* or .claude/settings.json. `./scripts/test.sh` is the single test gate (the Stop hook runs it, 540 s timeout) and must stay fast; each step says what it adds to test.sh.

# PLAN.md format
- Short context + global conventions (money type, error envelope from 11.2, etc.).
- Numbered steps. Each: title, spec sections covered, exact files, what to build (signatures/endpoints/tables), the test(s) that prove it, what scripts/test.sh runs after it. Each step sized for ONE Sonnet session (~80 turns); prefer fewer, larger, coherent steps over many tiny ones (aim ~10–14), but never so large that a session can't finish it with tests green.
- TDD, DRY, YAGNI, a commit per step.
- An acceptance mapping table: each Phase 1 exit criterion → step and test name.
- "Later phases (outline only)".
- "Questions for Maor": ONLY true blockers to Phase 1 (if none, say none). Spec open decisions (17) use their stated defaults unless truly blocking.
