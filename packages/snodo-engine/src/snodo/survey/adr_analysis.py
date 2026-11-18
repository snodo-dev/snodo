"""Read repository code with recon and return verified ADR proposals."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Callable

from snodo.survey.adr_proposals import ADRProposal, parse_adr_proposals


def _existing_titles(root: Path) -> list[str]:
    decisions = root / "docs" / "decisions"
    titles = []
    if decisions.is_dir():
        for path in sorted(decisions.glob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            for line in path.read_text(errors="replace").splitlines():
                if line.startswith("# "):
                    titles.append(line[2:].strip())
                    break
    return titles


def _prompt(titles: list[str]) -> str:
    title_text = "\n".join(f"- {title}" for title in titles) or "(none)"
    return (
        "Find a small number of decision records describing patterns the code "
        "consistently follows, not wishes or one-off choices. Only propose a "
        "pattern supported by at least three distinct files and no counterexample "
        "you found. Do not repeat these existing record titles:\n"
        f"{title_text}\n\n"
        "Read the cited files before citing them. Return at most five proposals "
        "as JSON: {\"proposals\":[{\"title\":\"...\",\"context\":\"...\","
        "\"decision\":\"...\",\"consequences\":\"...\","
        "\"evidence\":[{\"path\":\"repo/relative/path\"}]}]}. "
        "Include at least three different evidence file paths actually read per proposal."
    )


def analyze_adr_proposals(
    project_root: str | Path,
    *,
    call: Callable | None = None,
) -> tuple[list[ADRProposal], list[str], str | None]:
    """Ask the first configured recon model for ADRs; return proposals, drops, error.

    ``call`` is an injectable recon-call seam for tests. This operation does not write.
    """
    root = Path(project_root).resolve()
    try:
        from snodo.config import ConfigManager
        from snodo.recon import (
            RECON_DEFAULT_DEADLINE_SECONDS,
            call_agent_chain,
            resolve_agent_model,
            resolve_recon_agents,
        )
        from snodo.recon_settings import resolve_recon_settings

        config = ConfigManager().load()
        recon_config = config.get("llm", {}).get("recon", {})
        lanes = resolve_recon_agents(recon_models=recon_config.get("models", []))
        model = resolve_agent_model(lanes[0][0] if lanes and lanes[0] else "default")
        settings = resolve_recon_settings(config)
        caller = call or call_agent_chain
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            caller, str(root), [model], _prompt(_existing_titles(root)), ["./"],
            "intake-adr-analysis", settings["max_tool_turns"], settings.get("max_tokens", 16000),
        )
        try:
            result = future.result(timeout=settings.get("deadline_seconds", RECON_DEFAULT_DEADLINE_SECONDS))
        except TimeoutError:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            return [], [], f"ADR analysis timed out after {settings['deadline_seconds']} seconds."
        else:
            executor.shutdown(wait=True)
        if getattr(result, "error", None):
            return [], [], f"ADR analysis failed: {result.error}"
        proposals, dropped = parse_adr_proposals(
            getattr(result, "result", ""), root, existing_titles=_existing_titles(root), limit=5,
        )
        return proposals, dropped, None
    except Exception as exc:  # model/config errors are reported, never propagated
        return [], [], f"ADR analysis failed: {exc}"
