Implement **Step 2.5 of PLAN.md only** ("Reconciliation (FR-RC1, RC2, RC3) with a drift log and repeated-drift alert", in section `## 2c. Steps (Phase 2 ...)`), exactly as PLAN.md describes. Do not start Step 2.6.

Read first: CLAUDE.md, PROGRESS.md, PLAN.md §1 (global conventions) and Step 2.5, then only the SPEC.md sections Step 2.5 cites (SPEC.md is the source of truth; the owner approved all PLAN.md §5 questions on 20/10/2026, so their defaults are decisions; there is no automatic AD sync). Explore minimally; reuse what earlier steps built.

Rules:
- **The spec wins over PLAN.md.** If PLAN.md disagrees with SPEC.md, follow SPEC.md and note the difference in PROGRESS.md under Notes.
- **Create and change files only with the Write/Edit tools**, never with Bash redirection, heredocs, sed -i, tee or scripts. The format/lint hooks only run on Write/Edit.
- TDD: write the tests listed in Step 2.5 first, then the code. Don't delete, skip, or weaken existing tests.
- Keep `./scripts/test.sh` fast (under ~4 minutes total) and green. Do not change its E2E ports (4120–4122) or the E2E_* env handling; `scripts/e2e_stack.py` must keep working with its default ports (a live preview uses them).
- New API path prefixes must be added to the proxy regex in `frontend/vite.config.ts`.
- pgembed's bundled `psql` segfaults in this sandbox; use asyncpg/SQLAlchemy.
- The repo is PUBLIC (`tests/test_repo_hygiene.py` enforces it): never commit secrets, tokens, real hostnames or internal corporate names; use placeholders (corp.example) and generated dummy values in tests. Install snippets may mention the env var name ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL as text; never write ANTHROPIC_API_KEY anywhere.
- No paid APIs, no API keys that cost money, no Enterprise license. No sudo, tunnels, tailscale. Ports 4100–4199 only.
- Do not touch `.claude/hooks/*` or `.claude/settings*.json` in any way (the guard blocks it).
- If a real spec ambiguity blocks you, add it to PROGRESS.md under "Questions for owner", pick the safest option, and continue. Only stop without finishing if the question truly blocks the step; then say `BLOCKED: <question>` in your final message.
- When done: update PROGRESS.md (mark Step 2.5 done, Next = Step 2.6, notes/blockers), make sure `./scripts/test.sh` passes, then `git add -A && git commit` with message "Step 2.5: ...". **Do not `git push`** — the builder pushes after re-running the tests itself.
- Final message: one short paragraph: what was built, test count, commit hash, any open issues.
- Scheduler jobs take the injectable clock; tests move the fake clock, never `sleep` for real time.
- If you hit an intermittent pytest timeout that is unrelated to your change, do not spend more than a few turns on it: note it in PROGRESS.md (test name, where it hung) and continue.

If the working tree already has uncommitted changes for this step (an interrupted earlier run), inspect and continue from them.
