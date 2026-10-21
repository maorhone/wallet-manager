# Progress — Wallet Manager

## Status
Phase 1 and Phase 1b done (owner decision 20/10/2026: no automatic AD sync; onboarding portal API + manual member actions). Phase 2 backend through Step 2.5 done; Step 2.6 (PMO UI) and Step 2.7 (PMO grant actions, Projects tab) done. Step 2.8 (group Grants tab) and Step 2.9 (department Group heads tab) done. Step 2.10 (notifications bell and preferences) done: Phase 2 complete. Next = Phase 3.
Latest `./scripts/test.sh` (Step 2.5): green; pytest 449 passed (5 skipped real-target cases), vitest 24, Playwright 27; ruff, frontend lint/typecheck and formatting pass. Pytest 2m35s, Playwright 1.8 min, roughly 4.5–5 minutes including lint/setup (above the ~4-minute target; prior baseline 4m25s). No scripts, E2E ports or env handling changed.
Phase 1 (Steps 1–13): `./scripts/test.sh` green (pytest 217 passed, 4 skipped; vitest 21; Playwright 18; ~2 min).

## Done
- Step 2.10: UI `NotificationsBell` (header; unread count, drawer, mark read, scope link) and `NotificationPrefs` (on My wallet;
  critical kinds disabled), `useNotifications/useMarkRead/useNotificationPrefs`, `he.notifications.kinds`, Playwright
  `notifications.spec.ts` (+1, 34 total). Prefs come from `GET /me/notifications` (no separate GET prefs endpoint). Full
  `./scripts/test.sh` about 4m40s (Playwright ~1.9 min). Phase 2 acceptance tests all exist. No new owner questions.
  Flake: once `tests/scheduler/test_reconcile_job.py::test_key_and_mapping_corrections_block_lifecycle_and_show_sync` hit the 60 s
  timeout in the `provisioned` fixture (`drain` → `_policy` → `session.get(Policy)`); the file passes alone (15 passed).
- Step 2.9: UI Department Group heads tab (`/department/:id/group-heads`); `group_heads` in the department summary; Playwright +1
  (Playwright 33 total), pytest +1 (`test_group_heads_rows_for_rep`). `./scripts/test.sh` green.
- Step 2.8: UI Group Grants tab with share create, change and close; U5 and U2 grant half (see Notes).
- Step 2.7: UI PMO grant actions (extend, increase, close early) and Projects tab.
- Step 2.6: UI PMO page with Pool and Grants tabs and the create-grant wizard (see Notes).
- Step 2.5: FR-RC1–3 reconciliation, field-level drift log, repeated-drift alerts and metrics;
  delayed scheduler registration (lock 3), typed mapping list client/fake endpoint, and clearing
  of owned limits through desired-state outbox updates. Fourteen new tests cover diff rules,
  correction/drain/no-op, status flags, in-flight races, missing objects, outages and alerts.
- Codex review fixes for 2.1-2.4: verified all thirteen MAJOR findings against the code and
  reproduced them with failing regression tests before fixing their shared paths.
  - M1: share close now checks self-targeting, target tier and current membership, just like share writes.
  - M2: closure attributes against every eligible active and closed share, then stores only the requested closures.
  - M3: attribution is restricted to the current group/team and membership boundary from lifecycle
    audit, including a move away and back into the same team in one period.
  - M4: lifecycle takes the group lock before membership validation and share reads, even with no shares;
    closure refreshes share rows under row locks, preventing stale objects from returning money twice.
  - M5: expiry queues member recomputation in the closure transaction; restart no longer depends on an in-memory set.
  - M6: a transaction-scoped request lock now precedes replay lookup for every shared write-path request.
  - M7: closed grants retain their spent contribution in the closing budget month in desired team budgets,
    model pots and wallet summaries; only unused money is removed.
  - M8: historical attribution reads each local month's UTC window separately instead of interpreting UTC dates as local months.
  - M9: displayed attribution includes stored closed usage before allocating remaining spend to active shares.
  - M10: notification usage adds only active-share attribution after summing stored closed usage.
  - M11: grant lists, project summaries and the PMO card combine active snapshot attribution with stored usage.
  - M12: migration 0004 widens share/grant returns to NUMERIC(18,4), repairs rounded closed returns,
    and rebuilds grant return totals; regression asserts conservation after committed rows are reloaded.
  - M13: desired team updates explicitly send an empty model budget map, clearing the last removed pot.
  - Validation: relevant API/scheduler/unit subset: 75 passed. Final `./scripts/test.sh`: green,
    pytest 435 passed / 5 skipped, vitest 24, Playwright 27; ruff, frontend lint/typecheck and
    formatting pass. Total 4m25s in this sandbox. No E2E ports/env handling or test scripts changed.
- Scaffold: CLAUDE.md, hooks, settings, .gitignore
- Full SPEC.md v0.2 + litellm_wallets_erd.mmd; PLAN.md v2 (13 Phase 1 steps)
- Step 1: pyproject/uv.lock, `app/{config,db,models,errors,main}.py`, `app/schemas/common.py` (Money),
  Alembic async env + `0001_initial` (autogenerated from models), `scripts/dev_db.py`, `.env.example`,
  real `scripts/test.sh` (ruff check + format check + pytest), tests: schema vs §8.2, error envelope,
  advisory lock, healthz/readyz.

- Step 2: `app/litellm_client/{models,client,readonly,errors}.py` (typed client, transient/permanent errors, no logging),
  `fake_litellm/{db,state,controls,main}.py` (ERD tables, section-10 endpoints, `/_test/reset|outage`, `litellm_ro` role
  via `init_db`, also used by `scripts/dev_db.py`), tests in `tests/litellm/`, fixtures `fake_app`/`litellm`/`ro_engine`.
  `./scripts/test.sh` green (27 tests).

- Step 3: `fake_litellm/enforcement.py` (`/chat/completions`, `/v1/chat/completions`; key/JWT auth, R7 concurrency, R1/R2/R5/R6 budgets,
  SpendLogs charging, `make_test_jwt`), `/_test/config` and `/_test/spend`, `tests/contract/` (fake|real `target` fixture, helpers, README,
  A2/A3/A5), `tests/litellm/test_fake_enforcement.py`. `./scripts/test.sh` green (35 passed, 4 skipped real-target cases).

