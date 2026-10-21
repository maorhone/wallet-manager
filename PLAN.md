# PLAN.md: Wallet Manager for LiteLLM

> Replaces the old PLAN.md. Source of truth: **SPEC.md v0.3** and **litellm_wallets_erd.mmd**. Every step lists the spec sections it covers (§). If this plan and the spec disagree, the spec wins: record the conflict in PROGRESS.md under "Questions for owner" and pick the safest option.

## 0. Context

The Wallet Manager (WM) is a control plane in front of a LiteLLM Enterprise proxy (§5). It lets people allocate department budgets, group budgets, personal caps, model pots and concurrency limits down the org tree. It checks every allocation against its parent (LiteLLM does not check sums, R14–R16), stores the decision, and applies it to LiteLLM through an outbox (§5.3). It also shows every person their wallet (§12).

The real target (airgapped OpenShift, LiteLLM Enterprise, ADFS, AD) is not available here, so locally:
- **Fake LiteLLM** (`fake_litellm/`, FastAPI, port 4102). It implements only the §10 endpoints Phase 1 uses, plus the §3 runtime behavior the tests depend on: R1, R2, R5, R6, R7, R10, R11. It has test-only controls. It keeps its state in Postgres tables named and shaped after the ERD, so the WM read-only reader runs the §11.3 SQL as written.
- **Contract tests** (§15.2) run against the fake every time. They run against a real LiteLLM only when `LITELLM_BASE_URL` is set; otherwise they are skipped.
- **PostgreSQL via `pgembed`** for dev and tests. Production uses `DATABASE_URL` and `LITELLM_DATABASE_URL` (a read-only role, NFR6).
- **Auth**: real ADFS OIDC through the BFF, by configuration. `DEV_AUTH=1` adds a seeded-user login. There is no AD sync (owner decision 20/10/2026). The directory is read once at onboarding: `FixtureDirectory` now, `ldap3` in Phase 3.
- Ports on 0.0.0.0: UI 4100, API 4101, fake 4102. The E2E stack uses 4110 (UI), 4111 (API) and 4112 (fake).
- The sandbox has Python 3.13. Code must stay 3.12-compatible (NFR2): `requires-python = ">=3.12"`, ruff `target-version = "py312"`, and no 3.13-only APIs.
- Never edit `.claude/hooks/*` or `.claude/settings.json`. `./scripts/test.sh` is the only test gate. The Stop hook runs it with a 540 s timeout. Keep the whole run under about 300 s.

## 1. Global conventions (apply to every step)

**Money.** Credits are Decimal, using `NUMERIC(18,2)` for allocations and `NUMERIC(18,4)` for spend. Use `Decimal` for all server-side comparisons. One shared Pydantic type in `app/schemas/common.py`:
```python
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2),
                  PlainSerializer(float, return_type=float, when_used="json")]
```
JSON carries money as numbers. LiteLLM payloads get `float(...)`. The UI formats credits with thousands separators and no decimals, and percentages with one decimal (§12.1).

**Time.** Store and return UTC `timestamptz` (NFR8). Budget periods follow D1: calendar month in `APP_TIMEZONE` (default `Asia/Jerusalem`). The period query param is `period=YYYY-MM`. A missing param means the current period. Future periods return 422 `INVALID_REQUEST`. `department.budget_period` holds the LiteLLM duration string (default `"1mo"`). It is also sent as team and member `budget_duration`. Calendar alignment is re-verified in Phase 0 (V4).

**IDs.** `bigint identity` for `id` columns. `employee.upn` is the PK. LiteLLM ids are deterministic:
- `department.litellm_org_id`: chosen at seed time.
- `grp.litellm_team_id`: the AD group's short name.
- user_id: the upn.

Only the key token hash comes back from LiteLLM.

**Statuses** are `text` with `CHECK` constraints, using the values listed in §8.2.

**API paths** are verbatim from §11.1 at the root, with no `/api` prefix. The BFF adds only `/auth/login`, `/auth/callback`, `/auth/logout`, and, only when `DEV_AUTH=1`, `GET /auth/dev/users` and `POST /auth/dev/login`. Infra endpoints: `/healthz`, `/readyz`, `/metrics`. SPA routes must not collide with API prefixes: `/wallet`, `/group/:id/:tab`, `/department/:id/:tab`, `/login`.

**Write contract (§11, §5.3).** Every write (POST, PUT, PATCH) behaves as follows:
- **Reason**: required. It is sent in the header `X-Change-Reason` (percent-encoded UTF-8) because some §11.1 bodies are bare arrays (`[{upn, base_cap}]`) and the spec bodies stay verbatim. A missing or empty reason returns 422 `INVALID_REQUEST`.
- **Idempotency-Key**: optional. The request id is `uuid5(NS, f"{actor_upn}:{key}")` when the key is present, otherwise `uuid4()`. It is stored in `audit_log.request_id`. Outbox rows get `idempotency_key = f"{request_id}:{seq}"`, which links audit to LiteLLM calls (FR-A1) without extra tables. If a request id is replayed and audit rows already exist, the API returns 200 with the current state and writes nothing.
- **Response**: `{"request_id", "status": "pending"|"applied", "entities": [{"entity", "id", "sync_status", "values": {...}}]}`. It also sets the `X-Request-ID` header.
- **CSRF**: double-submit. The `wm_csrf` cookie (readable by JS) must match the `X-CSRF-Token` header, otherwise 403 `CSRF_FAILED` (NFR9).

**Error envelope (§11.2).** Flat JSON:
```json
{"code": "PARENT_OVERALLOCATED", "message": "<english>", "requested": 620000,
 "available": 600000, "parent_total": 600000, "details": {...}, "request_id": "..."}
```

| HTTP | code | when |
|---|---|---|
| 422 | `PARENT_OVERALLOCATED`, `PMO_POOL_INSUFFICIENT`, `SHARE_EXCEEDS_GRANT`, `SHARE_EXPIRY_AFTER_GRANT`, `LIMIT_OUT_OF_POLICY`, `SELF_ALLOCATION_FORBIDDEN`, `SCOPE_FORBIDDEN`, `GRANT_BELOW_SHARED` | exactly the §11.2 list, always 422 |
| 422 | `INVALID_REQUEST` | schema errors (FastAPI `RequestValidationError`), missing reason, bad period |
| 401 | `UNAUTHENTICATED` | no or expired session |
| 403 | `FORBIDDEN` | actor has no role that permits this action on the URL scope (viewer writes, U7; NFR9) |
| 403 | `CSRF_FAILED` | CSRF mismatch |
| 404 | `NOT_FOUND` | unknown entity, or outside every visible scope |

`SCOPE_FORBIDDEN` (422) means the actor is authorized on the URL scope, but the payload targets something outside their scope. Examples: a upn not in the group, a group_id from another department, a co-head's personal values.

Number semantics:
- `PARENT_OVERALLOCATED`: `requested` = new Σ children, `available` = parent total × factor, `parent_total` = parent total.
- `LIMIT_OUT_OF_POLICY`: `requested` = value, `details = {field, min, max}`.
- Bulk endpoints add `details.rows = [{upn|group_id, code}]`.

**Advisory locks (FR-V1).** `app/db.py: async def lock_parent(session, kind: LockKind, id: int)` runs `SELECT pg_advisory_xact_lock(:kind, :id)`.
- `LockKind.DEPARTMENT = 1` covers split, allowances and group pots.
- `GROUP = 2` covers member caps and member model caps.
- `PMO = 3` is for Phase 2.

Take the lock before reading any sums.

**Missing parent ceiling = 0.** A group pot for model X with no department allowance for X, or a member cap for X with no group pot for X, is checked against 0. This is the safest reading of FR-D3 and FR-G3; it is recorded in PROGRESS.md questions. Lowering a parent (budget, base, pot) never validates the children. The UI shows the §12.4 "Over-allocated" warning instead.

**Layering.**
- Routers in `app/routers/*` are thin.
- Business rules live in `app/services/*`.
- All LiteLLM I/O (HTTP and read-only SQL) lives in `app/litellm_client/` (NFR3). Nothing else imports httpx for LiteLLM or touches LiteLLM tables.

**TDD, DRY, YAGNI.** Write the failing test first. Reuse the shared write-path helper, desired-state builders, wallet formulas and UI components. Build nothing for later phases unless a Phase 1 formula needs it. Grant tables exist and are summed (they are empty in Phase 1).

**Per step:** keep `./scripts/test.sh` green. Make one commit per step (small intermediate commits are fine) and push to origin only. Update PROGRESS.md (done / next / blockers / questions).

**Lint loop.** The format hook uses `./.venv/bin/ruff` and the repo-root `node_modules/.bin/{prettier,eslint}`. Therefore:
- `ruff` is a dev dependency.
- The frontend is an **npm workspace of a root `package.json`**, so the hook finds prettier and eslint at the root.

**Public repository (NFR10).** After Phase 1b the repository becomes public.
- Never commit secrets, tokens, keys, certificates, real hostnames, IPs or internal corporate names (organizations, departments, people, AD DNs). Use placeholders (corp.example).
- Use placeholders only: `corp.example`, `litellm.corp.example`, `adfs.corp.example`, `DC=corp,DC=example`.
- `.env` stays gitignored; `.env.example` holds placeholders.
- Tests and scripts generate dummy secrets at run time (`secrets.token_urlsafe`) and never hardcode real-looking ones.
- `tests/test_repo_hygiene.py` enforces this on every run.
- Internal names to block go in an optional gitignored `.public-denylist`, which the test reads if present. Never commit that list.

## 2. Steps (Phase 1)

### Step 1: Backend foundation, the §8.2 schema, error envelope, test harness
**Spec:** §8.2, §11.2, §13 NFR2/NFR8, §5.1 (API), CLAUDE.md commands.

**Files:** `pyproject.toml`, `uv.lock`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`, `app/__init__.py`, `app/main.py`, `app/config.py`, `app/db.py`, `app/models.py`, `app/errors.py`, `app/schemas/common.py`, `scripts/dev_db.py`, `scripts/test.sh`, `tests/conftest.py`, `tests/test_schema.py`, `tests/test_errors.py`, `tests/test_db_lock.py`, `.env.example`.

**Build:**
- Dependencies: fastapi, uvicorn[standard], sqlalchemy[asyncio]>=2, asyncpg, alembic, pydantic>=2, pydantic-settings, httpx, authlib, itsdangerous, pyjwt[crypto], prometheus-client, pyyaml.
- Dev dependencies: pytest, pytest-asyncio, pytest-timeout, respx, ruff, pgembed.
- Do not add ldap3 until Phase 3.
- `app/config.py`: `Settings` (pydantic-settings, `.env`) with:
  - `DATABASE_URL`, `LITELLM_BASE_URL`, `LITELLM_MASTER_KEY`, `LITELLM_DATABASE_URL`
  - `APP_TIMEZONE`, `DEV_AUTH`, `SESSION_SECRET`, `COOKIE_SECURE`
  - `OIDC_*`: discovery URL, client id/secret, redirect URI, `upn`/name/groups claim names
  - `JWT_ISSUER`, the issuer written into LiteLLM JWT mappings
  - Worker intervals: `OUTBOX_POLL_S=1`, `SPEND_SNAPSHOT_INTERVAL_S=300`, `OVERALLOC_INTERVAL_S=300`
- `app/db.py`:
  - async engine and session factory.
  - `to_asyncpg_url(uri)`. pgembed returns `postgresql://postgres:@/postgres?host=/tmp/...`. Convert it to `postgresql+asyncpg://` and, if SQLAlchemy does not forward `host=`, pass it through `connect_args={"host": dir}`. Verify this first.
  - `lock_parent()` and the `LockKind` enum.
- `app/models.py`: SQLAlchemy 2 ORM for **all 20 tables of §8.2 with exactly those names and columns**: `department, department_model_allowance, grp, group_model_pot, employee, employee_model_cap, grant, grant_share, service_key, policy, role_binding, sync_run, sync_action, litellm_write, spend_snapshot, notification, notification_pref, audit_log`, with `grant` quoted. Rules:
  - CHECK constraints for the status and kind enums.
  - `grp.overalloc_factor NUMERIC default 1.0`.
  - Composite PKs where no `id` exists: `(department_id, model)`, `(group_id, model)`, `(upn, model)`, `(upn, kind)`, `policy.key`.
  - Indexes: `litellm_write(status, id)`, `audit_log(entity, entity_id, at)`, `spend_snapshot(scope_kind, scope_id, period)`.
- Migration `0001` creates them all (async Alembic env).
- `app/errors.py`:
  - `class ApiError(Exception)` with `(status, code, message, requested=None, available=None, parent_total=None, details=None)`.
  - Constructors per code, e.g. `parent_overallocated(requested, available, parent_total, rows=None)`.
  - Exception handlers for ApiError, RequestValidationError (→ 422 `INVALID_REQUEST`), and HTTPException 401/403/404 mapped into the envelope.
- `app/main.py`: `create_app(settings) -> FastAPI` with `/healthz` (process up) and `/readyz` (DB reachable).
- `scripts/dev_db.py`:
  - Starts a persistent pgembed server under `pgdata/`; check its `cleanup_mode` options with `help(PostgresServer)` and do not delete the data.
  - Creates the DBs `wm` and `litellm_fake` and the role `litellm_ro`.
  - Runs `alembic upgrade head`.
  - Prints the env lines to put in `.env`.
- `tests/conftest.py`:
  - One session-scoped `PostgresServer(tmp, cleanup_mode="delete")`, with DBs `wm_test` and `litellm_test`.
  - Migrate once.
  - An autouse fixture that runs `TRUNCATE ... RESTART IDENTITY CASCADE` on all WM tables (and on fake tables, from Step 2).
  - An `api` fixture: `httpx.AsyncClient(transport=ASGITransport(create_app(...)))`.
  - pytest config: `asyncio_mode=auto`, `asyncio_default_fixture_loop_scope="session"`, `asyncio_default_test_loop_scope="session"`, `timeout=60`.

**Tests:**
- `test_schema.py::test_tables_match_spec_8_2`: a literal transcription of §8.2 (table → column set) compared with `information_schema`.
- `test_errors.py::test_envelope_shape_for_api_error`, `::test_validation_error_is_422_invalid_request`.
- `test_db_lock.py::test_advisory_lock_serializes_two_sessions`: the second session blocks until the first commits.
- `::test_healthz_readyz`.

**test.sh after:**
```bash
set -euo pipefail; cd "$(dirname "$0")/.."
[ -d .venv ] || uv sync --quiet; uv sync --quiet --frozen
uv run ruff check . && uv run ruff format --check .
uv run pytest -q -x
```

---

### Step 2: LiteLLM client module (NFR3), fake management API, read-only DB reader
**Spec:** §10 (Phase 1 subset), §8.1, ERD, §3.2 R10/R11, §3.4 R18/R19, NFR3/NFR5/NFR6, §11.3.

**Files:**
- `app/litellm_client/{__init__,models,client,readonly,errors}.py`
- `fake_litellm/{__init__,main,db,state,controls}.py`
- `tests/litellm/test_client_vs_fake.py`, `tests/litellm/test_readonly.py`
- `tests/conftest.py` (adds the `fake_app`, `litellm` and `ro_engine` fixtures)

**Build:**
- `models.py`: one Pydantic v2 request model per endpoint, all `extra="forbid"`, dumped with `exclude_none=True`, plus response models.

  | Request model | Endpoint |
  |---|---|
  | `OrganizationNew` / `OrganizationUpdate` | `/organization/new`, `PATCH /organization/update` |
  | `TeamNew` / `TeamUpdate` | `/team/new`, `/team/update` |
  | `TeamMemberAdd` | `{team_id, member:{user_id, role}, max_budget_in_team}` |
  | `TeamMemberUpdate` | `{team_id, user_id, role?, max_budget_in_team, budget_duration, temp_budget_increase?, temp_budget_expiry?, model_max_budget?, tpm_limit?, rpm_limit?}`; validator: the two temp fields go together |
  | `TeamMemberDelete` | `/team/member_delete` |
  | `UserNew` | `{user_id, sso_user_id, user_email}` |
  | `KeyGenerate` / `KeyUpdate` | `{user_id, team_id, key_alias, max_parallel_requests, rpm_limit, model_tpm_limit}` |
  | `KeyBlock` | `{key}` |
  | `JwtKeyMappingNew` | `{jwt_issuer, jwt_claim_name, jwt_claim_value, token}` |

  - `ModelBudget = {budget_limit: float, time_period: str}` is the value type of `model_max_budget` maps (V1 re-verifies it).
  - **`KeyGenerate` and `KeyUpdate` have no `model_max_budget` field** (DR3/R6).
  - Response models: `KeyGenerateResponse{key, token}`, `TeamInfo`, `OrganizationInfo`, `KeyListResponse`.
- `client.py`: `class LiteLLMClient(httpx.AsyncClient-backed)`. It takes `base_url`, `master_key` and an injectable `transport` (tests use ASGITransport). It has one typed method per endpoint: `organization_new`, `organization_update`, `organization_info`, `team_new`, `team_update`, `team_info`, `team_member_add/update/delete`, `user_new`, `key_generate`, `key_update`, `key_block`, `key_unblock`, `key_list(user_id)`, `jwt_mapping_new`, `health()`. It raises:
  - `LiteLLMTransientError` for connect errors, timeouts and 5xx.
  - `LiteLLMPermanentError(status, body)` for 4xx.
  - It never logs the master key or a `key` value (NFR5).
- `readonly.py`: `class LiteLLMReader(engine)`, used with the `litellm_ro` role:
  - `org_allocations()`: the §11.3 query 1 grouping, also selecting `o.organization_id`, **without** the HAVING clause. The caller applies `allocated > dept_budget − reserve` (§11.3 note).
  - `overallocated_teams()`: §11.3 query 2 verbatim, also selecting `t.team_id`.
  - `spend_rows(start, end)`: from `"LiteLLM_SpendLogs"`, returns `(team_id, "user", model_group, day, Σspend)`.
  - `ping()`.
- Fake storage (`fake_litellm/db.py`): SQLAlchemy Core tables with the ERD names and columns, all identifiers quoted:
  - `"LiteLLM_BudgetTable"(budget_id, max_budget, budget_duration, model_max_budget jsonb, temp_budget_increase, temp_budget_expiry, tpm_limit, rpm_limit)`
  - `"LiteLLM_OrganizationTable"(organization_id, organization_alias, budget_id, models text[], spend, metadata jsonb)`
  - `"LiteLLM_OrganizationMembership"(user_id, organization_id, user_role)`
  - `"LiteLLM_TeamTable"(team_id, team_alias, organization_id, max_budget, spend, budget_duration, model_max_budget jsonb, tpm_limit, max_parallel_requests, members_with_roles jsonb, metadata jsonb)`
  - `"LiteLLM_TeamMembership"(user_id, team_id, budget_id, spend)`
  - `"LiteLLM_UserTable"(user_id, sso_user_id, user_email, organization_id, teams text[])`
  - `"LiteLLM_VerificationToken"(token, key_alias, user_id, team_id, max_budget, spend, max_parallel_requests, rpm_limit, tpm_limit, metadata jsonb, model_max_budget jsonb, blocked, organization_id)`
  - `"LiteLLM_JWTKeyMapping"(id, jwt_issuer, jwt_claim_name, jwt_claim_value, token)`
  - `"LiteLLM_SpendLogs"(request_id, api_key, "user", team_id, organization_id, model_group, "startTime", "endTime", spend)`

  `create_all` runs on startup. `model_tpm_limit` and the team member defaults are stored in `metadata` (§8.1, §10). An organization's budget lives in a `BudgetTable` row via `budget_id`. A member budget lives in a `BudgetTable` row via `TeamMembership.budget_id` (R2).
- Fake endpoints (`fake_litellm/main.py`):
  - Exactly the client's endpoints, parsed with `app.litellm_client.models` (DRY; real-shape drift is caught by contract tests and V1).
  - Auth: `Bearer FAKE_MASTER_KEY`.
  - `/key/generate` returns `key="sk-"+random` and `token=sha256(key)`. Only the hash is persisted.
  - `/team/new` with an existing `team_id` returns 400 "already exists".
  - `GET /health/liveliness`.
