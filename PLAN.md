# PLAN.md: Wallet Manager for LiteLLM, Phase 1

## Context
SPEC.md v0.2 arrived truncated: sections 3–15.2 are missing, including LiteLLM behavior, domain rules, architecture, FR detail, the LiteLLM write API (10), the UI API (11), screens (12) and NFRs. This plan builds the **Phase 1 vertical slice** from section 16:
- Backend: data model, LiteLLM client, outbox, departments, groups, members, limits, validation, self-allocation rule, audit, BFF login.
- UI: Shell, scope switcher, My wallet, Group (Overview/Members/History), Department (Overview/Split).
- Exit criteria: FR-D/G/E/V/A; A2, A3, A5; U1–U4, U6–U8.

Where the missing text would have decided something, the plan uses the builder defaults: fake LiteLLM, pgembed, DEV_AUTH plus real OIDC, and ports 4100/4101/4102. Each such choice is logged in PROGRESS.md under "Questions for owner".

## Ground rules (apply to every step)
- **TDD**: write the failing test first, then the least code that makes it pass. Commit at least once per step (`git commit`, push to origin only). Never edit `.claude/hooks/*` or `.claude/settings.json`.
- **Python 3.13 locally, 3.12 compatible**: `requires-python = ">=3.12"` and ruff `target-version = "py312"`. SPEC asks for 3.12, so this is a known skew.
- **`./scripts/test.sh` is the single gate.** The Stop hook gives it 540 s, so the full suite must stay under ~5 min. It grows step by step:
  1. `uv sync --frozen`
  2. `uv run ruff check . && uv run ruff format --check .`
  3. `uv run pytest -q`
  4. (from step 14) frontend lint, typecheck and vitest
  5. (from step 17) Playwright
- **Money**: `Numeric(14,4)` in the DB, `Decimal` in Python, JSON number with 2 dp in the API. Budget units = LiteLLM spend units = credits.
- **Error envelope** (all 4xx): `{"error": {"code": str, "message": str, "details": dict}}`.

| Code | HTTP | Meaning |
|---|---|---|
| `PARENT_OVERALLOCATED` | 409 | Children would exceed the parent. Details: `{parent_type, parent_id, limit, allocated_other, requested}` |
| `ALLOCATION_BELOW_CHILDREN` | 409 | A new limit is below what is already allocated under it |
| `SELF_ALLOCATION_FORBIDDEN` | 403 | Actor tried to change their own allocation |
| `POLICY_LIMIT_EXCEEDED` | 422 | Concurrency or limit outside the policy |
| `FORBIDDEN` | 403 | Role in the selected scope does not allow the action |
| `SCOPE_INVALID` | 403 | User does not hold the scope they sent |
| `NOT_FOUND` | 404 | |
| `VALIDATION_ERROR` | 422 | |

  Only `PARENT_OVERALLOCATED` and `SELF_ALLOCATION_FORBIDDEN` are named in the recovered SPEC. The other codes are assumptions, logged in PROGRESS.md.
- **D5 over-allocation factor** = `settings.overallocation_factor` (default 1.0).
- **D6/D7 concurrency policy**: `CONCURRENCY_DEFAULT=4`, `CONCURRENCY_MIN=1`, `CONCURRENCY_MAX=16`. Group heads and department reps work within [min, max]; a platform admin may exceed max.
- **D1 budget period**: `budget_duration="1mo"` is sent to LiteLLM, with `BUDGET_TIMEZONE` as config.
- **One employee = one membership = one personal key** in Phase 1. `memberships.user_id` is unique. Movers come with AD sync in Phase 3.
- **Role mirroring to LiteLLM (org_admin / team admin) is Phase 3.** Phase 1 roles live only in our DB; LiteLLM team members are written with `role="user"`.

---

## Steps

### Step 1: Backend scaffold, config, health, real test.sh
**Files:** `pyproject.toml`, `uv.lock`, `app/__init__.py`, `app/main.py`, `app/config.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_health.py`, `scripts/test.sh` (rewrite), `.env.example`, `PROGRESS.md`.

**Build:**
- Dependencies: fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg, alembic, pydantic>=2, pydantic-settings, httpx, authlib, itsdangerous, pgembed.
- Dev dependencies: pytest, pytest-asyncio (`asyncio_mode=auto`), respx, ruff.
- `app/config.py`: `class Settings(BaseSettings)` with these fields:
  - `env`, `database_url: str | None`, `pgembed_dir: Path | None`
  - `litellm_base_url`, `litellm_master_key`
  - `dev_auth: bool`, `session_secret`
  - `oidc_issuer`, `oidc_client_id`, `oidc_client_secret`, `oidc_redirect_uri`, `oidc_user_claim="upn"`
  - `outbox_interval_s=2.0`, `overallocation_factor=1.0`
  - `concurrency_default/min/max`, `budget_timezone="Asia/Jerusalem"`
  - `get_settings()` is cached.
  - Startup refuses `dev_auth=True` when `env == "production"`.
