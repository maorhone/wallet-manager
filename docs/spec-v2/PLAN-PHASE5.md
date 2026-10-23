## 2f. Steps (Phase 5: spec v2, "AI Visibility & Management" MVP)

> Branch: `phase-5` (Maor: build the new design on a new branch, keep the previous version). Sources: `docs/spec-v2/AI_VISIBILITY_MANAGEMENT_SOURCE_OF_TRUTH.md` (SoT), `docs/spec-v2/AI_VISIBILITY_MVP_WIREFRAMES.html` + `AI_MVP_WF_*.png` (WF-01..WF-08), `docs/spec-v2/MAOR-DECISIONS.md` (authoritative; overrides the gap analysis and the SoT where they differ).
> NOTE: GAP-ANALYSIS.md was lost in the 22/10 08:4x box swap; this plan was rebuilt from the SoT, the wireframes, the repo (main @ ca49ca5) and Maor's decisions.

The Phase 3/4 rules apply: §1 conventions, §11.2 error envelope, injectable clock, public repo, no external services, outbox for every LiteLLM write, audit for every write, Hebrew RTL UI. Additional rules:
- **Nothing is removed.** Existing pages, endpoints and behaviours stay working (legacy routes keep their tests). v2 adds a new shell and screens next to them.
- **Kept as today (Maor):** PMO pool and project grants (shown as a virtual pot "מענק פרויקט"); over-allocation (`overalloc_factor`, FR-V2 report) is NOT removed, v2 screens only display it; per-model quotas (department allowances, group model pots, employee model caps) stay as today and appear under an "מתקדם" section.
- **Excluded (Maor is clarifying with product):** section needs report and department recommendation (SoT §4.7, D-041 recommendation input). WF-03 shows the column as "בהמשך" with historical usage only. No step builds needs collection.
- **Terminology mapping:** organization (new) → department (existing) → group = מדור/section (existing `grp`, now nestable) → employee. Production system (3.3) = Internal System (department-funded). Org System = system inside the organization's virtual systems org. Internal Usage initiative = named record + LiteLLM service key(s) with `max_budget`.
- **Rate limits (Maor: per her spec):** only the AI team (`ai_team` role, admin implies it) changes RPM/TPM/concurrency. Section and department heads forward throughput requests; they do not reject or apply them.
- **Bulk allocation replaces** the employee's existing base quota (before/after shown).
- **Palette/structure (Maor: apply both):** CSS variables from the wireframes: `--bg #f2f4f7`, `--panel #fff`, `--line #d7dce2`, `--ink #1d2834`, `--muted #65717e`, `--sub #f7f8fa`, `--blue #49677e`, `--blue-light #eaf0f5`; font `Arial, 'Noto Sans Hebrew', sans-serif`; right-side nav with the wireframe areas.
- **Migrations:** one migration per backend step that needs it, numbered after the newest on main at build time (main had 0006). If main gains a migration later, renumber on the next main→phase-5 merge.
- **Time budget:** Phase 5 may add ≤ 12 Playwright tests and ≤ 120 s pytest growth.
- **Order:** backend 5.1–5.14, then UI 5.15–5.23 screen by screen. Each step ≤ ~1.5 h build. Size: S ≤ 45 min, M ≤ 90 min.
- **main keeps moving** (3.6, open-item fixes): merge main into phase-5 at step boundaries only, never mid-step.

### Step 5.1: Organization layer and nestable groups (schema) — M
**Spec:** SoT §2.7, §6.3; MAOR-DECISIONS 1+2 (dynamic hierarchy), 3 (org allocator = manager of the top node).
**Files:** `app/models.py` (`Organization`; `Department.organization_id`; `Grp.parent_group_id`), new migration, `app/seed.py`, `tests/db/test_org_hierarchy_schema.py`.
**Build:**
- `organization(id, name, departments_envelope, systems_envelope, budget_period, systems_litellm_org_id NULL, created_at)`; WM-only (no LiteLLM object for the org itself).
- Backfill: one default organization, every department attached. `Grp.parent_group_id` nullable FK, `CHECK (parent_group_id <> id)`, same department as parent (trigger or service check), depth ≤ 4.
- Role bindings gain `scope_kind='organization'` (org allocator role `org_allocator`).
**Tests:** `test_default_org_backfilled`, `test_group_parent_same_department`, `test_cycle_rejected`, `test_depth_limit`.
**Deps:** none.

