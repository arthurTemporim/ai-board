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

**Provider.** Claude through `langchain-anthropic` (`ChatAnthropic.with_structured_output`), as the Phase 3 brief says. The earlier plan said the LLM would sit behind a litellm service, so `LITELLM_BASE_URL` / `LITELLM_API_KEY` point the same client at a litellm proxy's Anthropic-format endpoint, and `AI_MODEL_OVERRIDE` maps every prompt onto a litellm alias. One code path covers both; nothing calls litellm's own SDK. If the proxy fronts local or open models, tool-calling structured output may be unreliable, which is what the single repair retry below is for. **Not yet exercised against a live model or proxy** (no key in this environment); the factory is tested with a fake chat model and construction of the real `ChatAnthropic` is tested.

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