- `app/main.py`: `create_app(settings) -> FastAPI` exposes `GET /healthz -> {"status": "ok"}`, and `app = create_app(get_settings())`.
- `scripts/test.sh`: `set -euo pipefail`, then uv sync, ruff check, ruff format --check, pytest.
- `PROGRESS.md` sections: Done / Next / Blockers / Questions for owner. Seed the questions list with every assumption in this plan.

**Test:** `tests/test_health.py::test_healthz` (httpx `ASGITransport`), and `test_dev_auth_refused_in_production`.

### Step 2: Database plumbing with pgembed and Alembic
**Files:** `app/db.py`, `alembic.ini`, `migrations/env.py` (async), `migrations/script.py.mako`, `scripts/devdb.py`, `tests/conftest.py`, `tests/test_db.py`.

**Build:**
- `app/db.py`:
  - `resolve_database_url(settings) -> str`. Uses `DATABASE_URL` if set. Otherwise it starts or attaches to pgembed in `settings.pgembed_dir` (default `./pgdata`, which is gitignored), creates the `wallet` DB if missing, and returns `postgresql+asyncpg://postgres@/wallet?host=<socketdir>`.
  - Before writing this, check the pgembed API with `uv run python -c "import pgembed; help(pgembed)"`.
  - Also provide `make_engine(url)`, `make_sessionmaker(engine)`, `Base(DeclarativeBase)`, and a FastAPI dependency `get_session()`.
- `migrations/env.py` uses the same resolver, so `uv run alembic upgrade head` works in dev.
- Test fixtures in `conftest.py`:
  - One pgembed server per session in `tmp_path_factory`, with a fresh DB, running `alembic upgrade head`.
  - An autouse `_truncate` fixture that runs `TRUNCATE ... RESTART IDENTITY CASCADE` on every table after each test. Truncate rather than rollback, so tests can do real concurrent commits.
  - `client` fixture: httpx AsyncClient over `create_app(test_settings)`.

**Test:** `tests/test_db.py::test_select_one`, `::test_alembic_upgrade_downgrade_roundtrip`.

### Step 3: Phase 1 data model and migration
**Files:** `app/models.py`, `migrations/versions/0001_phase1.py`, `tests/test_models.py`.

**Tables** (UUID PKs, `created_at` / `updated_at`):

| Table | Columns | Constraints |
|---|---|---|
| `users` | `upn`, `display_name`, `email`, `active`, `litellm_user_id`, `litellm_key_token` | `upn` unique. `litellm_key_token` is LiteLLM's token hash only, never the raw `sk-` key |
| `departments` | `name`, `budget`, `pmo_pool`, `litellm_org_id` | budget ≥ 0, pmo_pool ≥ 0 |
| `groups` | `department_id` FK, `name`, `is_staff`, `base_budget`, `litellm_team_id` | base_budget ≥ 0; partial unique index: one `is_staff` group per department (D13) |
| `model_pots` | `group_id`, `model`, `max_budget` | PK (group_id, model) |
| `memberships` | `group_id`, `user_id` (unique), `personal_cap`, `max_parallel_requests`, `tpm_limit` nullable, `rpm_limit` nullable | |
| `member_model_caps` | `membership_id`, `model`, `cap` | PK (membership_id, model) |
| `role_assignments` | `user_id`, `role`, `scope_type`, `scope_id` nullable | role enum: `platform_admin`, `department_rep`, `group_head`, `viewer`; scope_type enum: `platform`, `department`, `group`; unique (user_id, role, scope_type, scope_id) |
| `audit_log` | `id` bigserial, `at`, `actor_user_id`, `scope_type`, `scope_id`, `action`, `entity_type`, `entity_id`, `before` jsonb, `after` jsonb | index (scope_type, scope_id, at desc) |
| `outbox` | `id` bigserial, `target_type`, `target_id`, `status`, `attempts`, `next_attempt_at`, `last_error`, `sent_payload` jsonb, `created_at`, `applied_at` | target_type enum: `org`, `team`, `user`, `team_member`, `key`; status enum: `pending`, `applied`; index (status, next_attempt_at) |

**Test:** `tests/test_models.py`:
- `test_membership_user_unique`
- `test_one_staff_group_per_department`
- `test_negative_budget_rejected`
- `test_cascade_group_delete_removes_pots_and_caps`

### Step 4: Fake LiteLLM management server
**Files:** `fake_litellm/__init__.py`, `fake_litellm/main.py`, `fake_litellm/state.py`, `tests/fake_litellm/test_fake.py`.

**Build:**
- An in-memory FastAPI app. Every endpoint requires `Authorization: Bearer <FAKE_LITELLM_MASTER_KEY>` (default `sk-fake-master`).
- The response shapes copy LiteLLM's real ones; these are the only endpoints Phase 1 uses.

