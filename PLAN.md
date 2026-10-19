# PLAN.md: Wallet Manager for LiteLLM

> Replaces the old PLAN.md. Source of truth: **SPEC.md v0.2** and **litellm_wallets_erd.mmd**. Every step lists the spec sections it covers (§). If this plan and the spec disagree, the spec wins: record the conflict in PROGRESS.md under "Questions for owner" and pick the safest option.

## 0. Context

The Wallet Manager (WM) is a control plane in front of a LiteLLM Enterprise proxy (§5). It lets people allocate department budgets, group budgets, personal caps, model pots and concurrency limits down the org tree. It checks every allocation against its parent (LiteLLM does not check sums, R14–R16), stores the decision, and applies it to LiteLLM through an outbox (§5.3). It also shows every person their wallet (§12).

The real target (airgapped OpenShift, LiteLLM Enterprise, ADFS, AD) is not available here, so locally:
- **Fake LiteLLM** (`fake_litellm/`, FastAPI, port 4102). It implements only the §10 endpoints Phase 1 uses, plus the §3 runtime behavior the tests depend on: R1, R2, R5, R6, R7, R10, R11. It has test-only controls. It keeps its state in Postgres tables named and shaped after the ERD, so the WM read-only reader runs the §11.3 SQL as written.
- **Contract tests** (§15.2) run against the fake every time. They run against a real LiteLLM only when `LITELLM_BASE_URL` is set; otherwise they are skipped.
- **PostgreSQL via `pgembed`** for dev and tests. Production uses `DATABASE_URL` and `LITELLM_DATABASE_URL` (a read-only role, NFR6).
- **Auth**: real ADFS OIDC through the BFF, by configuration. `DEV_AUTH=1` adds a seeded-user login. AD sync is Phase 3; seeding uses a fixture.
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

**Phase 2: Grants and expiry (§7.3, §7.7, §7.9; A1, A9 manual move, A11; U5)**
- Backend:
  - `POST/PATCH /grants`, `/grants/{id}/close`, shares CRUD, `GET /departments/{id}/pmo-pool`, `/projects/{ref}/summary`, with `lock(PMO)` and codes `PMO_POOL_INSUFFICIENT`, `SHARE_EXCEEDS_GRANT`, `SHARE_EXPIRY_AFTER_GRANT`, `GRANT_BELOW_SHARED`.
  - FR-P4 temp-increase recomputation (both fields together), and the attribution formula §7.3.1 (unit tests).
  - Scheduler process: expiry, close, return.
  - Reconciliation FR-RC1/2, reusing `outbox/desired.py` against `/team/info`, `/organization/info` and `/key/list`, with a drift log.
  - Notifications FR-N plus `/me/notifications*`.
  - Manual move service (§9.3 order).
  - Extend the fake with the matching behavior.
- UI: PMO screens (§12.5.3), the Group Grants tab, the Department Group heads tab, notifications. Complete the grant half of U2, plus U5.

**Phase 3: AD sync, service keys, production (§9, FR-K; A9, A12)**
- `ldap3` directory, sync runs with dry-run and safety gate (`sync_run`, `sync_action`), approve and resolve endpoints, `/departments/{id}/review-items`, role mirroring (`org_admin`, team `admin`), leaver block and grace period.
- `/service-keys` with the secret shown once and only the hash stored; production organizations and systems.
- `PUT /policies`, Redis status in `/status`, service-to-service ADFS JWT for the sync service.
- UI: Department Review and Production tabs, Group Service keys tab, Platform screens (§12.5.5).

**Phase 4: Reports (FR-R)**
- `/reports/allocation`, `/reports/usage`, `/reports/grants`, `/reports/concurrency-peaks` (SpendLogs overlap), blocked-spend report, alerts.
- Reports screens and usage charts at every level.

Phase 0 (§16: LiteLLM configuration, V1–V5, A1–A8 and A10) needs the real environment. The `[real]` contract tests from Steps 3 and 9 are its starting point, and A6 can already run on the fake.

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
