# W5-03: Pure model resolver

## Intent
Map a user query ("gemini", "sonnet", "gpt4o") to discovered models.
Pure logic — no I/O, no HTTP. Takes a list of ModelInfo + query,
returns Exact / Ambiguous / NotFound. Interaction (disambiguation)
happens at the MCP layer (W5-04), not here.

## Dependency
Imports ModelInfo from infrastructure/model_discovery.py (W5-02).

## What to build

### infrastructure/model_resolver.py

Resolution (pydantic BaseModel):
  status: Literal["exact", "ambiguous", "not_found"]
  match: Optional[ModelInfo] = None        # exact only
  candidates: list[ModelInfo] = []         # ambiguous only
  query: str                               # echo back for not_found messages

resolve_model(query: str, candidates: list[ModelInfo]) -> Resolution

Matching strategy — normalized substring:
  - normalize(s): lowercase, strip - _ . /
  - Match query (normalized) as substring against BOTH the normalized
    bare model name (last segment of full_string) AND the normalized
    full_string
  - 1 match → exact
  - >1 match → ambiguous (return all candidates)
  - 0 matches → not_found

## Acceptance criteria
- "gpt4o" matches "gpt-4o"
- "sonnet" matches "claude-sonnet-4-20250514"
- "gemini" matching two providers → ambiguous with both candidates
- "gemini-3.5" matches "google/gemini-3.5-flash"
- nonexistent query → not_found with query echoed
- Resolution is pydantic
- Zero I/O — pure function

## Testing
- Unit test: each example above
- Unit test: ambiguous returns all matching candidates
- Unit test: normalization handles hyphens/dots/slashes/case
- Unit test: empty candidate list → not_found
- Full suite passes clean

## Constraints
- Pure logic — no HTTP, no file I/O, no network
- Import ModelInfo from model_discovery, do not redefine it
- Resolution is pydantic, not dataclass