| Endpoint | Fields / behavior |
|---|---|
| `POST /organization/new` | `{organization_alias, max_budget, budget_duration, models}` → `{organization_id, ...}` |
| `PATCH /organization/update` | |
| `GET /organization/info?organization_id=` | Returns `spend` |
| `POST /team/new` | `{team_alias, organization_id, max_budget, budget_duration, model_max_budget}` → `{team_id, ...}` |
| `POST /team/update` | |
| `GET /team/info?team_id=` | `{team_id, team_info:{spend, max_budget, model_max_budget}, team_memberships:[{user_id, spend, litellm_budget_table:{max_budget}}]}` |
| `POST /user/new` | `{user_id, user_email}` |
| `GET /user/info` | |
| `POST /team/member_add` | `{team_id, member:{user_id, role}, max_budget_in_team}` |
| `POST /team/member_update` | |
| `POST /team/member_delete` | |
| `POST /key/generate` | `{user_id, team_id, key_alias, max_parallel_requests, tpm_limit, rpm_limit, model_max_budget}` → `{key, token}` |
| `POST /key/update` | Takes `key` = token |
| `GET /key/info?key=` | |
| `GET /model/info` | Models from `FAKE_LITELLM_MODELS`, default `claude-sonnet-4,claude-haiku-4,qwen3-coder` |

- Test-only control routes:
  - `POST /_fake/reset`
  - `POST /_fake/outage {down: bool}`: while down, every management route returns 503.
  - `POST /_fake/spend {team_id, user_id?, spend}`
  - `GET /_fake/state`
- The fake runs as `uv run uvicorn fake_litellm.main:app --host 0.0.0.0 --port 4102` (already in CLAUDE.md).

**Test:** `tests/fake_litellm/test_fake.py`: auth required; create org → team → user → member → key flow; outage returns 503; `/_fake/spend` shows up in `/team/info`.

### Step 5: LiteLLM client module (NFR3 boundary) and contract tests
**Files:** `app/litellm_client/__init__.py` (exports only), `app/litellm_client/models.py`, `app/litellm_client/client.py`, `app/litellm_client/errors.py`, `tests/litellm_client/test_client.py`, `tests/contract/test_litellm_contract.py`.

**Build:**
- `class LiteLLMClient` wraps `httpx.AsyncClient(base_url, headers=Bearer, timeout=5)`. It is injectable via `transport=`. Methods:
  - `create_org` / `update_org` / `get_org`
  - `create_team` / `update_team` / `get_team`, where `get_team` returns `TeamInfo` with `spend` and `memberships: list[TeamMembershipInfo]`
  - `create_user`
  - `add_team_member` / `update_team_member`
  - `generate_key` / `update_key`
  - `list_models`
- Requests and responses are Pydantic models in `models.py`, using `extra="ignore"` so newer LiteLLM fields are tolerated.
- Errors: `LiteLLMUnavailable` for connection errors, timeouts and 5xx; `LiteLLMError(status, body)` for 4xx.
- **No other module imports httpx for LiteLLM.** A ruff `flake8-tidy-imports` banned-api rule enforces this, if it can be configured cleanly; otherwise a grep test.

**Test:**
- `tests/litellm_client/test_client.py` runs against the fake via `httpx.ASGITransport(app=fake_app)`: each method round-trips; outage raises `LiteLLMUnavailable`; a bad key raises `LiteLLMError(401)`.
- `tests/contract/test_litellm_contract.py` runs the same flow against a real proxy. It is marked `pytestmark = skipif(not os.getenv("LITELLM_BASE_URL"))`, and org tests also need `LITELLM_ENTERPRISE=1`.

### Step 6: Outbox (desired-state sync) and worker
**Files:** `app/outbox.py`, `app/sync.py` (payload builders), `app/main.py` (lifespan worker), `tests/test_outbox.py`.

**Design:**
- An outbox row says "push the current DB state of (target_type, target_id)". It holds no payload snapshot, so the latest state always wins and retries are idempotent.

**Functions:**
- `enqueue(session, target_type, target_id)` adds a row. Repeated calls coalesce: if a pending row already exists for the target, nothing new is added.
- `drain_once(sessionmaker, client) -> DrainResult(applied, failed, deferred)` processes pending rows whose `next_attempt_at <= now`, in id order, with `FOR UPDATE SKIP LOCKED`:
  - It calls `sync.push(target_type, entity, client)`. If the LiteLLM id is null this is a create, and the returned id is written back. Otherwise it is an update.
  - Dependencies: a team needs its org id; a team member needs the team and user ids; a key needs the user and team ids. If a dependency has no id yet, the row is **deferred** with no attempt counted.
  - On `LiteLLMUnavailable`: `attempts += 1`, backoff `min(2**attempts, 300)` s, record `last_error`.
  - Store `sent_payload` on the row for debugging.
