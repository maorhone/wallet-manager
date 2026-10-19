Implement **Step 7 of PLAN.md only** ("Group and member allocation: DR9 rule, caps, model caps, limits, member defaults, `GET /policies`"), exactly as PLAN.md describes. Do not start Step 8.

Read first: CLAUDE.md, PROGRESS.md, PLAN.md §1 (global conventions) and Step 7, then only the SPEC.md sections Step 7 cites. Read other spec sections only if the step needs them. Explore minimally; reuse what earlier steps built.

Rules:
- **The spec wins over PLAN.md.** SPEC.md is the source of truth. If PLAN.md disagrees with SPEC.md (counts, names, fields, status codes), follow SPEC.md and note the difference in PROGRESS.md under Notes.
- **Create and change files only with the Write/Edit tools**, never with Bash redirection, heredocs, sed -i, tee or scripts. The format/lint hooks only run on Write/Edit.
- TDD: write the tests listed in Step 7 first, then the code. Don't delete, skip, or weaken existing tests.
- Keep `./scripts/test.sh` fast (well under 5 minutes total) and green. If the step needs the test runner extended (e.g. a frontend), extend scripts/test.sh with Write/Edit and keep it runnable from a clean checkout.
- pgembed's bundled `psql` segfaults in this sandbox; use asyncpg/SQLAlchemy (see existing `app/db.py` and `scripts/dev_db.py`).
- No paid APIs, no API keys that cost money, no Enterprise license. No sudo, tunnels, tailscale, or ANTHROPIC_* variables. Ports 4100–4199 only.
- Do not touch `.claude/hooks/*` or `.claude/settings*.json` in any way (the guard blocks it).
- If a real spec ambiguity blocks you, add it to PROGRESS.md under "Questions for owner", pick the safest option, and continue. Only stop without finishing if the question truly blocks the step; then say `BLOCKED: <question>` in your final message.
- When done: update PROGRESS.md (mark Step 7 done, Next = Step 8, notes/blockers), make sure `./scripts/test.sh` passes, then `git add -A && git commit` with a clear message "Step 7: ...". **Do not `git push`** — the builder pushes after re-running the tests itself.
- Final message: one short paragraph: what was built, test count, commit hash, any open issues.