- Fake controls (`fake_litellm/controls.py`, enabled when `FAKE_TEST_CONTROLS=1`, which is the fake's default): `POST /_test/reset` and `POST /_test/outage {on}`. During an outage, management and health endpoints return 503; model traffic is unaffected.
- Read-only role: the test fixture and `dev_db.py` run `CREATE ROLE litellm_ro LOGIN; GRANT CONNECT, USAGE, SELECT ON ALL TABLES; ALTER DEFAULT PRIVILEGES ... SELECT` on `litellm_*`.

**Tests:**
- `test_client_vs_fake.py`:
  - `::test_<endpoint>_roundtrip` for each endpoint.
  - `::test_key_generate_returns_hash_and_plaintext_once`.
  - `::test_outage_raises_transient`.
  - `::test_4xx_raises_permanent`.
  - `::test_key_models_have_no_model_max_budget`.
- `test_readonly.py`:
  - `::test_overallocated_teams_query_11_3`: seed the fake via the client; members whose caps plus active temp increases exceed the team budget are flagged; an expired temp increase is not counted.
  - `::test_org_allocations_query_11_3`.
  - `::test_readonly_role_cannot_write`.

**test.sh after:** unchanged (pytest picks up the new tests).

---

### Step 3: Fake runtime enforcement (§3.1) and contract-test harness (A2/A3/A5 at the LiteLLM level)
**Spec:** §3.1 R1/R2/R5/R6/R7, §3.2 R10/R11, §15.2 A2/A3/A5, builder decision 2.

**Files:** `fake_litellm/enforcement.py`, `fake_litellm/main.py` (adds the model route), `fake_litellm/controls.py`, `tests/contract/{conftest,helpers}.py`, `tests/contract/test_a2_team_cap.py`, `tests/contract/test_a3_key_concurrency.py`, `tests/contract/test_a5_model_pot.py`, `tests/contract/README.md`.

**Build:**
- `POST /chat/completions` and `/v1/chat/completions`, a minimal OpenAI-shaped response. Auth:
  - `Bearer sk-...` → hash lookup.
  - Otherwise an HS256 JWT signed with `FAKE_JWT_SECRET`, with claims `iss` and `upn` → `LiteLLM_JWTKeyMapping` lookup (R10).
  - Unmapped → 403 (R11 reject). Blocked key → 401.
- Concurrency (R7): an in-memory per-token counter. If `in_flight ≥ max_parallel_requests`, return 429 at once. The slot is held for the whole request, including the injected latency, and released in `finally`.
- Budgets (R1), checked in this order: key, team member, team, organization.
  - Team member budget = `max_budget + (temp_budget_increase if temp_budget_expiry > now)` (R2).
  - A request is blocked when `spend ≥ limit`. The response is 400 `{"error":{"type":"budget_exceeded","scope":"key|team_member|team|organization|team_model|member_model","message":...}}`.
- Model budgets (R5): team `model_max_budget[model]` and member `model_max_budget[model]`, checked against Σ SpendLogs for that team (and user) and `model_group`. If the key has its own entry for that model, the team entry is skipped (R6). The A6 negative test lands in Phase 0 and needs this.
- After success: charge `cost_per_request` to key, membership, team and organization spend, and insert a SpendLogs row (`startTime`/`endTime` from the real duration).
- Controls:
  - `POST /_test/config {latency_ms, cost_per_request}`
  - `POST /_test/spend {team_id, user_id?, model, amount, at?}`: inserts SpendLogs and bumps the spend fields consistently.
  - Helper `make_test_jwt(upn)`.
- Contract harness (`tests/contract/conftest.py`):
  - Parametrized fixture `target` in `["fake", "real"]`.
  - `real` is skipped unless `LITELLM_BASE_URL` is set; it also needs `LITELLM_MASTER_KEY`, `LITELLM_DATABASE_URL` and `LITELLM_TEST_MODEL`.
  - Contract tests create their own objects with the client and call models with the **plaintext test key** returned by `/key/generate`. That key is held in memory only for the test and never stored.
  - `helpers.assert_blocked(resp, scope, target)` handles fake and real error formats.
  - README: the real target needs a mock model priced so each request costs exactly 1 credit, plus 2 replicas for A3 (§15.2).

**Tests:**
- `test_a3_key_concurrency.py::test_a3_key_parallel_limit[fake|real]`: key `max_parallel_requests=3`, latency 500 ms, 6 concurrent requests → exactly 3 succeed and 3 get 429.
- `test_a5_model_pot.py::test_a5_member_caps_within_pot[fake|real]`: pot X=10, member caps X=4 for 2 members, cost 1 → each member stops after 4 requests.
- `::test_a5_pot_binds_together[fake|real]`: member caps 6 and 6 written directly to LiteLLM → together they stop at 10.
- `test_a2_team_cap.py::test_a2_litellm_blocks_at_team_cap[fake|real]`: caps 60 + 60, team 100, written directly → blocked with scope `team` once team spend reaches 100, and `LiteLLMReader.overallocated_teams()` lists the team.
- `tests/litellm/test_fake_enforcement.py::test_unmapped_jwt_403`, `::test_temp_increase_counts_until_expiry`, `::test_member_cap_blocks`.

**test.sh after:** unchanged. Real-target cases show as skipped.

---

### Step 4: BFF auth (ADFS OIDC + dev login), session, CSRF, CSP, roles, directory, `GET /me`, seed fixture
**Spec:** §6, §12.1 (scope switcher data), §12.2, §12.10 (BFF cookie), §11 (auth), NFR5/NFR9, D11, builder decision 4.

**Files:** `app/auth/{__init__,oidc,dev,session,csrf}.py`, `app/security_headers.py`, `app/directory.py`, `app/permissions.py`, `app/routers/me.py`, `app/schemas/me.py`, `seed/dev.yaml`, `app/seed.py`, `scripts/seed.py`, `tests/auth/test_oidc.py`, `tests/auth/test_dev_login.py`, `tests/auth/test_csrf_csp.py`, `tests/test_permissions.py`, `tests/test_me.py`.

**Build:**
- Session: Starlette `SessionMiddleware` (signed cookie `wm_session`, httpOnly, `SameSite=Lax` for the OIDC redirect, `Secure` from config, max age 8 h).
  - Contents: `{upn, name, groups[], exp}`. `groups` is filtered at login to DNs present in `role_binding`, which keeps the cookie small.
  - ADFS tokens are never stored or sent to the browser (§12.10).
- `oidc.py` (authlib Starlette client):
  - `GET /auth/login` redirects to ADFS with state, nonce and PKCE.
  - `GET /auth/callback` exchanges the code, verifies the id_token via JWKS, reads the `OIDC_UPN_CLAIM`, `OIDC_NAME_CLAIM` and `OIDC_GROUPS_CLAIM` claims (values matched verbatim against `role_binding.ad_group_dn`), sets the session and the `wm_csrf` cookie, and redirects to `/`.
  - `POST /auth/logout` clears the session.
- `dev.py`: mounted only when `DEV_AUTH=1`, otherwise 404.
  - `GET /auth/dev/users` lists `[{upn, display_name, roles}]` from the fixture directory.
  - `POST /auth/dev/login {upn}` sets the same session as OIDC.
- `csrf.py`: middleware for POST/PUT/PATCH/DELETE (exempt: `/auth/*`). CSP middleware on every response:
  ```
  default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
  img-src 'self' data:; frame-ancestors 'none'
  ```
  plus `X-Content-Type-Options` (NFR9).
- `directory.py`: a `Directory` Protocol with `groups_of(upn)` and `members_of(dn)`. `FixtureDirectory(seed/dev.yaml)` implements it in Phase 1; LDAP comes in Phase 3. It provides dev-login groups, target roles for DR9, and FR-E3 names.
- `permissions.py`:
  - `Principal{upn, display_name, bindings: list[RoleBinding]}` comes from `role_binding` ⋂ session groups, recomputed on every request.
  - Roles: `employee` (implicit), `group_head(group)`, `pmo(department)`, `department_rep(department)`, `platform_admin(global)`, `viewer(global|department|group)`.
  - Helpers that return the authorizing role, or raise 403 `FORBIDDEN` (404 when the entity is not visible at all):

    | Helper | Who passes |
    |---|---|
    | `can_view_department(d)` | rep / pmo / viewer of d, admin, viewer global |
    | `can_edit_split(d)` | rep of d, admin |
    | `can_edit_dept_budget` | admin only |
    | `can_view_group(g)` | head of g, rep / pmo / viewer of g's department, viewer of g, admin |
    | `can_view_members(g)` | head of g, rep of g's department, admin (D11: not pmo, not viewer) |
    | `can_edit_group_pots_limits(g)` | rep of g's department, admin |
    | `can_edit_member_defaults(g)` | head of g, admin |
    | `can_write_members(g)` | head of g, rep of g's department, admin |

  - `target_tier(upn) -> 'platform_admin'|'department_rep'|'group_head'|'member'` comes from directory groups ⋂ `role_binding`.
- `GET /me`: `{upn, display_name, group:{id,name}, department:{id,name}, roles:[{role, scope_kind, scope_id, scope_name}], scopes:[{kind:"self"}, {kind:"group",id,name,role}, {kind:"department",id,name,role}, {kind:"platform"}], timezone, current_period, dev_auth}`.
- `seed/dev.yaml` and `app/seed.py` (WM rows only; Step 5 adds provisioning):
  - Departments: D1 with groups A1, A2, B1 and a D1 staff group, using the §12.8 wireframe numbers (budget 1,000,000; A1 250,000; A2 150,000; B1 350,000; staff 20,000; PMO 180,000; reserve 50,000). D2 with C1 and a D2 staff group.
  - Employees with base caps.
  - Policies: `concurrency {min:1, default:4, max:8}`, `rpm {min:1, default:120, max:600}`, `overalloc_factor_default 1.0`, `thresholds {near:0.8, full:1.0}` (D6 placeholders).
  - Role bindings and directory groups for these personas:

    | Persona | Roles | Group |
    |---|---|---|
    | `admin@` | platform admin | D1 staff |
    | `rep@` | rep D1 | D1 staff |
    | `pmo@` | PMO D1 | D1 staff |
    | `dana@` | head A1 | A1 |
    | `dani@`, `yael@` | — | A1 |
    | `noa@` | head A2 + viewer D1 (U6) | A2 |
    | `avi@` | — | A2 |
    | `viewer@` | viewer D1 | C1 |

  - `scripts/seed.py [--reset]` is idempotent.

**Tests:**
- `test_oidc.py::test_callback_creates_session_with_filtered_groups`: respx mocks discovery, JWKS and token endpoints with an RSA-signed id_token.
- `::test_callback_rejects_bad_nonce`.
- `test_dev_login.py::test_dev_login_404_without_DEV_AUTH`, `::test_dev_login_sets_session`.
- `test_csrf_csp.py::test_write_without_csrf_403`, `::test_csp_header_present`.
- `test_permissions.py::test_matrix_12_2` (parametrized persona × helper), `::test_target_tier`.
- `test_me.py::test_me_lists_scopes_for_two_roles`, `::test_me_401_without_session`.

**test.sh after:** unchanged.

---

### Step 5: Write path, outbox worker, audit, provisioning seed, `PUT /departments/{id}/budget`
**Spec:** §5.3, §5.4, §8.1, §8.2 (`litellm_write`, `audit_log`), §9.3 Joiner (seed only), FR-D1, FR-A1, FR-RC3 (retry), NFR4/NFR5.

**Files:** `app/writes.py`, `app/outbox/{__init__,desired,enqueue,worker}.py`, `app/worker.py`, `app/services/departments.py`, `app/routers/departments.py`, `app/schemas/departments.py`, `app/seed.py` (adds provisioning), `tests/test_write_path.py`, `tests/test_outbox.py`, `tests/test_seed_provisioning.py`, `tests/api/test_department_budget.py`.

**Build:**
- `writes.py`:
  - Dependency `write_context(request, principal, db) -> WriteContext`. It reads and decodes `X-Change-Reason` (required), derives `request_id`, and runs the replay check (existing audit rows for the request id → `ReplayError`, which the router turns into 200 with the current entities).
  - `WriteContext` methods: `lock(kind, id)`, `audit(entity, entity_id, before, after, actor_role)` (adds context ids such as `group_id` and `department_id` to `after`), `enqueue(entity, entity_id, endpoint)`.
  - The domain rows, audit rows and outbox rows are committed in one transaction.
  - `sync_status(entity, ids)`: the latest row per (entity, entity_id, endpoint), aggregated as failed > pending > applied.
  - `write_response(ctx, entities)`.
- `outbox/desired.py`: desired-state builders per §8.1, evaluated **at apply time**, so retries send the latest values and the ids and token hash are resolved then.

  | Builder | Request |
  |---|---|
  | `org(department)` | `organization_id, organization_alias, max_budget=budget, budget_duration` (`models` not sent in Phase 1; it would restrict models) |
  | `team(group)` | `team_id, team_alias, organization_id, max_budget = base + Σ active grants, budget_duration, model_max_budget = pots + Σ active model grants, tpm_limit, model_tpm_limit, team_member_budget = default_member_cap, team_member_budget_duration, team_member_model_max_budget` |
  | `member(employee)` | `max_budget_in_team = base_cap, budget_duration, model_max_budget = caps (+ model shares)`; temp fields both or neither |
  | `member_add(employee)` | `/team/member_add` |
  | `user(employee)` | `/user/new` |
  | `key_generate(employee)` | `/key/generate` |
  | `key(employee)` | `key = key_token_hash, team_id, max_parallel_requests, rpm_limit, model_tpm_limit` (never `model_max_budget`) |
  | `jwt_mapping(employee)` | `jwt_issuer = JWT_ISSUER, jwt_claim_name = "upn", jwt_claim_value = upn, token = hash` |

  The payload is written into `litellm_write.payload` at enqueue, then overwritten with the payload actually sent at apply.
- `outbox/worker.py`: `async def drain(sf, client) -> DrainResult` processes `pending` rows in `id` order:
  - Success → `applied`.
  - `LiteLLMTransientError` → stays `pending`, `attempts += 1`, `last_error` set, drain stops. The global exponential backoff (1 s up to 60 s) lives in memory, which keeps per-entity order and adds no columns (NFR4).
  - `LiteLLMPermanentError` → `failed` with `last_error`; drain continues.
  - Create endpoints are idempotent:
    - `/team/new` and `/organization/new` check `/team/info` and `/organization/info` first and switch to update if the object exists.
    - `/key/generate` checks `/key/list?user_id=` first and adopts an existing key.
    - `/user/new` treats a 4xx "already exists" as applied.
  - On key/generate apply, store `employee.key_token_hash = token` and drop the plaintext (NFR5).
- `app/worker.py`: `python -m app.worker` loops `drain` every `OUTBOX_POLL_S`. Steps 8 and 9 add the snapshot and over-allocation loops.
- `PUT /departments/{id}/budget {budget, budget_period}` (FR-D1, admin): audit → enqueue `org` → `write_response`.
- Seed provisioning enqueues, in this order:
  - `/organization/new` per department, then `/team/new` per group.
  - The §9.3 Joiner sequence per employee: `/user/new` → `/team/member_add` (base cap) → `/key/generate` → `/jwt/key/mapping/new`.
  - Audit actor `seed` with role `sync_service`.
  - `scripts/seed.py --reset` also calls fake `/_test/reset` and drains.

**Tests:**
- `test_write_path.py`:
  - `::test_missing_reason_422_invalid_request`
  - `::test_same_idempotency_key_replays_without_duplicates`
  - `::test_audit_row_has_who_role_before_after_reason_request_id` (FR-A1)
  - `::test_domain_audit_outbox_single_transaction` (failure injected after the audit insert → nothing persisted)
- `test_outbox.py`:
  - `::test_drain_applies_and_marks_applied`
  - `::test_outage_keeps_pending_then_applies_after_recovery` (§5.4; the backend side of U3)
  - `::test_permanent_error_marks_failed`
  - `::test_apply_uses_latest_desired_state`
  - `::test_personal_key_plaintext_never_stored` (scan the DB and payloads for `sk-`)
- `test_seed_provisioning.py::test_seed_creates_org_team_user_member_key_mapping` (checked via fake tables and a JWT request that succeeds).
- `test_department_budget.py::test_put_budget_pending_then_applied`, `::test_rep_cannot_set_budget_403`, `::test_viewer_write_403`.

**test.sh after:** unchanged.

---

### Step 6: Department allocation: split, model allowances, group pots, group limits, advisory locks
**Spec:** FR-D2, FR-D3, FR-D4, FR-V1, §11.1 (Departments, Groups: model-pots and limits), §11.2, §12.2, §12.5.4 (data), §12.6 F1.

**Files:** `app/services/departments.py`, `app/services/validation.py`, `app/routers/departments.py`, `app/routers/groups.py`, `app/schemas/{departments,groups}.py`, `tests/unit/test_validation.py`, `tests/api/test_split.py`, `tests/api/test_model_pots.py`, `tests/api/test_group_limits.py`, `tests/api/test_concurrency_locks.py`.

**Build:**
- `validation.py` holds pure functions, unit tested (§15.1):
  - `check_sum(children_total, parent_total, factor=1) -> None | ApiError(PARENT_OVERALLOCATED, requested, available=parent_total*factor, parent_total)`
  - `check_model_sums(new_map_by_child, parent_map, factor)`: per-model; a missing parent counts as 0.
  - `check_limit(field, value, policy, is_admin)`
- `GET /departments`: the departments visible to the principal.
- `PUT /departments/{id}/split {groups:[{group_id, base}], pmo_pool, reserve}` (rep of d, admin):
  - `lock(DEPARTMENT, id)`.
  - Groups that are not listed keep their base. `archived` groups are excluded from the sum.
  - Reject if Σ bases + pmo_pool + reserve > budget, with `details.rows` naming the changed rows.
  - A group_id outside the department → 422 `SCOPE_FORBIDDEN`.
  - Audit one row per changed group (entity `group`) plus one `department` row (pmo_pool/reserve).
  - Enqueue `team` for each changed group. PMO pool and reserve are logical, with no LiteLLM write (§1, §8.1).
- `PUT /departments/{id}/model-allowances {model: amount}` (admin; see questions): replaces the map (absent or 0 deletes). No LiteLLM write (R5).
- `PUT /groups/{id}/model-pots {model: amount}` (rep of the department, admin): `lock(DEPARTMENT, dept)`. Reject if, for any X, Σ group pots for X > allowance X (FR-D3), with details `{model}`. Enqueue `team`.
- `PUT /groups/{id}/limits {tpm_limit, model_tpm_limits}` (rep, admin): no sum rule. Enqueue `team` (FR-D4).
- All of these use `write_context` and return pending entities.

**Tests:**
- `test_validation.py::test_check_sum_*`, `::test_model_sums_missing_parent_is_zero`.
- `test_split.py`:
  - `::test_split_within_budget_pending_then_team_max_budget_applied`
  - `::test_split_over_budget_422_with_numbers` (requested / available / parent_total)
  - `::test_split_foreign_group_scope_forbidden`
  - `::test_staff_group_is_regular_row`
  - `::test_pmo_rep_viewer_permissions`
- `test_model_pots.py::test_pots_exceed_allowance_422`, `::test_pot_written_to_team_model_max_budget`.
- `test_group_limits.py::test_team_tpm_and_metadata_model_tpm`.
- `test_concurrency_locks.py::test_concurrent_splits_one_rejected` (FR-V1): two splits run with `asyncio.gather`, each valid alone and over budget together; a monkeypatched `asyncio.sleep` after the sum read widens the race window. Exactly one gets 422.

**test.sh after:** unchanged.

---

### Step 7: Group and member allocation: DR9 rule, caps, model caps, limits, member defaults, `GET /policies`
**Spec:** FR-G1–G5, FR-D5, DR6–DR9, §4.1, §6, §11.1 (Groups), §11.2, D5, D7.

**Files:** `app/services/members.py`, `app/services/personal_values.py`, `app/routers/groups.py`, `app/routers/policies.py`, `app/schemas/groups.py`, `tests/unit/test_personal_values.py`, `tests/api/test_member_caps.py`, `tests/api/test_member_model_caps.py`, `tests/api/test_member_limits.py`, `tests/api/test_member_defaults.py`.

**Build:**
- `personal_values.authorize(principal, target: Employee, group) -> actor_role` applies these rules in order:
  1. Target is the actor → 422 `SELF_ALLOCATION_FORBIDDEN` (always, including admins; FR-G5). Details carry `owner_role`.
  2. Tier `member` → allowed for the head of the group or an admin.
  3. Tier `group_head` → allowed for the rep of the group's department or an admin (FR-D5).
  4. Tier `department_rep` or `platform_admin` → allowed for admins only.
  5. Any other actor that holds a write role on the URL scope → 422 `SCOPE_FORBIDDEN`. Actors with no write role were already rejected with 403.
  6. A upn not in the group (or with status `left`) → 422 `SCOPE_FORBIDDEN`.
- `PUT /groups/{id}/members/caps [{upn, base_cap}]` (bulk, one transaction):
  - Authorize every row and collect the row errors. The first failing code by severity (SELF > SCOPE) becomes the envelope code, with `details.rows`.
  - `lock(GROUP, id)`. Reject if Σ caps of non-left members (with new values) > `base_budget × overalloc_factor` (FR-G1). Phase 1 uses `base_budget` and has no grants yet.
  - Enqueue `member` per changed employee.
- `PUT /groups/{id}/members/{upn}/model-caps {model: amount}`: authorize, `lock(GROUP)`. For each X, Σ member caps for X > pot X × factor → 422 (FR-G3). Enqueue `member`.
- `PUT /groups/{id}/members/{upn}/limits {max_parallel, rpm_limit, model_tpm_limits}`: authorize. `max_parallel` and `rpm_limit` must be within the policy `[min, max]`, else 422 `LIMIT_OUT_OF_POLICY` with `details{field, min, max}`. An admin may exceed `max` but not go below `min` (D7). Enqueue `key` (FR-G4).
- `PUT /groups/{id}/member-defaults {cap, model_caps}` (head of g, admin; FR-G2):
  - `cap` → `grp.default_member_cap`.
  - `model_caps` → `policy` row `member_defaults.group.<id>` = `{"model_caps": {...}}`. §8.2 has no column for it; see questions.
  - Enqueue `team`.
- `GET /policies`: any authenticated user, read-only. The UI needs the limit ranges. `PUT /policies` is Phase 3.

**Tests:**
- `test_personal_values.py`: one test per rule above, including `::test_admin_cannot_set_own_values`, `::test_cohead_scope_forbidden`, `::test_rep_sets_group_head_values`, `::test_staff_head_cannot_set_rep_values`.
- `test_member_caps.py`:
  - `::test_caps_within_base_applied_to_member_budget`
  - `::test_caps_over_base_422_with_numbers` (FR-G1)
  - `::test_overalloc_factor_allows_overbooking`
  - `::test_group_head_own_row_self_allocation_forbidden` (backend side of U2)
  - `::test_bulk_is_all_or_nothing`
  - `::test_concurrent_bulk_caps_one_rejected` (FR-V1, same race technique as Step 6)
- `test_member_model_caps.py::test_caps_vs_pot_422`, `::test_no_pot_means_zero`.
- `test_member_limits.py::test_out_of_policy_422`, `::test_admin_above_max_ok`, `::test_key_update_payload_has_no_model_max_budget`.
- `test_member_defaults.py::test_team_member_budget_fields_written`.

**test.sh after:** unchanged.

---

### Step 8: Spend snapshot and wallet read models: `/me/wallet`, `/me/usage`, group and department summaries, members list
**Spec:** §12.3 formulas, §12.4 states, §12.9, FR-E1–E3, §11.1 (`/me/wallet`, `/me/usage`, `/departments/{id}/summary`, `/groups/{id}/summary`, `/groups/{id}/members`), §5.4 row 3, D3, D11.

**Files:** `app/services/spend.py`, `app/services/wallet.py`, `app/services/history.py`, `app/services/owners.py`, `app/routers/{me,groups,departments}.py`, `app/schemas/wallet.py`, `app/worker.py` (adds the snapshot loop), `tests/unit/test_wallet_formulas.py`, `tests/unit/test_wallet_states.py`, `tests/test_spend_snapshot.py`, `tests/api/test_me_wallet.py`, `tests/api/test_group_summary.py`, `tests/api/test_department_summary.py`, `tests/api/test_members_list.py`.

**Build:**
- `spend.take_snapshot(wm, reader, now)`:
  - For the current period, and the previous one if missing, read `reader.spend_rows(period_start, period_end)` (bounds from `APP_TIMEZONE`, then UTC).
  - Map rows to WM: group by `litellm_team_id`; employee by `upn` when the row's team is the employee's current group ("membership spend"); department = Σ of its groups.
  - Write `spend_snapshot` rows with `scope_kind ∈ {department, group, employee}`, `period ∈ {"YYYY-MM", "YYYY-MM-DD"}`, `model` NULL (total) or the model name.
  - Delete and insert per period in one transaction.
  - If the read-only DB is unreachable, keep the old rows and log (§5.4).
- Worker loop every `SPEND_SNAPSHOT_INTERVAL_S`.
- `wallet.py` holds pure formulas (§12.3):
  - `department_wallet(B, Σbases, pmo, reserve, used)`
  - `group_wallet(base, Σactive_grants, Σmember_caps, Σactive_shares, used)`
  - `employee_wallet(base_cap, Σactive_shares, used)`
  - `effective_remaining = min(employee, group, department remaining)`
  - `binding_level ∈ {self, group, department}` (FR-E2)
  - `state(total, used, own_remaining, parent_remainings, allocated?)` → `healthy | near_limit | blocked_own | blocked_group | blocked_department | over_allocated`, with priority blocked_own > blocked_parent > near > healthy, using the policy thresholds. `left` and `blocked` statuses yield a muted state (§12.4).
- `history.values_as_of(entity, ids, fields, at)` serves past periods:
  - The latest audit `after` at or before `at`.
  - Else the earliest later audit `before`.
  - Else the current value.
- `owners.py` (FR-E3, DR9): `owner_of(target) -> {role, people:[{upn, display_name}]}` via `target_tier` + `role_binding` + `Directory.members_of`. Returns `[]` when the directory is unknown.
- Endpoints. Every money response includes `period`, `reset_date`, `spend_taken_at` (freshness), `read_only` (true for past periods) and per-entity `sync_status`.
  - `GET /me/wallet?period=`: `{wallet{total, base_cap, shares_total, used, remaining, percent, state, effective_remaining, binding{level, group_percent, department_percent}}, models[{model, cap, used, remaining}], shares[], limits{max_parallel, rpm_limit, model_tpm_limits}, group{id, name, percent, state}, department{id, name, percent}, owners{base_cap, model_caps, limits, shares}}`
  - `GET /me/usage?from=&to=&granularity=day`: `[{day, model, spend}]` from the day snapshots of the principal.
  - `GET /groups/{id}/summary?period=` (`can_view_group`): wallet card; `pots[{model, amount, used, members_caps_sum}]`; grants summary (empty in Phase 1); top 5 consumers and near/at-limit members (empty when `can_view_members` is false, D11); `pending_sync_count`; `actions{edit_members, edit_pots_limits, edit_defaults}`.
  - `GET /groups/{id}/members?period=` (`can_view_members`): rows `{upn, display_name, is_group_head, base_cap, shares_total, total, used, percent, model_caps, max_parallel, rpm_limit, status, state, sync_status, editable, owner{role, people}}`. Past periods take caps from `values_as_of`, which supports "copy previous period".
  - `GET /departments/{id}/summary?period=` (`can_view_department`): wallet card; `split{groups[{id, name, kind, base}], pmo_pool, reserve, unallocated}`; groups table `{base, grants, total, used, percent, state}`; `allowances[{model, amount, pots_sum}]`; `actions{edit_split, edit_budget, edit_allowances}`.

**Tests:**
- `test_wallet_formulas.py`: one test per row of the §12.3 table, including PMO (the formula alone; data comes in Phase 2).
- `test_wallet_states.py`: one test per §12.4 rule, including `::test_blocked_by_group_when_personal_left` and `::test_over_allocated_level`.
- `test_spend_snapshot.py::test_snapshot_groups_by_scope_and_model`, `::test_ro_db_down_keeps_last_snapshot`, `::test_employee_spend_only_current_group`.
- `test_me_wallet.py`:
  - `::test_fields_fr_e1`
  - `::test_binding_constraint_group_exhausted` (FR-E2; the data behind U4)
  - `::test_owner_names_from_directory` (FR-E3): a member → the group head's name; a group head → the rep's name.
- `test_group_summary.py::test_pmo_sees_summary_without_members`, `::test_viewer_aggregates_only`.
- `test_members_list.py::test_head_row_not_editable_names_rep`, `::test_past_period_uses_audit_values_and_read_only`.
- `test_department_summary.py::test_split_and_unallocated`.

**test.sh after:** unchanged.

---

### Step 9: Over-allocation report (FR-V2), `/status`, `/outbox`, `/audit`, metrics, end-to-end A2/A3/A5 through WM
**Spec:** FR-V2, §11.3, FR-A1, §11.1 (`/reports/overallocation`, `/status`, `/outbox`, `/audit`), §12.2 (reports and history rows), §5.4, NFR7, §15.2 A2/A3/A5.

**Files:** `app/services/overallocation.py`, `app/routers/{reports,platform,audit}.py`, `app/metrics.py`, `app/worker.py` (adds the over-allocation loop), `tests/api/test_overallocation.py`, `tests/api/test_status_outbox.py`, `tests/api/test_audit.py`, `tests/e2e_backend/test_a2_wm.py`, `tests/e2e_backend/test_a3_wm.py`, `tests/e2e_backend/test_a5_wm.py`.

**Build:**
- `overallocation.findings(wm, reader)` returns a list of `Finding{source: "litellm"|"wm", level: department|group|group_model|member_model, entity_id, name, allocated, total, model?}`.
  - From live LiteLLM (§11.3): organizations with `allocated > dept_budget − reserve`, using reserve from WM; and `overallocated_teams()`.
  - From WM tables (exact checks):
    - Σ bases + pmo + reserve > budget
    - Σ member caps > base × factor
    - Σ pots > allowance per model
    - Σ member model caps > pot × factor
- `GET /reports/overallocation`: admin sees all; rep and viewer see their departments only (§12.2 reports row). Other roles get 403.
- Scheduled check (FR-V2): the worker runs `findings` every `OVERALLOC_INTERVAL_S`, logs a structured warning for each finding, and sets the gauge `wm_overallocation_findings`. Notifications arrive in Phase 2 (FR-N).
- `GET /status`: any authenticated user, no secrets. Returns `{litellm_reachable` (cached 10 s `client.health()`)`, outbox{pending, failed, oldest_pending_at}, spend_taken_at, last_sync: null}`. Redis status comes in Phase 3. The UI banner depends on it (§5.4).
- `GET /outbox?status=` and `POST /outbox/{id}/retry` (admin): retry sets the row back to `pending`. The §12.3 sync badge retry needs these.
- `GET /audit?entity=&id=&actor=&from=&to=` (FR-A1, §12.2 history row):
  - Admin: any filter.
  - Otherwise `entity` and `id` are required and must be visible: an employee sees their own record; a group needs `can_view_members` or (head of g); a department needs rep of d; a viewer needs scope.
  - A group timeline includes rows where `entity = 'group'` and `entity_id = g`, plus employee rows where `after->>'group_id' = g`.
  - Each row includes `litellm_calls[{endpoint, status, attempts, last_error}]` joined on `idempotency_key LIKE request_id || ':%'`.
- `/metrics` (prometheus-client): `wm_outbox_depth{status}`, `wm_validation_rejections_total{code}`, `wm_spend_snapshot_age_seconds`, `wm_overallocation_findings`.

**Tests:**
- `test_overallocation.py::test_live_team_overallocation_flagged`, `::test_reserve_adjusted_department_check`, `::test_wm_exact_checks`, `::test_scope_filtering`.
- `test_status_outbox.py::test_status_reports_unreachable_during_outage`, `::test_retry_failed_row`.
- `test_audit.py`:
  - `::test_audit_includes_litellm_call_status` (FR-A1)
  - `::test_group_timeline_includes_member_changes`
  - `::test_employee_sees_only_own_record`
- `test_a2_wm.py::test_a2_bypass_then_report_flags`: member caps above the team budget written directly to the fake → requests blocked at scope `team` → `GET /reports/overallocation` contains the group.
- `test_a3_wm.py::test_a3_limits_set_via_wm_enforced`: the group head sets `max_parallel=3` via the API → drain → 6 concurrent JWT requests as the employee → 3×200 and 3×429.
- `test_a5_wm.py::test_a5_pots_and_caps_via_wm`: the rep sets pot X=10, the head sets two caps X=4 → drain → each member stops at 4. An attempt to raise one cap to 7 → 422 `PARENT_OVERALLOCATED`.

**test.sh after:** unchanged. Check that pytest stays under about 90 s.

---

### Step 10: Frontend shell, E2E harness, scope switcher, shared primitives
**Spec:** §12.1, §12.2 (navigation), §12.3 (sync badge), §12.7, §12.10, §5.4 (banner), U6 (switcher part), U8 (base).

**Files:**
- Root: `package.json` (`{"private":true,"workspaces":["frontend"]}` plus `lint`, `typecheck`, `test` and `e2e` scripts), `package-lock.json`, `eslint.config.js` (flat config: typescript-eslint, react-hooks), `.prettierrc`, `.prettierignore` (`SPEC.md`).
- `frontend/{package.json, vite.config.ts, tsconfig.json, index.html, playwright.config.ts}`.
- `frontend/src/{main.tsx, App.tsx, routes.tsx}`.
- `frontend/src/api/{client.ts, queries.ts, types.ts}`.
- `frontend/src/i18n/{he.ts, errors.ts}`, `frontend/src/format.ts`.
- `frontend/src/components/{Num, Credits, Pct, DateText, SyncBadge, StatusBanner, ScopeSwitcher, PeriodSelector, StateBadge, EmptyState, ErrorState}.tsx`.
- `frontend/src/pages/Login.tsx`.
- `frontend/src/**/*.test.ts(x)` (vitest).
- `scripts/e2e_stack.py`, `frontend/e2e/{fixtures.ts, shell.spec.ts}`.
- Update CLAUDE.md "Frontend install" to `npm ci` (repo root, workspaces).

**Build:**
- Vite + React + TS, **Mantine v7** (RTL via `DirectionProvider`, `<html lang="he" dir="rtl">`), `@mantine/charts` (Recharts, offline), TanStack Query, React Router.
- The Vite proxy forwards `^/(me|departments|groups|grants|projects|service-keys|production|policies|sync|outbox|status|reports|audit|auth|healthz)(/|$|\?)` to `API_ORIGIN` (default `http://127.0.0.1:4101`). Dev UI port 4100, E2E port 4110.
- `api/client.ts`: `apiFetch` with credentials. On writes it adds `X-CSRF-Token` (read from the cookie), `Idempotency-Key` (a fresh `crypto.randomUUID()` per confirm) and `X-Change-Reason` (`encodeURIComponent`). It parses the envelope into `ApiError{code, requested, available, parent_total, details}`. A 401 redirects to `/login`.
- `i18n/errors.ts`: a Hebrew message for every §11.2 code plus `FORBIDDEN`, `CSRF_FAILED`, `INVALID_REQUEST` and network failures. Each message includes the numbers and the next step (§12.7):
  - `PARENT_OVERALLOCATED`: `חילקת {requested} מתוך {available}. יש להפחית {requested−available} לפני שמירה`
  - `LIMIT_OUT_OF_POLICY`: `הערך {requested} מחוץ לטווח המותר ({min}–{max}). יש לבחור ערך בטווח או לפנות למנהל הפלטפורמה`
  - `SELF_ALLOCATION_FORBIDDEN`: `לא ניתן לשנות ערכים אישיים של עצמך. הערכים נקבעים על ידי {owner_role}`
  - `SCOPE_FORBIDDEN`: `היעד אינו בתחום האחריות שלך. יש לבחור עובד או קבוצה מתוך התחום`
  - Phase 2 codes get messages now too: `PMO_POOL_INSUFFICIENT`, `SHARE_EXCEEDS_GRANT`, `SHARE_EXPIRY_AFTER_GRANT`, `GRANT_BELOW_SHARED`.
- `format.ts`:
  - `credits(n)`: `Intl.NumberFormat('he-IL', {maximumFractionDigits: 0})`
  - `pct(n)`: one decimal
  - `date(d, tz)`: `dd/mm/yyyy`
  - `freshness(ts)`: `עודכן לפני N דקות`
- `<Num>` wraps numbers in `<bdi dir="ltr">`, and so do model names and ids (§12.1, U8).
- `SyncBadge`: `ממתין לסנכרון` / `עודכן` / `נכשל`, with text and an icon, never color alone. Admins get a retry button that calls `POST /outbox/{id}/retry`.
- `StatusBanner` polls `/status` every 5 s and shows a warning banner when `litellm_reachable` is false: changes are saved and will be synced.
- Queries holding pending entities refetch every 2 s until they are applied. Write responses are written into the query cache, which gives optimistic display of `pending` values (§12.10).
- `ScopeSwitcher` (top bar): "הארנק שלי", each group headed, each department (rep / PMO / viewer), and "פלטפורמה" for admins. The last scope is stored in `localStorage["wm.scope."+upn]`. The selected scope decides which actions the UI offers; the server's `actions{...}` flags are also respected.
- `PeriodSelector` defaults to `current_period`. Past periods put the UI in read-only mode.
- `Login.tsx`: an ADFS button (`/auth/login`). When `dev_auth` is on, a dev user picker (`/auth/dev/users` → `POST /auth/dev/login`).
- `scripts/e2e_stack.py`:
  - Starts pgembed in a temp dir and creates the DBs and the read-only role.
  - Runs migrations.
  - Starts the fake (4112), the API (4111, with `DEV_AUTH=1` and `SPEND_SNAPSHOT_INTERVAL_S=2`) and the worker (`OUTBOX_POLL_S=0.5`) as subprocesses.
  - Writes `frontend/.e2e-state.json` (gitignored) with the URLs and env.
  - Seeds and waits for `/readyz`.
- `playwright.config.ts`:
  - Two `webServer` entries: the stack (url `http://127.0.0.1:4111/readyz`) and `vite --port 4110` with `API_ORIGIN` set to 4111.
  - `workers: 1`, chromium only.
  - `fixtures.ts` provides `loginAs(page, upn)` and `resetData()`, which runs `uv run python scripts/seed.py --reset` with env from `.e2e-state.json`, and `fake(...)` for control calls.
- **If Chromium cannot launch** because system libraries are missing (there is no sudo):
  1. Run `npx playwright install chromium`, then try `chromium-headless-shell`.
  2. Run `ldd` on the browser binary to list the missing `.so` files.
  3. Fetch them without root: `apt-get download <pkgs>` and `dpkg -x` into `.cache/pwlibs/`. Export `LD_LIBRARY_PATH` from test.sh.
  4. If it still fails, record the exact error and the attempts as a **blocker in PROGRESS.md** and stop. Do not skip or remove E2E tests or their test.sh line.

**Tests:**
- vitest: `errors.test.ts::maps every code with numbers`, `format.test.ts` (separators, decimals, dates, `<bdi>` wrapping).
- Playwright `shell.spec.ts`:
  - `U6 switcher lists self + group + department for noa`
  - `U8 html is rtl/he and numbers render ltr`
  - `banner appears during fake outage`

**test.sh after:** append:
```bash
[ -d node_modules ] || npm ci --silent
npm run -s lint && npm run -s typecheck && npm run -s test -- --run
npx playwright install chromium >/dev/null
npm run -s e2e
```

---

### Step 11: "הארנק שלי" (My wallet)
**Spec:** §12.5.1, §12.8 wireframe 1, §12.3 (wallet card, usage chart), §12.4, FR-E1–E3, §12.6 F4, U4.

**Files:** `frontend/src/components/{WalletCard, ModelTable, SharesTable, UsageChart, OwnersBox, LimitsBox}.tsx`, `frontend/src/pages/MyWallet.tsx`, `frontend/src/components/WalletCard.test.tsx`, `frontend/e2e/wallet.spec.ts`.

**Build:**
- `WalletCard` is used unchanged at all levels: total, used, remaining and percent; a bar with markers at 80% and 100%; optional allocated and unallocated with the over-allocated warning; reset date; binding constraint line; freshness; `StateBadge` with the exact §12.4 texts:
  - `חסום: המכסה האישית נוצלה`
  - `חסום: תקציב הקבוצה נוצל`
  - `חסום: תקציב המחלקה נוצל`
- `MyWallet` sections, in the §12.5.1 order: wallet card with binding constraint (`מוגבל על ידי: ...`) → per-model table → active project shares (empty state in Phase 1) → limits (concurrency and rpm, with a one-line explanation that a 429 means too many parallel requests and clients retry) → group context (group percent, warning near the limit) → usage chart (`/me/usage`, daily, stacked by model, cap line) → "who can change what" with owner names (`OwnersBox`).
- No actions: notification preferences are Phase 2.
- Every card has empty, loading and error states.

**Tests:**
- vitest: `WalletCard.test.tsx` renders each §12.4 state with text, not color only.
- Playwright `wallet.spec.ts`:
  - `U4 group exhausted shows "חסום: תקציב הקבוצה נוצל" and names the group head`: `fake /_test/spend` pushes A1 team spend to its total while dani's personal spend stays low → wait for the snapshot → log in as dani.
  - `wallet shows limits and owners`.

**test.sh after:** unchanged (new specs run).

---

### Step 12: "הקבוצה" (Group): Overview, Members, History
**Spec:** §12.5.2 tabs 1, 2 and 6, §12.3 (allocation table, change summary, history timeline), §12.6 F2 and F6, §12.4, §12.1 (no auto-save), U1, U2, U3.

**Files:** `frontend/src/components/{AllocationTable, ChangeSummaryDialog, HistoryTimeline, MemberSidePanel, MemberDefaultsDialog}.tsx`, `frontend/src/pages/group/{GroupPage, Overview, Members, History}.tsx`, `frontend/src/components/AllocationTable.test.tsx`, `frontend/e2e/group.spec.ts`.

**Build:**
- `AllocationTable` is generic and reused in Step 13:
  - Rows have editable numbers or are read-only with an owner label.
  - A sticky footer shows total, allocated and remaining-to-allocate, recomputed as the user types. It turns red with Save disabled when the remainder is negative (client preview only; the server stays authoritative).
  - Row-level errors come from `details.rows`.
- `ChangeSummaryDialog`: before and after per row, the effect on the parent, a required reason. On confirm it sends one write; a 422 shows the Hebrew message with the server's numbers.
- Overview (§12.5.2 tab 1): group wallet card, pots per model (used vs Σ member caps), grants summary (empty), top consumers, members near or at the limit, pending sync count.
- Members (tab 2, §12.8 wireframe 2):
  - Columns: name, base cap, shares, total, used, %, model caps, concurrency, rpm, status badge, sync badge.
  - Base caps are edited inline via `PUT /groups/{id}/members/caps`.
  - **Own row and other non-`editable` rows are read-only**, labelled with the owner role and name (DR9/F6).
  - "חלק שווה" (distribute evenly): `(base × factor − Σ read-only rows) / editable rows`, floored; the remainder stays unallocated.
  - "העתק מתקופה קודמת" (copy previous period): fills from `GET /groups/{id}/members?period=<prev>`.
  - Both helpers only fill the table. Saving still goes through the change summary.
  - Side panel: model caps and limits, with ranges from `/policies`.
  - Dialog: defaults for new members (`member-defaults`).
- History (tab 6): `HistoryTimeline` over `GET /audit?entity=group&id=`, newest first, with actor, role, reason, before → after, and LiteLLM call status.
- The UI shows only the actions allowed by the server's `actions` flags and the selected scope.

**Tests:**
- vitest: `AllocationTable.test.tsx` (footer math, red and disabled state, distribute evenly).
- Playwright `group.spec.ts`:
  - `U1 caps above base: footer red, Save disabled`.
  - `U1 race returns PARENT_OVERALLOCATED with numbers`: dana loads Members; `admin@` raises another member's cap via `page.request` in a second context; dana's save shows `חילקת ... מתוך ...`.
  - `U2 own row read-only naming the department representative`.
  - `U2 direct API self-target returns 422 SELF_ALLOCATION_FORBIDDEN`. The grant-share half of U2 lands in Phase 2.
  - `U3 save during LiteLLM outage shows pending + banner, then applied after recovery`.
  - `history shows the change with reason`.

**test.sh after:** unchanged.

---

### Step 13: "המחלקה" (Department): Overview and Split; viewer, multi-role and RTL acceptance; Phase 1 wrap-up
**Spec:** §12.5.4 tabs 1 and 2, §12.8 wireframe 3, §12.6 F1, §12.5.6 (viewer), §12.2, U6, U7, U8, §16 Phase 1 exit.

**Files:** `frontend/src/pages/department/{DepartmentPage, Overview, Split}.tsx`, `frontend/src/components/SplitChart.tsx`, `frontend/e2e/{department.spec.ts, viewer.spec.ts, roles.spec.ts, rtl.spec.ts}`, `PROGRESS.md`.

**Build:**
- Overview (tab 1): department wallet card; split chart (groups, PMO pool, reserve, unallocated); groups table (base, grants, total, used, %, state); each group drills down to the Group page in **read mode**.
- Split (tab 2, F1):
  - `AllocationTable` rows: every group (the staff group as a regular row), "גוף פרויקטים" (PMO pool) and "רזרבה" (reserve).
  - Starts from the current values, which are last period's values at the start of a period.
  - Remaining-to-allocate footer → change summary showing the effect on each group → `PUT /departments/{id}/split` → sync badges per row.
  - Budget editing (`PUT /departments/{id}/budget`) appears inline here only for admins. The admin screens are Phase 3.
- Viewer (§12.5.6): the same screens without any action buttons. The Members tab is hidden (D11).
- Final pass:
  - Check the acceptance table below: each test exists and passes.
  - Update PROGRESS.md: Phase 1 done, the open questions below, and next = Phase 2.

**Tests (Playwright):**
- `department.spec.ts`:
  - `F1 split save pending then applied`
  - `split over budget footer red and disabled`
  - `drill-down group is read-only for rep`
- `viewer.spec.ts::U7 viewer sees no action buttons and write endpoints return 403`: covers split, model-pots, members/caps and budget via `page.request`.
- `roles.spec.ts::U6 permissions follow selected scope`: noa can edit A2 members in the group scope; in the D1 viewer scope, Split is read-only and `PUT split` returns 403.
- `rtl.spec.ts::U8 tables mirror and numbers/model names stay LTR`:
  - The first column's x position is greater than the last column's.
  - `<bdi dir=ltr>` contains `1,000,000` and `model-x` inside Hebrew text.
  - `dir=rtl` is inherited.

**test.sh after:** unchanged. Final check: the full run takes under 300 s.

---

## 2b. Steps (Phase 1b: onboarding API + manual member actions)

These rules apply to every Phase 1b step: the §1 conventions, the write contract, the error envelope (new codes per SPEC §11.2 v0.3), and `./scripts/test.sh` as the only gate.
- Do not change the e2e ports (test.sh uses 4120–4122).
- `scripts/e2e_stack.py` must keep working with its default ports for the live preview.
- Keep the full run under about 4 minutes. The budget added by Phase 1b is roughly 40 s of pytest plus 9 Playwright tests.

### Step 1b.1: Schema, fake LiteLLM lifecycle endpoints, client methods, chained outbox, repo hygiene
**Spec:** §8.2 (v0.3), §9.3 (order, chaining), §10 (v0.3), §3.5 V6/V7, NFR3, NFR10.

**Files:**
- `alembic/versions/0002_lifecycle.py`, `app/models.py`.
- `app/db.py`.
- `app/litellm_client/{models,client}.py`.
- `fake_litellm/{db,main,enforcement,controls}.py`.
- `app/outbox/{enqueue,worker,desired}.py`, `app/writes.py`.
- The Step 1 schema test: update its expected tables and columns to §8.2 v0.3. This is a spec change, not a weakening.
- `tests/test_repo_hygiene.py`, `.gitignore` (`.public-denylist`), `PLAN.md` §0/§1, `CLAUDE.md`, `PROGRESS.md` (copy the new questions).

**Build:**
- **Migration 0002:**
  - Employee status CHECK becomes `active|blocked|left|pending_placement`.
  - Add `employee.pending_department_id bigint NULL FK department`, `pending_reason text NULL CHECK in (no_budget_group, multiple_budget_groups, not_in_directory)`, `pending_since timestamptz NULL`.
  - Make `employee.group_id` nullable if it is not already.
  - Add `litellm_write.depends_on bigint NULL FK litellm_write(id)`.
  - Drop `sync_run` and `sync_action`, and remove their ORM classes.
- **`app/db.py`:** `LockKind.EMPLOYEE = 4` and `def employee_lock_id(upn: str) -> int` (crc32 & 0x7fffffff).
- **Client:**
  - New typed models `KeyDelete{keys: list[str]}`, `UserDelete{user_ids: list[str]}`, `JwtKeyMappingUpdate{jwt_issuer, jwt_claim_name, jwt_claim_value, is_active: bool}`, `JwtKeyMappingDelete{jwt_issuer, jwt_claim_name, jwt_claim_value}`.
  - New methods `key_delete`, `user_delete`, `jwt_mapping_update`, `jwt_mapping_delete`.
  - `team_member_delete`, `key_block`, `key_unblock` and `key_update` already exist.
- **Fake LiteLLM:**
  - `LiteLLM_JWTKeyMapping.is_active` boolean, default true.
  - `POST /key/delete` removes the tokens and the mappings that point at them.
  - `POST /user/delete`.
  - `POST /jwt/key/mapping/update` and `/delete`.
  - `/team/member_delete` returns 404 for a non-member.
  - Enforcement treats an inactive mapping like an unmapped upn (401/403, as for A10).
- **Fake test controls:**
  - `POST /_test/fail {endpoint, status: 500|400, times}` fails the next N calls to an endpoint.
  - `GET /_test/invariants` returns `[{token, user_id, team_id}]` for keys whose team has no membership for the key's user.
- **Outbox:**
  - `enqueue(..., *, payload: dict | None = None, depends_on: int | None = None) -> int`.
  - `WriteContext.enqueue(entity, entity_id, endpoint, *, payload=None, chain=False) -> int`. With `chain=True` the row depends on the previous row this context enqueued.
- **Worker:**
  - Skip a row while its `depends_on` row is not `applied` (it stays pending; add `DrainResult.held`).
  - `_FROZEN = {"/team/member_delete", "/key/delete", "/user/delete", "/jwt/key/mapping/delete"}` apply the stored payload instead of rebuilding it.
  - `_GONE_OK`: a 404 on the delete endpoints counts as applied.
- **Builders:** `/key/block` and `/key/unblock` → `KeyBlock(key=e.key_token_hash)`; `/jwt/key/mapping/update` → `is_active = e.status == "active"`.
- **`tests/test_repo_hygiene.py`:**
  - Scans `git ls-files` for private-key headers, `sk-` tokens, JWT-shaped strings, URLs and hosts outside an allow-list, and DN components other than `DC=corp,DC=example`. Lock files are allowed their registry hosts.
  - The allow-list is `*.example`, `*.test`, `localhost`, `127.0.0.1`, `0.0.0.0`, and the registry and docs hosts.
  - It also checks the optional `.public-denylist`.

**Tests:**
- `tests/litellm/test_fake_lifecycle.py`:
  - `test_key_delete_removes_key_and_mapping`
  - `test_user_delete`
  - `test_member_delete_non_member_404`
  - `test_inactive_mapping_rejects_jwt`
  - `test_fail_injection_then_ok`
  - `test_invariants_flags_key_without_membership`
- `tests/litellm/test_client_lifecycle.py`: `test_typed_bodies_for_new_endpoints`.
- `tests/test_outbox_chain.py`:
  - `test_dependent_held_while_predecessor_pending`
  - `test_failed_predecessor_holds_chain_until_retry`
  - `test_frozen_payload_not_rebuilt`
  - `test_delete_404_counts_as_applied`
  - `test_unchained_rows_keep_phase1_behavior`
- `tests/test_repo_hygiene.py::test_tracked_files_have_no_secrets_or_internal_names`.

**test.sh after:** unchanged. The new pytest files are collected automatically; all Phase 1 tests stay green.

### Step 1b.2: Kick, restore, move, delete and their permissions (backend)
**Spec:** §6 (v0.3), §9.3, §9.4, §11.1 member lifecycle, §11.2, §12.2, A9 (without shares), A12, A14.

**Files:**
- `app/services/lifecycle.py` (new).
- `app/permissions.py`.
- `app/schemas/members.py` (new).
- `app/routers/members.py` (new; register it in `app/main.py`).
- `app/services/views.py` (member rows gain `actions`).
- `app/seed.py` (import `JOINER` from lifecycle).
- `app/metrics.py` (`wm_lifecycle_actions_total{action}`, `wm_outbox_held`).

**Build:**
- **`app/permissions.py`:**
  - Group-scoped checks: `can_kick(session, p, group_id)` (admin, rep, head), `can_move(...)` (admin, rep) and `can_delete_member(...)` (admin). Each returns the role and uses the existing `_pick`/404-vs-403 rules.
- **`app/services/lifecycle.py`:**
  - `JOINER` (moved from seed); `async def provision(ctx, e: Employee) -> None` enqueues JOINER chained.
  - `async def close_member_shares(ctx, upn, reason: Literal["move", "manual"]) -> None`: a Phase 2 hook. It is a no-op now, with a docstring pointing at FR-P6.
  - `def check_target(role, actor_upn, upn, tier)`:
    - self → `SELF_ALLOCATION_FORBIDDEN` with `details.action`;
    - head with a tier other than member → `SCOPE_FORBIDDEN`;
    - rep with a rep or admin tier → `SCOPE_FORBIDDEN`.
    - Reuse `target_tier`.
  - `async def guard_idle(db, upn)` raises 409 `LIFECYCLE_IN_PROGRESS` if the employee has pending or failed rows.
  - `kick`, `restore`, `move(..., to_group_id)` and `delete_member(..., confirm_upn)` all take `(ctx, directory, group_id, upn)` and return `list[dict]`. Each:
    - takes `lock(EMPLOYEE)`;
    - checks the state, raising 409 `MEMBER_STATE_CONFLICT`;
    - writes audit plus the chained outbox rows in §9.3 order;
    - for move, enqueues `member_delete` with a frozen `{team_id: old, user_id}`;
    - for delete, enqueues a frozen mapping delete → key delete → member delete → user delete.
- **Move scope:** the target must be an active group. A rep's target must be in the rep's department, else `SCOPE_FORBIDDEN`. The base cap becomes the target's `default_member_cap` and model caps are removed.
- **Router:** `POST /groups/{id}/members/{upn}/{kick|restore|move|delete}` and `GET /groups/{id}/members/{upn}/move-targets`.
- **`/groups/{id}/members` rows:** add `actions: {kick, restore, move, delete}`, computed from the same checks so the UI shows only allowed buttons.

**Tests:**
- `tests/api/test_member_kick.py`:
  - `test_kick_blocks_key_and_mapping_inactive_order`
  - `test_restore_reverses`
  - `test_kick_twice_409_state_conflict`
  - `test_replay_same_idempotency_key_200_no_new_rows`
  - `test_kick_during_outage_pending_then_applied`
- `tests/api/test_member_move.py`:
  - `test_move_order_add_update_delete`
  - `test_move_sets_new_default_cap_clears_model_caps`
  - `test_move_while_rows_pending_409`
  - `test_move_same_group_409`
  - `test_move_calls_close_member_shares_hook`
- `tests/api/test_member_delete.py`:
  - `test_delete_order_mapping_key_member_user`
  - `test_confirm_mismatch_422`
  - `test_deleted_hidden_from_members_and_sums`
- `tests/api/test_lifecycle_permissions.py::test_matrix`, parametrized. Actions kick/restore/move/delete × actors admin, rep D1, head A1 on A1, head A1 on A2, head on co-head, PMO, viewer, plain employee, self. It expects 200, 403 `FORBIDDEN`, 404, 422 `SCOPE_FORBIDDEN` or 422 `SELF_ALLOCATION_FORBIDDEN` per SPEC §9.4. It also covers a rep moving into a D2 group (422 `SCOPE_FORBIDDEN`) and an admin moving across departments (200).
- `tests/e2e_backend/test_move_invariant.py`:
  - `test_a12_failure_before_each_substep_invariant_holds_then_retry_completes`: for each of the three endpoints, inject a failure, drain, assert `/_test/invariants` is empty, retry, drain, assert the final state.
  - `test_a9_move_requests_succeed_throughout`
- `tests/e2e_backend/test_kick_wm.py::test_a14_kick_rejects_jwt_restore_accepts`.

**test.sh after:** unchanged.

### Step 1b.3: Onboarding API, pending placement, place (backend)
**Spec:** §9.1, §9.2, §9.5, §11.1 onboarding and placements, §5.4, NFR5, NFR10, A15, D14–D20, D24.

**Files:**
- `app/auth/portal_jwt.py` (new).
- `app/services/onboarding.py` (new).
- `app/routers/onboarding.py` and `app/routers/placements.py` (new).
- `app/auth/csrf.py` (exempt `/onboarding`).
- `app/config.py`, `.env.example` (placeholders).
- `app/directory.py` (`class DirectoryUnavailable(Exception)`).
- `app/permissions.py` (`can_place(session, p, department_id: int | None)`, `can_view_pending(...)`: admin, plus rep when the department is known).
- `seed/dev.yaml`, `app/seed.py`, `scripts/e2e_stack.py`.
- `scripts/dev_onboard.py` (DEV_AUTH only; prints the onboarding response for a upn).
- `tests/helpers/jwt.py`.

**Build:**
- **Settings:** `adfs_jwks_url`, `adfs_issuer`, `onboarding_audience`, `onboarding_client_ids: list[str]`, `onboarding_user_audiences: list[str]`, `dev_jwt_secret`, `litellm_public_url` (default `https://litellm.corp.example`; e2e/dev point it at the fake). Startup fails if `dev_jwt_secret` is set without `dev_auth`.
- **`app/auth/portal_jwt.py`:**
  - `async def verify_portal(token) -> str` returns the client id.
  - `async def verify_user(token) -> str` returns the upn.
  - RS256 via JWKS (cached; reuse the OIDC metadata fetch in `app/auth/oidc.py` and joserfc). HS256 only when `dev_auth`. A strict alg allow-list.
  - `PortalAuth` dependency that never logs tokens.
- **`app/services/onboarding.py`:**
  - `def find_placement(directory, upn, groups, departments) -> Placement(group_id, department_id, reason)`. It uses `display_name()` for existence and `groups_of()`, and raises `DirectoryUnavailable` → 503.
  - `async def onboard(ctx, directory, upn, client_id) -> (dict, created: bool)`:
    - takes `lock(EMPLOYEE)`;
    - an existing employee is returned unchanged;
    - otherwise it inserts the employee, active with the default cap and `provision()`, or `pending_placement` with the `pending_*` fields.
  - `async def onboarding_status(db, settings, upn) -> dict` builds the §9.2 response. `install` is included only when ready, and never a secret.
  - `place(ctx, directory, upn, group_id)` and `pending_list(db, department_id | None | "all")`.
- **Routers:**
  - `POST /onboarding` requires `Idempotency-Key`, else 422. The reason is set by the server (`first-login onboarding via <client_id>`), and the actor role is `onboarding_portal`.
  - `GET /onboarding`.
  - `GET /departments/{id}/pending-placements`, `GET /pending-placements?department=`, `POST /pending-placements/{upn}/place`.
- **Seed (`seed/dev.yaml`), all `corp.example`:**
  - Directory-only users (`provision: false`):
    - `newbie@corp.example` (grp-b1) → ready;
    - `drifter@corp.example` (dept-d1 only) → pending `no_budget_group` D1;
    - `twin@corp.example` (grp-a1 + grp-b1) → pending `multiple_budget_groups` D1.
  - Seeded pending users for the UI:
    - `pazit@corp.example` (`pending: true`, dept-d1);
    - `lost@corp.example` (`pending: true`, no department).
  - Phase 1 counts must not change. Pending users have no group; if a Phase 1 test counts employees, change the fixtures only, never the assertions.
- **`scripts/e2e_stack.py`:** generates `DEV_JWT_SECRET` and the portal settings per run. Default ports are unchanged.

**Tests:**
- `tests/api/test_onboarding.py`:
  - `test_new_user_provisioning_then_ready_after_drain`
  - `test_joiner_order_user_member_key_mapping`
  - `test_same_idempotency_key_replay_no_new_rows`
  - `test_existing_user_other_key_no_changes`
  - `test_concurrent_first_calls_one_employee`
  - `test_no_group_pending_no_litellm_calls`
  - `test_multiple_groups_pending`
  - `test_not_in_directory_pending_unknown_department`
  - `test_kicked_returns_blocked`
  - `test_deleted_returns_blocked`
  - `test_install_only_when_ready_and_has_no_secret`
  - `test_secret_never_returned_stored_or_logged`: caplog, response bodies, and a WM DB scan for the fake's plaintext.
  - `test_directory_unavailable_503_writes_nothing`
  - `test_get_onboarding_never_writes`
  - `test_get_unknown_upn_404`
- `tests/auth/test_portal_jwt.py`:
  - `test_missing_portal_token_401`
  - `test_missing_user_assertion_401`
  - `test_client_not_allowlisted_403`
  - `test_portal_token_with_upn_rejected`
  - `test_user_wrong_audience_401`
  - `test_expired_401`
  - `test_hs256_rejected_without_dev_auth`
  - `test_alg_none_rejected`
  - `test_session_cookie_ignored`
- `tests/api/test_placement.py`:
  - `test_rep_places_pending_of_own_department_then_ready`
  - `test_rep_place_into_other_department_scope_forbidden`
  - `test_rep_cannot_see_unknown_department_404`
  - `test_admin_places_unknown_department`
  - `test_head_pmo_viewer_place_403_or_404`
  - `test_place_non_pending_409`
  - `test_place_self_forbidden`
  - `test_pending_list_scoped_to_department`
  - `test_pending_list_viewer_403`
- `tests/e2e_backend/test_onboarding_wm.py::test_a15_onboarded_user_jwt_request_succeeds`.

**test.sh after:** unchanged.

### Step 1b.4: UI: member actions in Group Members and the side panel (small UI step)
**Spec:** §12.5.2 (v0.3), §12.4, §12.6 F5/F9/F10, §12.7, U9–U11, U7.

**Files:**
- `frontend/src/components/MemberActionDialog.tsx` (new; reason field, optional group select, optional typed-upn confirm).
- `frontend/src/components/MemberSidePanel.tsx` ("פעולות" section).
- `frontend/src/pages/group/Members.tsx` (muted kicked rows, "מושעה" badge).
- `frontend/src/components/StateBadge.tsx`.
- `frontend/src/api/{queries,types}.ts` (`useMemberAction(groupId, period)`, `useMoveTargets(groupId, upn)`, `Member.actions`).
- `frontend/src/i18n/{he,errors}.ts` (`he.memberActions`, the new codes).
- `frontend/e2e/fixtures.ts` (`fakeFail(endpoint, times)` through `fake('/_test/fail', ...)`).
- `frontend/e2e/member-actions.spec.ts`, `frontend/src/components/MemberActionDialog.test.tsx`.

**Build:**
- Buttons appear only from server `actions`.
- Each action writes through the existing write helper (reason header, Idempotency-Key), invalidates members and summary, and shows the `SyncBadge` per row.
- Delete keeps the confirm button disabled until the typed upn matches exactly.
- Use existing components and Mantine RTL. Do not add a new design.

**Tests:**
- Vitest `MemberActionDialog.test.tsx`: `reason required`, `delete confirm disabled until exact upn`, `move lists targets`.
- Playwright `member-actions.spec.ts`:
  - `U9 head kicks and restores a member`
  - `U9 head sees no move or delete and no actions on own row`
  - `U10 rep moves a member within the department`
  - `U10 admin moves a member across departments`
  - `U11 admin delete requires typed upn`
  - `U7 viewer and PMO get 403 on lifecycle endpoints` (via `page.request`)

**test.sh after:** unchanged. Check the run time is still under about 4 min.

### Step 1b.5: UI: pending placement lists; Phase 1b wrap-up (small UI step)
**Spec:** §12.5.4 tab 5, §12.5.5 pending placement, §12.6 F7, U12, §16 Phase 1b exit.

**Files:**
- `frontend/src/components/PendingPlacementsTable.tsx` (new; reuses `MemberActionDialog` with a group select).
- `frontend/src/pages/department/Pending.tsx` (route `/department/:id/pending`, tab "ממתינים לשיבוץ", shown when `actions.place`).
- `frontend/src/pages/department/DepartmentPage.tsx`.
- `frontend/src/pages/Platform.tsx` (replaces the placeholder with a pending-placement section only).
- `frontend/src/routes.tsx`, `frontend/src/api/queries.ts` (`usePendingPlacements(deptId | 'all')`, `usePlace`), `frontend/src/i18n/he.ts`.
- `app/services/views.py` (department summary `actions.place`).
- `frontend/e2e/pending.spec.ts`, `PROGRESS.md`.

**Tests (Playwright `pending.spec.ts`):**
- `U12 rep places a pending user of the department`
- `U12 admin places an unknown-department user from the platform view`
- `U12 group head has no pending tab and the API returns 404`

**Wrap-up:**
- Every test in the table below exists and passes.
- `tests/test_repo_hygiene.py` passes.
- PROGRESS.md: Phase 1b done, questions D14–D25, next = Phase 2.

**test.sh after:** unchanged. Run it fully and record the time in PROGRESS.md.

### Phase 1b acceptance mapping

| Criterion | Step | Test(s) |
|---|---|---|
| §8.2 v0.3 schema (no sync tables, pending fields, `depends_on`) | 1b.1 | Step 1 schema test (updated), `test_outbox_chain.py::*` |
| §10 lifecycle endpoints in the fake and client (V6/V7 locally) | 1b.1 | `test_fake_lifecycle.py::*`, `test_client_lifecycle.py` |
| Chained, frozen, gone-ok outbox | 1b.1 | `test_outbox_chain.py::test_failed_predecessor_holds_chain_until_retry`, `::test_frozen_payload_not_rebuilt`, `::test_delete_404_counts_as_applied` |
| Kick / restore (§9.3, §9.4) | 1b.2, 1b.4 | `test_member_kick.py::*`; `member-actions.spec.ts` U9 |
| Move order and invariant (A9 without shares, A12) | 1b.2, 1b.4 | `test_member_move.py::test_move_order_add_update_delete`, `test_move_invariant.py::test_a12_…`, `::test_a9_…`; U10 |
| Delete with double confirm | 1b.2, 1b.4 | `test_member_delete.py::*`; U11 |
| Permissions, all actions × roles (allowed and 403/404/422) | 1b.2, 1b.3 | `test_lifecycle_permissions.py::test_matrix`, `test_placement.py::*` |
| Self-targeting forbidden | 1b.2, 1b.3 | `test_matrix[self-*]`, `test_placement.py::test_place_self_forbidden` |
| A14 kick blocks the JWT | 1b.2 | `test_kick_wm.py::test_a14_…` |
| Onboarding idempotency (per upn and Idempotency-Key) | 1b.3 | `test_onboarding.py::test_same_idempotency_key_replay_no_new_rows`, `::test_existing_user_other_key_no_changes`, `::test_concurrent_first_calls_one_employee` |
| Pending placement | 1b.3, 1b.5 | `test_onboarding.py::test_no_group_pending_no_litellm_calls`, `::test_multiple_groups_pending`, `test_placement.py::*`; U12 |
| Secret never shown (D15, NFR5) | 1b.3 | `test_onboarding.py::test_secret_never_returned_stored_or_logged`, `::test_install_only_when_ready_and_has_no_secret` |
| Portal authentication (D14) | 1b.3 | `test_portal_jwt.py::*` |
| A15 onboarded user can call the model | 1b.3 | `test_onboarding_wm.py::test_a15_…` |
| U7 viewer has no lifecycle actions | 1b.4 | `member-actions.spec.ts` "U7 viewer and PMO get 403…" |
| NFR10 public-repo hygiene | 1b.1 | `test_repo_hygiene.py::test_tracked_files_have_no_secrets_or_internal_names` |

---

## 2c. Steps (Phase 2: grants, scheduler, reconciliation, notifications)

Every Phase 2 step follows these rules: the §1 conventions, the write contract, the §11.2 error envelope, and `./scripts/test.sh` as the only gate.
- **Ports.** Do not change the e2e ports (test.sh uses 4120–4122). `scripts/e2e_stack.py` must keep working with its default ports for the live preview.
- **Time budget.** Phase 2 may add at most about 60 s of pytest and 8 Playwright tests. Keep the whole run under about 5 minutes.
- **Clock.** Nothing in `app/` calls `datetime.now()` directly. The API takes the time from `app.state.clock` and the scheduler takes it from its `clock` argument. Tests use `FakeClock` and move it forward instead of sleeping.
- **Writes and actors.** Every LiteLLM write goes through the existing outbox. The scheduler's writes use `WriteContext` like the API does:
  - grant jobs write as actor `system:scheduler` with role `scheduler`;
  - reconciliation writes as actor `system:reconcile` with role `reconciliation`.
- **Idempotent scheduler writes.** The request id of a scheduler write is `uuid5(NS, "<job>:<entity>:<id>:<event>")`. A rerun therefore finds the audit row that already exists and writes nothing (the same replay rule as §1).
- **No AD sync anywhere** (owner decision 20/10/2026). The directory is used only for display names and role holders.
- **Public repo.** Placeholders only (NFR10). `test_repo_hygiene.py` must keep passing.
- **Order.** Backend steps 2.1–2.5 come first. The UI steps 2.6–2.10 are small: one screen or one tab each.

### Step 2.1: Grants backend, PMO side (create, change, close, list, pool, projects)
**Spec:** §7.3 FR-P1, P2, P6 (grant level), P7, P8; §6 PMO row; §8.1 Team rows; §11.1 PMO and grants; §11.2; §12.3 PMO pool formula; A11.

**Files:**
- `alembic/versions/0003_grants_notifications.py` (new).
- `app/clock.py` (new).
- `app/main.py` (`create_app(..., clock: Clock | None = None)` stores `app.state.clock`).
- `app/services/grants.py`, `app/schemas/grants.py`, `app/routers/grants.py` (all new; register the router in `app/main.py`).
- `app/permissions.py`.
- `app/outbox/desired.py`.
- `app/services/views.py` and `app/services/wallet.py` (use the effective grant amount).
- `frontend/vite.config.ts` (proxy `/grants` and `/projects`).

**Build:**
- **Migration 0003.** No new tables or columns, because §8.2 already has them. It adds only indexes:
  - `ix_grant_status_starts (status, starts_at)` and `ix_grant_status_expires (status, expires_at)`;
  - `ix_grant_share_status_expires (status, expires_at)` and `ix_grant_share_upn_status (upn, status)`;
  - a partial unique index `uq_grant_share_active (grant_id, upn) WHERE status='active'`;
  - `uq_notification_dedupe` on `(upn, kind, (payload->>'key'))`, used by Step 2.4.

  `tests/test_schema.py` stays unchanged: it checks tables and columns, and none change.
- **`app/clock.py`:**
  ```python
  class Clock(Protocol):
      def now(self) -> datetime: ...            # aware UTC
      async def sleep(self, seconds: float) -> None: ...
  class SystemClock: ...                        # datetime.now(UTC), asyncio.sleep
  class FakeClock:
      def __init__(self, start: datetime): ...
      def advance(self, **delta) -> None: ...
      def set(self, at: datetime) -> None: ...
      async def sleep(self, seconds) -> None: ...   # advances, then await asyncio.sleep(0)
  ```
- **Effective amount and pool formulas:**
  - The effective amount of a grant is `amount − returned`.
  - `pool_available(db, dept_id, now) = pmo_pool − Σ effective(pending + active grants) − Σ used(grants closed in the current period)`. This is §12.3. Pending grants reserve pool money.
  - `shared(G) = Σ amount of all shares of G`, active and closed. Money returned from a closed share goes back to the pool, not to the grant.
- **`app/permissions.py`:**
  - `can_manage_grants(session, p, department_id)` allows admin and PMO.
  - `can_view_grants(session, p, department_id, group_id | None)` allows admin, plus PMO, rep or viewer on the department, plus head or viewer on the group.
  - Both reuse `_pick` and the existing 404-vs-403 rules.
- **`app/services/grants.py`** (all functions take `lock(PMO, department_id)` before reading any sums):
  - `async def create_grant(ctx, principal, clock, body) -> list[dict]`:
    - group 404 when it does not exist;
    - a PMO whose department differs from the group's gets 422 `SCOPE_FORBIDDEN` (FR-P8: the department is always the group's own);
    - a non-PMO, non-admin actor gets 403;
    - `expires_at <= max(starts_at, now)`, or a non-active group, gives 422 `INVALID_REQUEST`;
    - `amount > pool_available` gives 422 `PMO_POOL_INSUFFICIENT` with `{requested: amount, available, parent_total: pmo_pool}`.
    - The status is `active` if `starts_at <= now`, otherwise `pending`. When active, it enqueues `/team/update` for the group.
    - It writes a `grant` audit row.
  - `async def patch_grant(ctx, principal, clock, grant_id, amount=None, expires_at=None)`:
    - an increase is limited by the pool and gives `PMO_POOL_INSUFFICIENT` with `requested` = the increase;
    - `amount < shared(G)` gives `GRANT_BELOW_SHARED` with `{requested: amount, available: shared, parent_total: amount}`;
    - an `expires_at` before the latest active share's expiry gives `SHARE_EXPIRY_AFTER_GRANT`;
    - a closed grant gives 422 `INVALID_REQUEST` with `details.status`;
    - it enqueues `/team/update` when the grant is active.
  - `async def close_grant(ctx, principal, clock, grant_id, reason)` and the shared internal `async def _close_grant(ctx, grant, now, share_reason)`:
    - set the status to `closed` and `closed_at = now`;
    - set `used` = Σ of the shares' `used`, and `returned = amount − used`;
    - enqueue `/team/update`.
    - In this step the grant has no shares yet; Step 2.2 makes it close the active shares first.
  - `async def list_grants(db, principal, filters) -> list[dict]`. Each row: `{id, department_id, group_id, group_name, project_ref, purpose, amount, model, starts_at, expires_at, status, shared, unshared, used, returned, days_left, sync_status, actions:{edit, close, share}}`.
  - `pmo_pool(db, principal, dept_id, period)` returns `{department_id, period, pool, active_grants, used, returned, available, actions:{create_grant}}`. It reuses `wallet.pmo_wallet`.
  - `project_summary(db, principal, project_ref)` returns `{project_ref, totals:{amount, shared, used, returned}, grants:[row]}`. It includes only the grants the actor can see, and returns 404 if none are visible.