- `sync_status(session, target_type, ids) -> dict[id, "pending" | "applied"]` drives the UI badges.
- `GET /api/sync/status` → `{pending_count, last_error, litellm_reachable}`. Reachability comes from the most recent drain attempt and is held in an in-process holder.
- The lifespan task loops `drain_once` every `outbox_interval_s`. Setting the interval to `0` disables the loop; tests use that and call `drain_once` directly.

**Test:** `tests/test_outbox.py`:
- `test_create_then_update_writes_ids_back`
- `test_outage_keeps_pending_with_backoff`
- `test_recovery_applies` (prerequisite for U3)
- `test_dependency_deferred_until_parent_applied`
- `test_enqueue_coalesces`
- `test_latest_state_wins`

### Step 7: Session, DEV_AUTH, `/api/me`, scope authorization
**Files:** `app/auth/__init__.py`, `app/auth/session.py`, `app/auth/dev.py`, `app/auth/scopes.py`, `app/api/me.py`, `tests/auth/test_dev_auth.py`, `tests/auth/test_scopes.py`.

**Build:**
- Starlette `SessionMiddleware`: signed, HttpOnly, SameSite=Lax cookie that stores `user_id` only.
- `current_user` dependency: 401 if there is no session.
- CSRF: every non-GET `/api/*` request must carry header `X-CSRF: 1`, else 403. This works with SameSite and needs no token store.
- DEV_AUTH, only mounted when `dev_auth=True`:
  - `GET /auth/dev/users` → seeded users with their roles.
  - `POST /auth/dev/login {user_id}`
- `POST /auth/logout`
- `GET /api/me` → `{user:{id, display_name, upn}, scopes:[{key:"group:<id>", role, label}], has_wallet: bool}`. Every user with a membership also gets `self`.
- Scope selection: the selected scope arrives in header `X-Scope` (`platform` | `department:<id>` | `group:<id>` | `self`).
  - `selected_scope` dependency validates that the user holds that assignment (else `SCOPE_INVALID`).
  - `authorize(scope, action, resource)` is one pure function over this matrix:

| Role (scope) | Read | Write |
|---|---|---|
| platform_admin | all | all + admin endpoints |
| department_rep (department) | department + its groups | split, model pots, members of its groups |
| group_head (group) | group | model pots, members |
| viewer (department or group) | in scope | none → `FORBIDDEN` |

  - A resource counts as inside a scope when the group's `department_id` matches.

**Test:**
- `test_dev_login_sets_session`
- `test_dev_routes_absent_without_flag`
- `test_csrf_header_required`
- `test_me_lists_multiple_scopes` (prerequisite for U6)
- `test_scope_not_held_rejected`
- `test_authorize_matrix`: parametrized over role × action, including viewer → 403 (prerequisite for U7)
- `test_permissions_follow_selected_scope`: a user who is group_head of G1 and viewer of D is forbidden to write G1 when calling with `department:D`.

### Step 8: Real OIDC login (BFF)
**Files:** `app/auth/oidc.py`, `tests/auth/test_oidc.py`.

**Build:**
- Authlib Starlette client, registered from `oidc_issuer` discovery (`server_metadata_url`).
- `GET /auth/login`: authorization code flow with PKCE and state, redirecting to the IdP.
- `GET /auth/callback`: exchanges the code, validates the id_token (iss, aud, exp, nonce), reads `oidc_user_claim`, then looks up the active user by `upn`. Unknown or inactive users get 403 and a page saying "not provisioned". The callback sets the session and redirects to `/`.
- Routes are mounted only when `oidc_issuer` is set.
- Tokens are never stored or sent to the browser.

**Test:** `tests/auth/test_oidc.py` uses respx to mock discovery, JWKS (an RSA key generated in the test) and the token endpoint:
- `test_login_redirects_with_pkce`
- `test_callback_creates_session_for_known_user`
- `test_callback_rejects_unknown_user`
- `test_callback_rejects_bad_nonce`

### Step 9: Validation rules, audit writer, error envelope
**Files:** `app/errors.py`, `app/domain/__init__.py`, `app/domain/validation.py`, `app/audit.py`, `tests/domain/test_validation.py`, `tests/test_audit.py`.

