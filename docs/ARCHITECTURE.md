# Architecture

Jira-like Kanban for product feature requests, with an AI triage workflow (LangGraph + MCP + litellm). AI output is always a *suggestion*; only a human action applies it.

Decisions: (1) everything waits for Save; (2) a raw payload is persisted to Inbox, then run; (3) litellm fronts local/open models, so structured output is JSON mode + Pydantic validation + one repair retry; (4) seeded `user` table, header picker, no passwords.

## Design challenges (scope control)
- 8 logical steps, 4 LLM calls: `intake`, `analyze` (need + problem proposal + tags), `score`, `brief` (brief + stakeholder draft). Duplicate detection = local embeddings + one LLM confirmation for candidates above threshold. The 8 steps stay selectable by name.
- 4 MCP servers over stdio: one long-lived client session pool per API process, not per run. Fallback: in-process via the same adapter interface.
- Nodes never import `core`; deterministic reads/writes go only through MCP tools. MCP servers import `core` and never the FastAPI app.
- Board-level Save needs optimistic concurrency (`version`), or concurrent users overwrite each other.
- Ordering uses lexorank `position` strings so a move changes one row.

## 1. Data model (SQLAlchemy, SQLite, Alembic)
- `user(id, name, role)` seeded.
- `board(id, name, created_at)`; `column(id, board_id, name, position, wip_limit null)`.
- `card(id, board_id, column_id, position, title, description, requester_type[customer|internal|partner|unknown], progress 0-100, votes, priority_score null, problem_id null, version, created_at, updated_at, created_by)`.
- `tag(id, board_id, name, color)`; `card_tag(card_id, tag_id)`.
- `comment(id, card_id, author_id, body, created_at)`; `feedback_entry(id, card_id, source, quote, customer_ref null, created_at)`.
- `problem(id, board_id, title, statement, created_at)`: the underlying customer problem; cards are N:1.
- `card_link(id, card_id, other_card_id, kind[related|duplicate], status[suggested|confirmed], created_by)`; unique on the unordered pair.
- `rubric(id, board_id, name, version, criteria_json, active)`; criteria `[{key,label,weight,scale,anchors}]`.
- `card_score(id, card_id, rubric_id, rubric_version, per_criterion_json, total, run_id)`.
- `suggestion(id, card_id, run_id, kind[tag|link|problem|score|brief|comms|field_edit], payload_json, confidence, status[pending|accepted|rejected|applied], decided_by, decided_at)`. Only a user action leads to `applied`.
- `workflow_run(id, card_id, mode[full|steps], steps_json, status[running|awaiting_human|succeeded|failed], thread_id, started_at, finished_at, error)`.
- `ai_call(id, run_id, node, prompt_name, prompt_version, model, input_json, output_json, confidence, latency_ms, tokens_in, tokens_out, repaired, error)`.
- `card_embedding(card_id, model, dim, vector, text_hash)`; brute-force cosine in numpy.
- LangGraph checkpoints live in a separate SQLite file (`SqliteSaver`).

Layout: `core/models`, `core/repositories`, `core/services` (move/reorder, apply-suggestion, scoring math, link merge), `core/db.py`.

## 2. Graph design (ai/graph)
**State**: `run_id, card_id, card_snapshot, steps_requested|None, intake, analysis, candidates, dup_verdicts, problem_proposal, score, tags, brief, comms, human_input, errors, completed_steps`.

**Flow**: `intake` -> `analyze` -> `find_related` -> `group_problem` -> `score` -> `brief` -> `comms`. Tag suggestion is folded into `analyze`. Nodes write suggestions through an MCP persistence tool; none mutate card fields.

**Routing**
- `router` at START: if `steps_requested` is set, jump to the first selected step; every node ends with `next_selected()`. Partial runs use the same graph with a `should_run(step)` guard, and load prior outputs from the DB when a later step needs an earlier one (e.g. `score` without `analyze` reads the last accepted analysis).
- `intake` vague -> `human_checkpoint(clarify)` -> back to `intake`.
- `find_related` with `dup_confidence >= 0.9` -> `human_checkpoint(confirm_duplicate)`; if confirmed, skip to `brief`.
- Optional `human_checkpoint(approve_brief)` before `comms` via a run flag.
- **Interrupts**: `interrupt({kind, payload})`; resume with `Command(resume=value)`; `SqliteSaver`, `thread_id = run_id`.
- **Failures**: recorded in `ai_call.error` and `state.errors`; steps depending on missing output are skipped; run `succeeded` with warnings, or `failed` if `intake` fails.