- **`app/outbox/desired.py`:**
  - Team `max_budget` = base + Σ effective amount of **all** active grants. This fixes the current filter `model IS NULL`: FR-P2 adds A to `max_budget` even when X is set.
  - Team `model_max_budget[X]` = pot + Σ effective amount of active grants for X.
- **Endpoints.** The reason travels in `X-Change-Reason` as in §1. The `{reason}` body field on close is optional and only echoed back.

  | Endpoint | Body | Who | Errors |
  |---|---|---|---|
  | `POST /grants` | `{group_id, amount, model?, starts_at, expires_at, project_ref, purpose}` | admin, PMO | 403, 404, 422 `SCOPE_FORBIDDEN` / `PMO_POOL_INSUFFICIENT` / `INVALID_REQUEST` |
  | `PATCH /grants/{id}` | `{amount?, expires_at?}` | admin, PMO | 403, 404, 422 `PMO_POOL_INSUFFICIENT` / `GRANT_BELOW_SHARED` / `SHARE_EXPIRY_AFTER_GRANT` / `INVALID_REQUEST` |
  | `POST /grants/{id}/close` | `{reason}` | admin, PMO | 403, 404, 422 `INVALID_REQUEST` (already closed) |
  | `GET /grants?department=&group=&status=&project_ref=` | | visible scopes | 403 / 404 per filter scope |
  | `GET /departments/{id}/pmo-pool?period=` | | admin, PMO, rep, viewer | 403, 404, 422 (future period) |
  | `GET /projects/{project_ref}/summary` | | visible grants only | 404 |

  Write responses list entities `grant` (values `{status, amount, expires_at, returned}`) and `group` (sync of `/team/update`).

