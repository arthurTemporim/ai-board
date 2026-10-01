# Decisions

Short records of choices that are not obvious from the code.

## Frontend dependencies (`apps/web`)

Runtime: `react`, `react-dom`, `@dnd-kit/core|sortable|utilities`, `@tanstack/react-query`. Everything else is build or test tooling. No UI component library, no router, no form library, no icon pack.

| Dependency | Why |
|---|---|
| `@dnd-kit/*` | Required by the brief. Its keyboard sensor and screen-reader announcements give us accessible drag and drop. |
| `@tanstack/react-query` | Required by the brief. Gives us the cache that optimistic updates and rollback are built on. |
| `tailwindcss` (+ `postcss`, `autoprefixer`) | Required by the brief. |
| `vitest`, `jsdom`, `@testing-library/*` | Component and hook tests. Vitest shares Vite's config, so there is no second transform pipeline. |

Deliberately not added:
- **Router**: only two pieces of navigation state exist (`?board=` and `?card=`). `state/urlState.ts` (40 lines) keeps them in the address bar so refresh, back/forward and links to related cards work.
- **Headless UI / Radix**: the drawer and modals need focus-in, Tab trap, Escape and focus-return. `components/useDialog.ts` does this in about 60 lines and is covered by the browser checks below.
- **Form library**: the forms are small; drafts are plain state.
- **openapi-typescript**: `api/types.ts` is hand-written (about 100 lines). Revisit when the API grows; the plan listed generation as a cut candidate.

## Saving: drag is immediate, the drawer has Save

The original plan said every change waits for an explicit Save. For this phase the brief asked for optimistic drag and drop with rollback, so:
- **Moves** apply instantly and are sent to the server in the background (`useMoveCard`). On failure the previous board is restored and a message explains why. A 409 (someone else changed the card) also triggers a refetch.
- **Drawer edits** (title, description, tags, progress, status) are held until **Save changes**, as originally planned. The draft records the card `version` at the first edit and sends it with the save, so a change made by someone else in the meantime is reported as a conflict instead of being overwritten.
- Comments, votes and feedback are actions, not edits, and apply immediately.

The Save-everything model from the plan is not implemented for board moves; say so if it should come back.

## Drag and drop

- One focusable element per card. **Enter** opens the drawer; **Space** picks up, arrows move, **Space** drops, **Esc** cancels. Enter is removed from dnd-kit's start keys for this.
- The pointer needs to travel 5px before a drag starts, so a click still opens the drawer.
- Dropping on a card puts the dragged card at that card's index; dropping on a column body appends. `board/dnd.ts` holds the pure logic (`moveFromDragEnd`, `applyMove`) and mirrors the server's clamp and renumbering, so the optimistic state matches what the server returns.
- **Dragging is disabled while a tag filter or search is active.** Positions are indexes over all cards in the column on the server, so reordering a filtered subset would be ambiguous. A note explains this on screen.
- Moves in flight are counted; only the last one to settle refetches the board, so a slow response cannot overwrite a newer optimistic state.

## Filtering and search

Client-side, over the cards already loaded with the board (title, description and tag names). Boards here hold tens to low hundreds of cards; the server `q` and `tag_id` parameters exist for when that stops being true.

## Card indicators

The card shows possible duplicates, related requests and pending AI suggestions. These counts (`duplicate_count`, `related_count`, `pending_draft_count`) are computed by the backend as subqueries on `Card`, so the board loads in one request. Nothing creates links or drafts yet (the AI workflow does, later); until then the indicators only appear for rows inserted by hand.

## Dev setup

- The Vite dev server proxies `/api` to the API container, so the browser only talks to one origin. The API still allows CORS for `http://localhost:5173` for anyone who runs the pieces separately.
- `node_modules` lives in a named volume so the host does not need Node and the bind mount does not hide the image's packages.

## How this was verified

- `make test`: pytest (backend) and vitest (card rendering, drag-end logic, optimistic update and rollback, filters).
- A throwaway Playwright script (not committed; it needs a seeded database and a browser image) drove the real UI in Chromium: mouse drag across columns, keyboard reorder, persistence after reload, editing and saving in the drawer, comments, votes, feedback, links between related cards, Escape and focus return, filters, create-card, tag colors, board switching, and rollback when the server rejects a move. It found two real bugs that unit tests did not (Escape stopped working after following a link inside the drawer; focus was not restored after a card changed column).

## LLM layer (`ai/llm`, `ai/prompts`)

