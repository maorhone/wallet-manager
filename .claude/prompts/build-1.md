Implement **Step 1 of PLAN.md only** ("Backend foundation, the §8.2 schema, error envelope, test harness"), exactly as PLAN.md describes. Do not start Step 2.

Read first: CLAUDE.md, PLAN.md §1 (global conventions) and Step 1, and SPEC.md §8.2, §11.2, §13. Read other spec sections only if Step 1 needs them.

Requirements:
- TDD: write the tests listed in Step 1 first, then the code.
- §8.2 in SPEC.md is the source of truth for tables and columns. Step 1 says "20 tables" but lists 18 names: transcribe §8.2 exactly, and if the counts differ, follow the spec and note it in PROGRESS.md.
- Verify the pgembed + asyncpg unix-socket URL handling before building on it (Step 1 says how).
- Rewrite scripts/test.sh into the real runner shown under "test.sh after" in Step 1 (keep it executable). It must pass from a clean checkout, and must stay fast (well under 5 minutes).
- Add `pgdata/` handling to .gitignore if needed. Never commit .env; ship .env.example.
- Update PROGRESS.md: mark Step 1 done (or what remains), set Next = Step 2, and copy all 10 decisions from PLAN.md §5 "Questions for Maor" into PROGRESS.md under "Questions for owner", verbatim in substance.
- Do not edit .claude/hooks/* or .claude/settings.json. No sudo, tunnels, or ANTHROPIC_* variables.
- When ./scripts/test.sh passes, commit with a clear message and `git push origin main` (never force).