**Tests:**
- `tests/api/test_grants.py`:
  - `test_create_active_grant_raises_team_max_budget_after_drain`
  - `test_model_grant_raises_max_budget_and_pot`
  - `test_future_grant_pending_reserves_pool_no_litellm_call`
  - `test_amount_above_pool_422_with_numbers`
  - `test_pmo_other_department_group_scope_forbidden`
  - `test_head_viewer_employee_403`
  - `test_expiry_before_start_invalid`
  - `test_patch_increase_checks_pool`
  - `test_patch_decrease_below_shared_grant_below_shared` (inserts a share row directly)
  - `test_patch_expiry_before_share_expiry_422`
  - `test_close_returns_amount_to_pool_and_lowers_team`
  - `test_replay_same_idempotency_key_200`
- `tests/api/test_grants_read.py`:
  - `test_list_filters_and_visibility`
  - `test_pmo_pool_formula`
  - `test_project_summary_aggregates_visible_grants`
- `tests/api/test_grants_concurrency.py::test_a11_concurrent_grants_one_pool_insufficient`. It uses `asyncio.gather` with the `asyncio.sleep(0)` patch trick from the split lock test.
- `tests/unit/test_clock.py::test_fake_clock_advance_and_sleep`

**test.sh after:** unchanged.

### Step 2.2: Shares backend, attribution (7.3.1), FR-P4/P5 member values, share close
**Spec:** §7.3 FR-P3, P4, P5, P6 (share level); §7.3.1; §8.1 Team member rows; §8.2 grant_share; §10 membership (both temp fields together); DR9/FR-G5; U2 (API half); §12.5.1 shares.

