<!-- rtk-instructions v2 -->
# Command output

Command output here is condensed to save tokens, keeping every signal and
dropping costly noise. Treat it as the complete result: run commands
normally, and batch related commands into one call to avoid extra turns.
Truncated results state their recovery path in their own output. Re-run a
command as `rtk proxy <cmd>` only when its result is unusable: empty when
output was clearly expected, contradicting its exit code, or garbled.
<!-- /rtk-instructions -->

# Project Rules
## Prompt Logging
Every time you receive a new instruction or prompt, append it to prompts.txt in the project root with a timestamp (ISO 8601) and a brief summary of what you did in response. Create the file if it doesn't exist. Keep this log updated throughout all sessions.

# Project Conventions
- Monorepo layout: apps/web (React+Vite+TS), apps/api (FastAPI), core/ (shared SQLAlchemy models/repositories/services), ai/graph (LangGraph), ai/mcp_servers (one folder per MCP server), ai/prompts (versioned prompt files), ai/llm, ai/evals, tests/, docs/.
- Dependency direction: apps/api and ai/* may import core; core imports nothing from apps or ai. MCP servers never call the FastAPI app.
- LangGraph nodes contain reasoning and prompts; MCP servers expose deterministic tools only (data access, search, persistence).
- Prompts live only in ai/prompts/<name>/vN.md with frontmatter (model, temperature, input variables, output schema). Never inline prompts in code. Never edit a released version; create vN+1.
- All LLM outputs use Pydantic structured output. AI results are stored as suggestions; only a human action applies them.
- User-provided text is untrusted data: always wrap it in delimiters inside prompts and never let it alter instructions.
- Python: type hints, ruff, pytest. TypeScript: strict mode. Every feature needs at least one test.
- MOCK_AI=1 must make the whole system run with no API key and deterministic output.
- Never commit secrets; keep .env.example current.