## 3. MCP servers (official Python SDK / FastMCP, stdio; `ai/mcp_servers/<name>_mcp/server.py`)
All inputs and outputs are typed (Pydantic), every tool and parameter has a description, all tools are deterministic with no LLM calls. **Tools that would change card state only create suggestions** (pending drafts, or links/problem attachments with status `suggested`); no tool applies a suggestion, that is a human action in the API. Servers import `core`, never the FastAPI app.
- **cards_mcp**: reads `get_card`, `list_cards(board_id, filters)`, `get_card_context`, `list_problems`; proposals `update_card_fields`, `add_comment`, `add_tag`, `link_cards(type, confidence, rationale, status="suggested")`, `attach_problem`, `save_suggestion(kind, payload)`.
- **similarity_mcp**: `index_card`, `index_board`, `find_similar_cards(card_id, top_k, min_score)`. Local `fastembed` embeddings (`BAAI/bge-small-en-v1.5`) stored as float32 blobs in `card_embedding`, cosine similarity in numpy; `MOCK_AI=1` uses a deterministic hashed bag-of-words embedder.
- **scoring_mcp**: `get_rubric` (from `config/rubric.yaml`: criteria, weights, scale, descriptions), `save_scores(card_id, scores[])` (stores a `Score` row and returns the priority; does not change the card), `compute_priority(card_id | ratings)`.
- **comms_mcp**: `save_draft`, `list_drafts`, `get_templates` (from `config/comms_templates.yaml`). Nothing is sent.
- **Client**: `ai/mcp_servers/client.py` (`MCPToolkit`) starts all four servers, keeps one persistent stdio session per server, and returns LangChain tools via `langchain-mcp-adapters`; `toolkit.call(name, **args)` returns the structured result. `make mcp-tools` lists every tool.

Differences from the first design: `compute_score`/`compare_to_peers` became `compute_priority`; `find_similar` became `find_similar_cards`; `embed_card` became `index_card` (+ `index_board`); `render_template` and `get_stakeholders` were dropped; `get_card_context` and `list_problems` were kept; `create_card` was dropped (cards are created through the API).

## 4. Prompt registry (ai/prompts, ai/llm)
- Files `ai/prompts/<name>/vN.md`: YAML frontmatter `{name, version, model_alias, temperature, input_variables[], output_schema: "module:Class", released}` + Jinja2 body. User-provided variables are always rendered inside `<user_content>...</user_content>` by the loader helper `wrap_untrusted`.
- `ai/llm/registry.py`: `load(name, version="latest-released")` validates variables, resolves the schema class, returns `PromptSpec`. A committed hash manifest makes a test fail if a released file changes.
- `ai/llm/client.py`: `LiteLLMClient.complete_structured(spec, vars)` via litellm `acompletion`, config from `LITELLM_BASE_URL`, `LITELLM_API_KEY`, aliases in `ai/llm/models.yaml`. JSON mode + Pydantic validation + one repair retry. Each call writes an `ai_call` row. `MOCK_AI=1` swaps in `MockClient` (fixtures keyed by prompt name + input hash).
- Prompts (every output has `confidence: float 0-1`):
  1. `intake` -> `{is_actionable, vague_reasons[], missing_info_questions[], normalized_title, requester_type_guess}`
  2. `analyze` -> `{underlying_need, user_problem_statement, suggested_tags[{name, reason}], existing_problem_id|null, new_problem_title|null}`
  3. `dup_confirm` -> `{verdict: duplicate|related|distinct, rationale}` per candidate
  4. `score` -> `{criteria: [{key, rating 1-5, rationale}]}`
  5. `brief` -> `{summary, recommendation: plan|review|decline|needs_info, rationale, risks[], open_questions[]}`
  6. `comms` -> `{audience, subject, body, tone}`

## 5. API contract (FastAPI `apps/api`, prefix `/api`)
Mutating calls take `X-User-Id`; card writes require `version` (409 on mismatch).
- `GET /boards`, `POST /boards`, `GET /boards/{id}` (columns + light cards).
- `PUT /boards/{id}/layout` `{columns:[{id,name,position}], moves:[{card_id, column_id, position, version}]}`: the board-level Save.
- `GET /cards/{id}` (full detail + suggestions), `POST /cards`, `PATCH /cards/{id}` `{fields, tags_add, tags_remove, applied_suggestion_ids, version}`: the drawer Save.
- `POST /cards/{id}/comments`, `POST /cards/{id}/feedback`, `POST /cards/{id}/votes`.
- `GET /tags?board_id`, `POST /tags`; `GET /boards/{id}/cards?tag=&q=`.
- `GET/PUT /boards/{id}/rubric`.
- `POST /workflow/runs` `{card_id}` or `{card:{board_id,title,description,requester_type}}`, optional `steps[]`, `require_brief_approval` -> `202 {run_id, card_id, status}`.
- `POST /workflow/runs/{id}/steps/{step}`: single-step shortcut.
- `GET /workflow/runs/{id}` -> `{status, completed_steps, pending_interrupt:{kind,payload}|null, suggestions[], ai_calls[], error}` (poll 1-2 s).
- `POST /workflow/runs/{id}/resume` `{interrupt_id, value}`.
- `POST /suggestions/{id}/decision` `{decision: accept|reject}` only marks the decision and returns the proposed patch. Nothing is applied until the card Save sends `applied_suggestion_ids`; `core` applies them atomically and rejects if `version` changed.
- Runs execute as an asyncio background task (`ainvoke`). No SSE; polling suffices.