### Step 5.2: Hierarchy API: create org, add groups above/below, move — M
**Spec:** MAOR-DECISIONS 1+2; §12.2 permissions pattern.
**Files:** `app/routers/platform.py`, `app/services/platform.py`, `app/permissions.py`, `app/routers/groups.py`, `tests/api/test_hierarchy_api.py`.
**Build:**
- `POST /organizations` (admin only). `POST /departments` gains `organization_id`.
- `POST /groups` with `parent_group_id` (department head/rep under the department or any descendant; group head under its own group; admin anywhere). `POST /groups/{id}/insert-parent` creates a group above an existing one (department head/admin).
- `PATCH /groups/{id}/parent` moves a subtree inside the department (department head/admin), audit row, no LiteLLM team change (teams stay flat in LiteLLM).
- Permission inheritance: the head of an ancestor group manages descendants; `org_allocator` = manager of the top node; admin can act for it.
**Tests:** `test_admin_creates_org`, `test_dept_head_adds_group_above_and_below`, `test_group_head_adds_subgroup_only_under_own`, `test_ancestor_head_manages_descendant`, `test_move_subtree_audited`, `test_non_admin_cannot_create_org_403`.
**Deps:** 5.1.

### Step 5.3: Budget math for nested groups — M
**Spec:** SoT D-055 (ledger, no Parent+Children double sum); existing §12.3 formulas.
**Files:** `app/services/views.py`, `app/services/groups.py`, `app/services/overallocation.py`, `app/services/validation.py`, `tests/api/test_nested_group_budget.py`.
**Build:**
- A child group's `base_budget` is carved from its parent group's allocatable: `parent allocatable = base_budget − Σ child base_budget − Σ member caps`. Top-level groups unchanged (carved from the department).
- Summaries roll up spend without double counting. Over-allocation factor behaviour unchanged (kept per Maor) and applies per node; FR-V2 report lists nested findings.
**Tests:** `test_child_budget_reduces_parent_allocatable`, `test_rollup_no_double_count`, `test_overallocation_kept_for_nested`, `test_flat_departments_unchanged` (regression).
**Deps:** 5.2.

### Step 5.4: Org Systems as a virtual org, key moves — M
**Spec:** SoT D-056, D-076, D-077; MAOR-DECISIONS 2 (virtual org that holds service/system keys).
**Files:** `app/models.py` (`ProductionSystem.funding in ('department','organization')`, `organization_id`, `owner_upn`, `responsible_department_id`), migration, `app/services/production.py`, `app/services/service_keys.py`, `app/outbox/*`, `fake_litellm/*`, `tests/api/test_org_systems.py`.
**Build:**
- On first org-system creation, outbox creates LiteLLM organization `orgsys-<org_id>` (stored in `organization.systems_litellm_org_id`); each org system is a team in it, funded from `organization.systems_envelope` (Σ ≤ envelope).
- `POST /service-keys/{id}/move {target_system_id}`: admin/org allocator moves an existing key to an org system (outbox `key/update team_id`), audit, history kept; spend after the move counts to the org system only.
- Org systems never count in the department's spend (responsible department is a label only).
**Tests:** `test_virtual_org_created_once`, `test_org_system_budget_within_envelope`, `test_move_key_updates_team_via_outbox`, `test_org_system_spend_not_in_department_totals`, `test_owner_view_only_403_on_writes`.
**Deps:** 5.1.

### Step 5.5: Internal Systems funded from the department envelope — S
**Spec:** SoT §5.1 (Internal System), D-076; MAOR-DECISIONS 6 (by ownership).
**Files:** `app/services/production.py`, `app/services/views.py`, `app/models.py` (`ProductionSystem.managing_group_id`), migration, `tests/api/test_internal_systems.py`.
**Build:** existing production systems become `funding='department'`; their budget is deducted from the department allocatable (in addition to `prod_budget` compatibility: `prod_budget` becomes derived = Σ internal system budgets, legacy endpoints keep working). Optional managing section (delegation); owner is view+request.
**Tests:** `test_internal_system_budget_reduces_department_allocatable`, `test_managing_section_can_edit_budget_within_delegation`, `test_legacy_production_endpoints_still_pass`.
**Deps:** 5.4.