**Provider.** Two, chosen by `LLM_PROVIDER` (default: `openai` when `LITELLM_BASE_URL` is set, otherwise `anthropic`):
- `openai` (`langchain-openai`): any OpenAI-compatible endpoint. This is the configured setup: a litellm proxy in front of local Ollama models (`gemma4:12b`, `llama3.2:3b`, `lfm2.5:8b`, `nimble:9b`).
- `anthropic` (`langchain-anthropic`): Claude, directly or through a proxy's Anthropic-format endpoint. This is what the Phase 3 brief asked for and is kept working; prompt v1 is written for it.
Nothing calls litellm's own SDK. `AI_MODEL_OVERRIDE` forces one model for every prompt (A/B tests).

**Structured output with local models (measured against the proxy, not assumed).** Tool calling (`function_calling`) is rejected by the Ollama backend (`UnsupportedParamsError`). `json_schema` worked on all four chat models for all seven prompts; `json_mode` was inconsistent (one parse failure on `llama3.2:3b`). So the openai provider defaults to `json_schema` (`LLM_STRUCTURED_METHOD` to change). Some providers *raise* schema/JSON errors instead of returning them, so a `ValueError` from the model call is treated as a schema failure (one repair attempt), not as a network failure. `gemma4:12b` looks like a reasoning model: with `max_tokens` of 600-1200 it twice returned empty or truncated output, and with 4096 it succeeded (17-32 s). Budget generously for it.

**Which model runs which prompt.** Prompt v1 names Claude models. Released prompts cannot be edited, and a model is part of a prompt file, so switching models meant **v2 of all seven prompts** (bodies identical, only `model` differs; a test enforces that) and making v2 active. v2 uses `llama3.2:3b` for everything, on the only real measurement available: `judge_duplicate` on the 10 labelled pairs in `labels.json` (7 duplicates, 3 related-but-distinct; one run each, temperature 0):

| model | correct | time for 10 |
|---|---|---|
| `llama3.2:3b` | 8/10 | 25 s |
| `nimble:9b` | 5/10 | 74 s |
| `gemma4:12b` | 4/10 (+1 failed) | 185 s |
| `lfm2.5:8b` | 2/10 | 79 s |