**Build:**
- `class DomainError(Exception)` carries `code`, `http_status`, `details`. One exception handler turns it into the envelope; FastAPI's `RequestValidationError` is mapped to `VALIDATION_ERROR`.
- Pure validation functions (FR-V). Each takes `Decimal` values and returns `None` or raises:
  - `check_children_fit(parent_type, parent_id, limit, children: Mapping[id, Decimal], factor)` → `PARENT_OVERALLOCATED` with numbers. It is reused for department → (groups + PMO pool), group → member caps, group → model pots (each pot ≤ base), pot → member model caps, and member model cap ≤ personal cap.
  - `check_not_below_children(limit, allocated)` → `ALLOCATION_BELOW_CHILDREN`.
  - `check_not_self(actor_id, target_user_id)` → `SELF_ALLOCATION_FORBIDDEN`.
  - `check_concurrency(value, policy, actor_is_platform_admin)` → `POLICY_LIMIT_EXCEEDED`.
  - Reserve = `budget − Σgroups − pmo_pool` (derived, never stored).
- `audit.record(session, actor, scope, action, entity_type, entity_id, before, after)` writes in the same transaction as the change (FR-A).

**Test:**
- `tests/domain/test_validation.py`: table-driven cases for every rule, including the exact-boundary case (equal is allowed) and factor 1.0 vs 1.2.
- `tests/test_audit.py::test_record_in_same_txn_rolls_back_with_it`

### Step 10: Departments: admin create, Overview, Split (FR-D)
**Files:** `app/api/departments.py`, `app/api/admin.py`, `app/services/departments.py`, `app/schemas.py`, `tests/api/test_departments.py`.

**Endpoints:**
- `POST /api/admin/departments {name, budget}` creates the department and its staff group (D13) and enqueues `org` and `team`.
- `PATCH /api/admin/departments/{id} {budget}` runs `check_not_below_children`.
- `POST /api/admin/departments/{id}/groups {name}`
- `POST /api/admin/role-assignments {user_id, role, scope_type, scope_id}`
- `GET /api/departments/{id}` → `{id, name, budget, pmo_pool, reserve, allocated, spend|null, sync, groups:[{id, name, is_staff, base_budget, members_allocated, spend|null, sync}]}`. Spend is read through `client.get_org` / `get_team`; on `LiteLLMUnavailable` it is `null`.
- `PUT /api/departments/{id}/split {pmo_pool, groups:[{id, base_budget}]}`:
  - In one transaction, run `SELECT department FOR UPDATE` and then validate with the department→children rule, plus each group's `check_not_below_children` against its member caps.
  - Then write the values, add one audit row per changed group or pool, enqueue `team` for each changed group, and return the overview.

**Test:** `tests/api/test_departments.py`:
- `test_split_happy_path_enqueues_and_audits`
- `test_split_overallocated_409_with_numbers`
- `test_concurrent_splits_one_wins` (prerequisite for U1): `asyncio.gather` of two clients whose writes each fit alone but not together; exactly one gets 409.
- `test_split_below_member_caps_409`
- `test_viewer_cannot_split_403`
- `test_overview_spend_null_when_litellm_down`

### Step 11: Groups: Overview, model pots, History (FR-G part 1, FR-A read)
**Files:** `app/api/groups.py`, `app/services/groups.py`, `app/schemas.py`, `tests/api/test_groups.py`.

**Endpoints:**
- `GET /api/groups/{id}` → `{id, name, department:{id, name}, heads:[{id, display_name}], base_budget, members_allocated, unallocated, spend|null, model_pots:[{model, max_budget, members_allocated}], sync}`
- `PUT /api/groups/{id}/model-pots {pots:[{model, max_budget}]}`:
  - Each model must be in `client.list_models()`. The model list is cached for 60 s and falls back to the models already present in the DB when LiteLLM is down.
  - Each pot must fit the base budget, and must not drop below the member model caps under it.
  - Enqueues `team` (`model_max_budget`) and writes audit rows.
- `GET /api/groups/{id}/history?before=<audit_id>&limit=50` → audit rows for the group and its memberships, with actor display names.
- `GET /api/models`

**Test:** `tests/api/test_groups.py`:
- `test_overview_shape`
- `test_pot_over_base_409`
- `test_pot_unknown_model_422`
- `test_pot_change_enqueues_team`
- `test_history_lists_changes_newest_first`
- `test_viewer_read_ok_write_403`

### Step 12: Members, limits, personal keys (FR-G part 2, self-allocation rule)
**Files:** `app/api/members.py`, `app/services/members.py`, `app/sync.py`, `tests/api/test_members.py`.

**Endpoints:**
- `POST /api/admin/groups/{id}/members {upn, display_name, email}` upserts the user and creates a membership with `personal_cap=0` and `max_parallel_requests=CONCURRENCY_DEFAULT`. It enqueues `user`, `team_member` and `key`. The `key` push creates a single personal key, `key_alias="personal:<upn>"`, and stores only its token.
- `GET /api/groups/{id}/members` → `[{membership_id, user:{id, display_name, upn}, personal_cap, model_caps:{model: cap}, max_parallel_requests, tpm_limit, rpm_limit, spend|null, is_self, editable, sync}]`. `sync` merges the `team_member` and `key` statuses.
- `PUT /api/groups/{id}/members {members:[{membership_id, personal_cap, model_caps, max_parallel_requests, tpm_limit, rpm_limit}]}`:
  - The request is a bulk, atomic save. Run `SELECT group FOR UPDATE`.
  - If any row's user is the actor and any of its values change → `SELF_ALLOCATION_FORBIDDEN` (U2).
  - Then validate member caps against the base budget, per-model caps against the pot and the personal cap, and concurrency against the policy.
  - Write the values, add audit rows, and enqueue `team_member` (`max_budget_in_team`) and `key` (limits + `model_max_budget`).
