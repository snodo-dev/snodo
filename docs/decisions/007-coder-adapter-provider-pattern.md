---
adr: 007
status: Accepted
---

## 007: Coder adapter + code-host provider pattern

- **Status**: Accepted
- **Context**: The engine needs to produce code from task specs (coder) and interact with code-hosting platforms (provider — GitHub, local, future GitLab). A direct dependency on a specific LLM library or platform API would lock the engine to one vendor, violating the architectural principle from decisions.md: "Don't lock to vendors. Core capabilities work without vendor tools. Vendor integrations are plugins."
- **Decision**: Abstract both behind interfaces. `Coder` (ABC, `core/interfaces.py:11`) defines `implement(spec) → CodeArtifact`. Two shipped adapters: `LiteLLMAdapter` (100+ LLM backends via litellm) and `MockAdapter` (deterministic stub for testing). The code-host provider is detected at runtime: `detect_provider()` in `providers/registry.py:24` inspects the project root and returns a `CodeHostProvider` — currently `GitHubProvider` (`providers/github.py:16`, backed by PyGithub) or `LocalProvider`. The `PrMCP` consumes the abstract provider, not a concrete API.
- **Consequences**: Switching LLM backends requires no engine changes — pass a different model string to `LiteLLMAdapter`. Adding a new code host (e.g., GitLab) means writing one new provider class. The `--mock` flag bypasses LLM calls entirely, enabling CI testing and protocol configuration validation.
- **Alternatives considered**: Direct OpenAI/Anthropic dependency — rejected by explicit architectural principle. Multi-provider detection heuristics — rejected in favour of explicit `detect_provider()` with clear fallback.
- **Evidence**: Audit log entries 31 (4.10, 2025-05-25) and 27 (4.6, 2025-05-25); commits `21475265`, `e7e8727e`; `core/interfaces.py:11`, `providers/registry.py:24`, `coders/litellm.py`, `coders/mock.py`.

---
