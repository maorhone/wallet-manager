# Wallet Manager for LiteLLM: Requirements and Specification

Version 0.2 · 2026-10-19 · Status: ready for implementation planning

> **BUILDER NOTE (incomplete SPEC):** The parent-conversation relay to the executor truncated ~53 160 characters between the start of section 3 and the end of section 15.2. The text below is the verbatim recoverable portion plus this note. Sections that are only partially present are marked `<<<TRUNCATED>>>`. Planning assumptions from the builder (Phase 1 vertical slice, fake LiteLLM, DEV_AUTH, pgembed Postgres, ports 4100–4102) still apply. Maor / parent should replace this file with the full v0.2 SPEC when available. Companion file `litellm_wallets_erd.mmd` was not supplied in the relay.

This document specifies a web system ("Wallet Manager") that lets an organization allocate model budgets, per-model allowances and concurrency limits down an organizational hierarchy, on top of a LiteLLM Enterprise proxy. It has a web UI for every role, and a backend server that sits between the UI and LiteLLM. LiteLLM remains the runtime enforcement engine. The Wallet Manager owns allocation decisions, validates them, writes them to LiteLLM through its management API, keeps them in sync with Active Directory, and reports on them.

Companion file: `litellm_wallets_erd.mmd` (Mermaid ERD of the LiteLLM tables this system writes to).

Changes in 0.2: system architecture (section 5), managers' personal wallets (4.1, FR-G5), employee self-view (FR-E), web UI and UX (section 12), UI-facing API (11.1), UI phases in the delivery plan.

---

## 1. Glossary

| Hebrew | Term in this spec | LiteLLM object |
|---|---|---|
| מחלקה | Department | Organization (`LiteLLM_OrganizationTable`) |
| קבוצה | Group | Team (`LiteLLM_TeamTable`) |
| עובד / מפתח | Employee | User (`LiteLLM_UserTable`) + team membership (`LiteLLM_TeamMembership`) |
| מפתח (API) | Personal key | Virtual key (`LiteLLM_VerificationToken`), exactly one per employee |
| נציג מחלקה | Department representative | Organization member with role `org_admin` |
| ראש קבוצה | Group head | Team member with role `admin` |
| גוף פרויקטים | Projects body (PMO) | No LiteLLM object. A logical pool inside the department, owned by this system |
| מטה מחלקה | Department staff group | A regular Team under the department, for the representative, PMO staff and other department-level people |
| מענק | Grant | Time-limited budget given by the PMO to a group, then shared to employees |
| מכסה | Cap | A spend ceiling at some level |
| קופת מודל | Model pot | A per-model spend ceiling (`model_max_budget`) |
| ענף | Branch | Not modeled. Out of scope |

Budget units are LiteLLM spend units. The organization's internal model price list makes one unit equal one credit (see section 18).

---

## 2. Goals and non-goals

### 2.1 Goals
- G1. A department representative splits the department budget between groups, a PMO pool and a reserve.
- G2. A group head splits the group budget between employees, as personal caps.
- G3. The PMO grants time-limited budget to a group for a project. The group head shares it to the employees who work on the project. Unused grant money returns to the PMO pool when it expires or when the employee leaves the group.
- G4. Per-model allowances: a group-level pot for a specific model, and per-employee caps for that model.
- G5. Per-employee concurrency (`max_parallel_requests`) and rate limits on the single personal key.
- G6. Membership follows Active Directory automatically: joiners, movers, leavers.
- G7. Guarantee, at allocation time, that no level allocates more than it received (LiteLLM does not check sums; see 3.3).
- G8. Production systems and group bots get keys that are not tied to a person.
- G9. Every person sees the state of their own wallet, and every manager sees the state of the wallets under them.
- G10. Reports: allocation versus spend at every level, grant usage, over-allocation, concurrency peaks.

### 2.2 Non-goals (this version)
- Branch level, LiteLLM Projects.
- Client-side login tooling for Claude Code (the `apiKeyHelper` script). Assume a valid ADFS JWT reaches LiteLLM.
- Per-employee per-model concurrency. LiteLLM has no native support; a custom hook is a possible later phase (section 14).
- Guaranteed GPU capacity. Concurrency limits are usage ceilings; the serving layer (vLLM) queues overload.
- Pixel-level visual design. Follow the organization's design system; this spec defines screens, content, actions and behavior.