- `sync.push` for `team_member` and `key`: the payload is built from the DB.

**Test:** `tests/api/test_members.py`:
- `test_add_member_creates_user_member_key_in_fake`: drain, then assert on `/_fake/state`.
- `test_caps_over_base_409` (U1 API side)
- `test_self_row_change_403_self_allocation_forbidden` (U2)
- `test_self_row_unchanged_is_allowed`
- `test_model_cap_over_pot_409`
- `test_model_cap_over_personal_cap_409`
- `test_concurrency_above_max_422_unless_platform_admin`
- `test_save_while_litellm_down_returns_200_pending_then_applied` (U3 API side)
- `test_dept_rep_can_edit_group_heads_row`

### Step 13: My wallet (FR-E) and the seed fixture
**Files:** `app/api/wallet.py`, `app/domain/wallet.py`, `scripts/seed.py`, `tests/api/test_wallet.py`, `tests/test_seed.py`.

**Build:**
- `GET /api/me/wallet` → `{group:{id, name, heads:[...]}, personal_cap, spend|null, remaining|null, model_caps:[{model, cap, spend|null}], max_parallel_requests, tpm_limit, rpm_limit, status, blocked_by:{type:"group", group_name, heads:[...]}|null, sync}`.
- `status` is computed by a pure function `wallet_status(personal_cap, member_spend, group_max, group_spend) -> "ok" | "blocked_group" | "blocked_personal" | "unknown"`:
  - group exhaustion takes precedence;
  - `unknown` when spend cannot be read.
- `scripts/seed.py` is idempotent and goes through the services, so outbox rows are created. Run it with `uv run python scripts/seed.py [--drain]`. It creates:
  - 1 department ("מחלקת פיתוח", budget 10000) with its staff group and 2 groups;
  - about 6 users, including a platform admin, a department rep, two group heads, a user who is group_head of G1 and viewer of the department (U6), a pure viewer (U7), and employees with caps.
  - Every seeded user's upn is listed in `/auth/dev/users`.

**Test:**
- `tests/api/test_wallet.py`:
  - `test_wallet_ok`
  - `test_wallet_blocked_by_group_names_head` (U4 API side: set spend via `/_fake/spend`)
  - `test_wallet_unknown_when_litellm_down`
  - `test_no_membership_404`
- `tests/test_seed.py::test_seed_idempotent`: run twice, same counts.

### Step 14: Frontend scaffold, Shell, login, scope switcher (RTL)
**Files:**
- `frontend/package.json`, `package-lock.json`, `vite.config.ts` (proxy `/api`, `/auth` → `API_TARGET`, default `http://127.0.0.1:4101`)
- `tsconfig*.json`, `eslint.config.js`, `.prettierrc`, `index.html` (`<html lang="he" dir="rtl">`)
- `src/main.tsx`, `src/App.tsx` (react-router), `src/api/client.ts`, `src/api/hooks.ts`
- `src/scope.tsx`, `src/i18n/he.ts`, `src/format.tsx`
- `src/components/Shell.tsx`, `src/components/ScopeSwitcher.tsx`, `src/components/SyncBadge.tsx`, `src/components/SyncBanner.tsx`
- `src/pages/Login.tsx`, `src/styles.css`
- `src/**/*.test.tsx`
- `scripts/test.sh` (add the frontend block)

**Build:**
- `api/client.ts`: `apiFetch<T>(path, {method, body})` sends `X-CSRF: 1` and `X-Scope` from context, and parses the error envelope into `ApiError {code, message, details}`.
- The selected scope lives in the URL (`/s/:scope/...`) plus localStorage.
- `Login`: lists `/auth/dev/users` buttons when that endpoint answers 200, and otherwise shows an "התחברות" link to `/auth/login`.
- `ScopeSwitcher` lists `/api/me` scopes plus "הארנק שלי".
- `SyncBanner` polls `/api/sync/status` every 5 s and shows a banner when `pending_count > 0 && !litellm_reachable`.
- `format.tsx`: `<Credits value/>` uses `Intl.NumberFormat('he-IL')` inside `<bdi dir="ltr">`; `<ModelName/>` uses `<bdi dir="ltr">`.
- `styles.css` uses CSS logical properties only (`margin-inline-start`, `text-align: start`), so layouts mirror.
- Strings come only from `he.ts`. No i18n library and no UI library.
- `test.sh` frontend block: `(cd frontend && { [ -d node_modules ] || npm ci; } && npm run lint && npm run typecheck && npx vitest run)`