**Files:**
- `app/services/attribution.py` (new; pure functions plus the spend loader).
- `app/services/shares.py` (new).
- `app/schemas/grants.py` and `app/routers/grants.py`.
- `app/services/grants.py` (`_close_grant` closes the active shares first).
- `app/permissions.py` (`can_share(session, p, group_id)`: admin, plus rep of the department, plus head of the group).
- `app/outbox/desired.py`.
- `app/litellm_client/models.py` (relax `TeamMemberUpdate` so that `temp_budget_increase=0` with `temp_budget_expiry=None` is valid; both keys are always sent).
- `fake_litellm/main.py` (`/team/member_update` returns 400 if only one of the two temp fields is present).
- `app/services/views.py` (`/me/wallet` share rows).

**Build:**
- **`attribution.py`:**
  ```python
  @dataclass(frozen=True)
  class ShareSpan: id: int; amount: Decimal; expires_at: datetime; used: Decimal | None = None  # closed: stored used
  def attribute(member_spend: Decimal, base_cap: Decimal, shares: Sequence[ShareSpan]) -> dict[int, Decimal]
      # sort by (expires_at, id); used(s) = clamp(spend − base − Σ used(earlier), 0, s.amount)
  def attribute_periods(spend_by_period: Mapping[str, Decimal], base_cap: Decimal,
                        shares_by_period: Mapping[str, Sequence[ShareSpan]]) -> dict[int, Decimal]
      # sum per share across periods, clamp to amount (grant spanning a period reset)
  async def member_spend_by_period(db, client, reader, upn, team_id, since, now, tz) -> dict[str, Decimal]
  ```
  `member_spend_by_period` takes its numbers from three sources:
  - **Current period:** `client.team_info(team_id).team_memberships[upn].spend`.
  - **Earlier periods since `grant.starts_at`:** `reader.spend_rows(start, end)` filtered by team and user.
  - **If LiteLLM is unreachable:** the `spend_snapshot` employee rows, with a WARNING log.
- **`app/services/shares.py`.** Every function takes `lock(GROUP, grant.group_id)` and then loads the grant `FOR UPDATE`.
  - `create_shares(ctx, principal, clock, grant_id, rows)` is a bulk, all-or-nothing write. It checks each row in this order:
    1. role (`can_share`), else 403/404;
    2. the grant is active, else 422 `INVALID_REQUEST`;
    3. per row: self gives `SELF_ALLOCATION_FORBIDDEN` with `details.owner_role`; a head targeting a co-head, or a upn outside the grant's group, gives `SCOPE_FORBIDDEN` (reuse `target_tier`);
    4. the employee is not active: 409 `MEMBER_STATE_CONFLICT`;
    5. the upn already has an active share on this grant: 422 `INVALID_REQUEST` with `details.reason = "share_exists"`;
    6. `e > E` gives `SHARE_EXPIRY_AFTER_GRANT`; `e <= now` gives `INVALID_REQUEST`;
    7. `shared(G) + Σ new > A` gives `SHARE_EXCEEDS_GRANT` with `{requested, available: A − shared, parent_total: A}`.

    Bulk errors carry `details.rows`. Each new share enqueues `/team/member_update` (entity `employee`).
  - `patch_share(ctx, principal, clock, grant_id, share_id, amount=None, expires_at=None)` applies the same checks, then enqueues `/team/member_update`.
  - `async def close_shares(ctx, shares, now, reason: Literal["expiry","move","manual"], client, reader) -> list[int]` is the one closing routine used by everything. It:
    - computes `used` with `attribute_periods` over the employee's shares in each period (active, plus closed ones with their stored `used`);
    - sets `returned = amount − used`, `status=closed` and `closed_reason`;
    - adds `grant.returned += returned`;
    - enqueues `/team/member_update` for the employee and `/team/update` for the group;
    - writes a `grant_share` audit row.
  - `close_share(...)` is the route wrapper and always uses reason `manual`.
  - `_close_grant` calls `close_shares(..., share_reason)` first. After that, `grant.used = Σ shares.used` and `grant.returned = amount − used`.
- **Desired member values (`/team/member_update`, rebuilt at apply time):**
  - `temp_budget_increase = Σ active share amounts`.
  - `temp_budget_expiry = min(active share expires_at)`.
  - With no active shares, send `0` and `null`. Both keys are always sent (FR-P4).
  - `model_max_budget[X] = personal cap + Σ active shares of grants with model X` (FR-P5).
- **`/me/wallet`.** Share rows become `{share_id, grant_id, project_ref, model, amount, used, expires_at, days_left}`. `used` is attributed from the snapshot spend.
- **Endpoints:**

  | Endpoint | Body | Who | Errors |
  |---|---|---|---|
  | `GET /grants/{id}/shares` | | admin, PMO, rep, head of the group, viewer of the scope | 403, 404 |
  | `POST /grants/{id}/shares` | `[{upn, amount, expires_at}]` | head (own group), rep (department), admin | 403, 404, 409 `MEMBER_STATE_CONFLICT`, 422 `SHARE_EXCEEDS_GRANT` / `SHARE_EXPIRY_AFTER_GRANT` / `SELF_ALLOCATION_FORBIDDEN` / `SCOPE_FORBIDDEN` / `INVALID_REQUEST` |
  | `PATCH /grants/{id}/shares/{share_id}` | `{amount?, expires_at?}` | same | same |
  | `POST /grants/{id}/shares/{share_id}/close` | `{reason}` | same | 403, 404, 422 `INVALID_REQUEST` (already closed) |

  Share rows: `{id, upn, display_name, amount, expires_at, status, used, returned, closed_reason, sync_status, actions:{edit, close}}`.

**Tests:**
- `tests/unit/test_attribution.py`:
  - `test_base_first_then_earliest_expiry`
  - `test_clamp_zero_and_amount`
  - `test_closed_share_used_counts_as_earlier`
  - `test_period_reset_sums_periods`
  - `test_tie_on_expiry_ordered_by_id`
- `tests/unit/test_desired_member.py`:
  - `test_temp_increase_sum_and_earliest_expiry`
  - `test_no_shares_sends_zero_and_null_together`
  - `test_model_share_raises_member_model_cap`
- `tests/api/test_shares.py`:
  - `test_share_writes_temp_fields_after_drain`
  - `test_sum_above_grant_share_exceeds_grant`
  - `test_expiry_after_grant_422`
  - `test_head_shares_to_self_self_allocation_forbidden` (U2 API)
  - `test_rep_shares_to_group_head_ok`
  - `test_head_to_cohead_scope_forbidden`
  - `test_upn_outside_group_scope_forbidden`
  - `test_second_active_share_same_upn_invalid`
  - `test_close_share_used_returned_pool_and_team_lowered`
  - `test_close_grant_closes_shares_then_returns_remainder`
  - `test_viewer_pmo_cannot_share_403`
- `tests/litellm/test_fake_lifecycle.py::test_member_update_temp_fields_must_come_together`
- `tests/api/test_me_wallet.py::test_share_rows_have_project_used_expiry`

**test.sh after:** unchanged.

### Step 2.3: Scheduler process, injectable clock, grant and share activation/expiry, FR-P4 recompute, share closing on move and delete
**Spec:** §5.1 Scheduler; §7.3 FR-P2, P4, P6; §7.3.1; §9.3 move and delete rows; §12.6 F3 step 4 and F5 step 2; NFR4; A1; A9 (with a share); owner requirements 1–2.

**Files:**
- `app/scheduler/__init__.py`, `app/scheduler/__main__.py` (`python -m app.scheduler`), `app/scheduler/loop.py`, `app/scheduler/jobs.py` (all new).
- `app/db.py` (`LockKind.JOB = 5`).
- `app/config.py`, `.env.example`.
- `app/services/lifecycle.py` (fill in `close_member_shares`).
- `app/metrics.py`.
- `fake_litellm/{controls,enforcement,main}.py` (fake clock).
- `scripts/e2e_stack.py` (start the scheduler).
- `tests/conftest.py` (fixtures `fake_clock` and `run_job`).

**Build:**
- **Settings:**
  - `scheduler_grants_interval_s = 60`
  - `scheduler_notify_interval_s = 900` (used by Step 2.4)
  - `reconcile_interval_min = 5` (used by Step 2.5)
  - `scheduler_lock_timeout_s = 300`
- **`app/scheduler/loop.py`:**
  ```python
  @dataclass(frozen=True)
  class Job: name: str; lock_id: int; interval: timedelta; run: Callable[[], Awaitable[object]]; run_at_start: bool
  async def run_loop(jobs: Sequence[Job], session_factory, clock: Clock, *, stop: asyncio.Event) -> None
  def build_jobs(settings, session_factory, client, reader, directory, clock) -> list[Job]
  async def main(settings: Settings | None = None) -> None   # SystemClock, SIGTERM sets stop
  ```
  - Each job's next run time is kept in memory.
  - When a job is due, the loop calls `pg_try_advisory_lock(5, job.lock_id)` on a dedicated connection. If another replica holds the lock, the job is skipped and logged. Otherwise the job runs under `asyncio.wait_for(timeout)` and the lock is released.
  - An exception is logged and counted, and the loop keeps going.
  - It sleeps with `clock.sleep(min(time to the next due job, 1 s))`.
  - Grants: lock id 1, runs at start. Notifications: lock id 2, runs at start. Reconcile: lock id 3, first run after one interval.
- **Restart safety.**
  - All state lives in the DB (statuses and timestamps), so a restart catches up on everything that is overdue.
  - The jobs select rows with `FOR UPDATE SKIP LOCKED` and re-check `status` in the `UPDATE ... WHERE status='active'`. Two replicas therefore never close the same row twice, even without the job lock.
- **Metrics:** `wm_scheduler_job_runs_total{job,outcome}` and `wm_scheduler_job_last_success_timestamp{job}`.
- **`app/scheduler/jobs.py`:**
  ```python
  @dataclass
  class GrantsResult: activated: int; shares_closed: int; grants_closed: int; members_recomputed: int
  async def run_grants(session_factory, client, clock, *, reader, settings) -> GrantsResult
  ```
  It runs these phases in order, with one transaction per grant:
  1. **Activate:** pending grants with `starts_at <= now` become `active` and enqueue `/team/update` (FR-P2).
  2. **Close expired shares:** active shares with `expires_at <= now` are closed with reason `expiry`, in expiry order (`close_shares`).
  3. **Close expired grants:** active grants with `expires_at <= now` go through `_close_grant(..., share_reason="expiry")`.
  4. **FR-P4 recompute:** every employee touched in phases 2–3 gets exactly one `/team/member_update`. Both temp fields are rebuilt at apply time from the shares that are still active.

  Each event uses the deterministic request id `uuid5(NS, "grants:<entity>:<id>:<event>")`, so a rerun is a no-op.
- **`close_member_shares(ctx, upn, reason)`.**
  - It closes the employee's active shares with reason `move` or `manual`. The returns and team lowering are those of FR-P6, computed with the spend in the **old** team while that membership still exists.
  - Its outbox rows are **not** chained before the move chain, so they never hold up the move. The old team's `/team/update` is rebuilt at apply time.
  - The signature gains `clock, client, reader` as keyword arguments. `move` and `delete_member` pass them from `request.app.state`.
- **Fake LiteLLM.**
  - `POST /_test/clock {offset_s}` and `GET /_test/clock`; `/_test/reset` clears the offset.
  - `fake_now()` replaces `datetime.now(UTC)` in `enforcement.py`, including the temp-increase expiry check (R2).
- **`scripts/e2e_stack.py`.** Also starts `python -m app.scheduler` with `SCHEDULER_GRANTS_INTERVAL_S=2`, `SCHEDULER_NOTIFY_INTERVAL_S=2` and the default reconcile interval (5 min, first run after one interval).
  - This is the same for the test.sh stack (4120–4122) and the live preview (4100/4111/4112).
  - The live preview also gets `SEED_GRANTS=1`, decided by the API port being the default 4111 (see Step 2.4 seed).
- **Unchanged:** the outbox, snapshot and overallocation loops stay in `python -m app.worker`.

**Tests:**
- `tests/scheduler/test_loop.py`:
  - `test_jobs_run_at_their_intervals_with_fake_clock`
  - `test_job_exception_logged_loop_continues`
  - `test_second_replica_skips_while_job_lock_held`
  - `test_stop_event_ends_loop`
