# Recon: multi-agent read-only exploration via MCP

## Intent
Recon is the exploration phase before specification. The orchestrator
calls recon(query, paths, agents) when it needs to understand the
codebase before writing a spec. One or more agents independently answer
the same question by reading files within the given paths. Results are
returned as raw text — the orchestrator synthesises them into a spec.

Recon is:
- Always read-only (read_file, list_files tools only — no write, no git,
  no commit)
- Always async (returns recon_id, orchestrator polls get_recon_status)
- Always transient in intent (but recorded in .snodo/recons/ for audit
  and future snodo-cloud use)
- Optional — the orchestrator decides when it's needed
- Provider-agnostic — uses the same adapter infrastructure as the coder
  (LiteLLM today, OpenCode tomorrow, GeminiCLI next)

Multiple agents answer the same question independently — raw results
returned, orchestrator synthesises. This is the quorum principle applied
at the framing phase.

This formalises snodo's core methodology: explore → spec → validate →
execute → validate. The recon tool makes explore a first-class protocol
concept and automates the ways of working the paper describes.

## What to build

### .snodo/recons/<recon_id>/ (storage)
Mirror of .snodo/jobs/ pattern. Per-recon directory:
  state.json   — status, timestamps, query, paths, agents
  results.json — list of {agent, model, result, error} when complete

ReconState pydantic:
  recon_id: str  (rec_ prefix, same generation as j_ jobs)
  query: str
  paths: list[str]  (e.g. ["./", "src/auth/"])
  agents: list[str]  (model strings; "default" → configured model)
  status: Literal["running", "complete", "failed"]
  created_at: float
  completed_at: Optional[float]

ReconResult pydantic:
  agent: str   (the agent name as passed in)
  model: str   (resolved model string used)
  result: str  (raw text answer)
  error: Optional[str]

### ReconManager (jobs/recon.py or infrastructure/recon.py)
Mirrors JobManager pattern:
  submit(query, paths, agents) → recon_id
    - generates rec_<6hex> ID
    - writes initial state.json (running)
    - spawns background process (same pattern as job submit)
    - the background process:
        * resolves each agent ("default" → get_model(), named → resolve
          via model_resolver or pass directly as liteLLM string)
        * fans out N parallel LLM calls via completion_fn (validator
          pattern — pure LLM call, no tools beyond read_file/list_files)
        * each agent receives: query + paths as context
        * collects raw text results
        * writes results.json
        * updates state.json to complete/failed

  get_status(recon_id) → ReconState + results if complete
  list_recons(limit) → recent recons

### Adapter symmetry — READ-ONLY CONSTRAINT
The same adapter that runs the coder runs recon — but with a constrained
tool surface. For LiteLLMAdapter today:
  - recon uses the completion_fn with read_file/list_files tools only
  - No write_file, no git ops, no commit tools
  - The constraint is at the tool-surface level, not the adapter level

For OpenCodeAdapter (Wave 11):
  - Recon = opencode session with read-only tool policy
    (POST /session/{id}/permissions → reject all write operations)
  - Coder = opencode session with full tool access

The adapter decides how to enforce read-only. The recon interface is
the same regardless of adapter.

### MCP tools (mcp/tools.py TOOL_REGISTRY)

recon:
  description: "Dispatch a read-only exploration query to one or more
    agents. Returns a recon_id immediately. Agents independently read
    the codebase to answer the query. Use get_recon_status to poll for
    completion, then get_recon_results for the raw answers. Use when
    you need to understand the codebase before writing a spec."
  inputSchema:
    query: string (required)
    paths: array of strings (required, e.g. ["./"] for monorepo root)
    agents: array of strings (optional, default ["default"])
  requires_token: False

get_recon_status:
  description: "Get the status of a recon query."
  inputSchema:
    recon_id: string (required)
  requires_token: False

get_recon_results:
  description: "Get the raw results of a completed recon query. Returns
    one result per agent. Results are raw text — synthesise them into
    a spec."
  inputSchema:
    recon_id: string (required)
  requires_token: False

All three available in producer mode (and any mode that benefits from
exploration).

### ReconToolHandler (mcp/recon_handlers.py, new)
Follows JobToolHandler / ModelToolHandler pattern:
  handle_recon(args) → {"recon_id": "rec_...", "status": "running"}
  handle_get_recon_status(args) → ReconState dict
  handle_get_recon_results(args) → {"results": [...], "status": ...}

### MCP server wiring (mcp/server.py)
  self._recon_handler = ReconToolHandler(project_root)
  Dispatch in call_tool for recon, get_recon_status, get_recon_results

## Acceptance criteria
- recon(query, paths, agents) creates a rec_ job, returns immediately
- Background process fans out N agents in parallel
- Each agent reads files within paths using read_file/list_files only —
  no write tools
- Results stored in .snodo/recons/<id>/results.json
- get_recon_status returns running/complete/failed
- get_recon_results returns raw text per agent
- "default" agent resolves to the configured model
- Named agents resolve to model strings (liteLLM direct, no discovery
  required)
- Read-only constraint enforced — no file writes possible during recon
- Provider-agnostic: same interface works with LiteLLM (today) and
  OpenCode (Wave 11)

## Testing
- Unit: submit creates state.json with correct fields
- Unit: get_status reads state and results correctly
- Unit: "default" agent resolves to configured model
- Unit: named agent passes through as liteLLM model string
- Unit: results.json written with one entry per agent
- Unit: read-only tool surface (no write_file in the tool list)
- Integration: end-to-end recon with a real query returns text results
- Full suite passes

## Constraints
- Read jobs/__init__.py (JobManager pattern to mirror), mcp/job_handlers.py
  (handler pattern), infrastructure/model_resolver.py, cli/config.py
  (get_model for "default"), validators/llm_validator.py (_call_llm
  pattern for the read-only LLM call) before touching anything
- Mirror the job system structure exactly — same polling pattern, same
  file layout, same handler pattern
- Read-only is a hard constraint: the tool surface passed to the LLM
  must not include any write operations
- Results are raw text — snodo does not summarise or synthesise
- This is Wave 11 pre-work: the adapter symmetry (same interface for
  LiteLLM and OpenCode) must be clean before OpenCode lands
