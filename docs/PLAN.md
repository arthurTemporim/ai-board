# Plan

See `ARCHITECTURE.md` for the design. No application code exists yet.

## Decisions
- Everything waits for Save (board Save + drawer Save; accepting a suggestion only takes effect on Save).
- A raw workflow payload is persisted to the board's Inbox, then run.
- litellm fronts local/open models: JSON mode + Pydantic validation + one repair retry.
- Seeded `user` table with a header picker; no passwords.
- Fully dockerized; every Makefile target calls `docker compose`.

## 8. Success metrics
1. **Suggestion acceptance rate** per kind = accepted / (accepted + rejected), from the `suggestion` table. Targets: tag >= 60%, problem link >= 50%. Reported by a query and the `ai/evals` report.
2. **Duplicate precision**: share of human-decided suggested duplicates that were accepted (target >= 85%, false-merge rate <= 5%), plus recall on a labelled golden set in `ai/evals`.
3. **Triage time and scoring stability**: median time from card creation to the Triaged column (from move timestamps) against a baseline; score stability = std-dev of total over 3 repeated runs on the golden set (target <= 0.5 on a 0-10 scale). Latency p50/p95 from `ai_call`.

## 9. Phases (each ends demoable)
- **P0 Scaffold + Docker**: `docker/` Dockerfiles, compose (+dev override), Makefile wrapping compose, repo layout, `.env.example`, `MOCK_AI`, ruff/pytest/tsc strict. *Demo*: `make build && make up` brings up web + api + built MCP images, health checks green, `make test` runs in containers.
- **P1 Board core**: models, Alembic, seed, board/card/tag/comment APIs, React board with dnd-kit, drawer, filter/search, draft + Save with versions. *Demo*: full manual Kanban with Save.
- **P2 LLM foundation**: prompt registry + hash manifest, litellm client, structured output + repair, `ai_call` logging, MockClient, injection wrapper. *Demo*: run the `intake` prompt on a card under `MOCK_AI=1` and live via litellm.
- **P3 MCP servers + local embeddings**: four servers with schemas and tests, adapter pool. *Demo*: `find_similar` and `compute_score` callable via `make mcp-inspect`.
- **P4 Graph (happy path)**: nodes, state, persisted suggestions, `POST /workflow/runs` + poll, partial runs. *Demo*: run on a card, suggestions appear in the drawer, accept then Save applies.
- **P5 Routing + human checkpoint**: vague/duplicate branches, interrupt/resume with the SQLite checkpointer, RunPanel interrupt forms. *Demo*: a vague card pauses, the user answers, the run resumes, including across an API restart.
- **P6 Evals + metrics**: golden set, injection cases, acceptance/duplicate/stability report. *Demo*: `make eval` prints the metric table.

## Cut first if time is short
Comms step + `comms_server`, `compare_to_peers`, brief-approval checkpoint, rubric editing UI, problem-grouping UI polish, OpenAPI type generation, multi-board management UI (keep the API).

## Phase 1 implementation notes (backend foundation, done)
Deviations from the original design, all deliberate:
- **Card `position` is a contiguous integer** (0..n-1) per column, renumbered inside the move transaction, instead of lexorank strings. Simpler to reason about and test; revisit only if move contention shows up.
- **Model set** follows the Phase 1 brief: `AIRun` replaces `workflow_run` + `ai_call` (step runs point at the workflow run via `parent_run_id`), `Draft` replaces `suggestion`, `CardProblem` is a join table (status suggested/confirmed) instead of `card.problem_id`, `Vote` is a table (popularity = count), and `Score` snapshots the rubric inline (no `rubric` table yet). `User` stays (needed for votes and comment authors).
- **Migrations live in `migrations/`** (not `alembic/`, which would shadow the installed package).
- Seed data creates no `CardLink`/`Problem` rows; ground truth is in `data/seed/labels.json` (regenerate with `python -m data.seed.seed --write-labels`).
- `make seed` wipes existing rows before loading.

## Phase 2 implementation notes (frontend, done)
See `DECISIONS.md` for the reasoning. In short: drag and drop saves optimistically (this replaces the "everything waits for Save" rule for board moves only; the drawer still has Save); filters are client-side and turn dragging off; no router or UI library; `docker compose` runs the Vite dev server (`make dev`).