The bigger models were worse and slower, mostly by saying "related" or "distinct" for real duplicates, so I did not assign larger models to other prompts on a hunch. Every model returned valid output for all seven prompts; only `judge_duplicate` has accuracy data. Scoring, tagging, analysis and drafting quality on `llama3.2:3b` is **unmeasured**; `llama3.2:3b` also gave noticeably different ratings from the other models on `score_request` in a single sample. The next step is a per-prompt eval with labelled data (see the plan's metrics) before trusting these on real decisions.

**Prompt list.** `analyze_need`, `judge_duplicate`, `group_problem`, `score_request`, `suggest_tags`, `decision_brief`, `draft_update`, as listed in the brief. This replaces the prompt names in the original plan (`intake`, `analyze`, `dup_confirm`, `score`, `brief`, `comms`). There is no separate intake/vagueness prompt: `analyze_need` returns `is_actionable` and clarifying questions.

**Files and versions.** `ai/prompts/<name>/vN.md` = YAML frontmatter + a Jinja2 body split into `=== system ===` and `=== user ===` sections. `ai/prompts/registry.yaml` names the active version of each prompt. Extra frontmatter key beyond the brief: `max_tokens`. Variables are declared as plain names or `{name, untrusted: true}`.

**Untrusted text.** The loader, not the template author, does the wrapping:
- variables declared `untrusted: true` are rendered as `<user_data name="...">` blocks; structured values become JSON inside the block;
- text inside a block cannot close it or forge another (`<user_data` / `</user_data` are defused to `&lt;...`);
- loading fails if an untrusted variable is referenced in the system section, or if a template uses undeclared variables or leaves a declared one unused;
- text that is *derived* from user text (an earlier model's analysis, tag names, problem titles) is treated as untrusted too, because it can carry an injection forward. Trusted inputs are enum values from the database, numbers, computed scores and the rubric.
This reduces the risk; it does not remove it. The model can still be persuaded, so outputs are schema-validated, scores and totals are computed in code from bounded ratings, and nothing the model returns is applied without a human decision.

**Released versions are immutable.** `registry.yaml` stores a sha256 for each released version (`make freeze-prompts` records new ones). A released file that no longer matches its hash fails to load. Change a prompt by adding `vN+1` and switching `active`. A version that is not yet frozen can be edited freely while it is being developed.

**Outputs.** One Pydantic model per prompt in `ai/llm/schemas.py`, all with `confidence` (0-1) and a short `rationale`, `extra="forbid"`. The model rates rubric criteria 1-5; weights and totals are not part of any schema. `group_problem` rejects inconsistent decisions (an id with "new", no id with "existing").

**Calls.** Retry with exponential backoff and jitter on timeouts, dropped connections, 408/409/429 and 5xx (the SDK's own retries are turned off so attempts are counted once). Client errors (400/401/403/404) fail immediately. A reply that does not match the schema gets one repair attempt with the validation error fed back (as plain user text, because replaying a tool call would require tool results). Timeout, attempts and the model override come from environment variables.

**Logging.** `run_prompt()` writes one `AIRun` row per call: `prompt_name`, `prompt_version`, `model` (the effective one), `input_hash` (sha256 of prompt ref + variables), `input`, `output`, `confidence`, `latency_ms`, tokens, status and error. It flushes but does not commit, so the caller's transaction decides whether a failed row survives. Migration `0002` adds `input_hash`, `tokens_in`, `tokens_out`.

**MockLLM (`MOCK_AI=1`).** Outputs are a pure function of the variables (text overlap for duplicates and grouping, rubric keys for scores, score ratio for the brief), validated against the real schemas. The text of a request is never interpreted as an instruction.

## MCP servers (`ai/mcp_servers`)

**"Only suggestions".** The brief says write tools may only create suggestions or drafts unless explicitly named as applying a human decision. So `update_card_fields`, `add_comment` and `add_tag` keep their requested names but *propose*: each stores a pending `Draft` (`field_edit`, `comment`, `tag`) and returns `applied: false`; `link_cards` and `attach_problem` store rows with status `suggested` and accept no other status; `save_scores` stores a `Score` row and leaves `card.priority_score` alone. No tool applies anything, because applying belongs to the API where a person clicks. The tool descriptions start with "PROPOSE" so a model choosing tools is told this too. A test calls every write tool on one card and asserts the card, its tags, comments and votes are identical afterwards. Asking again is idempotent: an identical pending draft is returned instead of a new one, and a suggested link is refreshed in place; a link a person confirmed is never changed.

**Additions for the suggestion model.** `DraftKind.comment`; `confidence` and `rationale` on `card_link` and `card_problem`; a `card_embedding` table (migration `0003`). New core services: `SuggestionService` (drafts, links, problems) and `scoring` (rubric loading and priority maths, in `core` so the API can reuse it).

**Priority maths.** Priority is the weighted average of the ratings rescaled to 0-100, `100 * sum(w_i * (r_i - min) / (max - min)) / sum(w_i)`. Every criterion is "higher = more reason to do it" (effort is `effort_fit`, 5 = cheap). `save_scores` requires a rating for every criterion and rejects unknown or repeated ones. The rubric and the comms templates are YAML in `config/`, editable without code changes (`RUBRIC_PATH`, `COMMS_TEMPLATES_PATH` override them); a score stores the rubric name and version it was computed with.

**Embeddings.** Provider by `EMBEDDING_PROVIDER` (default `litellm` when a proxy is configured, else `fastembed`; `MOCK_AI=1` always uses the hash embedder). The proxy's embedding models are called over plain HTTP (`LiteLLMEmbedder`), not the OpenAI SDK, because the SDK always sends an `encoding_format` that litellm rejects for Ollama-backed models. Measured on the seed data (30 cards, 7 duplicate pairs, real proxy):

| embedder | dim | time for 30 cards | duplicate in top 3 | duplicate scores | related-pair scores |
|---|---|---|---|---|---|
| `nomic-embed-text-v2-moe` (default) | 768 | 4 s | 6/7 | 0.31-0.55 | 0.34-0.48 |
| `qwen3-embedding:8b` | 4096 | 57 s | 7/7 | 0.54-0.70 | 0.47-0.57 |
| fastembed `bge-small-en-v1.5` | 384 | local | 6/7 | 0.56-0.73 | 0.59-0.65 |

nomic is the default for speed (indexing happens as cards are created); qwen3 is one setting away and finds every duplicate, but costs about 14x the time and 10x the storage. No embedder separates duplicates from related pairs by score alone (ranges overlap), so the judge prompt decides. Vectors are stored per card as unit-length float32 blobs; search loads the board's vectors and ranks by dot product, ties broken by card id so results are deterministic. A stored vector is only refreshed by `index_card`, so it can be stale after a card's text changes; `index_card` compares a hash of the text and skips unchanged cards. `find_similar_cards` indexes the query card itself if needed and reports how many cards on the board are still unindexed. The model downloads on first use (about 130 MB) into `FASTEMBED_CACHE_DIR` (`/data/fastembed` in compose); baking it into the image, as the original plan suggested, is left for later. Adding `fastembed` grew the API image from about 400 MB to 730 MB.

*Measured on the seed data with `data/seed/labels.json`* (one run, 7 true duplicate pairs): the real model puts the partner in the top 3 for 6 of 7 (scores 0.56-0.73); the mock embedder manages 4 of 7 because it only matches shared words. The three related-but-distinct pairs score 0.59-0.65, inside the duplicate range, so similarity finds candidates but cannot decide duplicate vs related: that decision is the `judge_duplicate` prompt's job, and no threshold on this score should auto-link cards.

**Processes and environment.** Each server is a child process of whoever starts it (for the API, inside the `api` container), which keeps SQLite to one container. The MCP SDK starts children with a minimal environment, so `client.py` passes an explicit list (database URL, `MOCK_AI`, config paths, cache dirs, and the litellm proxy address and key, which `similarity_mcp` needs for embeddings). `ANTHROPIC_API_KEY` and everything else is not passed: the servers make no text-generation calls.

**stdout is the protocol.** Found by running the real model: a native library printed its download log to file descriptor 1 and corrupted the JSON-RPC stream, which no mock-based test could reveal. `serve()` now points fd 1 at stderr and keeps the real stdout only for the protocol; a test writes to fd 1 directly to prove it. The same run showed the downloader needs a writable `HOME`, so compose sets `HOME=/tmp` for the api service (the container runs as your uid, which has no home).

**Verification.** `tests/mcp` starts real server processes and calls every tool through the official MCP client against a temporary SQLite file; `tests/core/test_scoring.py` covers the maths directly. Two tests that download and run the real embedding model are opt-in (`RUN_FASTEMBED=1`) and were run by hand.

## LangGraph workflow (`ai/graph`)

See `ARCHITECTURE.md` section 2 for the design; this records the choices and what running it showed.

**The brief's "low confidence routes to human_checkpoint" needed a definition.** Two cases ask a person: an *actionable* analysis the model is not sure of (`analysis_min_confidence`, default 0.4), and a *duplicate* verdict between `link_threshold` (0.6) and `merge_threshold` (0.85). A vague request does not ask: it goes straight to `needs_info`, because "not actionable" is already the answer. Duplicates below `link_threshold` are ignored, which can lose real duplicates (see below); the thresholds are the knob.

**What a person's answer does.** Their decision is stored in `human_decisions` and applied through the same proposing tools: a confirmed duplicate becomes a *suggested* duplicate link plus a merge proposal flagged `human_confirmed`. The graph still never confirms a link or applies a merge; that stays a click in the app.

**Runs can be paused for days, so they must survive.** State is checkpointed to SQLite after every node; resuming works from a new process (a test closes the first `Workflow`, its MCP servers and its connections, and resumes from files alone).

**Short transactions, everywhere.** The graph process and the MCP server processes write to one SQLite file. Holding a write transaction open across a model call (20-30 s on a local 12B model) would lock the servers out, so AIRun writes are small committed transactions, model calls run in a thread with their own `run_prompt_logged` logging, and no session is open while waiting.

**Bug found only by running it for real: thread ids.** The first version used the run's database id as the LangGraph thread id. After `make seed` wiped the database, run ids restarted at 1 while the old checkpoint for thread "1" was still in the checkpoint file, and a new run silently resumed the old run's state (the report mixed two cards). The thread id is now a random id stored on the workflow's AIRun row, and a test forces an id collision (and fails when the old scheme is put back). Other defects caught while testing: the checkpoint step row stayed `running` while the workflow waited; a run on a missing card crashed on a foreign key instead of reporting "not found".

**Results of running it on the real models (seed data, `nomic` embeddings, `llama3.2:3b`).** A full pass over one card takes about 20 s. What it showed, unvarnished:
- The Okta card's true duplicate (the SAML card) is **not found with the defaults**: it is outside the top 5 by similarity, and at top 8 the 3B judge calls it "related" at 0.45, below `link_threshold`. The other sign-on card (Azure) is found and correctly sent to a person (0.65). So duplicate recall on this board is imperfect; `GRAPH_TOP_K`, a stronger embedding model (`qwen3-embedding:8b` ranked better) or a stronger judge are the levers, and none is tuned yet.
- `llama3.2:3b` answered `group_problem` with problem id 7 when the board had no such problem, which is the id in the prompt's example. The guard in `group_problem` rejects ids that are not on the board, records the error and attaches nothing, so this is safe but wasteful. A v3 of that prompt with an example that does not contain an id would likely fix it; not done here because it changes a released prompt.
- Everything else in the full pass produced valid, plausible output. Quality of scores, briefs and drafts on the 3B model remains unmeasured.

**Mock mode is a wiring test, not a quality test.** Under `MOCK_AI=1` the hash embedder scores differently worded duplicates at about 0.17, below the default `min_similarity` (0.2), so the seed's duplicates are not surfaced; the tests use a lower floor and near-identical text.

**Not here yet.** The API endpoints that start, poll and resume runs and that apply accepted suggestions come next; `Workflow` is the object they will call.

## Connecting the API, the workflow and the UI (Phase 6)

**Runs are background tasks in the API process, reading is database-only.** `WorkflowRuntime` starts one `Workflow` (one set of MCP server processes, one checkpoint connection) on the first request and shares it. The MCP sessions use anyio scopes that must be opened and closed by the same task, but every HTTP request is its own task, so a dedicated host task owns the workflow for the life of the process (found by the first test run: "exit cancel scope in a different task"). `GET /workflows/runs/{id}` is built from the AIRun rows alone (`ai/graph/reports.py`); a paused checkpoint writes its question to its step row. So polling is cheap, works while the run is busy, and survives a restart. On startup, runs left `running` by a previous process are marked failed ("Interrupted by a server restart"); runs waiting for a person are kept, because their state is in the checkpoint file.

**The card is committed before the run starts.** The graph reads it through MCP servers in other processes, so the card (from a `{card: ...}` payload) is created in its own committed transaction first.

**Accepting is the only door.** `core/services/decisions.py` is the one place a suggestion becomes a change, and it writes the audit entry in the same transaction. Suggestions live in three tables (drafts, links, problem attachments), so ids are `draft:N`, `link:N`, `problem:C:P`. Routes are card-scoped (`/cards/{id}/suggestions/...`) so the audit entry knows which card the person was looking at, and a suggestion can only be decided from a card it belongs to.

**Choices the brief left open:**
- *Which card survives a merge:* the older one, unless the merge proposal names another. The duplicate card is **not** deleted, moved or closed; only its votes move (people who already voted on the primary are dropped, so nobody counts twice) and the two stay linked. There is no undo button; the audit entry lists the moved user ids, so it can be reversed by hand.
- *Accepting a brief or a comment* posts it as a comment by the person accepting, so it becomes part of the card's record. *Accepting a message draft* only marks it approved: **nothing is ever sent** (there is no email/Slack integration); the person copies it.
- *Score "edit"* is an override of individual ratings with a required reason. The total is recomputed by the rubric code, never typed in by hand, so it stays consistent with the weights.
- *Rejecting is remembered:* `rejected` is a link/problem status, `suggest_link` never refreshes a rejected link, and `find_related` skips pairs a person already decided, which also saves model calls.
- *"Needs review"* means a pending suggestion, a suggested link or problem (on either card of a pair), or a run paused at a checkpoint. Merge proposals are not counted twice (they belong to the link).
- *Filters and dragging:* the "Needs review" filter turns dragging off like the other filters.

**Frontend.** The AI panel in the card drawer has "Run AI triage", a "Run one step" menu, a progress indicator (polled every second while running), the checkpoint question when paused, and the suggestion list grouped by kind with accept / edit / reject (and Override for scores). The board has a "Needs review" filter with a count; the card tile shows duplicate, related and pending-suggestion indicators. "New card" can also "Create and run AI triage" in one step. No new dependencies.

**Verification.** API tests start real MCP server processes against a temporary SQLite file and run the real graph in mock mode (`tests/workflow_api`), including the acceptance flow; decision logic is tested for every suggestion type and error (`tests/test_suggestions_api.py`); `make e2e` drives the real UI in Chromium against an isolated stack (own database, own ports, mock mode): submit a duplicate of a seeded card, run triage, see the merge suggestion with confidence and reasoning, accept it, watch the votes consolidate (7 to 8), check the audit trail, the "Needs review" filter and the metrics page.