- Step 4: `app/auth/{oidc,dev,session,csrf}.py` (signed `wm_session` cookie, authlib OIDC with PKCE/nonce, dev login only when `DEV_AUTH=1`,
  double-submit CSRF middleware), `app/security_headers.py` (CSP + nosniff), `app/directory.py` (`FixtureDirectory`), `app/permissions.py`
  (Principal, §12.2 helpers, `target_tier`), `GET /me`, `seed/dev.yaml` + `app/seed.py` + `scripts/seed.py [--reset]`, tests in `tests/auth/`,
  `tests/test_permissions.py`, `tests/test_me.py`; fixtures `seeded`/`make_client`/`login` in `tests/conftest.py`.

- Step 5: `app/writes.py` (WriteContext, `write_context` dependency, replay handler, `sync_status`, `write_response`), `app/outbox/{desired,enqueue,worker}.py`,
  `app/worker.py`, `PUT /departments/{id}/budget` (`app/services|routers|schemas/departments.py`), seed provisioning (`seed(..., settings=)`;
  `scripts/seed.py [--reset]` resets the fake, seeds and drains). Tests: `tests/test_write_path.py`, `test_outbox.py`, `test_seed_provisioning.py`,
  `tests/api/test_department_budget.py`; fixtures `prov_settings`/`provisioned`/`put_budget`.

- Step 6: `app/services/{validation,groups}.py`, `app/routers/groups.py`, `app/schemas/groups.py`; `GET /departments`, `PUT /departments/{id}/split|model-allowances`,
  `PUT /groups/{id}/model-pots|limits` (all under `lock(DEPARTMENT)` where sums are read). Tests: `tests/unit/test_validation.py`, `tests/api/test_{split,model_pots,group_limits,concurrency_locks}.py`.

- Step 7: `app/services/{personal_values,members}.py`, `app/routers/policies.py`; `PUT /groups/{id}/members/caps|member-defaults`,
  `PUT /groups/{id}/members/{upn}/model-caps|limits`, `GET /policies`. Tests: `tests/unit/test_personal_values.py`,
  `tests/api/test_member_{caps,model_caps,limits,defaults}.py`.

- Step 8: `app/services/{spend,wallet,history,owners,views}.py`; `GET /me/wallet`, `/me/usage`, `/groups/{id}/summary`, `/groups/{id}/members`,
  `/departments/{id}/summary`; worker runs `snapshot_loop` next to the outbox loop. Tests: `tests/unit/test_wallet_{formulas,states}.py`,
  `tests/test_spend_snapshot.py`, `tests/api/test_{me_wallet,group_summary,members_list,department_summary}.py`; fixture `snapshot` and `tests/api/periods.py`.

- Step 9: `app/services/{overallocation,audit}.py`, `app/metrics.py`, `app/routers/{reports,platform,audit}.py` (`GET /reports/overallocation`, `/status`,
  `/outbox`, `POST /outbox/{id}/retry`, `/audit`, `/metrics`); worker runs `overallocation_loop`; `create_app(settings, litellm=, reader=)` holds the LiteLLM client and
  reader in `app.state`. Tests: `tests/api/test_{overallocation,status_outbox,audit}.py`, `tests/e2e_backend/test_a{2,3,5}_wm.py`; fixture `login_wm`.

- Step 10: root npm workspace (`package.json`, `eslint.config.js`, `.prettierrc`), `frontend/` (Vite, React, Mantine 7 RTL, TanStack Query, Router):
  `api/{client,queries,types}.ts`, `i18n/{he,errors}.ts`, `format.ts`, `scopes.ts`, components (Num, Credits, Pct, DateText, SyncBadge, StatusBanner,
  ScopeSwitcher, PeriodSelector, StateBadge, EmptyState, ErrorState), `pages/Login.tsx`, routes with placeholder pages. `scripts/e2e_stack.py`,
  `frontend/e2e/{fixtures,shell.spec}.ts`, `playwright.config.ts`. test.sh now runs lint, typecheck, vitest (8) and Playwright (3) after pytest.

- Step 11: `frontend/src/components/{WalletCard, ModelTable, SharesTable, UsageChart, OwnersBox, LimitsBox}.tsx`, `pages/MyWallet.tsx` (route `/wallet`),
  `useMyWallet`/`useMyUsage`, `he.wallet` strings. Tests: `WalletCard.test.tsx` (8), `e2e/wallet.spec.ts` (2, incl. U4).
  `./scripts/test.sh` green (pytest 217 passed, 4 skipped; vitest 16; Playwright 5).

- Step 12: `frontend/src/components/{AllocationTable, ChangeSummaryDialog, HistoryTimeline, MemberSidePanel, MemberDefaultsDialog}.tsx`,
  `pages/group/{GroupPage, Overview, Members, History}.tsx` (routes `/group/:id/{overview,members,history}`), `useGroupSummary|useGroupMembers|usePolicies|useGroupAudit|useGroupWrite`,
  `he.groupPage|summary|history`. Backend: `/groups/{id}/members` rows carry `model_tpm_limits`, `/groups/{id}/summary` carries `group.default_member_cap`.
  Tests: `AllocationTable.test.tsx` (5), `e2e/group.spec.ts` (6). `./scripts/test.sh` green (pytest 217 passed, 4 skipped; vitest 21; Playwright 11).

- Step 13: `frontend/src/pages/department/{DepartmentPage,Overview,Split}.tsx` (routes `/department/:id/{overview,split}`), `components/SplitChart.tsx`,
  `useDepartmentSummary|useDepartmentWrite`, `he.departmentPage`. Split reuses `AllocationTable` + `ChangeSummaryDialog`; admins also get inline budget editing.
  Group page hides the Members tab when `actions.edit_members` is false (viewer, PMO; D11). Backend: `/departments/{id}/summary` rows carry `sync_status`
  (+ `split.pmo_reserve_sync_status`). E2E: `department.spec.ts` (4), `viewer.spec.ts` (U7), `roles.spec.ts` (U6), `rtl.spec.ts` (U8).
  Phase 1 acceptance table in PLAN §3 checked: every listed test exists and passes.