## 6. Frontend (apps/web: React + Vite + TS strict + Tailwind + dnd-kit)
- Pages: `BoardListPage`, `BoardPage`. Components: `BoardHeader` (board switcher, user picker, tag filter, search, Save/Discard with dirty badge), `Column`, `CardTile`, `CardDrawer` (Details, Comments, Feedback, Grouping, AI tabs), `SuggestionItem`, `RunPanel` (step checkboxes, run all/step, progress, interrupt form), `RubricSettings`.
- State: TanStack Query for server state; a Zustand **draft layer** (pending moves, card patches, accepted suggestion ids). Save flushes via `PUT /layout` and `PATCH /cards/{id}`; 409 shows a reload dialog; leaving while dirty prompts.
- dnd-kit: `DndContext` + `SortableContext` per column; a drop computes a lexorank between neighbours.
- Workflow: `useWorkflowRun(runId)` polls until terminal or `awaiting_human`, renders the interrupt form, submits to `/resume`. New suggestions appear in the drawer AI tab.
- API types generated from the FastAPI OpenAPI schema (`openapi-typescript`).

## 7. Risks and tradeoffs
- **Prompt injection**: one loader helper enforces delimiters (unit-tested); the system prompt declares delimited text is data; outputs are schema-validated; LLM output can only reach `save_suggestions`, never card-mutating tools; evals include injection cases.
- **Hallucinated merges**: needs embedding score >= threshold *and* an LLM verdict; links stay `suggested` until a human confirms; high-confidence duplicates go to a checkpoint; evals track false-merge rate.
- **Cost/latency**: at most 4 LLM calls per card, k=5 duplicate candidates, embeddings cached by `text_hash`, selectable steps, per-run token budget, latency logged per call.
- **Scoring consistency**: anchored 1-5 scales, temperature 0, math in `compute_score`, rubric version stored with each score, optional 2-sample check flags low confidence when ratings differ by more than 1.
- **Local models**: structured-output failure -> repair retry -> soft node failure; evals gate model changes.
- **Save model**: draft lives only in the client (lost on crash; accepted). 409 on conflicts.
- **SQLite**: WAL, single writer.

## 10. Docker
Only Docker + make are needed on the host; the Makefile only wraps `docker compose`.

**Images (`docker/`)**
- `web.Dockerfile`: `dev` (Vite + HMR, bind mount), `build` (`vite build`), `prod` (nginx serving `dist`, proxying `/api`).
- `backend.Dockerfile` (python-slim + uv): `core/`, `apps/api`, `ai/` and all MCP server code; stages `base`, `dev`, `prod`. The fastembed model is downloaded at build time.
- `mcp.Dockerfile`: from the backend `base`, entrypoint `python -m ai.mcp_servers.<name>`.

**Compose (`docker-compose.yml` + `docker-compose.dev.yml`)**
- Services: `web` (5173 dev / 8080 prod), `api` (8000; runs `alembic upgrade head`, then uvicorn), `mcp-cards`, `mcp-similarity`, `mcp-scoring`, `mcp-comms` (`target: mcp`, profile `mcp`; build, `--selftest`, inspect).
- **Stdio vs containers**: stdio cannot cross containers without mounting the docker socket (rejected). The `api` container spawns the MCP servers as stdio subprocesses from the code in its own image; the `mcp-*` services build, self-test and debug each server in isolation. Network-separated MCPs later means switching the adapter to streamable-HTTP; tool contracts stay the same.
- Volumes: `db-data` (app DB + checkpoint DB, single writer), `model-cache`; source bind mounts in dev only.
- `.env` via `env_file`; `.env.example` has `LITELLM_BASE_URL`, `LITELLM_API_KEY`, `MOCK_AI=1`, model aliases, DB paths. litellm is external, reached via `host.docker.internal` (`extra_hosts: host-gateway`).
- Healthchecks: api `/api/health`, web HTTP 200; `depends_on: service_healthy`.

**Makefile -> compose**: `build` (web, api, 4 mcp services; `build-web|build-api|build-mcp`), `up`, `down`, `logs`, `migrate`, `seed`, `test` (pytest + npm test via `compose run --rm`), `lint`, `eval`, `mcp-inspect`, `prod-up`, `clean` (`down -v`).

**Risks**: slow first build (mitigated by layer cache and `model-cache`), WSL bind-mount permissions (run as host uid), Vite HMR in Docker (polling watcher).
