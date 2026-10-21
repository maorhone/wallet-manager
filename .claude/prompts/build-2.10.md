Implement **Step 2.10 of PLAN.md only** ("UI: notifications bell and preferences; Phase 2 wrap-up (small UI step)", in section `## 2c. Steps (Phase 2 ...)`), exactly as PLAN.md describes. Do not start Step (none; Phase 2 ends with this step).

Read first: CLAUDE.md, PROGRESS.md, PLAN.md §1 (global conventions) and Step 2.10, then only the SPEC.md sections Step 2.10 cites (SPEC.md is the source of truth; the owner approved all PLAN.md §5 questions on 20/10/2026, so their defaults are decisions; there is no automatic AD sync). Explore minimally; reuse what earlier steps built.

Rules:
- **The spec wins over PLAN.md.** If PLAN.md disagrees with SPEC.md, follow SPEC.md and note the difference in PROGRESS.md under Notes.
- **Create and change files only with the Write/Edit tools**, never with Bash redirection, heredocs, sed -i, tee or scripts. The format/lint hooks only run on Write/Edit.
- TDD: write the tests listed in Step 2.10 first, then the code. Don't delete, skip, or weaken existing tests.
- Keep `./scripts/test.sh` fast (under ~4 minutes total) and green. Do not change its E2E ports (4120–4122) or the E2E_* env handling; `scripts/e2e_stack.py` must keep working with its default ports (a live preview uses them).
- New API path prefixes must be added to the proxy regex in `frontend/vite.config.ts`.
- pgembed's bundled `psql` segfaults in this sandbox; use asyncpg/SQLAlchemy.
- The repo is PUBLIC (`tests/test_repo_hygiene.py` enforces it): never commit secrets, tokens, real hostnames or internal corporate names; use placeholders (corp.example) and generated dummy values in tests. Install snippets may mention the env var name ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL as text; never write ANTHROPIC_API_KEY anywhere.
- No paid APIs, no API keys that cost money, no Enterprise license. No sudo, tunnels, tailscale. Ports 4100–4199 only.
- Do not touch `.claude/hooks/*` or `.claude/settings*.json` in any way (the guard blocks it).
- If a real spec ambiguity blocks you, add it to PROGRESS.md under "Questions for owner", pick the safest option, and continue. Only stop without finishing if the question truly blocks the step; then say `BLOCKED: <question>` in your final message.
- **PRD.md (living Product Requirements Document, owner request 20/10/2026).** If `PRD.md` does not exist at the repo root, create it first (Write tool, concise, about 150–300 lines, Hebrew-friendly English is fine): product purpose and users/roles, the key owner decisions (no automatic AD sync, onboarding API called by an external portal, manual member actions, approved questions 1–27), and a functionality section per area (budgets and allocation, wallets and reports, onboarding and member lifecycle, grants and shares, scheduler, reconciliation, notifications, UI screens) with status Done / In progress / Planned per phase, built from SPEC.md, PLAN.md and PROGRESS.md (read them, do not invent). Also add one line to CLAUDE.md Rules: "Update PRD.md whenever a step changes product functionality." In every step, update PRD.md for what this step built (status and a short description of the behavior a user/integrator sees) and include it in the step commit.
- When done: update PROGRESS.md (mark Step 2.10 done, Next = Step (none; Phase 2 ends with this step), notes/blockers), make sure `./scripts/test.sh` passes, then `git add -A && git commit` with message "Step 2.10: ...". **Do not `git push`** — the builder pushes after re-running the tests itself.
- Final message: one short paragraph: what was built, test count, commit hash, any open issues.
- Scheduler jobs take the injectable clock; tests move the fake clock, never `sleep` for real time.
- If you hit an intermittent pytest timeout that is unrelated to your change, do not spend more than a few turns on it: note it in PROGRESS.md (test name, where it hung) and continue.

If the working tree already has uncommitted changes for this step (an interrupted earlier run), inspect and continue from them.