### Step 5.6: Internal Usage initiatives — M
**Spec:** SoT D-067, §4.8.5, WF-06; MAOR-DECISIONS 11 (initiative = LiteLLM key(s) with max_budget).
**Files:** `app/models.py` (`Initiative(id, name UNIQUE per department, owner_upn, group_id, period_start, period_end, budget, status, tech_status)`; `ServiceKey.owner_kind` gains `initiative`), migration, `app/services/initiatives.py`, `app/routers/initiatives.py`, `tests/api/test_initiatives.py`.
**Build:** section head creates an initiative (directly or from an approved request, 5.7); budget carved from the section allocatable; keys attached to it get `max_budget` = share of the initiative budget (outbox). Record creation ≠ key creation: `tech_status` = `not_connected|pending|connected`. Owner: `GET` only.
**Tests:** `test_section_head_creates_initiative_budget_carved`, `test_key_attached_gets_max_budget`, `test_duplicate_name_409`, `test_owner_read_only`, `test_record_without_key_is_not_connected`.
**Deps:** 5.3.

### Step 5.7: Requests center backend: credits and new initiative — M
**Spec:** SoT D-044, D-068, D-074, §5.1.5 flows 2–3.
**Files:** `app/models.py` (`Request(id, type credits|initiative|throughput, scope_kind, scope_id, requester_upn, handler_role, handler_scope, status submitted|forwarded|approved|rejected|applied|pending_apply|failed, amount, payload JSONB, created_at, updated_at)`, `RequestEvent`), migration, `app/services/requests.py`, `app/routers/requests.py`, `app/services/notifications.py` (kinds `request_*`), `tests/api/test_requests.py`.
**Build:**
- `POST /requests`, `GET /requests?box=mine|to_handle&type=&status=`, `GET /requests/{id}`, `POST /requests/{id}/decide {approve|reject, amount}`.
- Credits: section head approves → raises the employee's base cap (or a share) within allocatable; initiative: approve → creates the initiative via 5.6. One record and one status regardless of entry point. In-app notification to requester on decision.
**Tests:** `test_employee_requests_credits_section_head_approves_cap_raised`, `test_initiative_request_approved_creates_initiative`, `test_reject_notifies_requester`, `test_filters_by_type_and_status`, `test_other_section_head_403`.
**Deps:** 5.6.

### Step 5.8: Throughput requests and AI-team-only rate limits — M
**Spec:** SoT D-061, D-065, §5.1.5; MAOR-DECISIONS 5 (per her spec).
**Files:** `app/services/requests.py`, `app/permissions.py`, `app/routers/{groups,members,departments,policies}.py` (rate-limit writes), role `ai_team`, `tests/api/test_throughput_route.py`.
**Build:**
- Throughput request route: requester → section head (forward) → department head (forward) → AI team (`apply` with new RPM/TPM/max_parallel via outbox, or `reject`). Section/department heads cannot reject or apply throughput, only forward (and comment).
- Rate-limit fields (`max_parallel`, `rpm_limit`, `tpm_limit`, `model_tpm_limits`) become writable only by `ai_team`/admin; department reps and group heads get 403 (behaviour change approved by Maor; update affected existing tests, note in PROGRESS).
- Apply status `pending_apply` until the outbox write succeeds, then `applied` / `failed` (D-071).
**Tests:** `test_throughput_forwarded_section_then_department_then_ai_team_applies`, `test_section_head_cannot_reject_throughput`, `test_rep_rate_limit_write_403`, `test_ai_team_apply_pending_then_applied`.
**Deps:** 5.7.

### Step 5.9: Transfers of unused balances (drawer backend) — M
**Spec:** SoT D-069, D-071, WF-01B.
**Files:** `app/models.py` (`Transfer(id, source_kind, source_id, target_kind, target_id, amount, status, created_by, created_at)`), migration, `app/services/transfers.py`, `app/routers/transfers.py`, `tests/api/test_transfers.py`.
**Build:** `POST /transfers/preview` and `POST /transfers` between employees / initiatives / sub-groups / internal systems inside the actor's authority; only unused balance is releasable; total budget unchanged; one ledger row + audit + in-app notifications to affected; status `done|pending_apply|failed` from the outbox. Org systems are never a source or target from a department.
**Tests:** `test_preview_before_after`, `test_cannot_move_spent_amount_422`, `test_outside_authority_403`, `test_org_system_not_allowed`, `test_total_unchanged_and_audited`.
**Deps:** 5.6.