- Step 1b.1: migration `0002_lifecycle` (employee `pending_*` columns + status CHECK, `litellm_write.depends_on`, `sync_run`/`sync_action` dropped, ORM updated),
  `LockKind.EMPLOYEE` + `employee_lock_id`, client `key_delete|user_delete|jwt_mapping_update|jwt_mapping_delete` (+ typed bodies), fake endpoints
  (`/key/delete`, `/user/delete`, `/jwt/key/mapping/update|delete`, `is_active` honored by enforcement, `/_test/fail`, `/_test/invariants`),
  chained outbox (`enqueue(..., payload, depends_on)`, `WriteContext.enqueue(..., payload, chain)`, worker `held`, `_FROZEN`, `_GONE_OK`, key block/unblock and mapping update builders),
  `tests/test_repo_hygiene.py`, `.public-denylist` gitignored. Tests: `tests/litellm/test_fake_lifecycle.py` (6), `test_client_lifecycle.py` (1),
  `tests/test_outbox_chain.py` (5), hygiene (1); `tests/test_schema.py` now matches §8.2 v0.3 (16 tables).

- Step 1b.2: `app/services/lifecycle.py` (`JOINER`, `provision`, `close_member_shares` hook, `check_target`, `guard_idle`, `kick|restore|move|delete_member`, `move_targets`, `row_actions`),
  `app/routers/members.py` (`POST /groups/{id}/members/{upn}/kick|restore|move|delete`, `GET .../move-targets`), `app/schemas/members.py`, `can_kick|can_move|can_delete_member`,
  `member_state_conflict`/`lifecycle_in_progress` errors, `/groups/{id}/members` rows carry `actions` (deleted employees are no longer listed), metrics `wm_lifecycle_actions_total{action}`, `wm_outbox_held`.
  Tests: `tests/api/test_member_{kick,move,delete}.py`, `test_lifecycle_permissions.py` (matrix 48 + 3), `tests/e2e_backend/test_move_invariant.py` (A12 x3, A9), `test_kick_wm.py` (A14).

- Step 1b.3: `app/auth/portal_jwt.py` (`verify_portal|verify_user|PortalAuth`, RS256 via JWKS, HS256 only with `DEV_AUTH`), `app/services/onboarding.py`
  (`find_placement`, `onboard`, `onboarding_status`, `place`, `pending_list`), `app/routers/{onboarding,placements}.py` (`POST|GET /onboarding`,
  `GET /departments/{id}/pending-placements`, `GET /pending-placements?department=`, `POST /pending-placements/{upn}/place`), `can_place|can_view_pending`,
  `DirectoryUnavailable` + 503 `DIRECTORY_UNAVAILABLE`, settings + `.env.example`, CSRF exempts `/onboarding`, `make_request_id` in `app/writes.py`, metric `wm_onboarding_calls{status}`,
  seed users (newbie, drifter, twin directory-only; pazit, lost pending), `scripts/dev_onboard.py`, e2e stack generates the portal settings. Vite proxy has `onboarding|pending-placements`.
  Tests: `tests/api/test_onboarding.py` (15), `tests/auth/test_portal_jwt.py` (14), `tests/api/test_placement.py` (14), `tests/e2e_backend/test_onboarding_wm.py` (A15, A10); `tests/helpers/jwt.py`, fixtures `make_portal`/`portal`.

- Step 1b.4: `components/MemberActionDialog.tsx` (reason, move target select, typed-upn confirm), side panel "פעולות" section driven by `Member.actions`,
  muted rows + "מושעה" badge (`StateBadge` `suspended`, `AllocRow.muted`), `useMemberAction|useMoveTargets`, `he.memberActions`, new error codes
  (`MEMBER_STATE_CONFLICT`, `LIFECYCLE_IN_PROGRESS`, `DIRECTORY_UNAVAILABLE`), e2e `fakeFail`. Tests: `MemberActionDialog.test.tsx` (3), `e2e/member-actions.spec.ts` (6).

- Step 1b.5: `components/PendingPlacementsTable.tsx` (reuses `MemberActionDialog` with action `place` and a group select), `pages/department/Pending.tsx`
  (tab "ממתינים לשיבוץ" at `/department/:id/pending`, shown when `actions.place`), `pages/Platform.tsx` (pending section only), `usePendingPlacements|usePlace|useDepartmentGroups|useDepartmentIds`,
  `he.pending`, department summary `actions.place` (admin or rep). Tests: `e2e/pending.spec.ts` (3, U12). Phase 1b acceptance table in PLAN §2b checked: every listed test exists and passes,
  `test_repo_hygiene.py` passes. Questions D14–D25 are in "Questions for owner" (11, 12) and the notes above.

- Step 2.1: `app/clock.py` (`Clock`, `SystemClock`, `FakeClock`; `create_app(..., clock=)` stores `app.state.clock`), migration `0003` (indexes only),
  `app/{schemas,services,routers}/grants.py` (`POST/PATCH /grants`, `POST /grants/{id}/close`, `GET /grants`, `GET /departments/{id}/pmo-pool`,
  `GET /projects/{ref}/summary`), `can_manage_grants` / `can_view_grants`, effective amount (`amount − returned`) in `desired.py` and `views._grants_by_group`.
  Tests: `tests/api/test_grants*.py` (17), `tests/unit/test_clock.py`. `./scripts/test.sh`: pytest 363 passed, 4 skipped; vitest 24; Playwright 27.

- Step 2.2: `app/services/{attribution,shares}.py`, `GET|POST /grants/{id}/shares`, `PATCH /grants/{id}/shares/{sid}`, `POST .../shares/{sid}/close`,
  `can_share`, `ShareIn|SharePatchIn`, member_update desired values (FR-P4/P5, both temp keys always sent), `/me/wallet` share rows, `_close_grant` closes shares first,
  fake `/team/member_update` 400 on one temp field. `PRD.md` created. Tests: `tests/unit/test_{attribution,desired_member}.py`, `tests/api/test_shares.py` (11),
  `test_me_wallet` share rows, fake lifecycle temp-fields test. `./scripts/test.sh`: pytest 385 passed, 4 skipped.