**Test:** vitest + @testing-library/react:
- `ScopeSwitcher.test.tsx`: lists both scopes and the wallet; changing scope updates `X-Scope` on the next request.
- `format.test.tsx`: number wrapped in `bdi`, he-IL grouping.
- `client.test.ts`: error envelope parsed.

### Step 15: My wallet, Group Overview, Group History screens
**Files:** `frontend/src/pages/MyWallet.tsx`, `frontend/src/pages/group/GroupLayout.tsx` (tabs), `frontend/src/pages/group/Overview.tsx`, `frontend/src/pages/group/History.tsx`, plus a test file per page.

**Build:**
- **MyWallet:** cap, spend and remaining, a progress meter, a per-model caps table, concurrency and rate limits, and a SyncBadge.
  - When `status === "blocked_group"`, show `חסום: תקציב הקבוצה נוצל` and the head names, i.e. "פנה/י אל <head>" (U4).
- **Group Overview:** base, allocated and unallocated, spend, and a model pots table, editable when the role allows and saved with `PUT model-pots`.
- **History:** a paged table of time, actor, action, entity, before→after.
- Action buttons render only when `/api/me` shows a write role for the selected scope (U7). The `can(scope, action)` helper mirrors the backend matrix.

**Test:**
- `MyWallet.test.tsx`: blocked-group message with the head's name; sync badge pending.
- `Overview.test.tsx`: no edit button for a viewer.
- `History.test.tsx`: renders rows.

### Step 16: Group Members and Department Overview/Split screens
**Files:** `frontend/src/components/AllocationTable.tsx` (shared editable table + footer), `frontend/src/pages/group/Members.tsx`, `frontend/src/pages/department/DepartmentLayout.tsx`, `frontend/src/pages/department/Overview.tsx`, `frontend/src/pages/department/Split.tsx`, plus tests.

**Build:**
- **`AllocationTable`** is shared (DRY) by Members and Split:
  - Props: `rows`, `limit`, `editableRow(row)`, `onSave(changes)`.
  - The footer shows Σ allocated vs limit and turns red with Save disabled when Σ exceeds limit × factor (U1). The factor comes from `/api/me` config.
  - It shows a SyncBadge per row.
  - It maps `PARENT_OVERALLOCATED` errors to a Hebrew message with the numbers from `details`.
- **Members:** the own row (`is_self`) is read-only (U2). Expanding a row shows per-model caps and concurrency.
- **Department Overview:** budget, groups, PMO pool, reserve, spend.
- **Split:** group base budgets and the PMO pool, with the reserve derived live.
- Mutations invalidate TanStack queries; after a save, refetch until the badges reach `applied`.

**Test:**
- `AllocationTable.test.tsx`: over-limit → red footer + disabled Save; server 409 shows the numbers.
- `Members.test.tsx`: own row has no inputs.
- `Split.test.tsx`: the reserve recomputes.

### Step 17: Playwright harness and U6, U7, U8
**Files:** `scripts/e2e_stack.py`, `app/api/e2e.py` (mounted only when `E2E=1`), `frontend/playwright.config.ts`, `frontend/e2e/fixtures.ts`, `frontend/e2e/u6-scopes.spec.ts`, `frontend/e2e/u7-viewer.spec.ts`, `frontend/e2e/u8-rtl.spec.ts`, `scripts/test.sh` (add the Playwright block).

**Build:**
- `e2e_stack.py` is one process. It:
  - starts pgembed in a temp dir and migrates;
  - serves the fake LiteLLM on 127.0.0.1:4112 and the API on 127.0.0.1:4111 (`DEV_AUTH=1`, `E2E=1`, `OUTBOX_INTERVAL_S=0.5`) as two uvicorn servers in one asyncio loop;
  - seeds and drains.
- `POST /_e2e/reset` truncates, re-seeds, resets the fake and drains.
- `playwright.config.ts` runs chromium only, with `workers: 1`. Its `webServer` entries are:
  1. the `e2e_stack` process (health check on `:4111/healthz`);
  2. `vite --port 4110 --strictPort` with `API_TARGET=http://127.0.0.1:4111`.
  - Using 4110–4112 avoids clashing with dev servers on 4100–4102.
- `fixtures.ts`: `loginAs(page, upn)` through the DEV picker, and a `reset` call in `beforeEach`.
- Installing the browser is a one-time step: `npx playwright install chromium`. If chromium cannot launch for lack of system libraries (no sudo), record it under Blockers in PROGRESS.md; do not skip the tests.
- `test.sh` Playwright block: `(cd frontend && npx playwright test)`