### Step 5.10: Bulk allocation (replace) backend — S
**Spec:** SoT D-066, D-075, WF-07; MAOR-DECISIONS 8 (replace).
**Files:** `app/services/bulk.py`, `app/routers/groups.py`, `tests/api/test_bulk_allocation.py`.
**Build:** `POST /groups/{id}/bulk-allocation/preview {upns|all, amount, overrides:{upn:amount}}` → rows with before/after base cap, effective total, remaining allocatable, over-allocation warning (current factor kept, not blocked beyond today's rule); `POST .../bulk-allocation` commits in one transaction, outbox per employee, audit.
**Tests:** `test_preview_replaces_existing_cap`, `test_overrides_applied`, `test_commit_atomic_and_audited`, `test_over_allocation_follows_existing_factor`.
**Deps:** 5.3.

### Step 5.11: Rate card and metered usage value — S
**Spec:** SoT D-049, D-054, §7.1.1; MAOR-DECISIONS 14.
**Files:** `app/models.py` (`RateCard(model, input_per_1m, output_per_1m, currency, effective_from)`), migration, `app/routers/policies.py` (`GET/PUT /rate-card`, admin), `app/services/spend.py` (snapshot also stores prompt/completion tokens per model), `fake_litellm` (tokens in SpendLogs), `tests/api/test_rate_card.py`.
**Build:** metered value = Σ tokens × rate per model; if any consumed model has no rate → `null` and the UI shows "לא זמין". Never summed with wallet debit.
**Tests:** `test_metered_value_from_tokens_and_rates`, `test_missing_rate_returns_null`, `test_rate_card_admin_only`.
**Deps:** none (backend).

### Step 5.12: Per-project/tool aggregation by request header tag — M
**Spec:** SoT D-059, D-063; MAOR-DECISIONS 12 (client hook adds a header tag with the project name).
**Files:** `clients/hooks/` (README + sample hooks: shell wrapper/env for Claude Code `ANTHROPIC_CUSTOM_HEADERS`, Codex/OpenAI-compatible clients, generic), `fake_litellm/enforcement.py` (store tags from the header into SpendLogs `request_tags`), `app/litellm_client/readonly.py` (`spend_by_tag`), `app/routers/reports.py` (`by=tag`), `app/config.py` (`WM_PROJECT_TAG_HEADER`, default `x-litellm-tags`; verify against the deployed LiteLLM version in Phase 0), `tests/api/test_usage_by_tag.py`.
**Build:** tag format `project:<name>` (and optional `tool:<name>`); untagged spend shown as "ללא תיוג". Aggregation per employee, initiative and section.
**Tests:** `test_tagged_requests_aggregated_by_project`, `test_untagged_bucket`, `test_tag_visibility_follows_member_rules`.
**Deps:** none (backend).

### Step 5.13: Block reasons and user status — M
**Spec:** SoT D-060–D-062, D-064, §5.1.4 cases a–e; MAOR-DECISIONS 13 (MUST: user sees whether rate limit, wallet exhausted or service down).
**Files:** `app/services/blocks.py` (classifier), `app/litellm_client/readonly.py` (failed requests from SpendLogs/ErrorLogs), `fake_litellm` (error rows: 429 budget, 429 rpm/tpm/parallel, 503 congestion, 401/403, 5xx), `app/routers/me.py` (`GET /me/status`), `app/routers/reports.py` (`GET /reports/blocks?scope=` aggregated by reason), `tests/api/test_block_reasons.py`.
**Build:** reasons `CREDITS_EXHAUSTED`, `RATE_LIMIT` (sub: rpm|tpm|concurrency, Retry-After if known), `MODEL_CONGESTION`, `ACCESS_DENIED`, `SERVICE_ERROR`, `UNKNOWN` — never map every 429/timeout to budget. Each reason carries a next-best-action code. `/me/status`: wallet state, limits (progressive detail), last blocks with reason, `taken_at`. Managers: counts by reason and consumer, no request content.
**Tests:** `test_budget_429_classified_credits_exhausted`, `test_parallel_limit_with_positive_balance_is_rate_limit`, `test_5xx_is_service_error_not_budget`, `test_unclassified_is_unknown`, `test_manager_aggregate_scope_only`.
**Deps:** none (backend).

### Step 5.14: Service status (health polling) — S
**Spec:** SoT D-060, case (e); MAOR-DECISIONS 13 (status screen).
**Files:** `app/scheduler/jobs.py` (`run_service_health`, interval policy), `app/models.py` (`ServiceHealth(model, state ok|limited|loaded|unknown, detail, checked_at)`), migration, `app/routers/platform.py` (`GET /status/service`, all authenticated users; detail for ai_team/admin), `fake_litellm` (`/health` per model, switchable), `tests/scheduler/test_service_health.py`.
**Build:** stale (> 2× interval) or missing → `unknown` "עודכן לאחרונה ב…", never "תקין".
**Tests:** `test_health_states_mapped`, `test_stale_shows_unknown`, `test_detail_hidden_from_employee`.
**Deps:** none (backend).

### Step 5.15: UI shell v2: palette, navigation, shared components — M
**Spec:** SoT D-072, D-073, §5.2; wireframes shell; MAOR-DECISIONS 9.
**Files:** `frontend/src/styles/v2.css` (palette vars), `frontend/src/v2/Shell.tsx`, `frontend/src/v2/components/{KpiCard,DistributionBar,FundingTag,StatusPill,AttentionList,Drawer}.tsx`, `frontend/src/routes.tsx` (`/v2/*`), `frontend/src/i18n/he.ts` (`he.v2`), `frontend/e2e/v2-shell.spec.ts`.
**Build:** right-side nav: ה-AI שלי / ניהול תקציב - מדור / תקציב ומערכות - מחלקה / הקצאה ארגונית / בקשות / מערכות באחריותנו / מצב שירות / איך זה עובד; items by accumulated roles and scope; last area restored from localStorage with safe fallback when permission was removed. `/v2` becomes the default landing after login; old pages stay under their routes.
**Tests:** Vitest `DistributionBar.test.tsx` (three states sum to total; over-allocated marker); Playwright `employee sees only personal items; section head sees budget items; last area restored`.
**Deps:** backend not required (uses existing `/me`).

### Step 5.16: UI WF-04 Personal ("ה-AI שלי") — S
**Files:** `frontend/src/v2/pages/Personal.tsx`, queries for `/me/wallet`, `/me/usage`, `/me/status`, `reports/usage?by=tag`.
**Build:** one wallet (Code + personal MaaS), usage by actual model and by project tag, limits with progressive disclosure, service status pill, last block reason with next action, CTAs "בקשת Credits" / "בקשת Throughput" (open request form).
**Tests:** Playwright `employee blocked by concurrency with positive balance sees RATE_LIMIT explanation and throughput CTA`.
**Deps:** 5.12, 5.13, 5.15 (5.7 for submitting).

### Step 5.17: UI WF-01 Section budget + transfer drawer (WF-01B) — M
**Files:** `frontend/src/v2/pages/SectionBudget.tsx`, `TransferDrawer.tsx`; backend `GET /budget/overview?scope=` and `GET /attention?scope=` (small read-only endpoints in `app/routers/views.py`, built here: 4 KPIs, bar, rows; attention groups requests / shortage risks (≥ policy threshold, default 85%) / reallocation opportunities (unused grants alongside pending requests)).
**Build:** period + 4 KPI (metered value secondary, "לא זמין" when null) → bar → "דורש את תשומת לבך" (3 groups) → table of employees, initiatives, sub-groups → drawer transfer with preview/confirm; CTA to bulk screen; per-model quotas under "מתקדם" (existing components).
**Tests:** `tests/api/test_budget_overview.py` (kpis, attention groups); Playwright `section head transfers unused credits with preview`.
**Deps:** 5.9, 5.11, 5.15.

### Step 5.18: UI WF-07 Bulk allocation — S
**Files:** `frontend/src/v2/pages/BulkAllocation.tsx`.
**Tests:** Playwright `section head sets a uniform amount with one override, sees before/after and remaining, confirms`.
**Deps:** 5.10, 5.15.

### Step 5.19: UI WF-02/02B Department: budget, comparison, systems, hierarchy — M
**Files:** `frontend/src/v2/pages/Department.tsx`, `HierarchyEditor.tsx`.
**Build:** 4 KPI → section comparison (with fair denominators) + period comparison → tabs "התקציב שלנו" / "מערכות באחריותנו" (Internal Systems `מחלקתי ייעודי`, Org Systems `ארגוני`, never summed) → hierarchy tree with "הוסף קבוצה מתחת / מעל" and move (5.2).
**Tests:** Playwright `department head adds a group above a section and sees org systems outside department totals`.
**Deps:** 5.2–5.5, 5.15, 5.17 (overview endpoint).

### Step 5.20: UI WF-05 Requests center — M
**Files:** `frontend/src/v2/pages/Requests.tsx`, `RequestForm.tsx`.
**Build:** tabs "שלי" / "לטיפולי", filters type+status, detail with history, actions by role (approve/reject credits and initiative; forward-only throughput for heads; apply for AI team). Action Center items link here.
**Tests:** Playwright `employee submits throughput; section head forwards; department head forwards; AI team applies; requester sees applied`.
**Deps:** 5.7, 5.8, 5.15.

### Step 5.21: UI WF-06 Initiative / system details — S
**Files:** `frontend/src/v2/pages/Initiative.tsx`, `SystemDetail.tsx`.
**Build:** budget/consumption/balance, technical connection status, limits, blocks by reason, trend by model, key alias only (no secret), funding owner, "בקשת שינוי"; admin/org allocator: "העבר מפתח למערכות ארגוניות".
**Tests:** Playwright `owner sees initiative usage read-only and opens a change request`.
**Deps:** 5.4, 5.6, 5.13, 5.15.

### Step 5.22: UI WF-03 Organization allocation (two envelopes) — S
**Files:** `frontend/src/v2/pages/OrgAllocation.tsx`; `GET/PUT /organizations/{id}/envelopes`, `PUT /organizations/{id}/departments/{dep}/budget` (thin, reuse department budget write).
**Build:** summary of two separate envelopes → tabs "מחלקות" (historical usage, manual allocation; recommendation column shows "בהמשך" — excluded) / "מערכות ארגוניות" (allocation per org system); admin: "ארגון חדש".
**Tests:** `tests/api/test_org_envelopes.py`; Playwright `org allocator allocates to a department and an org system; envelopes stay separate`.
**Deps:** 5.1, 5.4, 5.15.

### Step 5.23: UI service status + WF-08 guide; Phase 5 wrap-up — S
**Files:** `frontend/src/v2/pages/{ServiceStatus,Guide}.tsx`, `PROGRESS.md`, `PRD.md`.
**Build:** status per model with updated-at and unknown state, limits explanation, block reasons glossary; guide page per WF-08 (static content, role tabs, FAQ, CTA to requests).
**Tests:** Playwright `service status shows unknown when stale`; wrap-up: all Phase 5 tests pass, hygiene passes, record run time vs budget.
**Deps:** 5.14, 5.15.

### Phase 5 acceptance mapping (summary)
| SoT decision | Steps |
|---|---|
| Hierarchy + org + dynamic groups (Maor 1–3) | 5.1–5.3, 5.19, 5.22 |
| Two envelopes, Org Systems virtual org, key moves (D-056/D-076/D-077) | 5.4, 5.5, 5.19, 5.21, 5.22 |
| Internal Usage initiatives (D-067) | 5.6, 5.21 |
| Requests center, throughput route (D-065/D-068/D-074) | 5.7, 5.8, 5.20 |
| Transfers, bulk allocation (D-066/D-069/D-075) | 5.9, 5.10, 5.17, 5.18 |
| 4 KPI, bar, Action Center (D-050–D-055) | 5.11, 5.17 |
| Per-project tag breakdown (D-059, Maor 12) | 5.12, 5.16 |
| Block reasons + service status (D-060–D-064, Maor 13) | 5.13, 5.14, 5.16, 5.23 |
| Shell, palette, last area (D-072/D-073, Maor 9) | 5.15 |
| Excluded: needs report / dept recommendation; kept: over-allocation, per-model quotas, PMO grants | — |

### Size and estimate
14 backend steps (5 S, 9 M) and 9 UI steps (5 S, 4 M). With Codex building and Claude reviewing in one track: about 3.5–4 working days for all of Phase 5. Core (5.1–5.10, 5.15–5.17, 5.20) ≈ 2 working days.