- Step 2.3: `app/scheduler/{loop,jobs,__main__}.py` (`python -m app.scheduler`; `Job`, `run_loop`, `build_jobs`, `run_grants` -> `GrantsResult`), `LockKind.JOB`,
  scheduler settings + `.env.example`, metrics `wm_scheduler_job_runs{job,outcome}` / `wm_scheduler_job_last_success_timestamp{job}`, `lifecycle.close_member_shares`
  (move -> `move`, delete -> `manual`), fake LiteLLM clock (`POST|GET /_test/clock`, `FakeState.now()`), `scripts/e2e_stack.py` starts the scheduler (grants/notify 2 s),
  fixtures `fake_clock` / `run_job`, helpers `tests/helpers/grants.py`. Tests: `tests/scheduler/test_{loop,grants_job}.py` (4 + 6), move/delete share tests (3, the hook test was replaced),
  `tests/contract/test_a1_temp_increase.py` (`[fake]`; `[real]` skipped), `tests/e2e_backend/test_a1_wm.py`, A9 with a share.

- Step 2.4: `app/services/notifications.py` (`KINDS` with critical flags, `notify` with mute check, `ON CONFLICT DO NOTHING` dedupe and optional email, `heads_of`, `reps_or_admins`, /me helpers),
  `app/services/notify_scan.py` (employee/group 80/100, grant expiring/underused, outbox failed), `app/notify_email.py`, `app/routers|schemas/notifications.py`
  (`GET /me/notifications?unread=`, `POST /me/notifications/{id}/read`, `PUT /me/notification-prefs`), `jobs.run_notifications` -> `NotifyResult` (registered, lock 2, 900 s),
  event hooks in `onboard` (pending), `move`, `create_grant` (now takes `directory`), settings `notify_email_enabled|smtp_host|smtp_port|notify_email_from|seed_grants` + `.env.example`,
  seed `grants:` (PRJ-ATLAS, A2, 30,000, 60 d, share 5,000 to avi) applied only with `SEED_GRANTS=1`; `e2e_stack.py` sets it only when the API port is the default 4111.
  Tests: `tests/api/test_notifications.py` (5), `test_notification_events.py` (3), `tests/scheduler/test_notifications_job.py` (6), `tests/unit/test_notify_email.py` (2), `tests/test_seed_grants.py` (1).
  `./scripts/test.sh`: pytest 417 passed, 5 skipped; vitest 24; Playwright 27 (2.0 min).

## Next
- Step (none; Phase 2 ends with Step 2.10). Next = Phase 3.

## Blockers
- None.

## Notes
- Step 2.9: `MemberSidePanel` gained `withBaseCap` (one-row `PUT /groups/{gid}/members/caps`); `ShareDialog` members type loosened.
  The summary's head rows include `model_tpm_limits` (not in PLAN) so the panel's limits write does not clear them. Tab is shown when any
  row has `actions.edit` (a viewer/PMO never sees it). "Own row read-only" is covered by the API test (a rep who is also a group head);
  the Playwright test cannot log in as such a user because the dev seed has no head with department access. Two no-op Bash slips
  (an empty `cat >>` heredoc and a `sed` on /dev/null) changed nothing. One full run hit the 60s pytest-timeout in
  `test_lifecycle_permissions.py::test_matrix[rep_on_self-delete]` (fixture CPU starvation); it passed alone and in the next full run.
- Step 2.9 stop-hook reruns: `hasPendingDepartment` now includes `group_heads` (the new Playwright test needed the 1 s poll). Other full runs
  failed on a different unrelated test each time while the sandbox was slow: pytest 60 s fixture timeouts (`test_grants.py::test_head_viewer_employee_403`,
  `test_review_grants.py::test_review_close_target_authorization[...]`; both pass alone), Playwright `group.spec.ts` U3 (outage banner within 20 s:
  `/status` cache 10 s + 5 s poll leaves little margin) and `member-actions.spec.ts` U9 ("timeout while setting up context", browser start).
- Flaky, unrelated to 2.8 (frontend only): `tests/api/test_grants_concurrency.py::test_a11_concurrent_grants_one_pool_insufficient`
  hit the 60s pytest-timeout in fixture setup (`fake_litellm/main.py` `key_generate`, inside SQLAlchemy cache-key generation, pure CPU)
  while the sandbox was starved; it passes alone in 2s and in the earlier full green run (458 passed).
  Later reruns: `tests/api/test_split.py::test_split_over_budget_422_with_numbers` hit the same 60s fixture timeout, and a run with
  pytest 458 passed / vitest 38 failed only Playwright `wallet.spec.ts` U4 (known snapshot-timing flake, see Step 2.6 notes; 31 of 32 passed).
- Step 2.8: `pages/group/Grants.tsx` (tab `grants` in `GroupPage`; grant list, per-grant shares panel with change/close, all gated by
  server `actions.share`), `components/ShareDialog.tsx` (create and change modes; expiry defaults to and is capped at the grant's
  expiry instant; remainder live; own row disabled with owner role), `useShareWrite`, `he.shares`. `SharesTable` and the wallet share
  rows already existed (Step 2.2/Phase 1) and are unchanged. Vitest +3, Playwright +2. No spec/PLAN differences.
- Review 2.7: all 7 majors fixed (read_only gating + editor closes on period change; idempotency key kept per payload; close preview
  amount-used-returned; /me timezone for dates; inline before/after + pool-effect table in `GrantEditDialog` instead of
  `ChangeSummaryDialog` (that one rotates its key on every error); 15 s refetch for project summary and shares; query-param summary route).
  Not done: the close preview is computed client-side, not by the server with Decimal (credits are whole numbers in the UI).
- Flaky, unrelated to 2.7 (backend untouched): `tests/api/test_lifecycle_permissions.py::test_matrix[rep-delete]` hit the 60s
  pytest-timeout once while FastAPI was building a route (`fastapi/params.py` in `create_app`); the file passes on rerun (51 passed).
  Further 60s timeouts hit random tests during fixture setup (`tests/api/test_placement.py::test_rep_places_pending_of_own_department_then_ready`,
  `tests/api/test_shares.py::test_blocked_member_conflict_and_patch`, `tests/api/test_member_defaults.py::test_member_defaults_permissions`
  inside `yaml.safe_load` of the seed file, a pure-CPU parse): the whole process stalls (one run took 406s for 100 tests), so the
  sandbox is starved at times; the tests pass in 2-9s alone and in full green runs (453 passed). Not caused by Step 2.7 (frontend only).
  Earlier, an e2e `resetData` TRUNCATE once reported a deadlock; also passed on rerun.
  Root cause found 21/10 00:30: TRUNCATE (test `_clean_tables`, `fake_db.reset_db`, `seed --reset`) waited on or deadlocked with
  live sessions (leaked test sessions, or the e2e stack's API/worker). Fix: `app.db.truncate_with_retry` (own short transaction,
  `lock_timeout` 5s, retry on 40P01/55P03), tests drop leaked in-transaction sessions before retrying (`tests/helpers/db.py`),
  `scripts/test.sh` no longer hides a left-side failure of `&&`. Two consecutive full runs green: 457 pytest / 26 vitest / 30 Playwright.