---

## 3. LiteLLM behavior this design relies on

All items in 3.1 to 3.4 were verified against th
<<<TRUNCATED ~53160 characters — original continued through roughly sections 3–15.2 including architecture (5), functional requirements FR-D/G/E/V/A/P/RC/N/K/R, LiteLLM write surface (10), UI API (11), web UI (12), NFRs, AD sync (9), etc.>>>

h the policy's default concurrency | Session completes, 429s are retried by the client |

### 15.3 UI end-to-end tests (Playwright)

| # | Scenario | Expected |
|---|---|---|
| U1 | Group head enters caps above the group base | Footer turns red, Save disabled; a race with another user returns `PARENT_OVERALLOCATED` with numbers |
| U2 | Group head tries to edit their own row or share a grant to themselves | Row read-only; API returns `SELF_ALLOCATION_FORBIDDEN` if called directly |
| U3 | Save while LiteLLM is down | Values saved, badges show pending, banner shown; badges turn applied after recovery |
| U4 | Employee whose group is exhausted but personal cap is not | Wallet shows "חסום: תקציב הקבוצה נוצל" and names the group head |
| U5 | PMO grant then group head share | Employee sees the share with expiry; PMO sees shared and unshared amounts |
| U6 | User with two roles | Scope switcher lists both scopes and the personal wallet; permissions follow the selected scope |
| U7 | Viewer | No action buttons; write endpoints return 403 |
| U8 | RTL rendering | Numbers and model names render correctly inside Hebrew text; tables mirror |

---

## 16. Delivery plan

| Phase | Backend | UI | Exit criteria |
|---|---|---|---|
| 0 | LiteLLM configuration, contract tests, verify V1 to V5 | | A1 to A8 and A10 pass |
| 1 | Data model, LiteLLM client, outbox, departments, groups, members, limits, validation, self-allocation rule, audit, BFF login | Shell, scope switcher, My wallet, Group (Overview, Members, History), Department (Overview, Split) | FR-D, FR-G, FR-E, FR-V, FR-A; A2, A3, A5; U1, U2, U3, U4, U6, U7, U8 |
| 2 | Grants and shares, scheduler for expiry and recomputation, reconciliation, notifications | PMO screens, Group Grants tab, Department Group heads tab | FR-P, FR-RC, FR-N; A1, A9 (manual move), A11; U5 |
| 3 | AD sync with dry-run and safety gate, role mirroring, service keys, production | Department Review and Production tabs, Group Service keys tab, Platform screens | Section 9, FR-K; A9, A12 |
| 4 | Reports and alerts | Reports screens, usage charts at every level | FR-R; concurrency peaks from SpendLogs |

---

## 17. Open decisions

| # | Decision | Default in this spec |
|---|---|---|
| D1 | Budget period | Monthly, calendar month, configured timezone |
| D2 | Attribution order between base cap and shares | Base first, then shares by earliest expiry |
| D3 | Grants that span a budget period reset | Allowed; usage measured from SpendLogs by time window |
| D4 | Member who moves mid-period: cap in the new group | The new group's default cap, full |
| D5 | Over-allocation factor for groups and model pots | 1.0 (no overbooking) |
| D6 | Concurrency policy: default, minimum and maximum per key | To be set from pilot measurements (FR-R4) |
| D7 | Who may raise an employee's concurrency above default | Group head within policy max; platform admin above it |
| D8 | Production Organization: one global or one per department | One per department |
| D9 | Internal price list per model, including cached input tokens | Required before go-live (section 18) |
| D10 | Grace period before removing a leaver's membership | 30 days |
| D11 | Who sees employee-level usage | Group head for their group; department representative for the department; PMO only per share of their grants; viewers only aggregates |
| D12 | Notification channel | In-app always; email if an internal relay exists |
| D13 | Department staff group | One per department, created by the platform admin at setup |

---

## 18. Credits and pricing

Credits are LiteLLM spend computed from per-model prices in the model configuration (`model_info` input and output cost per token). Claude Code resends long prefixes each turn, so cached input tokens need their own lower price, and vLLM must report cached tokens in `usage` for LiteLLM to price them. Verify this on the deployed versions before setting budgets, otherwise long sessions burn budget far faster than their real GPU cost.