- `tests/scheduler/test_grants_job.py`:
  - `test_pending_grant_activates_at_start_time`
  - `test_share_expiry_closes_returns_and_recomputes_next_share` (two shares, the earliest expires, the member gets the second share's amount and expiry)
  - `test_grant_expiry_closes_all_shares_and_unshared_remainder`
  - `test_run_twice_is_noop`
  - `test_two_concurrent_runs_never_double_close`
  - `test_restart_catches_up_on_overdue`
- `tests/api/test_member_move.py`:
  - `test_move_closes_active_shares_reason_move` (replaces the hook test `test_move_calls_close_member_shares_hook`, which is a spec change)
  - `test_move_share_return_does_not_hold_move_chain`
- `tests/api/test_member_delete.py::test_delete_closes_shares_reason_manual`
- `tests/contract/test_a1_temp_increase.py::test_a1_increase_expires_without_external_action[fake]`. It moves the fake clock 3 min; `[real]` waits for real and is skipped without `LITELLM_BASE_URL`.
- `tests/e2e_backend/test_a1_wm.py::test_a1_share_expiry_blocks_then_scheduler_closes_and_returns`
- `tests/e2e_backend/test_move_invariant.py::test_a9_move_with_active_share_closes_and_returns_to_pool`

**test.sh after:** test.sh itself is unchanged. The e2e stack now also runs the scheduler.

### Step 2.4: Notifications backend (FR-N1 triggers, FR-N2 mute, `/me/notifications*`)
**Spec:** §7.9 FR-N1, FR-N2; §8.2 notification and notification_pref; §11.1 Session and self; §12.5.3 (group head notified after a grant); §12.6 F3 step 1 and F5 step 4; §9.5; owner requirement 4.

**Files:**
- `app/services/notifications.py`, `app/notify_email.py`, `app/routers/notifications.py`, `app/schemas/notifications.py` (all new).
- `app/scheduler/jobs.py` (`run_notifications`).
- `app/services/{onboarding,lifecycle,grants}.py` (the event hooks).
- `app/services/owners.py` (reuse `holders()`).
- `app/config.py`, `.env.example`.
- `seed/dev.yaml` and `app/seed.py` (`grants:` section).

**Build:**
- **Kinds.** Critical kinds cannot be muted.

  | kind | trigger | recipients | critical |
  |---|---|---|---|
  | `employee_80`, `employee_100` | employee used ≥ 80% / 100% of their total (snapshot plus `employee_wallet`) | the employee | 100 only |
  | `group_80`, `group_100` | group used ≥ 80% / 100% (`group_wallet`) | group heads | 100 only |
  | `grant_expiring` | active grant with `expires_at − now ≤ 7 d` | group heads and the department's PMO | no |
  | `grant_underused` | attributed used < 20% of the amount and ≤ 7 d left | PMO and group heads | no |
  | `pending_placement` | onboarding or place leaves a user pending | the department's reps, or platform admins when the department is unknown | no |
  | `member_moved_in` | move | heads of the target group | no |
  | `outbox_failed` | a `litellm_write` row is `failed` | platform admins | yes |
  | `grant_received` | grant created (§12.5.3, F3) | group heads | no |
  | `drift_repeated` | used by Step 2.5 | platform admins | yes |

- **`async def notify(db, upns, kind, scope_kind, scope_id, payload, key) -> int`.**
  - It skips users who muted a non-critical kind.
  - It inserts with `ON CONFLICT DO NOTHING` on `uq_notification_dedupe`. Keys look like `emp:<period>:80`, `grant:<id>:expiring` or `outbox:<id>`.
  - If `notify_email_enabled` (default `False`) is set, it also calls `notify_email.send(settings, upn, subject, body)` (smtplib to `smtp_host`, placeholder `smtp.corp.example`). There is no relay in tests.
- **Where it is called.**
  - Event kinds are written inside the domain write's transaction: `onboard`/`place` for pending, `move`, and `create_grant`.
  - Threshold kinds come from `async def run_notifications(session_factory, client, clock, *, settings, directory) -> NotifyResult(created: dict[str, int])`.
- **Endpoints.** These are personal UI state, not allocations. They need CSRF but no reason header and no audit row (new question 3).

  | Endpoint | Body / response | Errors |
  |---|---|---|
  | `GET /me/notifications?unread=` | `{items:[{id, kind, critical, scope_kind, scope_id, payload, created_at, read_at}], unread_count, prefs:[{kind, critical, muted}]}` | 401 |
  | `POST /me/notifications/{id}/read` | → `{id, read_at}` | 401, 404 (not the caller's own) |
  | `PUT /me/notification-prefs` | `{"<kind>": bool}` → prefs | 401, 422 `INVALID_REQUEST` (unknown kind, or muting a critical kind) |

- **Seed.** A `grants:` section adds one active grant (project `PRJ-ATLAS`, A2, 30,000, expiring in 60 days) with one share to `avi@corp.example`.
  - It is applied only when `Settings.seed_grants` is true (default false).
  - The pytest fixtures and the test.sh e2e stack therefore keep the Phase 1 numbers. The PMO user `pmo@corp.example` already exists.

**Tests:**
- `tests/api/test_notifications.py`:
  - `test_list_mark_read_own_only_404_other`
  - `test_mute_noncritical_suppresses_new`
  - `test_mute_critical_422`
  - `test_unknown_kind_422`
- `tests/scheduler/test_notifications_job.py`:
  - `test_employee_80_and_100`
  - `test_group_80_and_100_to_heads`
  - `test_grant_expiring_7_days`
  - `test_grant_underused`
  - `test_outbox_failed_to_admins`
  - `test_run_twice_no_duplicates`
- `tests/api/test_notification_events.py`:
  - `test_pending_placement_notifies_rep_or_admins_when_unknown`
  - `test_move_notifies_new_group_head`
  - `test_grant_created_notifies_group_head`
- `tests/unit/test_notify_email.py::test_email_disabled_by_default_no_smtp`

**test.sh after:** unchanged.

### Step 2.5: Reconciliation (FR-RC1, RC2, RC3) with a drift log and repeated-drift alert
**Spec:** §7.7; §8.1 (all owned fields; roles are excluded per §6); DR5; §5.3; NFR4 and NFR7 (drift count); §12.5.5 (drift corrections log).

**Files:**
- `app/services/reconcile.py` (new; pure diff plus the job).
- `app/scheduler/jobs.py` (`run_reconcile`).
- `app/outbox/desired.py` (expose `desired_fields(session, settings, object, id) -> dict`, reusing the builders).
- `app/litellm_client/{client,models}.py` (`jwt_mapping_list()` if missing).
- `fake_litellm/main.py` (`GET /jwt/key/mapping/list` if missing; `/key/list` returns `metadata`, `blocked`, `team_id`, `max_parallel_requests`, `rpm_limit`).
- `app/metrics.py`, `app/config.py`.

**Build:**
- **Settings:** `reconcile_interval_min = 5`, `reconcile_drift_alert_count = 3`, `reconcile_drift_window_h = 24`.
- **Signatures:**
  ```python
  @dataclass(frozen=True)
  class FieldDrift: object: str; id: str; field: str; live: object; desired: object
  def diff(object: str, id: str, desired: dict, live: dict) -> list[FieldDrift]
      # Decimal tolerance 0.01; None == missing; dicts per key; datetimes to the second
  @dataclass
  class ReconcileResult: checked: int; drifts: list[FieldDrift]; corrected: int; skipped_in_flight: int; missing: int; alerts: int
  async def run_reconcile(session_factory, client, clock, *, settings) -> ReconcileResult
  ```
- **What is compared.** These are the §8.1 owned fields:

  | LiteLLM object | Live source | Fields |
  |---|---|---|
  | Organization | `organization_info` | `max_budget`, `budget_duration` |
  | Team | `team_info` | `organization_id`, `max_budget`, `model_max_budget`, `tpm_limit`, `metadata.model_tpm_limit`, `team_member_*` |
  | Membership of active and blocked employees | `team_info` | `max_budget_in_team`, `budget_duration`, `temp_budget_increase` + `temp_budget_expiry`, `model_max_budget` |
  | Personal key | `key_list(user_id)` | `team_id`, `max_parallel_requests`, `rpm_limit`, `metadata.model_tpm_limit`, `blocked` |
  | JWT mapping | `jwt_mapping_list` | `is_active` follows employee status |

  Role fields are never compared.
- **Skips:**
  - Entities with any pending or failed `litellm_write` rows, checked before the live read **and again after it** (`id > max id at start`). In-flight writes are not drift.
  - Archived groups, and employees who are `left` or `pending_placement`.
- **Correction.** Each one is a transaction with:
  - an `audit_log` row (actor `system:reconcile`, role `reconciliation`, entity `organization|team|employee|key|jwt_mapping`, `before={field: live}`, `after={field: desired}`, reason `drift correction`). This is the drift log. Admins read it through the existing `GET /audit?actor=system:reconcile`;
  - one outbox row on the normal endpoint (`/organization/update`, `/team/update`, `/team/member_update`, `/key/update`, `/key/block|unblock`, `/jwt/key/mapping/update`), rebuilt at apply time. It is retried with backoff and stays `pending` until applied (RC3).
- **Missing objects** (no membership, no key, no mapping) are logged at WARNING and counted. They are **never recreated** automatically (new question 4).
- **Repeated drift.** When `≥ reconcile_drift_alert_count` correction audit rows exist for the same entity, id and field within the window, the job:
  - logs a WARNING;
  - calls `notify(admins, "drift_repeated", key=f"drift:{object}:{id}:{field}:{date}")`;
  - increments `wm_reconcile_alerts_total`.
- **Metrics:** `wm_reconcile_drift_total{object,field}` and `wm_reconcile_last_run_timestamp`.
- **No fake control needed.** Tests create drift by calling the fake's management API directly through `litellm`, the way a LiteLLM UI edit would.

**Tests:**
- `tests/unit/test_reconcile_diff.py`:
  - `test_decimal_tolerance_and_none_missing`
  - `test_model_dict_per_key`
  - `test_role_fields_ignored`
- `tests/scheduler/test_reconcile_job.py`:
  - `test_team_max_budget_drift_corrected_and_logged`
  - `test_member_temp_fields_drift_corrected_together`
  - `test_key_blocked_and_mapping_follow_status`
  - `test_in_flight_entity_skipped`
  - `test_no_drift_no_writes`
  - `test_repeated_drift_alerts_once_per_day`
  - `test_missing_membership_reported_not_recreated`
  - `test_litellm_down_run_fails_cleanly_no_writes`
  - `test_run_twice_second_is_noop_after_drain`

**test.sh after:** unchanged. In the e2e stack, reconciliation first runs after 5 minutes, so it does not touch Playwright runs.

### Step 2.6: UI: PMO page, Pool and Grants tabs with the create wizard (small UI step)
**Spec:** §12.5.3 tabs 1–2 and the create wizard; §12.2 PMO row; §12.3 PMO pool and grant list; §12.7; A11 in the UI.

**Files:**
- `frontend/src/pages/pmo/{PmoPage,Pool,Grants}.tsx` (new; route `/pmo/:id/:tab` with tabs `pool|grants`).
- `frontend/src/components/GrantList.tsx` (new; the shared §12.3 grant list, reused in Step 2.8).
- `frontend/src/components/GrantWizard.tsx` (new; Mantine Stepper: project and purpose → group, with its current state → amount and optional model, showing the pool available and blocking above it → dates → summary with the reason).
- `frontend/src/routes.tsx`, `frontend/src/scopes.ts` (a PMO scope routes to `/pmo/<dept>/pool`).
- `frontend/src/api/{queries,types}.ts` (`usePmoPool(deptId, period)`, `useGrants(filters)`, `useGrantWrite()`).
- `frontend/src/i18n/{he,errors}.ts` (`he.pmo`; add `PMO_POOL_INSUFFICIENT`, `GRANT_BELOW_SHARED`, `SHARE_EXCEEDS_GRANT`, `SHARE_EXPIRY_AFTER_GRANT` if missing).
- `frontend/e2e/fixtures.ts` (`apiAs(page, upn)` for API setup with CSRF).
- `frontend/e2e/pmo.spec.ts`.

**Build:**
- Pool card with available, active, used and returned. Grants table filtered by project, group, status and "expiring soon" (client side, `days_left ≤ 7`).
- The create button shows only when `actions.create_grant` is set.
- Reuse `WalletCard`, `SyncBadge`, `ChangeSummaryDialog` and Mantine RTL.

**Tests:**
- Vitest `GrantWizard.test.tsx`: `blocks amount above pool`, `expiry before start invalid`.
- Playwright `pmo.spec.ts`:
  - `PMO creates a grant through the wizard and pool available drops`
  - `A11 PMO amount above pool is blocked and the race returns PMO_POOL_INSUFFICIENT`

**test.sh after:** unchanged. Two Playwright tests are added.

### Step 2.7: UI: PMO grant actions (extend, increase, close early) and Projects tab (small UI step)
**Spec:** §12.5.3 tab 2 actions and tab 3; FR-P7; FR-P6 (close early).

**Files:**
- `frontend/src/components/GrantEditDialog.tsx` (new; modes `extend|increase|close`, with the reason).
- `frontend/src/pages/pmo/Projects.tsx` (new; tab `projects`; one row per `project_ref`, drill-down to grants and shares through `GET /projects/{ref}/summary` and `GET /grants/{id}/shares`).
- `frontend/src/pages/pmo/{PmoPage,Grants}.tsx`.
- `frontend/src/api/queries.ts` (`useProjectSummary`, `useGrantShares`), `frontend/src/i18n/he.ts`.
- `frontend/e2e/pmo.spec.ts`.

**Tests:**
- Playwright: `PMO extends then closes a grant early and the projects tab shows used and returned`.

**test.sh after:** unchanged. One Playwright test is added.

### Step 2.8: UI: Group Grants tab with share create, change and close; wallet share rows; U5 and U2 grant half (small UI step)
**Spec:** §12.5.2 tab 3; §12.5.1 active project shares; §12.6 F3; U2; U5.

**Files:**
- `frontend/src/pages/group/Grants.tsx` (new; `/group/:id/grants`, tab shown to head, rep, PMO, viewer and admin; actions only from the server `actions`).
- `frontend/src/components/ShareDialog.tsx` (new):
  - member rows with amount and expiry, defaulting to the grant's expiry and never later;
  - the unshared remainder updates live;
  - the actor's own row is disabled and names the owner role (DR9).
- `frontend/src/pages/group/GroupPage.tsx` (`TABS` gains `grants`).
- `frontend/src/components/SharesTable.tsx` (project, amount, used, expiry with days left).
- `frontend/src/api/queries.ts` (`useShareWrite`).
- `frontend/src/i18n/he.ts`.
- `frontend/e2e/grants.spec.ts`.

**Tests:**
- Vitest `ShareDialog.test.tsx`: `remainder updates and blocks above grant`, `expiry capped at grant expiry`, `own row disabled`.
- Playwright `grants.spec.ts`:
  - `U5 PMO grant then group head share; employee sees the share with expiry; PMO sees shared and unshared` (PMO creates through the UI)
  - `U2 group head cannot share to themselves and the API returns SELF_ALLOCATION_FORBIDDEN`

**test.sh after:** unchanged. Two Playwright tests are added.

### Step 2.9: UI: Department Group heads tab (small UI step)
**Spec:** §12.5.4 tab 4; DR9; FR-D5; §12.6 F6.

**Files:**
- `app/services/views.py`: department summary gains `group_heads: [{upn, display_name, group_id, group_name, base_cap, shares, model_caps, max_parallel, rpm_limit, sync_status, actions:{edit, share}}]`. It reuses the existing permission checks.
- `tests/api/test_department_summary.py`: new key; add `test_group_heads_rows_for_rep` (pytest).
- `frontend/src/pages/department/GroupHeads.tsx` (new; `/department/:id/group-heads`).
- `frontend/src/pages/department/DepartmentPage.tsx` (tab shown when any row has `actions.edit`).
- Reuses `MemberSidePanel`: base cap through `PUT /groups/{gid}/members/caps` with one row, model caps and limits through the existing endpoints. `ShareDialog` limits the choice to the head's group's active grants.
- `frontend/src/i18n/he.ts`, `frontend/e2e/department.spec.ts`.

**Tests:**
- Playwright: `Rep edits a group head base cap from the Group heads tab; the head's own row stays read-only`.

**test.sh after:** unchanged. One Playwright test is added.

### Step 2.10: UI: notifications bell and preferences; Phase 2 wrap-up (small UI step)
**Spec:** §7.9 FR-N1 and FR-N2; §12.5.1 (notification preferences); §16 Phase 2 exit.

**Files:**
- `frontend/src/components/NotificationsBell.tsx` (new; in the shell header: unread count, list drawer, mark read, a link per scope).
- `frontend/src/components/NotificationPrefs.tsx` (new; on `MyWallet.tsx`; critical kinds shown disabled).
- `frontend/src/pages/MyWallet.tsx`, the shell layout file.
- `frontend/src/api/queries.ts` (`useNotifications`, `useMarkRead`, `useNotificationPrefs`).
- `frontend/src/i18n/he.ts` (`he.notifications.kinds`).
- `frontend/e2e/notifications.spec.ts`, `PROGRESS.md`.

**Tests:**
- Playwright: `Group head sees grant_received, marks it read, mutes grant_expiring; critical kinds cannot be muted`.

**Wrap-up:**
- Every test in the table below exists and passes. `test_repo_hygiene.py` passes.
- Record in PROGRESS.md:
  - the full run time (pytest growth at most about 60 s; 7 new Playwright tests);
  - the new questions;
  - Next = Phase 3.

**test.sh after:** unchanged. Run it fully and record the time.

### Phase 2 acceptance mapping

| Criterion | Step | Test(s) |
|---|---|---|
| FR-P1 create grant, pool check | 2.1, 2.6 | `test_grants.py::test_amount_above_pool_422_with_numbers`, `::test_create_active_grant_raises_team_max_budget_after_drain`; `pmo.spec.ts` "PMO creates…" |
| FR-P2 activation raises team and pot | 2.1, 2.3 | `test_grants.py::test_model_grant_raises_max_budget_and_pot`; `test_grants_job.py::test_pending_grant_activates_at_start_time` |
| FR-P3 shares ≤ grant, no self-share | 2.2, 2.8 | `test_shares.py::test_sum_above_grant_share_exceeds_grant`, `::test_expiry_after_grant_422`, `::test_head_shares_to_self_self_allocation_forbidden`, `::test_rep_shares_to_group_head_ok` |
| FR-P4 temp increase = Σ shares, earliest expiry, both fields | 2.2, 2.3 | `test_desired_member.py::*`; `test_grants_job.py::test_share_expiry_closes_returns_and_recomputes_next_share`; `test_fake_lifecycle.py::test_member_update_temp_fields_must_come_together` |
| FR-P5 model share raises personal cap | 2.2 | `test_desired_member.py::test_model_share_raises_member_model_cap` |
| FR-P6 close at expiry, on request, on move or delete | 2.1–2.3 | `test_grants.py::test_close_returns_amount_to_pool_and_lowers_team`, `test_shares.py::test_close_share_used_returned_pool_and_team_lowered`, `test_grants_job.py::test_grant_expiry_closes_all_shares_and_unshared_remainder`, `test_member_move.py::test_move_closes_active_shares_reason_move`, `test_member_delete.py::test_delete_closes_shares_reason_manual` |
| FR-P7 extend, increase, below shared rejected | 2.1, 2.7 | `test_grants.py::test_patch_increase_checks_pool`, `::test_patch_decrease_below_shared_grant_below_shared`; `pmo.spec.ts` "PMO extends then closes…" |
| FR-P8 no cross-department grant | 2.1 | `test_grants.py::test_pmo_other_department_group_scope_forbidden` |
| §7.3.1 attribution | 2.2 | `test_attribution.py::*` |
| FR-RC1 desired vs live every N min | 2.3, 2.5 | `test_reconcile_job.py::test_team_max_budget_drift_corrected_and_logged`; `test_loop.py::test_jobs_run_at_their_intervals_with_fake_clock` |
| FR-RC2 fix, log, repeated-drift alert | 2.5 | `test_reconcile_job.py::test_repeated_drift_alerts_once_per_day`, `::test_key_blocked_and_mapping_follow_status`, `::test_member_temp_fields_drift_corrected_together` |
| FR-RC3 idempotent, retried, pending until applied | 2.5 | `test_reconcile_job.py::test_run_twice_second_is_noop_after_drain`, `::test_in_flight_entity_skipped`, `::test_litellm_down_run_fails_cleanly_no_writes` |
| FR-N1 employee 80% / 100% | 2.4 | `test_notifications_job.py::test_employee_80_and_100` |
| FR-N1 group 80% / 100% | 2.4 | `::test_group_80_and_100_to_heads` |
| FR-N1 grant expiring in 7 days | 2.4 | `::test_grant_expiring_7_days` |
| FR-N1 grant under 20% used, 7 days left | 2.4 | `::test_grant_underused` |
| FR-N1 new pending placement | 2.4 | `test_notification_events.py::test_pending_placement_notifies_rep_or_admins_when_unknown` |
| FR-N1 employee moved in | 2.4 | `::test_move_notifies_new_group_head` |
| FR-N1 outbox failures | 2.4 | `test_notifications_job.py::test_outbox_failed_to_admins` |
| FR-N1 email off by default | 2.4 | `test_notify_email.py::test_email_disabled_by_default_no_smtp` |
| FR-N2 mute non-critical | 2.4, 2.10 | `test_notifications.py::test_mute_noncritical_suppresses_new`, `::test_mute_critical_422`; `notifications.spec.ts` |
| A1 temp increase expires with no external action | 2.3 | `test_a1_temp_increase.py::test_a1_…[fake]` (`[real]` with env), `test_a1_wm.py::test_a1_share_expiry_blocks_then_scheduler_closes_and_returns` |
| A9 move with an active share | 2.3 | `test_move_invariant.py::test_a9_move_with_active_share_closes_and_returns_to_pool` |
| A11 concurrent grants over the pool | 2.1, 2.6 | `test_grants_concurrency.py::test_a11_…`; `pmo.spec.ts` "A11…" |
| U2 grant half | 2.2, 2.8 | `test_shares.py::test_head_shares_to_self_…`; `grants.spec.ts` "U2…" |
| U5 | 2.8 | `grants.spec.ts` "U5…" |
| Scheduler is a separate process (`python -m app.scheduler`) with fixed intervals | 2.3 | `test_loop.py::test_jobs_run_at_their_intervals_with_fake_clock`, `::test_stop_event_ends_loop`; the e2e stack starts it |
| Injectable clock; jobs callable as `run_<job>(session_factory, client, clock)` | 2.1, 2.3 | `test_clock.py`; every `tests/scheduler/test_*_job.py` calls the job directly with `FakeClock` |
| Safe twice and across restarts; replicas never double-close | 2.3–2.5 | `test_grants_job.py::test_run_twice_is_noop`, `::test_two_concurrent_runs_never_double_close`, `::test_restart_catches_up_on_overdue`; `test_loop.py::test_second_replica_skips_while_job_lock_held`; `test_notifications_job.py::test_run_twice_no_duplicates` |
| No AD sync | all | no job reads the directory except for names and role holders; nothing new to test |
| NFR10 hygiene | all | `test_repo_hygiene.py::test_tracked_files_have_no_secrets_or_internal_names` |

---

## 2d. Steps (Phase 3: directory, service keys, production)

Every Phase 3 step follows the §1 conventions, the write contract (reason in `X-Change-Reason`, optional Idempotency-Key, `{request_id, status, entities}`), the §11.2 envelope, and `./scripts/test.sh` as the only gate.
- **Ports.** Do not change the e2e ports (4120–4122). `scripts/e2e_stack.py` must keep working with its default ports (live preview: UI 4100, API 4111, fake 4112).
- **Time budget.** Phases 3 and 4 together may add at most about 90 s of pytest and about 10 Playwright tests. Phase 3 takes 6 of them.
- **Clock.** Nothing in `app/` calls `datetime.now()`. Use `app.state.clock` or the job's `clock`.
- **No AD sync anywhere** (owner decision 20/10/2026). The `ldap3` directory is used for exactly three things:
  - the one-time onboarding lookup;
  - owner names (FR-E3) and role holders;
  - login roles, only when the OIDC token has no groups claim.

  It has no sync, dry-run, safety gate, role mirroring or leaver grace period. `FixtureDirectory` stays the dev and test default.
- **No external services.** Tests need no LDAP server (ldap3 `MOCK_SYNC`), no Redis server (the fake reports Redis state), and no paid API.
- **Secrets (NFR5).** A service key secret is returned once, in the create or rotate response only. It is never stored, logged, audited or put into an outbox payload; only the LiteLLM token hash is kept. Responses that carry a secret send `Cache-Control: no-store`.
- **Public repo (NFR10).** Use placeholders only (`ldaps://dc.corp.example`, `DC=corp,DC=example`). `test_repo_hygiene.py` must keep passing. Tests generate dummy passwords with `secrets.token_urlsafe`.
- **No Enterprise features.**
  - Rotation is generate-new plus delete-old. It does not use `/key/regenerate`.
  - Redis status comes from LiteLLM's `/health/readiness`.
- **Order.** Backend Steps 3.1–3.4 come first. UI Steps 3.5–3.9 are small: one tab each.

### Step 3.1: ldap3 Directory with an offline contract test, OIDC role fallback
**Spec:** §5.1 Directory lookup; §9.1; §5.4 (AD unreachable); FR-E3; §6 (roles at login); NFR2 (ldap3, no sync CronJob); NFR5; NFR10; §16 Phase 3 (directory contract test).

**Files:**
- `pyproject.toml` and `uv.lock`: add `ldap3`. If `uv add ldap3` cannot reach an index, stop and record a blocker in PROGRESS.md.
- `app/directory.py`: `LdapDirectory`, `make_directory`.
- `app/config.py` and `.env.example`.
- `app/main.py`: `create_app(..., directory: Directory | None = None)`.
- `app/errors.py`: an exception handler mapping `DirectoryUnavailable` to 503 `DIRECTORY_UNAVAILABLE`.
- `app/services/owners.py`.
- `app/auth/oidc.py`.
- `app/auth/dev.py`: keeps a `FixtureDirectory` for the dev user list.
- `tests/directory/__init__.py`, `tests/directory/ldap_mock.py`, `tests/directory/test_ldap_directory.py`, `tests/directory/test_directory_contract.py` (all new).
- `tests/auth/test_oidc.py`, `tests/api/test_onboarding.py`.

**Build:**
- **Settings.**
  - `directory_backend: Literal["fixture","ldap"] = "fixture"`
  - `ldap_url = ""` (example `ldaps://dc.corp.example`)
  - `ldap_bind_dn = ""` and `ldap_bind_password = ""` (`.env` only, never logged)
  - `ldap_user_base = ""` (example `OU=Users,DC=corp,DC=example`)
  - `ldap_timeout_s = 5`, `ldap_cache_ttl_s = 300`
- **`make_directory(settings) -> Directory`.**
  - `fixture` returns `FixtureDirectory(settings.seed_file)`.
  - `ldap` returns `LdapDirectory(settings)`. A missing url or base raises `RuntimeError` at startup.
- **`LdapDirectory`.** Signature: `LdapDirectory(settings, connect: Callable[[], ldap3.Connection] | None = None, now=time.monotonic)`. It is synchronous like the Protocol and keeps the Protocol unchanged.
  - `groups_of(upn)`: search `(&(objectClass=user)(userPrincipalName=<escaped>))` and read `memberOf`. Direct membership only. Not cached, because it is the one-time onboarding read.
  - `members_of(dn)`: search `(&(objectClass=user)(memberOf=<escaped dn>))` and return `userPrincipalName`. Cached for the TTL.
  - `display_name(upn)`: read `displayName`. Returns `None` when the user is not found. Only positive results are cached, so `not_in_directory` is never stale.
  - Values are escaped with `ldap3.utils.conv.escape_filter_chars`.
  - Any `ldap3.core.exceptions.LDAPException` or `OSError` (including a failed bind) raises `DirectoryUnavailable` with no credentials in the message.
- **Callers.**
  - Onboarding already maps the error to 503 and writes nothing.
  - Writes that need `target_tier` now fail closed with 503 `DIRECTORY_UNAVAILABLE` through the new handler.
  - `owners.holders` catches `DirectoryUnavailable` and returns `[]`, so the wallet still renders with the owner's role but no names.
- **OIDC login roles.** If the token's groups claim is missing or empty, use `directory.groups_of(upn)`. On `DirectoryUnavailable`, log a warning without the upn's groups and create an employee-only session (least privilege).
- **Contract test without a server.** `tests/directory/ldap_mock.py: mock_connect(yaml_path) -> Callable[[], Connection]` does the following:
  - builds `Server("mock.corp.example", get_info=ldap3.OFFLINE_AD_2012_R2)` and `Connection(..., client_strategy=ldap3.MOCK_SYNC)`;
  - adds a bind entry with a password generated at run time;
  - loads `seed/dev.yaml` users as `user` entries (`userPrincipalName`, `displayName`, `memberOf`).

  The same dataset backs both implementations.
- **Not built:** an LDAP sync job, mirroring, or a dry-run.

**Tests:**
- `tests/directory/test_directory_contract.py::test_contract[fixture]` and `::test_contract[ldap]`, covering `groups_of`, `members_of` and `display_name` (known, unknown, no groups).
- `tests/directory/test_ldap_directory.py`:
  - `test_filter_values_escaped`
  - `test_bind_failure_raises_directory_unavailable`
  - `test_socket_error_raises_directory_unavailable`
  - `test_members_of_cached_until_ttl`
  - `test_unknown_user_not_cached`
  - `test_make_directory_selects_backend_and_refuses_missing_config`
  - `test_real_ldap[real]`: skipped unless `LDAP_TEST_URL` is set.
- `tests/auth/test_oidc.py`:
  - `test_login_without_groups_claim_uses_directory`
  - `test_login_directory_down_employee_only`
- `tests/api/test_onboarding.py::test_ldap_directory_down_503_nothing_written` (an `LdapDirectory` whose `connect` raises).
- `tests/api/test_me_wallet.py::test_owner_names_degrade_when_directory_down`.

**test.sh after:** unchanged. `uv sync` installs ldap3.

### Step 3.2: Platform backend: PUT /policies, Redis status, department and group creation, role bindings
**Spec:** §11.1 Platform (`GET|PUT /policies`, `/status`); §12.5.5 (Departments, Policies, Status); §9.3 "New budget group"; D13; FR-D1; §8.2 `policy`, `role_binding`; §5.4 Redis row; §10 Info and health.

**Files:**
- `app/routers/policies.py`, `app/routers/platform.py`, `app/routers/departments.py`.
- `app/routers/role_bindings.py` (new; register it in `app/main.py`).
- `app/services/platform.py` (new; policies, department and group create, bindings).
- `app/schemas/platform.py` (new).
- `app/litellm_client/{client,models}.py`: `health_readiness()`, `HealthReadiness`.
- `fake_litellm/main.py`: `GET /health/readiness`.
- `fake_litellm/controls.py`: the `Config.redis` field.
- `app/permissions.py`.
- `frontend/vite.config.ts`: proxy `/role-bindings`.
- `tests/api/test_policies_put.py`, `tests/api/test_platform_create.py`, `tests/api/test_role_bindings.py` (new); `tests/api/test_status_outbox.py`; `tests/litellm/test_client_vs_fake.py`.

**Build:**
- **`PUT /policies`** (admin; others 403).
  - Body: a partial map of the policy keys `GET /policies` returns today, plus `overalloc_factors: {"<group_id>": number}`, which writes `grp.overalloc_factor` within `[1.0, 2.0]`.
  - Errors, all 422 `INVALID_REQUEST`:
    - an unknown key or a `member_defaults.*` key gives `details.key`;
    - `min ≤ default ≤ max` violated gives `details.field`;
    - an unknown group id gives `details.group_id`.
  - One audit row per changed key or group (entity `policy` or `group`). No LiteLLM call.
  - Existing key limits outside the new range are left alone (the Q4 rule).
- **`GET /status`** adds the following fields:
  - `redis`, one of `"connected" | "unreachable" | "not_configured" | "unknown"`;
  - `litellm_version`;
  - `pending_placements` (an integer for admins, null for everyone else).

  `client.health_readiness()` calls LiteLLM `GET /health/readiness` and parses `{status, db, cache, litellm_version}`. Mapping:
  - `cache` null gives `not_configured`;
  - `cache == "redis"` with `status == "healthy"` gives `connected`;
  - a readiness 503 or an unhealthy status with `cache` set gives `unreachable`;
  - LiteLLM unreachable gives `unknown`.

  One call serves both reachability and Redis, through the existing 10 s cache in `_reachable`.

  In the fake, `/health/readiness` is unauthenticated. `/_test/config` gains `redis: Literal["none","ok","down"] = "none"`, and `"down"` returns 503 with `cache: "redis"`. No Redis server is used anywhere.
- **`POST /departments`** (admin).
  - Body: `{name, litellm_org_id, ad_group_dn, budget, budget_period="1mo"}`.
  - Enqueues `/organization/new` through the existing builder.
  - A duplicate `litellm_org_id` or `ad_group_dn` gives 422 `INVALID_REQUEST` with `details.field`.
  - The entity is `department`.
- **`POST /departments/{id}/groups`** (admin).
  - Body: `{name, litellm_team_id, ad_group_dn, kind: "regular"|"department_staff", default_member_cap=0}`.
  - It creates `grp` with `base_budget=0` and status `active`, and enqueues `/team/new` (organization_id, `max_budget=0`, §9.3).
  - Errors: 404 unknown department; 422 `INVALID_REQUEST` for a duplicate team id or DN, or a second `department_staff` group (D13).
- **`GET /role-bindings?scope_kind=&scope_id=`** and **`PUT /role-bindings`** (admin).
  - Body: `{scope_kind: "global"|"department"|"group", scope_id, role, ad_group_dns: [dn]}`. It replaces the set for that (scope, role).
  - Allowed roles:
    - `department`: rep, PMO, viewer;
    - `group`: group head, viewer;
    - `global`: admin, viewer.

    Use the role constants in `app/permissions.py`.
  - Removing the last global admin binding gives 422 `INVALID_REQUEST`.
  - GET rows carry `members: [{upn, display_name}]` through `owners.holders`-style lookups.
  - Changes take effect at the next login (existing behavior).
  - Audit entity `role_binding`. Nothing is mirrored into LiteLLM (§6).

**Tests:**
- `tests/api/test_policies_put.py`:
  - `test_admin_updates_concurrency_range_and_audits`
  - `test_min_above_max_invalid`
  - `test_unknown_key_invalid`
  - `test_non_admin_403`
  - `test_overalloc_factor_per_group_then_caps_check_uses_it`
- `tests/api/test_status_outbox.py`:
  - `test_status_redis_not_configured_by_default`
  - `test_status_redis_connected_and_down`
  - `test_status_litellm_down_redis_unknown`
  - `test_pending_placements_count_admin_only`
- `tests/api/test_platform_create.py`:
  - `test_create_department_enqueues_organization_new`
  - `test_create_group_team_new_zero_budget_after_drain`
  - `test_second_staff_group_invalid`
  - `test_duplicate_team_id_invalid`
  - `test_non_admin_403`
- `tests/api/test_role_bindings.py`:
  - `test_replace_department_rep_binding_takes_effect_on_next_login`
  - `test_last_admin_binding_cannot_be_removed`
  - `test_role_not_allowed_for_scope_invalid`
- `tests/litellm/test_client_vs_fake.py::test_health_readiness_shapes`

**test.sh after:** unchanged.

### Step 3.3: Production organizations and systems
**Spec:** FR-K1, FR-K2 (systems); §4 (Production tree), DR4, D8; §11.1 `POST /production/departments/{id}/systems`; §12.5.4 tab 6; §12.5.5 (create the production organization and systems); §10 Department and Group rows.

**Files:**
- `alembic/versions/0005_production_service_keys.py` (new).
- `app/models.py`, `tests/test_schema.py`.
- `app/db.py`: `LockKind.PRODUCTION`, the next free value.
- `app/services/production.py`, `app/schemas/production.py`, `app/routers/production.py` (new; register in `app/main.py`).
- `app/outbox/desired.py` and `app/outbox/worker.py`: new entities `production_org` and `production_system`.
- `app/services/spend.py`: snapshot `scope_kind="system"` from team spend of production teams.
- `app/services/views.py`: department summary `actions.view_production`, `actions.edit_production`.
- `app/permissions.py`.
- `frontend/vite.config.ts`: proxy `/production`.

**Build:**
- **Migration 0005** (it also covers Step 3.4):
  - `department.prod_litellm_org_id text NULL UNIQUE`
  - `department.prod_budget numeric(18,2) NULL`
  - `department.prod_budget_period text NOT NULL DEFAULT '1mo'`
  - table `production_system (id bigint identity PK, department_id bigint FK, litellm_team_id text UNIQUE, name text, budget numeric(18,2) DEFAULT 0, status text CHECK IN ('active','archived') DEFAULT 'active', created_at timestamptz)`
  - on `service_key`:
    - `CHECK owner_kind IN ('group','system')` and `CHECK status IN ('pending','active','failed','revoked')`;
    - add `created_at timestamptz`, `created_by text`;
    - partial unique index on `litellm_token_hash WHERE litellm_token_hash IS NOT NULL`;
    - index `(owner_kind, owner_id)`.

  `tests/test_schema.py` adds the table and columns, with a comment pointing to owner question 1.
- **`PUT /production/departments/{id}`** (admin).
  - Body: `{budget, budget_period?}`.
  - The first call sets `prod_litellm_org_id = f"{litellm_org_id}-prod"` and enqueues `/organization/new` (alias `"<name> production"`). Later calls enqueue `/organization/update`.
  - Lowering the budget below Σ systems is accepted (Q4); the GET then shows `over_allocated: true`.
  - Entity `production_org` (id = department id).
- **`POST /production/departments/{id}/systems`** (admin; spec body `{name, budget}`).
  - It takes `lock(PRODUCTION, id)` before reading any sums.
  - Errors: 404 department; 422 `INVALID_REQUEST` with `details.reason="no_production_org"`; Σ system budgets + budget > `prod_budget` gives 422 `PARENT_OVERALLOCATED` `{requested, available, parent_total}`.
  - It inserts the row with `litellm_team_id = f"sys-{id}"` and enqueues `/team/new` (`organization_id` = the production org, `max_budget`, `budget_duration`).
  - The team never belongs to the department org (DR4).
- **`GET /production/departments/{id}`** (admin; rep of the department; others 403 per the §12.2 production row).
  - Returns `{department_id, org: {litellm_org_id, budget, budget_period, used, over_allocated, sync_status} | null, systems: [{id, name, litellm_team_id, budget, used, keys_count, sync_status}], spend_taken_at, actions: {edit}}`.
  - `used` comes from the snapshot (§12.9).
- Production objects are not reconciled. FR-RC covers the §8.1 fields only.

**Tests:**
- `tests/api/test_production.py`:
  - `test_create_production_org_then_update_budget`
  - `test_system_team_in_production_org_not_department_org_dr4`
  - `test_systems_over_production_budget_422_with_numbers`
  - `test_system_without_production_org_invalid`
  - `test_rep_reads_viewer_and_head_403`
  - `test_non_admin_writes_403`
  - `test_replay_same_idempotency_key_200`
- `tests/api/test_production_concurrency.py::test_concurrent_systems_one_parent_overallocated`
- `tests/test_spend_snapshot.py::test_system_spend_snapshotted`

**test.sh after:** unchanged.

### Step 3.4: Service keys: create (secret shown once), rotate, revoke, list
**Spec:** FR-K2 (system keys), FR-K3, FR-K4; NFR5; §11.1 `POST /service-keys`, `/service-keys/{id}/rotate | /revoke`; §10 Service key row; §12.5.2 tab 5; §6 (group head manages group service keys); FR-A1; FR-G4 and D7 for limits.

**Files:**
- `app/litellm_client/{client,models}.py`: `key_service_account_generate(body: ServiceAccountKeyGenerate) -> KeyGenerateResponse`.
- `fake_litellm/main.py`: `POST /key/service-account/generate`.
- `fake_litellm/db.py`: a `models` column on `LiteLLM_VerificationToken`. An existing dev fake DB needs `scripts/dev_db.py` to reset it, as in 1b.1.
- `fake_litellm/enforcement.py`: enforce the key's `models` and `max_budget` for `user_id=null` keys.
- `app/services/service_keys.py`, `app/schemas/service_keys.py`, `app/routers/service_keys.py` (new).
- `app/services/validation.py`: `group_allocated(db, group)`.
- `app/services/members.py`: the FR-G1 sum uses it.
- `app/services/views.py`: group summary `actions.service_keys {view, manage}`, and `allocated` includes key budgets.
- `app/services/spend.py`: snapshot `scope_kind="service_key"` keyed by `api_key` = token hash.
- `app/permissions.py`: `can_manage_service_keys`, `can_view_service_keys`.
- `app/errors.py`: `LITELLM_UNAVAILABLE`.
- `frontend/vite.config.ts`: proxy `/service-keys`.

**Build:**
- **`POST /service-keys`** with the spec body `{owner_kind: group|system, owner_id, alias, max_budget, max_parallel, rpm_limit, models}`.
  1. Permission check. Group keys: group head of that group, or admin. System keys: admin. Otherwise 403, or 404 for an unknown owner.
  2. Lock: `lock(GROUP, id)` or `lock(PRODUCTION, department_id)`.
  3. Validation:
     - group: `group_allocated + max_budget > base × overalloc_factor` gives 422 `PARENT_OVERALLOCATED`, where `group_allocated` = Σ member base caps + Σ active group key budgets;
     - system: Σ active key budgets + max_budget > system budget gives 422 `PARENT_OVERALLOCATED`;
     - `max_parallel` and `rpm_limit` go through the existing `check_limit` with the policy ranges (admin may exceed the max, D7); failures give 422 `LIMIT_OUT_OF_POLICY`;
     - an owner whose `/team/new` row is not applied gives 422 `INVALID_REQUEST` with `details.sync_status`.
  4. First transaction: insert the `service_key` row (`pending`), the audit row, and a `litellm_write` row for `/key/service-account/generate` with status `pending`. That payload contains no secret, because LiteLLM generates it. Commit.
  5. Call `app.state.litellm.key_service_account_generate(team_id, key_alias=f"wm-sk-{id}", max_budget, max_parallel_requests, rpm_limit, models)` synchronously. It cannot be queued without storing the secret.
  6. Second transaction:
     - on success, store `token` in `litellm_token_hash`, set status `active`, mark the write row `applied`;
     - on a LiteLLM error, set the key and the write row to `failed` (with `last_error`) and return 503 `LITELLM_UNAVAILABLE`.
  7. Response: the write response with entity `service_key` (values `{alias, max_budget, max_parallel, rpm_limit, models, status}`), plus top-level `secret`, plus `Cache-Control: no-store`.

  An idempotent replay returns 200 without `secret`.
- **`POST /service-keys/{id}/rotate`** `{}` (same permissions).
  - Status must be `active`, otherwise 422 `INVALID_REQUEST` with `details.status`.
  - It generates a new key synchronously with the same values, swaps the hash, and enqueues `/key/delete {keys: [old_hash]}` with a payload fixed at enqueue (§8.2).
  - It returns the new `secret` once.
- **`POST /service-keys/{id}/revoke`** `{}`.
  - `active` becomes `revoked`, and it enqueues `/key/delete` with a fixed payload.
  - Revoking an already revoked key gives 422 `INVALID_REQUEST`.
- **`GET /service-keys?owner_kind=&owner_id=`** (view: head, rep or viewer of the group or its department, and admin for group keys; rep and admin for system keys).
  - Row fields: `{id, owner_kind, owner_id, alias, max_budget, max_parallel, rpm_limit, models, status, used, sync_status, created_at, actions: {rotate, revoke}}`.
  - `failed` keys are listed (they still count toward nothing). Hashes are never returned.
- **NFR5.** The secret must not reach logging. The client already avoids logging. Neither the routers nor the services log request or response bodies.

**Tests:**
- `tests/api/test_service_keys.py`:
  - `test_head_creates_group_key_secret_once_hash_stored`
  - `test_secret_absent_from_db_audit_outbox_and_logs` (scans `service_key`, `audit_log`, `litellm_write` text and `caplog`)
  - `test_replay_returns_200_without_secret`
  - `test_group_keys_plus_caps_over_base_parent_overallocated`
  - `test_member_caps_check_counts_key_budgets`
  - `test_limits_out_of_policy_422_admin_above_max_ok`
  - `test_litellm_down_503_key_failed_nothing_usable`
  - `test_rotate_new_secret_old_key_deleted_after_drain`
  - `test_revoke_deletes_key_twice_invalid`
  - `test_rep_viewer_list_no_actions_and_write_403`
  - `test_system_key_admin_only_and_within_system_budget`
- `tests/contract/test_service_keys.py::test_service_key_budget_parallel_and_models[fake]` (`[real]` with env).
- `tests/e2e_backend/test_service_keys_wm.py::test_group_key_spend_draws_from_team_budget`
- `tests/litellm/test_client_vs_fake.py::test_service_account_generate_returns_key_and_hash`

**test.sh after:** unchanged.

### Step 3.5: UI: Group Service keys tab (small UI step)
**Spec:** §12.5.2 tab 5; FR-K3, FR-K4; NFR5; §12.1; §12.7.

**Files:**
- `frontend/src/pages/group/ServiceKeys.tsx` (new; `/group/:id/service-keys`).
- `frontend/src/pages/group/GroupPage.tsx`: `TABS` gains `service-keys`, shown when `actions.service_keys.view`.
- `frontend/src/components/ServiceKeyList.tsx` (new; reused by Step 3.6).
- `frontend/src/components/ServiceKeyDialog.tsx` (new; create form plus reason, through `ChangeSummaryDialog`).
- `frontend/src/components/SecretOnceModal.tsx` (new). It shows the secret with a copy button and a warning. The secret lives only in component state from the mutation result and is never in the TanStack Query cache. Closing it clears the secret, and it cannot be reopened.
- `frontend/src/api/{queries,types}.ts`: `useServiceKeys(ownerKind, ownerId)`, `useServiceKeyWrite()`.
- `frontend/src/i18n/{he,errors}.ts`: `he.serviceKeys`, plus `LITELLM_UNAVAILABLE`.
- `frontend/e2e/service-keys.spec.ts`.

**Tests:**
- Vitest `SecretOnceModal.test.tsx`: `secret cleared after close`, `copy button copies the secret`.
- Playwright:
  - `Group head creates a service key, sees the secret once, rotates and revokes it`
  - `Viewer sees the service keys tab without actions and POST /service-keys returns 403`

**test.sh after:** unchanged. Two Playwright tests are added.

### Step 3.6: UI: Department Production tab (small UI step)
**Spec:** §12.5.4 tab 6; §12.2 production row (rep views, admin edits); FR-K1, FR-K2; §12.3 wallet card.

**Files:**
- `frontend/src/pages/department/Production.tsx` (new; `/department/:id/production`):
  - the org `WalletCard`;
  - a systems table (budget, used, keys, sync);
  - for admin: "set production budget" and "add system" dialogs with reason, and a per-system keys drawer reusing `ServiceKeyList`, `ServiceKeyDialog` and `SecretOnceModal` with `owner_kind=system`.
- `frontend/src/pages/department/DepartmentPage.tsx`: tab shown when `actions.view_production`.
- `frontend/src/api/queries.ts`: `useProduction(deptId)`, `useProductionWrite()`.
- `frontend/src/i18n/he.ts`, `frontend/e2e/department.spec.ts`.

**Tests:**
- Playwright: `Admin creates the production organization and a system with a key; the representative sees the Production tab read-only`.

**test.sh after:** unchanged. One Playwright test is added.

### Step 3.7: UI: Platform shell and Departments tab (small UI step)
**Spec:** §12.5.5 Departments (create, budget and period, representatives from AD); §9.3 New budget group; D13; DR9 (representatives' personal values).

**Files:**
- `frontend/src/pages/platform/PlatformPage.tsx` (new; replaces `frontend/src/pages/Platform.tsx`).
  - Route `/platform/:tab` with tabs `pending|departments|operations|policies|audit`.
  - `/platform` redirects to `pending`, so the existing `pending.spec.ts` stays unchanged.
- `frontend/src/pages/platform/{Pending,Departments}.tsx` (new). `Pending` holds the current `PendingPlacementsTable`.
- `frontend/src/components/RoleBindingsEditor.tsx` (new). Per role, AD group DNs with the resolved member names; the reason goes through `ChangeSummaryDialog`.
- `frontend/src/routes.tsx`, `frontend/src/scopes.ts` (the platform scope goes to `/platform`).
- `frontend/src/api/queries.ts`: `useCreateDepartment`, `useCreateGroup`, `useRoleBindings`, `useRoleBindingsWrite`.
- `frontend/src/i18n/he.ts`, `frontend/e2e/platform.spec.ts` (new).

**Build:**
- The Departments table lists the department budget and period through the existing `PUT /departments/{id}/budget`.
- It has "create department" and "create group" dialogs (kind regular or staff).
- A "representatives' personal values" link opens `/group/<staff group id>/members` (DR9: admin edits there).

**Tests:**
- Playwright: `Admin creates a department, its staff group and a representative binding; the new group shows sync applied at zero budget`.

**test.sh after:** unchanged. One Playwright test is added.

### Step 3.8: UI: Platform Operations tab: status with Redis, outbox, drift log (small UI step)
**Spec:** §12.5.5 Outbox and reconciliation, Status; §11.1 `/status`, `/outbox`, `/outbox/{id}/retry`; FR-RC2 (drift corrections log); §5.4 Redis row.

**Files:**
- `frontend/src/pages/platform/Operations.tsx` (new):
  - status card: LiteLLM reachability, Redis (`לא מוגדר` when `not_configured`), outbox depth, pending placements, spend freshness;
  - outbox table with a status filter and retry;
  - drift corrections from `GET /audit?actor=system:reconcile`, rendered by `HistoryTimeline`.
- `frontend/src/api/queries.ts`: `useOutbox(status)`, `useRetryOutbox`; extend `useStatus`.
- `frontend/src/i18n/he.ts`, `frontend/e2e/platform.spec.ts`.

**Tests:**
- Playwright: `Admin sees Redis not configured, retries a failed outbox row and it turns applied` (uses `fakeFail`).

**test.sh after:** unchanged. One Playwright test is added.

### Step 3.9: UI: Platform Policies and Audit search tabs; Phase 3 wrap-up (small UI step)
**Spec:** §12.5.5 Policies, Audit search; §11.1 `PUT /policies`, `GET /audit`; §16 Phase 3 exit.

**Files:**
- `frontend/src/pages/platform/{Policies,Audit}.tsx` (new).
  - `Policies` edits the concurrency and rpm ranges, thresholds and per-group over-allocation factors, through `ChangeSummaryDialog` with a reason.
  - `Audit` is a search form (entity, id, actor, from, to) showing results in `HistoryTimeline`.
- `frontend/src/api/queries.ts`: `usePoliciesWrite`, `useAuditSearch`.
- `frontend/src/i18n/he.ts`, `frontend/e2e/platform.spec.ts`, `PROGRESS.md`, `PRD.md`.

**Tests:**
- Vitest `Audit.test.tsx`: `builds the query from the filters`.
- Playwright: `Admin lowers the concurrency maximum; a head saving a limit above it gets LIMIT_OUT_OF_POLICY`.

**Wrap-up:**
- Every test in the mapping below exists and passes, and `test_repo_hygiene.py` passes.
- Record in PROGRESS.md: the run time (pytest growth so far, plus 6 Playwright tests), the new questions, and Next = Phase 4.

**test.sh after:** unchanged. Run it fully and record the time.

### Phase 3 acceptance mapping

| Criterion | Step | Test(s) |
|---|---|---|
| FR-K1 production Organization with its own budget | 3.3, 3.6 | `test_production.py::test_create_production_org_then_update_budget`; `department.spec.ts` "Admin creates the production organization…" |
| FR-K2 system Team and service keys via `/key/service-account/generate` | 3.3, 3.4, 3.6 | `test_production.py::test_system_team_in_production_org_not_department_org_dr4`, `::test_systems_over_production_budget_422_with_numbers`; `test_service_keys.py::test_system_key_admin_only_and_within_system_budget`; `test_client_vs_fake.py::test_service_account_generate_returns_key_and_hash` |
| FR-K3 group service keys draw from the group budget | 3.4, 3.5 | `test_service_keys.py::test_group_keys_plus_caps_over_base_parent_overallocated`, `::test_member_caps_check_counts_key_budgets`; `test_service_keys_wm.py::test_group_key_spend_draws_from_team_budget`; contract `test_service_key_budget_parallel_and_models[fake]` |
| FR-K4 rotate and revoke | 3.4, 3.5 | `test_service_keys.py::test_rotate_new_secret_old_key_deleted_after_drain`, `::test_revoke_deletes_key_twice_invalid`; `service-keys.spec.ts` "Group head creates…" |
| FR-K4 and NFR5: secret shown once, only the hash stored | 3.4, 3.5 | `test_service_keys.py::test_head_creates_group_key_secret_once_hash_stored`, `::test_secret_absent_from_db_audit_outbox_and_logs`, `::test_replay_returns_200_without_secret`; `SecretOnceModal.test.tsx` |
| DR4 production keys outside the department org | 3.3 | `test_production.py::test_system_team_in_production_org_not_department_org_dr4` |
| Permissions: head manages group keys, rep views production, viewer read-only | 3.3–3.5 | `test_service_keys.py::test_rep_viewer_list_no_actions_and_write_403`; `test_production.py::test_rep_reads_viewer_and_head_403`; `service-keys.spec.ts` "Viewer…" |
| ldap3 Directory: onboarding lookup, owner names, login roles | 3.1 | `test_directory_contract.py::test_contract[ldap]`; `test_oidc.py::test_login_without_groups_claim_uses_directory`; `test_me_wallet.py::test_owner_names_degrade_when_directory_down` |
| `DirectoryUnavailable` on LDAP errors, 503, nothing written | 3.1 | `test_ldap_directory.py::test_bind_failure_raises_directory_unavailable`, `::test_socket_error_raises_directory_unavailable`; `test_onboarding.py::test_ldap_directory_down_503_nothing_written` |
| Directory contract test without a real LDAP | 3.1 | `test_directory_contract.py::test_contract[fixture]` and `[ldap]` (MOCK_SYNC); `test_real_ldap[real]` with `LDAP_TEST_URL` |
| No AD sync | all | no job, endpoint or setting reads the directory except for onboarding, names, role holders and the login fallback |
| `PUT /policies` | 3.2, 3.9 | `test_policies_put.py::*`; `platform.spec.ts` "Admin lowers the concurrency maximum…" |
| Redis status in `/status`, works without Redis | 3.2, 3.8 | `test_status_outbox.py::test_status_redis_not_configured_by_default`, `::test_status_redis_connected_and_down`, `::test_status_litellm_down_redis_unknown`; `platform.spec.ts` "Admin sees Redis not configured…" |
| Group creation by admin, `/team/new` at zero budget (§9.3) | 3.2, 3.7 | `test_platform_create.py::test_create_group_team_new_zero_budget_after_drain`; `platform.spec.ts` "Admin creates a department…" |
| Department Production tab | 3.6 | `department.spec.ts` "Admin creates the production organization…" |
| Group Service keys tab | 3.5 | `service-keys.spec.ts` (2 tests) |
| Platform screens: Departments, representatives, Operations, Policies, Audit search, Pending (Phase 1b) | 3.7–3.9 | `platform.spec.ts` (3 tests); `Audit.test.tsx`; the existing `pending.spec.ts` is unchanged |
| FR-V1 locks on new parents | 3.3, 3.4 | `test_production_concurrency.py::test_concurrent_systems_one_parent_overallocated` |
| NFR10 hygiene | all | `test_repo_hygiene.py::test_tracked_files_have_no_secrets_or_internal_names` |


## 2e. Steps (Phase 4: reports)

The Phase 3 rules apply: §1 conventions, §11.2 envelope, injectable clock, the same ports, public repo, no external services. Additional rules:
- **Time budget.** Phase 4 may add 4 Playwright tests and the remainder of the 90 s pytest budget.
- **Data sources.** Reports read spend from `spend_snapshot`, including the day snapshots `/me/usage` already reads (§12.9). The only live read is concurrency peaks. That read goes through the read-only `LiteLLMReader` on `LiteLLM_SpendLogs`, with a range of at most 31 days.
- **Permissions** follow the §12.2 reports row plus D11 and Q7:
  - department-level aggregates for admin, rep and viewer;
  - member-level rows only where `can_view_members` passes (head, rep, admin);
  - employees see their own data;
  - PMO sees grants of their department only.
- **Period rules.** `period=YYYY-MM` follows §1, and future periods give 422. `from`/`to` are dates in `APP_TIMEZONE`. If `from > to`, or the range is too long, the result is 422 `INVALID_REQUEST`.
- **SPA route.** The UI route is `/report/...` because `/reports` is an API prefix (§1).
- **Order.** Backend Steps 4.1–4.3, then UI Steps 4.4–4.7.

### Step 4.1: Allocation-versus-spend and grants reports (backend)
**Spec:** FR-R1, FR-R2; §11.1 `GET /reports/allocation`, `GET /reports/grants`; §12.3 formulas; D11; §12.2 reports row.

**Files:**
- `app/services/reports.py` (new). It reuses `app/services/{views,wallet,grants,spend}.py`.
- `app/schemas/reports.py` (new), `app/routers/reports.py`.
- `tests/api/test_reports_allocation.py`, `tests/api/test_reports_grants.py` (new).

**Build:**
- **`GET /reports/allocation?level=department|group|employee&id=&period=`.** Returns `{level, id, name, period, total, allocated, spend, remaining, pct, state, models: [{model, allocation, spend, pct}], rows: [...], spend_taken_at}`.
  - `rows` are the children: groups, PMO pool and reserve for a department; members for a group, with base cap, shares, total, spend and per-model caps; none for an employee.
  - Rows use the §12.3 formulas, reusing the existing summary builders rather than new math.
  - Permissions: department level uses `can_view_department` (PMO gets 403); group level uses `can_view_members`; employee level is self or `can_view_members` on the employee's group. Unknown entities give 404.
- **`GET /reports/grants?department=&project_ref=`.** Returns `{rows: [{project_ref, departments, groups, grants, amount, shared, unshared, used, returned}], totals}`.
  - It aggregates `grants.list_grants` rows, so visibility is inherited: PMO and rep of the department, group heads for their group's grants, viewer, admin.

**Tests:**
- `tests/api/test_reports_allocation.py`:
  - `test_department_rows_groups_pmo_reserve_and_models`
  - `test_group_rows_members_with_shares_and_model_caps`
  - `test_employee_level_self_and_head_only`
  - `test_viewer_group_level_403_department_ok`
  - `test_past_period_uses_that_period_snapshot`
  - `test_future_period_422`
- `tests/api/test_reports_grants.py`:
  - `test_by_project_totals_match_project_summary`
  - `test_pmo_other_department_hidden`
  - `test_head_sees_own_group_only`

**test.sh after:** unchanged.

### Step 4.2: Usage series, concurrency peaks, blocked spend (backend, fake SpendLogs times)
**Spec:** FR-R1 (history), FR-R4, FR-R5; §11.1 `GET /reports/usage`, `GET /reports/concurrency-peaks`; §12.3 usage chart; D6.

**Files:**
- `fake_litellm/controls.py`:
  - `/_test/spend` gains `start`, `end` (default `at`) and `api_key`;
  - new `POST /_test/spend/bulk` (a list of the same rows in one transaction).
- `fake_litellm/enforcement.py`: confirm it records real `startTime` and `endTime` per request.
- `app/litellm_client/readonly.py`: `concurrency_peaks(start, end, *, team_ids=None, users=None) -> list[PeakRow(user, day, peak, at)]`.
  - One SQL statement: `+1` at `startTime` and `−1` at `endTime`, a running `SUM() OVER (PARTITION BY user ORDER BY t, delta)` with ends before starts at the same instant (half-open), then the max per user per day, plus a `user IS NULL` global row.
- `app/services/reports.py`, `app/routers/reports.py`, `frontend/e2e/fixtures.ts` (`fakeSpend(rows)` helper).

**Build:**
- **`GET /reports/usage?scope=employee|group|department|global&id=&from=&to=&granularity=day|week|month&by=model|member`.**
  - Returns `{series: [{bucket, key, spend}], cap, taken_at}` from the day snapshots, bucketed in `APP_TIMEZONE`.
  - Range at most 400 days.
  - `by=member` needs `can_view_members` (viewer and PMO get 403).
  - `global` is admin or global viewer only.
  - If group- or department-level day snapshots are missing, extend the snapshot job in `app/services/spend.py` to write them.
- **`GET /reports/concurrency-peaks?scope=global|department|group|employee&id=&from=&to=`.**
  - Returns `{daily: [{day, peak, at}], members: [{upn, display_name, peak, at, max_parallel}]}`, sorted by peak, top 100.
  - Range at most 31 days.
  - Viewers get `daily` only.
- **`GET /reports/blocked?scope=&id=&from=&to=`** (FR-R5).
  - Returns `rows: [{day, upn, display_name, group_id, kind: "base"|"model", model, cap, period_spend_to_date, day_spend}]`.
  - A row appears for each day an employee's cumulative period spend has reached ≥ 100% of their current total (base cap + active shares) or of a model cap.
  - Same member visibility as usage `by=member`.

**Tests:**
- `tests/litellm/test_readonly_peaks.py`:
  - `test_overlaps_counted_half_open`
  - `test_peak_per_user_and_global`
  - `test_rows_outside_window_ignored`
- `tests/api/test_reports_usage.py`:
  - `test_group_by_model_daily_series_matches_snapshot`
  - `test_by_member_viewer_403`
  - `test_week_and_month_buckets_in_app_timezone`
  - `test_range_validation_422`
- `tests/api/test_reports_peaks.py`:
  - `test_peaks_per_employee_and_global_from_spendlogs`
  - `test_department_scope_filters_teams`
  - `test_range_over_31_days_422`
- `tests/api/test_reports_blocked.py`:
  - `test_employee_at_base_cap_listed_per_day_with_day_spend`
  - `test_model_cap_reached_listed`
  - `test_below_cap_not_listed`

**test.sh after:** unchanged.

### Step 4.3: Alerts: over-allocation breach and concurrency at the key limit
**Spec:** FR-V2 ("alerts on any breach"), FR-R3; §5.1 Scheduler (scheduled reports); FR-N1 and FR-N2 conventions; NFR7; D6.

**Files:**
- `app/services/notifications.py`: `KINDS` gains `overallocation` (critical) and `concurrency_at_limit` (non-critical).
- `app/services/overallocation.py`: `scheduled_check` notifies.
- `app/scheduler/jobs.py` and `app/scheduler/loop.py`: `run_report_alerts(session_factory, client, clock)`, daily, `scheduler_report_alerts_interval_s = 86400`, under the existing job-lock pattern.
- `app/config.py`, `app/metrics.py`: `wm_concurrency_peak{scope="global"}`, `wm_blocked_employees`.
- `frontend/src/i18n/he.ts`: `he.notifications.kinds`.

**Build:**
- **Over-allocation alert.** Each finding notifies platform admins and the reps of its department.
  - Kind `overallocation`, dedupe key `overalloc:<level>:<entity>:<model>:<YYYY-MM-DD>` through `uq_notification_dedupe`.
  - It is critical, so it cannot be muted.
- **Concurrency alert.** `run_report_alerts` reads yesterday's peaks. A member whose peak reached their key `max_parallel` triggers one `concurrency_at_limit` notification per day to admins, carrying the count and the top members. This is pilot input for D6.
- **Metrics.** Both gauges are updated by the same job.
- No migration: notification kinds are text.

**Tests:**
- `tests/scheduler/test_report_alerts_job.py`:
  - `test_overallocation_alert_to_admins_and_rep_once_per_day`
  - `test_concurrency_at_limit_alert_to_admins`
  - `test_run_twice_no_duplicates`
- `tests/api/test_notifications.py::test_mute_overallocation_critical_422`

**test.sh after:** unchanged.

### Step 4.4: UI: Reports page shell, Allocation and Over-allocation tabs (small UI step)
**Spec:** §12.2 reports row; FR-R1, FR-R3; §12.1 (period selector, past read-only, freshness).

**Files:**
- `frontend/src/pages/reports/{ReportsPage,Allocation,Overallocation}.tsx` (new).
  - Route `/report/:kind/:id/:tab`, where `kind` is `me|group|department|global`.
  - Tabs are shown from the scope: Allocation for all kinds; Over-allocation for department and global.
- `frontend/src/routes.tsx`.
- `frontend/src/scopes.ts`: a "דוחות" link per selected scope.
- The shell layout file.
- `frontend/src/api/queries.ts`: `useAllocationReport`, `useOverallocationReport`.
- `frontend/src/i18n/he.ts`: `he.reports`.
- `frontend/e2e/reports.spec.ts` (new).
- Reuse `WalletCard`, `ModelTable`, `StateBadge`, `PeriodSelector`.

**Tests:**
- Playwright: `Rep opens the department allocation report with group and model rows, and a past period shows that period's values`.

**test.sh after:** unchanged. One Playwright test is added.

### Step 4.5: UI: usage charts at every level (small UI step)
**Spec:** §12.3 usage chart (daily, stacked by model, cap line); §12.5.2 and §12.5.4 overviews; FR-R1; §16 Phase 4 (usage charts at every level).

**Files:**
- `frontend/src/components/UsageChart.tsx`: accept a generic `series` plus `cap`, using the existing offline chart library. `MyWallet` keeps working.
- `frontend/src/pages/reports/Usage.tsx` (new tab): by model or by member, plus granularity. The by-member toggle shows only when allowed.
- `frontend/src/pages/group/Overview.tsx` and `frontend/src/pages/department/Overview.tsx`: add the chart.
- `frontend/src/api/queries.ts`: `useUsageReport`.
- `frontend/e2e/reports.spec.ts`.

**Tests:**
- Vitest `UsageChart.test.tsx`: `stacks series by key and draws the cap line`.
- Playwright: `Group head sees the group usage chart stacked by model and switches to by member; a viewer has no by-member option`.

**test.sh after:** unchanged. One Playwright test is added.

### Step 4.6: UI: Grants-by-project and Blocked tabs (small UI step)
**Spec:** FR-R2, FR-R5; §12.2 (PMO: own department grants).

**Files:**
- `frontend/src/pages/reports/{Grants,Blocked}.tsx` (new tabs).
- `frontend/src/api/queries.ts`: `useGrantsReport`, `useBlockedReport`.
- `frontend/src/i18n/he.ts`, `frontend/e2e/reports.spec.ts`.

**Tests:**
- Playwright: `PMO sees grant totals by project; the representative sees blocked employees per day`.

**test.sh after:** unchanged. One Playwright test is added.

### Step 4.7: UI: Concurrency peaks tab; Phase 4 wrap-up (small UI step)
**Spec:** FR-R4; D6; §16 Phase 4 exit.

**Files:**
- `frontend/src/pages/reports/Concurrency.tsx` (new): a daily global peak line and a top-employees table with `max_parallel`.
- `frontend/src/api/queries.ts`: `useConcurrencyPeaks`.
- `frontend/e2e/reports.spec.ts`: seeds overlapping SpendLogs through `fakeSpend`.
- `PROGRESS.md`, `PRD.md`.

**Tests:**
- Playwright: `Admin sees global and per-employee concurrency peaks from SpendLogs overlaps`.

**Wrap-up:**
- All tests in the mapping below exist and pass, and `test_repo_hygiene.py` passes.
- Record the run time against the budget (Phase 3 plus Phase 4: pytest growth at most about 90 s; 10 Playwright tests) and Next = Phase 0 (real environment).

**test.sh after:** unchanged. Run it fully and record the time.

### Phase 4 acceptance mapping

| Criterion | Step | Test(s) |
|---|---|---|
| FR-R1 allocation vs spend, department / group / employee, per period, per model | 4.1, 4.4 | `test_reports_allocation.py::test_department_rows_groups_pmo_reserve_and_models`, `::test_group_rows_members_with_shares_and_model_caps`, `::test_employee_level_self_and_head_only`, `::test_past_period_uses_that_period_snapshot`; `reports.spec.ts` "Rep opens…" |
| FR-R1 usage history | 4.2, 4.5 | `test_reports_usage.py::*`; `reports.spec.ts` "Group head sees the group usage chart…" |
| FR-R2 grants by project | 4.1, 4.6 | `test_reports_grants.py::*`; `reports.spec.ts` "PMO sees grant totals…" |
| FR-R3 over-allocation findings | Phase 1 Step 9, 4.4 | `test_overallocation.py::*` (existing); `reports.spec.ts` (Over-allocation tab) |
| FR-R4 concurrency peaks per employee and global from SpendLogs overlaps | 4.2, 4.7 | `test_readonly_peaks.py::*`; `test_reports_peaks.py::test_peaks_per_employee_and_global_from_spendlogs`; `reports.spec.ts` "Admin sees global and per-employee…" |
| FR-R5 blocked spend per day | 4.2, 4.6 | `test_reports_blocked.py::*`; `reports.spec.ts` "…blocked employees per day" |
| Alerts: over-allocation breach (FR-V2) | 4.3 | `test_report_alerts_job.py::test_overallocation_alert_to_admins_and_rep_once_per_day`, `::test_run_twice_no_duplicates`; `test_notifications.py::test_mute_overallocation_critical_422` |
| Alerts: concurrency at the key limit (D6) | 4.3 | `test_report_alerts_job.py::test_concurrency_at_limit_alert_to_admins` |
| Usage charts at every level | 4.5 | `UsageChart.test.tsx`; `reports.spec.ts` "Group head sees…"; `wallet.spec.ts` (existing) |
| Report visibility (§12.2, D11) | 4.1, 4.2 | `test_reports_allocation.py::test_viewer_group_level_403_department_ok`; `test_reports_usage.py::test_by_member_viewer_403`; `test_reports_grants.py::test_pmo_other_department_hidden` |
| NFR10 hygiene | all | `test_repo_hygiene.py` |

---

## 3. Phase 1 acceptance mapping (§16 exit criteria)

| Criterion | Step | Test(s) |
|---|---|---|
| FR-D1 budget | 5 | `tests/api/test_department_budget.py::test_put_budget_pending_then_applied` |
| FR-D2 split | 6, 13 | `test_split.py::test_split_over_budget_422_with_numbers`, `::test_split_within_budget_pending_then_team_max_budget_applied`; e2e `department.spec.ts` |
| FR-D3 pots vs allowance | 6 | `test_model_pots.py::test_pots_exceed_allowance_422`, `::test_pot_written_to_team_model_max_budget` |
| FR-D4 group tpm | 6 | `test_group_limits.py::test_team_tpm_and_metadata_model_tpm` |
| FR-D5 group heads' values | 7 | `test_personal_values.py::test_rep_sets_group_head_values`, `::test_cohead_scope_forbidden` |
| FR-G1 base caps | 7, 12 | `test_member_caps.py::test_caps_over_base_422_with_numbers`, `::test_overalloc_factor_allows_overbooking` |
| FR-G2 member defaults | 7 | `test_member_defaults.py::test_team_member_budget_fields_written` |
| FR-G3 model caps vs pot | 7 | `test_member_model_caps.py::test_caps_vs_pot_422` |
| FR-G4 limits in policy | 7 | `test_member_limits.py::test_out_of_policy_422`, `::test_admin_above_max_ok` |
| FR-G5 self-allocation | 7 | `test_member_caps.py::test_group_head_own_row_self_allocation_forbidden`, `test_personal_values.py::test_admin_cannot_set_own_values` |
| FR-E1 / E2 / E3 | 8, 11 | `test_me_wallet.py::test_fields_fr_e1`, `::test_binding_constraint_group_exhausted`, `::test_owner_names_from_directory`; e2e `wallet.spec.ts` |
| FR-V1 locks | 6, 7 | `test_concurrency_locks.py::test_concurrent_splits_one_rejected`, `test_member_caps.py::test_concurrent_bulk_caps_one_rejected`, `test_db_lock.py` |
| FR-V2 live report | 9 | `test_overallocation.py::test_live_team_overallocation_flagged`, `::test_reserve_adjusted_department_check`, `::test_wm_exact_checks` |
| FR-A1 audit | 5, 9 | `test_write_path.py::test_audit_row_has_who_role_before_after_reason_request_id`, `test_audit.py::test_audit_includes_litellm_call_status` |
| A2 | 3, 9 | `tests/contract/test_a2_team_cap.py::test_a2_litellm_blocks_at_team_cap[fake]` (`[real]` with env), `tests/e2e_backend/test_a2_wm.py::test_a2_bypass_then_report_flags` |
| A3 | 3, 9 | `tests/contract/test_a3_key_concurrency.py::test_a3_key_parallel_limit[fake]` (`[real]`), `tests/e2e_backend/test_a3_wm.py::test_a3_limits_set_via_wm_enforced` |
| A5 | 3, 9 | `tests/contract/test_a5_model_pot.py::test_a5_member_caps_within_pot[fake]`, `::test_a5_pot_binds_together[fake]` (`[real]`), `tests/e2e_backend/test_a5_wm.py::test_a5_pots_and_caps_via_wm` |
| U1 | 12 | `group.spec.ts`: "U1 caps above base…", "U1 race returns PARENT_OVERALLOCATED…" |
| U2 | 12 | `group.spec.ts`: "U2 own row read-only…", "U2 direct API self-target…" (grant half in Phase 2) |
| U3 | 5, 12 | `test_outbox.py::test_outage_keeps_pending_then_applies_after_recovery`; `group.spec.ts`: "U3 save during LiteLLM outage…" |
| U4 | 8, 11 | `test_me_wallet.py::test_binding_constraint_group_exhausted`; `wallet.spec.ts`: "U4 group exhausted…" |
| U6 | 10, 13 | `shell.spec.ts`: "U6 switcher lists…"; `roles.spec.ts`: "U6 permissions follow selected scope" |
| U7 | 13 | `viewer.spec.ts`: "U7 viewer sees no action buttons and write endpoints return 403" |
| U8 | 10, 13 | `shell.spec.ts`: "U8 html is rtl…"; `rtl.spec.ts`: "U8 tables mirror…" |

## 4. Later phases (outline only)

**Phase 2: Grants and expiry (§7.3, §7.7, §7.9; A1, A9 with a share, A11; U5)**
- Backend:
  - `POST/PATCH /grants`, `/grants/{id}/close`, shares CRUD, `GET /departments/{id}/pmo-pool` and `/projects/{ref}/summary`. They use `lock(PMO)` and the codes `PMO_POOL_INSUFFICIENT`, `SHARE_EXCEEDS_GRANT`, `SHARE_EXPIRY_AFTER_GRANT`, `GRANT_BELOW_SHARED`.
  - FR-P4 temp-increase recomputation (both fields together), and the attribution formula §7.3.1 (unit tests).
  - Scheduler process: expiry, close, return.
  - Reconciliation FR-RC1/2, reusing `outbox/desired.py` against `/team/info`, `/organization/info` and `/key/list`, with a drift log. The JWT mapping active flag and blocked keys follow employee status.
  - Fill in `lifecycle.close_member_shares` (reason `move` | `manual`) for move and delete (FR-P6). A9 with an active share.
  - Notifications FR-N plus `/me/notifications*`: new pending placement (rep, or admins for unknown department), employee moved in (new group head), outbox failures.
  - Extend the fake with the matching behavior.
- UI: PMO screens (§12.5.3), the Group Grants tab, the Department Group heads tab, notifications. Complete the grant half of U2, plus U5.

**Phase 3: Directory, service keys, production (FR-K)**
- `ldap3` implementation of `Directory` (one-time onboarding lookup, owner names, login roles), with `DirectoryUnavailable` on LDAP errors. It needs a contract test against a test LDAP. There is no sync, dry-run, safety gate, role mirroring or leaver grace period (owner decision 20/10/2026).
- `/service-keys` with the secret shown once and only the hash stored; production organizations and systems.
- `PUT /policies`, Redis status in `/status`, and group creation by the platform admin (`/team/new` at zero budget, §9.3).
- UI: Department Production tab, Group Service keys tab, the rest of the Platform screens (§12.5.5). Pending placement there is already done in Phase 1b.

**Phase 4: Reports (FR-R)**
- `/reports/allocation`, `/reports/usage`, `/reports/grants`, `/reports/concurrency-peaks` (SpendLogs overlap), blocked-spend report, alerts.
- Reports screens and usage charts at every level.

Phase 0 (§16: LiteLLM configuration, V1–V7, A1–A8 and A10) needs the real environment. The `[real]` contract tests from Steps 3 and 9 are its starting point; add `[real]` variants of A12, A14 and A15 for V6/V7. A6 can already run on the fake.

## 5. Questions for Maor

**None block Phase 1.** The builder copies the decisions below into PROGRESS.md under "Questions for owner", applies the safest option as stated, and keeps going:
1. **`SCOPE_FORBIDDEN` vs U7.** No role on the URL scope returns 403 `FORBIDDEN` (U7, NFR9). An out-of-scope target in the payload returns 422 `SCOPE_FORBIDDEN` (§11.2).
2. **Default model caps for new members (FR-G2).** §8.2 has no column, so they are stored in `policy` key `member_defaults.group.<id>`.
3. **Missing parent ceiling.** A missing department allowance or group pot counts as 0 for FR-D3 and FR-G3.
4. **Lowering a parent below its children** (budget, base, pot) is accepted. The §12.4 "Over-allocated" warning shows instead.
5. **Department model allowances** are set by the platform admin. §12.2 does not grant this to the representative.
6. **Reason transport.** The reason travels in the `X-Change-Reason` header, so the bare-array bodies stay as in §11.1. Idempotency replay uses `audit_log.request_id`, so no extra table is needed.
7. **D11 for viewers and PMO.** They get group and department aggregates only; `/groups/{id}/members` returns 403 for them.
8. **Team `budget_duration`.** It is listed in §10 but not in §8.1; it is set to the department period so team spend resets. Calendar-month alignment of `"1mo"` is re-checked in Phase 0 (V4).
9. **Organization `models`.** It is not sent in Phase 1, because sending it would restrict the models a department can use.
10. **D6 concurrency and rpm policy.** Placeholder values (concurrency 1/4/8, rpm 1/120/600) until pilot measurements exist.

Phase 1b questions (apply the default and keep going):

11. **Onboarding portal authentication (D14).** Two ADFS JWTs are checked against the ADFS JWKS:
    - the portal's client-credentials token, with an allow-listed client id (`ONBOARDING_CLIENT_IDS`) and the WM audience;
    - the user's ADFS JWT, which supplies `upn`, with allow-listed audiences (`ONBOARDING_USER_AUDIENCES`; empty disables onboarding).

    Dev uses HS256 test-signed tokens only with `DEV_AUTH=1`. Please confirm the client id claim (`appid` vs `azp`) and the user token audience ADFS issues to the portal.
12. **Key delivery (D15).** The personal key secret is never returned. Claude Code authenticates with the user's ADFS JWT, which LiteLLM maps by upn, so nothing can be lost and there is no "rotate key" action. If the portal must hand out a static key instead, we add "show the secret once" plus an explicit rotate action, which reverses NFR5.
13. **Delete order (D16).** The order is: delete the mapping, delete the key, remove the membership, delete the user. Your draft removed the membership first, which would leave the key on a team without membership.
14. **Department representative kick and restore (D17).** Yes, for members and group heads of their department; not for other representatives.
15. **Over-allocation from default caps (D18).** Onboarding, place and move add the full default cap even if the group becomes over-allocated. The group then shows the warning; the action is not rejected.
16. **Unknown-department pending users (D19)** are handled by the platform admin only. **A deleted user who calls onboarding again (D20)** gets `blocked`; only an admin can re-place them.
17. **Move details (D21).** Personal model caps are cleared and concurrency/rpm kept. Moving a group head does not change their AD role.
18. **JWT mapping inactive flag (D22, V6).** If LiteLLM has no such flag, kick deletes the mapping and restore recreates it.
19. **In-progress guard (D23).** Move, delete and place return 409 `LIFECYCLE_IN_PROGRESS` while earlier changes are unapplied. Kick and restore are always queued in order.
20. **Public repository (D25, NFR10).** Placeholders only (`corp.example`, `litellm.corp.example`). `.env` stays gitignored, tests generate dummy secrets, and a hygiene test scans tracked files. Before publishing, the owner should scan the full git history (not only HEAD) and provide internal names to block through a gitignored `.public-denylist`.
21. **F5/F6 wording.** F6 (a manager's own cap, DR9) is unrelated to sync, so it is kept. F5 (move) and F7 (sync review) are replaced, and F8–F10 are added.

Phase 2 questions (apply the default and keep going):

22. **Manual share close.** When a group head closes a share, the unused amount goes back to the **PMO pool**, the same as expiry and move (FR-P6), not back into the grant's unshared remainder. This fails toward less group budget. A head who only wants to change the amount can use PATCH.
23. **Clearing the temporary increase.** With no active shares the plan sends `temp_budget_increase: 0, temp_budget_expiry: null` together. This needs re-checking on the real LiteLLM version in Phase 0.
24. **Notification read and preference writes** are exempt from the reason header and audit, because they are personal UI state (like `/auth/logout`). CSRF still applies. §11 literally says every write needs a reason. Should those two endpoints require one anyway?
25. **Missing LiteLLM objects found by reconciliation** (no membership, key or mapping) are reported and alerted, never recreated automatically. This avoids duplicate keys.
26. **Pending (future-dated) grants reserve PMO pool money** from creation, so two future grants cannot overbook the pool.
27. **F1 "group heads are notified of split changes"** is not an FR-N1 trigger and is not built in Phase 2. Should it be added?

Phase 3/4 questions (apply the default and keep going):

28. **Where production data is stored (FR-K1, FR-K2, D8).** §8.2 has no place for it. **Default:** three new columns on `department` plus a new `production_system` table. Production objects are not reconciled.
29. **Service key creation needs LiteLLM to be up.** The secret can only be shown once if LiteLLM is called during the request; queuing the call would mean storing the secret. **Default:** if LiteLLM is down, return 503 `LITELLM_UNAVAILABLE` (a new error code) and create no usable key. Rotation creates a new key and deletes the old one, without `/key/regenerate`.
30. **Group service key budgets count toward the group's allocation (FR-K3).** **Default:** member caps plus active key budgets must stay within base × factor. This applies to both the FR-G1 check and §12.3 "allocated", so it is rejected with `PARENT_OVERALLOCATED`. System keys must fit within the system budget.
31. **Endpoints not in §11.1.** **Default:** add `POST /departments`, `POST /departments/{id}/groups`, `GET|PUT /role-bindings`, `GET|PUT /production/departments/{id}`, `GET /service-keys` and `GET /reports/blocked`. There is no PATCH for system budgets or service key limits; only rotate and revoke.
32. **LDAP down outside onboarding.** **Default (fail closed):**
33. **FR-R5 compares spend with current caps, not the caps in force on each day.** **Default:** use current caps (base + active shares, model caps). Historical caps from audit would come later.
34. **Over-allocation factors in Policies (§12.5.5).** **Default:** `PUT /policies` sets `grp.overalloc_factor` per group, within [1.0, 2.0], admin only.