- Step 2.7: `GrantEditDialog` (extend|increase|close with reason), PMO `Grants` actions column from server
  `actions`, `Projects` tab (rows aggregated client-side from `GET /grants?department=`; drill-down via
  `GET /projects/{ref}/summary` and `GET /grants/{id}/shares`), `apiPatch`, `useGrantAction/useProjectSummary/useGrantShares`.
  Playwright +1 (28). No vitest added. No spec/PLAN differences. One small process slip: a `sed -i` was used once
  for an import edit in `queries.ts` (formatter/lint passed afterwards).
- Step 2.6: PMO page (`/pmo/:id/:tab`, pool|grants), `GrantList` (shared, optional `actions` column for 2.7),
  `GrantWizard` (the final summary step reuses `ChangeSummaryDialog`), `usePmoPool/useGrants/useGrantWrite`,
  `apiAs` e2e helper. A department scope with role `pmo` routes to `/pmo/<dept>/pool`. Vitest +2, Playwright +2.
  The wizard group list and model choices come from the department summary (PMO may view it); `he.pmo` added;
  the four error codes already existed in `errors.ts`. No spec/PLAN differences.
  Latest runs: pytest 453 passed / 5 skipped, vitest 26, Playwright 29 (all green when run to completion).
  Intermittent, unrelated flakes seen in three full runs, each passing alone and on rerun: Playwright
  `wallet.spec.ts` U4 (snapshot timing), `resetData()` failing in `scripts/seed.py --reset` (hit
  `member-actions.spec.ts` U10), and pytest `tests/api/test_shares.py::test_upn_outside_group_scope_forbidden`.
- Step 2.5: reconciliation uses the existing desired builders, durable outbox, audit endpoint and
  critical notifications. Delayed five-minute scheduler job uses lock id 3 and the injected clock.
  Owned allocation/status fields are repaired; roles are excluded, missing objects are warned and
  counted without recreation, and in-flight entities are skipped before and after live reads.
  No new API prefix or frontend changes. PRD updated. All 14 new tests pass; final test.sh green
  (counts/timing above). The final empty-member-model-map refinement also passed the focused
  reconciliation/desired-member subset (14 tests). No spec/plan conflicts newly identified.
  No commit or push: the builder owns the step commit because .git is read-only.
- Review 2.1-2.4: m1 deferred as requested. Plan: insert an email-delivery outbox row in the
  notification transaction, with a stable notification delivery key; deliver only committed rows,
  retry separately, and test rollback/retry with email enabled.
