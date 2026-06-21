# Spec: snodo recon — CLI command + config-backed multi-model fan-out

## Why
Multi-model recon already works internally: ReconManager.submit(query,
paths, agents=[...]) runs models in parallel (ThreadPoolExecutor),
returns results keyed by agent+model, and is usage-tracked (same
litellm path as coder/validators).

Different models surface different findings, so multi-model recon has
real value (unlike redundant validators, where verdicts rarely differ).

Gaps to close:
- No CLI recon command (only `logs` shows recon results today).
- No config-backed default fan-out — caller must name models.
- num_agents count is the low-friction primary path; explicit model
  list stays as an escape hatch.

## Config — defaults + priority list
infrastructure/config.py — add to LlmConfig a `recon` block:

  llm:
    recon:
      num_agents: 1          # default fan-out when caller specifies nothing
      models: []             # ordered priority list, e.g. [m1, m2, m3]

- num_agents default 1, overridable by caller.
- models: ordered; slot i is used for the i-th recon agent.

## Shared resolver — recon/__init__.py
resolve_recon_agents(requested_n, recon_models, recon_default_n,
                     explicit_agents=None) -> list[str]

Precedence (most specific wins):
  1. explicit_agents non-empty            -> return explicit_agents (escape hatch)
  2. n = requested_n if not None
        else recon_default_n if not None
        else 1
  3. resolve n against recon_models:
       - recon_models EMPTY:
           * n in (None, 1): return ["default"]
           * n > 1:          return ["default"] + WARN to stderr
             "no llm.recon.models configured; running default once
              (requested {n} ignored)"
       - recon_models PRESENT:
           * take first n models in order
           * for any slot i >= len(recon_models): WARN to stderr
             "no model configured for recon slot {i+1}" and skip
             (do NOT fail)

All warnings -> STDERR. Results/primary output -> STDOUT.

## CLI command (new) — cli/commands/recon_cmd.py
  snodo recon "<query>" [paths...] [--agents N]

- --agents: typer.Option(None, "--agents", help="Number of recon
  models to fan out to (from llm.recon.models). Omit to use
  llm.recon.num_agents default.")
- read llm.recon.{num_agents, models} from config
- agents = resolve_recon_agents(requested_n=--agents,
            recon_models=cfg.models, recon_default_n=cfg.num_agents)
- call ReconManager.submit(query, paths, agents=agents)
- register command in cli/main.py

## MCP — add num_agents, keep agents (both via shared resolver)
mcp/tools.py recon tool schema (currently exposes agents: list[str]
at ~459-490) — add optional num_agents: int.

mcp/recon_handlers.py handle_recon — resolve via the SAME shared
resolver so CLI and MCP behave identically:
  agents = resolve_recon_agents(
      requested_n   = arguments.get("num_agents"),
      recon_models  = cfg.recon.models,
      recon_default_n = cfg.recon.num_agents,
      explicit_agents = arguments.get("agents"),
  )
  -> ReconManager.submit(query, paths, agents=agents)

num_agents is the primary low-friction path ("give me 2" without
naming models). agents=[...] stays for explicit orchestrator control.
If BOTH passed: explicit agents wins, WARN that num_agents ignored.

## Behaviour summary (CLI + MCP identical)
- nothing specified            -> config num_agents (default 1) vs models
- --agents N / num_agents=N     -> N from models, warn on overflow
- explicit agents=[...]         -> exactly those, ignore counts (warn)
- no models configured + N>1    -> default once + warn

## Tests
- resolve(None, [m1,m2,m3], 2)        -> [m1,m2]            (config default applies)
- resolve(3,    [m1,m2,m3], 2)        -> [m1,m2,m3]         (caller overrides default)
- resolve(5,    [m1,m2,m3], 2)        -> [m1,m2,m3] + 2 stderr warns
- resolve(None, [m1,m2,m3], None)     -> [m1]               (default n=1)
- resolve(None, [], 2)                -> ["default"] + 1 warn
- resolve(None, [], None)             -> ["default"], no warn
- resolve(2, [m1,m2,m3], 2, explicit_agents=["x","y"]) -> ["x","y"] + warn (explicit wins)
- CLI: snodo recon dispatches resolved agents to submit()
- MCP: num_agents resolves via shared helper; agents list still works
- warnings on stderr, results on stdout
- config: llm.recon.num_agents defaults to 1 when unset; models to []

## Touch
infrastructure/config.py (LlmConfig.recon block),
recon/__init__.py (resolve_recon_agents helper),
cli/commands/recon_cmd.py (new), cli/main.py (register),
mcp/tools.py (num_agents in schema), mcp/recon_handlers.py (resolver wiring)

Commit: feat(recon): CLI command + config-backed num_agents multi-model fan-out (CLI + MCP)