**Test:**
- **U6:** a two-role user sees both scopes plus the personal wallet. In `department:D` (viewer) G1 is read-only; in `group:G1` (head) it is editable.
- **U7:** the viewer sees no action buttons, and `page.request.put(...)` with the viewer's cookie gets 403.
- **U8:** `document.documentElement.dir === "rtl"`; the first column header is to the right of the last; a model name and a number inside Hebrew text keep the exact LTR text (`toHaveText("claude-sonnet-4")`, with he-IL grouped digits).

### Step 18: Playwright U1–U4 and the Phase 1 exit check
**Files:** `frontend/e2e/u1-overallocation.spec.ts`, `frontend/e2e/u2-self.spec.ts`, `frontend/e2e/u3-litellm-down.spec.ts`, `frontend/e2e/u4-blocked.spec.ts`, `docs/acceptance-phase1.md`, `PROGRESS.md`.

**Test:**
- **U1:** the group head types caps above the base: red footer, Save disabled. Race: two browser contexts load Members and both save allocations that each fit alone; the second sees the Hebrew `PARENT_OVERALLOCATED` message with numbers.
- **U2:** own row read-only; a direct `PUT` returns `SELF_ALLOCATION_FORBIDDEN`. Sharing a grant to oneself is Phase 2 and is noted in the doc.
- **U3:** `/_fake/outage {down:true}`, then save: 200, badges pending, banner visible. Then `{down:false}`: badges become applied and the banner hides, using `expect.poll`.
- **U4:** `/_fake/spend` exhausts the group; the employee's wallet shows `חסום: תקציב הקבוצה נוצל` and the head's name.

**Exit check:**
- `docs/acceptance-phase1.md` maps FR-D/G/E/V/A, A2/A3/A5 and U1–U4/U6–U8 to test names. A2/A3/A5 map to `tests/contract/*`, skipped here, with their status marked "needs full SPEC + real LiteLLM".
- Also run the dev stack on 4100–4102 and open the UI to check it by hand.
- Update PROGRESS.md.

---

## Verification (end of Phase 1)
1. `./scripts/test.sh` is green: ruff, pytest (unit + API + outbox, contract tests skipped), frontend lint/typecheck/vitest, Playwright U1–U4 and U6–U8.
2. Manual dev stack:
   - `uv run python scripts/devdb.py`, `uv run alembic upgrade head`, `uv run python scripts/seed.py --drain`
   - fake LiteLLM on 4102, API on 4101 (`DEV_AUTH=1`), UI on 4100
   - Log in as each seeded role, switch scopes, save a split and member caps, and watch the badges go pending → applied.
3. With a real LiteLLM: `LITELLM_BASE_URL=... LITELLM_MASTER_KEY=... [LITELLM_ENTERPRISE=1] uv run pytest tests/contract`.

## Later phases (outline only)
- **Phase 2: Grants, scheduler, reconciliation, notifications (FR-P, FR-RC, FR-N; A1, A9 manual move, A11; U5).**
  - Tables `grants` and `grant_shares`, with attribution D2 (base first, then earliest expiry).
  - An APScheduler-style in-process scheduler for expiry and returning money to the PMO pool. Grant time windows are measured from SpendLogs (D3).
  - A reconciler that diffs LiteLLM state against the DB and re-enqueues the outbox.
  - In-app notifications, plus email if a relay exists (D12).
  - UI: PMO screens, the Group Grants tab, the Department Group heads tab.
- **Phase 3: AD sync (section 9), role mirroring, service keys (FR-K), production.**
  - LDAP sync with dry-run and a safety gate. Joiners, movers (D4) and leavers (D10, 30-day grace).
  - Mirror department_rep → `org_admin` and group_head → team `admin`.
  - Non-personal service and bot keys (G8); one production Organization per department (D8).
  - UI: Department Review/Production tabs, Group Service keys tab, Platform screens (which replace the admin-API-only setup).
  - Deployment: OpenShift manifests, airgapped mirrors, ADFS OIDC config.
- **Phase 4: Reports and alerts (FR-R).** Allocation vs spend at every level, grant usage, over-allocation, concurrency peaks from SpendLogs, and usage charts.

## Questions for Maor (true blockers only)
1. **Please send the complete SPEC.md v0.2 and `litellm_wallets_erd.mmd`.** About 53k characters (sections 3–15.2) were lost in relay. Phase 1 exit criteria name FR-D/G/E/V/A and acceptance tests A2, A3, A5, none of which can be checked without that text. Every rule and error code above other than `PARENT_OVERALLOCATED` and `SELF_ALLOCATION_FORBIDDEN` is a builder assumption.
2. **Which LiteLLM Enterprise version should we target, and can we get a reachable instance plus license for contract tests?** Endpoint shapes differ by version (for example `PATCH` vs `POST /organization/update`, team-level `model_max_budget`). A2/A3/A5 can only be verified against a real proxy; until then they stay skipped.