- Review 2.1-2.4: FR-P6's explicit unused-only reduction takes precedence over PLAN Step 2.1's
  active-only team formula (and SPEC §8.1's shorthand). Spent credit remains in the closing month's
  team ceiling/model pot; closing a grant must not consume the group's base headroom a second time.
  Return amounts use spend precision rather than PLAN §1's two-decimal allocation precision.
- Review 2.1-2.4: the old Step 2.3 note about a crash losing member recomputation is superseded:
  each closing transaction now queues its own recomputation. A member touched by multiple closing
  transactions can have multiple safe, latest-state member updates; no volatile final phase is required.
- Step 2.4: `pending_placement` is written only by onboarding (`place` always resolves a pending user, it never leaves one pending). Scans use the snapshot (`Spend`) and the wallet views
  (`_Group`), so a notification appears after the next spend snapshot; dedupe keys are `emp:<period>:80|100` (per person and kind), `grp:<id>:<period>:80|100`, `grant:<id>:expiring|underused|received`,
  `pending:<upn>:<since>`, `moved:<upn>:<request_id>`, `outbox:<write_id>`. A grant is "underused" when Σ shares' attributed/stored used < 20% of the amount (no shares = 0). Notification
  payloads carry their `key`. `notify_scan` is split from `notifications` so grants/lifecycle/onboarding can import the latter without a cycle through `views`. Endpoints write no audit row
  (personal UI state). Email is sent through `asyncio.to_thread` inside the request transaction; a failure only logs. Tooling slip: one scratch file written via Bash `printf` to `/tmp`
  (outside the repo, unused afterwards).
- Step 2.3: `WriteContext.enqueue` now sets the chain predecessor only from `chain=True` rows (before, any earlier row of the context became the predecessor), otherwise the share-return
  `/team/update` queued before the move chain would have held the move up. `build_jobs` registers only the grants job for now. `Job` has an extra `timeout_s` field (from `scheduler_lock_timeout_s`).
  Phases 2-3 close with `member_update=False` and phase 4 enqueues the single `/team/member_update` per touched member; a crash between a phase commit and phase 4 loses that recompute
  (the share's own expiry in LiteLLM still ends the increase, and the team `/team/update` is queued in the closing transaction). Move/delete queue no `/team/member_update` for the closed shares
  (the membership goes). Tests create shares to the same member in two grants for "next share" (one active share per member per grant). `PATCH`-shortening a grant below its share
  expiry is not possible through the API, so the grant-expiry test edits `expires_at` in the DB. Tooling slip: one `sed -i` on `tests/scheduler/test_grants_job.py` (import path) and
  empty `cat >>`/`python3` heredocs through Bash that wrote nothing.
- Step 2.2: DR9 (no grant shares to yourself, any role) wins over FR-P3's "the representative can"; a self share is always `SELF_ALLOCATION_FORBIDDEN`.
  "Shared" for the grant ceiling is Σ amount of all shares (active and closed), since closed amount = used + returned. Attribution across periods treats a share as present in a month
  overlapping `[grant.starts_at, share.expires_at]`; `/me/wallet` and the share list attribute from the current-period snapshot only (active shares). A share without expiry falls back to the grant's.
  `test_grants_read::test_pmo_pool_formula` set up an *active* share with a stored `used`; close now recomputes usage, so the setup uses a closed share (same assertions).
  Grant writes now take the PMO lock then the GROUP lock (shares take GROUP only). Tooling slip: one `sed -i` on a test file (fixed afterwards with Edit).
  New routes live under `/grants`, already in the vite proxy regex.
- Step 2.1: the group summary `grants` list and `/grants` rows show only what the spec needs; closing a pending grant enqueues no `/team/update` (LiteLLM never saw it).
  A closed grant's `used` is Σ of its shares' `used` (0 until Step 2.2). `/pmo-pool` for a past period uses live pending/active grants plus grants closed in that period.
  PMO of another department creating a grant gets 422 `SCOPE_FORBIDDEN`; a user with no PMO binding anywhere gets 403 (PLAN's wording). `/grants` and `/projects` were already in the vite proxy regex.
  Existing `datetime.now()` calls (period_ctx etc.) from Phase 1 are untouched; new grant code uses `app.state.clock` only.
  `uq_notification_dedupe` (Step 2.4) is in migration 0003 as PLAN says; the indexes are not declared on the SQLAlchemy models (migration is the source).
- Step 2.1 flake: `e2e/member-actions.spec.ts` "U9 head kicks and restores a member" once timed out waiting for `sync-badge` = applied (stayed `pending` for 60 s) in a full run;
  it passed on the next two full runs. Unrelated to grants (outbox drain timing in the E2E stack).
- Step 11: `/me/shares` rows have no project name or per-share usage yet (Phase 1 has no grants), so `SharesTable` shows the grant id and `used` defaults to 0.
  The usage chart draws daily stacked bars (left axis) plus a running-total line against the cap line, since a monthly cap cannot be compared with a single day.
  The "contact" alert for group/department blocks names the group head from `owners.base_cap`.
- PLAN.md Step 1 says "20 tables" but lists 18; SPEC §8.2 has exactly 18. Followed the spec (18).
- pgembed URI `postgresql://postgres:@/postgres?host=/tmp/..` works with `postgresql+asyncpg://`:
  SQLAlchemy forwards `host=` to asyncpg as a unix-socket dir (verified). `to_asyncpg_url()` does the swap.
- pgembed's bundled `psql` (`PostgresServer.psql`) segfaults in this sandbox; databases are created via
  asyncpg (`app.db.create_database`) instead.
- `PostgresServer` needs a `Path`, not `str`, for pgdata.
- `litellm_ro` role + SELECT grants are created by `fake_litellm.db.init_db` (called from `scripts/dev_db.py` and the test fixtures).
- §8.2 gives no PK for `spend_snapshot` / `role_binding` (nullable `model` / `scope_id`): no DB PK, ORM maps the
  columns as identity. CHECK constraints only for statuses/kinds the spec enumerates (grp, employee, grant,
  grant_share); `sync_action`, `litellm_write`, `service_key` statuses are unconstrained until their steps.
- ruff (format) also rewrote Python snippets inside *.md, which made `ruff format --check .` fail on a clean checkout; `*.md` is now excluded in pyproject.

- Step 2: SPEC §10 also lists `/organization/member_add` (org_admin for department reps, §8.1); PLAN's method list omitted it,
  so `organization_member_add` was added. Other §10 endpoints (member_update, user/update, service-account, jwt update/delete/list)
  are left for the steps that need them.
- Step 2: `KeyUpdate`/`KeyBlock` take `key` (token hash or plaintext; the fake accepts both). `/health/liveliness` is unauthenticated, as in LiteLLM.
  `spend_rows` buckets days in UTC; the caller maps periods to `APP_TIMEZONE`.
- Step 2: the fake does not yet enforce R14 (team budget <= org budget at allocation time); add it in Step 3 if the contract tests need it.
- Step 3: the contract tests did not need R14, so the fake still does not enforce it. Model budgets (R5) sum all SpendLogs and ignore `time_period`.
  Fake error scopes: `key|team_member|team|organization|team_model|member_model` (a key-level model entry reports `key`). Fake JWT issuer is
  `https://adfs.test`, secret from `FAKE_JWT_SECRET`. The `real` target cases are untested here (skipped without env).

- Step 4: permission helpers are `async f(session, principal, id)` and return the authorizing role. An unknown entity, or one the actor cannot
  view at all, gives 404; visible but not permitted gives 403. `can_edit_dept_budget(principal)` is sync (admin only).
- Step 4: scopes in `/me` carry `role` per entry; a global binding (admin, global viewer) appears as `{kind:"platform", role}`; `roles` also lists the implicit `employee`.
- Step 4: the fixture directory's users double as the seed's employees (group = the user's DN that matches a group's `ad_group_dn`).
  `SEED_FILE` setting (default `seed/dev.yaml`). Bad state/nonce/signature at the OIDC callback returns 401 `UNAUTHENTICATED`.
- Step 4: the `groups` claim in the session is filtered at login; a `role_binding` added later takes effect only after re-login. `joserfc` (an authlib dependency) is imported directly.

- Step 5: `drain(sf, client, settings)` takes settings (JWT issuer, builders). `write_response` commits the transaction. A replayed request returns 200 with
  the audit rows' `after` values (they include context ids such as `department_id`). `/team/member_add` and `/user/new` treat a 400 "already ..." as applied.
  Builders for `/team/member_update`, `/key/update`, `/team/update`, `/organization/update` exist for later steps. The enqueue-time payload is a preview
  (null when state is missing, e.g. the JWT mapping before the key exists); the apply-time payload overwrites it.
- Step 5: one edit to app/seed.py's `_provision` and `ruff format app/outbox/worker.py` were done via a script/formatter run instead of Edit/Write.

- Step 6: split always writes one `department` audit row (pmo_pool/reserve) plus one `group` row per changed base; `archived` groups are left out of the sum.
  Pot checks cover only the models in the request, so an old over-allocation of another model does not block an edit. `ApiError` bodies convert Decimal to float.
  `set_split` has an `await asyncio.sleep(0)` after the sum check; the lock test patches it to widen the race window. `check_limit` exists and is unit tested but is not used until Step 7.
  A PMO/viewer split returns 403; a user who cannot see the department at all (group head) gets 404.

- Step 7: `check_limit` now follows D7 (admin may exceed `max`, never go below `min`); Step 6's version let admins bypass `min`. Bulk caps authorize every row first
  (all-or-nothing); the envelope code is SELF_ALLOCATION_FORBIDDEN if any row is a self-target, else the first row error, with `details.rows`. `SELF_ALLOCATION_FORBIDDEN`
  carries `details.owner_role` (member→group_head, group_head→department_rep, rep/admin→platform_admin). A department rep writing a plain member's values gets 422
  `SCOPE_FORBIDDEN` (PLAN rule 2: head or admin only). Outbox entity for member writes is `employee` (id = upn), as in the seed. `GET /policies` omits the internal
  `member_defaults.group.*` rows. A missing concurrency/rpm policy row skips the range check. SPEC §11.1 and PLAN agree on all Step 7 paths and fields.

- Step 8: `take_snapshot(sf, reader, now)` takes the session factory and an aware `now` (its tzinfo is APP_TIMEZONE). Month rows (`YYYY-MM`) and day rows
  (`YYYY-MM-DD`, UTC date from SpendLogs) both hold a total (`model` NULL) and per-model rows. Day rows are only kept for days inside the calendar month, so spend in the
  last hours of the previous UTC day (local midnight offset) is in the month total but not in a day row. The previous period is only filled when it has no rows.
- Step 8: I did not create `app/schemas/wallet.py`; the read models are plain dicts built in `app/services/views.py` (money serialises as JSON numbers). PLAN's `owners_of`
  became `owner_of(db, directory, upn, group)`; `owners.holders()` lists the people bound to a role on a scope. SPEC §11.1 lists `/me/usage` without `sync_status`, so `/me/usage` has
  only `[{day, model, spend}]`. `/me/wallet`, summaries and members carry `period`, `reset_date`, `spend_taken_at`, `read_only`; `sync_status` is per entity.
- Step 8: `state` priority is blocked_own > blocked_parent > over_allocated > near_limit > healthy (PLAN left over_allocated's place open); `left`/`blocked` give `muted`.
  Binding level ties go to the narrower level (self, then group). Past periods take caps, limits, group bases and department budget/pmo/reserve from `values_as_of`; pots,
  allowances, active grants and shares always use the current rows (no audit history for them yet). `/groups/{id}/members` includes `left` rows (muted) and excludes them from sums.
- Step 8: Tooling slip: one `sed -i` on `app/worker.py` (a docstring) was run through Bash instead of Edit.

- Step 9: Status line above counts: 217 passed, 4 skipped after this step. Finding shape: `{source, level, entity_id, name, allocated, total, department_id, model}`; response `{"findings": [...]}`.
  `group_model` findings use the department id (Σ pots across its groups vs the department allowance), `member_model` the group id. Live findings for an org/team unknown to WM carry the
  LiteLLM id and no department, so only admin and global viewers see them. A group-scoped viewer, PMO and heads get 403 on the report. If the LiteLLM database is unreachable the report
  returns the WM checks only. The seed's "D1 staff" group (3 members at 10000 on a 20000 base) is over-allocated from the start, so it shows up in every report; tests filter it out.
- Step 9: `/outbox` and `POST /outbox/{id}/retry` are admin only; retry needs the usual reason header and writes an `outbox` audit row. `/audit`: non-admins must give `entity` and `id`
  (employee: own record, or `can_view_members` of the person's group; group: `can_view_members`, so PMO and viewers get 403/404 per D11; department: `can_view_department`).
  `/status` caches `client.health()` for 10 s per app instance. `/metrics` is unauthenticated and refreshes outbox depth and snapshot age from the DB on each scrape; the
  `wm_overallocation_findings` gauge is set by the worker and by `/reports/overallocation` (the worker is a separate process, so its gauge is not on the API's `/metrics` until Phase 2/3 adds a worker scrape port).
  `wm_validation_rejections_total` counts 422 `ApiError`s by code.

- Step 12: the Members table hides members who left (the server excludes them from sums too). The footer total is the group base; the overallocation factor is not
  in the summary, so the client preview uses factor 1 (the server stays authoritative). The side panel's limits write resends the member's `model_tpm_limits` unchanged
  because the endpoint replaces them. The defaults dialog only sets the cap: the stored default model caps cannot be read back, so saving sends `model_caps: {}`.
  Row errors show a short label; the full Hebrew message with the server's numbers is in the change summary. Tabs Grants, Models and Service keys are Phase 2.

- Step 13: PMO pool and reserve are WM-only (no LiteLLM call), so their sync badge is always "applied"; the pending badge shows on group rows (team budget).
  The drill-down to a group has no separate "read mode": the rep's edit rights come from the server (`editable` per row), so ordinary members show read-only with their owner.
  Split sends `budget_period: "1mo"` with a budget edit because the summary does not return it. The Department page has only Overview and Split; Models, PMO and Settings are later phases.
  PLAN's rtl test wording ("first column x greater than last") is checked on the groups table in RTL. Flaky once: `group.spec.ts` U3 banner wait timed out in one full run
  (the banner depends on the 10 s `/status` cache + 5 s poll); it passed on rerun. `wallet.spec.ts` U4 (30 s wait on the 2 s snapshot loop + 15 s wallet refetch) failed once too and then passed in two reruns. Both are timing flakes in
  Step 10–11 tests; consider shortening the UI refetch interval in the E2E stack.
 

- Step 1b.4: the side panel closes after any member action; the row then shows the sync badge. A kicked member is `status: blocked` (muted row, "מושעה"); delete sets `left`,
  so the row disappears at once. `fakeFail(endpoint, times)` exists in `e2e/fixtures.ts` but no test needs it yet. The vitest file uses `@vitest-environment jsdom` (first jsdom test).

- Step 1b.5: the placement group select offers every non-archived group of the department (admin with an unknown department: all departments), including the staff group;
  the server stays authoritative on department scope. `tests/api/test_department_summary.py` expectations gained the new `place` key. A group head gets 404 on the
  department pending list (no role on the department), so the tab is absent.

## Questions for owner
**Owner approved all 21 defaults below on 20/10/2026, including 10 (keep placeholder concurrency/rpm until pilot), 11 (client id claim order appid, client_id, azp) and 12 (no static key, ADFS JWT mapped by upn).**
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
11. **Onboarding portal authentication (D14).** Two ADFS JWTs against the ADFS JWKS: the portal's client-credentials token (allow-listed `ONBOARDING_CLIENT_IDS`, WM audience) and the user's ADFS JWT (`upn`, `ONBOARDING_USER_AUDIENCES`; empty disables onboarding). Dev: HS256 only with `DEV_AUTH=1`. Please confirm the client id claim (`appid` vs `azp`) and the user token audience.
12. **Key delivery (D15).** The personal key secret is never returned; Claude Code uses the user's ADFS JWT mapped by upn, so no rotate action. A static key handed out by the portal would need "show once" plus rotate and reverses NFR5.
13. **Delete order (D16).** Mapping → key → membership → user. The owner's draft removed the membership first, which would leave the key on a team without membership.
14. **Department representative kick and restore (D17).** Yes for members and group heads of their department; not for other representatives.
15. **Over-allocation from default caps (D18).** Onboarding, place and move add the full default cap; the group shows the warning, the action is not rejected.
16. **Unknown-department pending users (D19)** are handled by the platform admin only. **A deleted user calling onboarding again (D20)** gets `blocked`; only an admin can re-place them.
17. **Move details (D21).** Personal model caps cleared, concurrency/rpm kept. Moving a group head does not change their AD role.
18. **JWT mapping inactive flag (D22, V6).** If LiteLLM has no such flag, kick deletes the mapping and restore recreates it.
19. **In-progress guard (D23).** Move, delete and place return 409 `LIFECYCLE_IN_PROGRESS` while earlier changes are unapplied; kick and restore are always queued in order.
20. **Public repository (D25, NFR10).** Placeholders only; hygiene test scans tracked files. Before publishing, scan the full git history and provide internal names via a gitignored `.public-denylist`.
21. **F5/F6 wording.** F6 (a manager's own cap, DR9) is unrelated to sync and is kept; F5 and F7 are replaced, F8–F10 added.

- Step 1b.1: SPEC §8.2 v0.3 has 16 tables (PLAN counts differently); followed the spec. A 404 on `/jwt/key/mapping/update` or `/delete` in the fake means no such mapping.
  `/_test/fail` keys on the request path and is cleared by `/_test/reset`. The fake's `LiteLLM_JWTKeyMapping.is_active` column is added by `create_all` only on a fresh fake
  database; an existing dev fake DB needs `scripts/dev_db.py` / a reset. `held` rows in `drain` are rows whose `depends_on` row is not `applied`; a transient error still ends the drain.
  The hygiene test treats single-label hosts (`fake`, `postgres`) as test aliases and checks every tracked text file; lock files skip the URL check. Tooling slip: one no-op `sed -i /dev/null` call (failed, changed nothing).
  No new API path prefix was added in this step, so `frontend/vite.config.ts` is unchanged.
- Step 1b.2: lifecycle functions take the `principal` as well as PLAN's `(ctx, directory, group_id, upn)` (permissions need it). Check order: role (404/403) → self / tier (422) → target in URL group
  (422 `SCOPE_FORBIDDEN`) → state (409) → `LIFECYCLE_IN_PROGRESS` (move, delete only) → target group (404 unknown, 422 outside the rep's department, 409 same or non-active group).
  `CONFIRMATION_MISMATCH` is checked after the role and tier checks. Self errors carry `details {action, owner_role}`. Delete keeps `group_id` (history), sets `status=left`, `left_at`, clears model caps
  and `key_token_hash`, and skips the `/key/delete` row when the employee has no key hash. A moved employee gets the target group's `default_member_cap`; if that is 0 the fake rejects their requests (budget), as in LiteLLM.
  `/groups/{id}/members` no longer lists `left` employees (F10). No new path prefix (`/groups` is already proxied). `provision()` is not called yet (Step 1b.3 uses it); the seed still queues the Joiner steps per endpoint.
- Step 1b.3: `provision()` is now used by onboarding and place. SPEC wins over PLAN: the portal token's client id claim is `appid`, else `client_id`, else `azp` (§9.2; PLAN names none); a portal
  token carrying `upn` gives 401. `install` is omitted (not null) unless `ready`; its `ANTHROPIC_AUTH_TOKEN` is a text placeholder. Replays of `POST /onboarding` find the existing employee
  and return 200 with the same `request_id` (uuid5 of upn + key), not the audit-replay shape. Empty `ONBOARDING_USER_AUDIENCES` makes both endpoints 404. Directory is only read for unknown upns
  (existing employees never get a 503). Missing `ADFS_JWKS_URL` falls back to the `jwks_uri` of the OIDC discovery document. `place` check order: employee 404 → role (`can_place`: rep needs a known
  department, else 404) → self/tier → state 409 (admin may also place `left`) → `LIFECYCLE_IN_PROGRESS` → group 404 → rep's group must be in the employee's pending department (422 `SCOPE_FORBIDDEN`).
  The pending lists carry `department_id|name` besides SPEC's four fields; the platform list is admin only (403 for others). Seeded pending users are created with `pending_since` = seed time.
  A15 sets B1's `default_member_cap` in the test because the seed leaves it 0 (the fake rejects a 0-budget member). `/status` does not show the pending count yet.
- Step 10: Chromium (headless shell) launches here without extra system libraries; no `.cache/pwlibs` workaround needed.
- Step 10: `eslint-formatter-unix` is a root devDependency because the lint hook calls `eslint -f unix`.
- Step 10: the scope switcher is a native `<select>` (always in the DOM, keyboard accessible); the period is the `?period=YYYY-MM` URL param.
  The platform scope routes to `/platform` (placeholder). `/status` caches LiteLLM reachability for 10 s, so the outage banner takes up to ~15 s to show.
- Step 10: `apiPost(..., write?)` sends reason/idempotency headers only when `write` is given; `/auth/logout` and `/outbox/{id}/retry` are called without a reason.
- Step 10: while editing I appended `frontend/.e2e-state.json` to `.gitignore` with a shell `printf >>` (the content is correct; it was not a Write/Edit).
- Review 2.6 fixes: past-period grant creation blocked in the UI (read_only), wizard dates use the /me timezone, pool card remaining = available, PMO and representative scopes have distinct keys (`pmo:<id>`). Behavior is described by the existing PRD text, so no PRD change. No MINOR findings.
