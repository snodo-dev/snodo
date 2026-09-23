"""Optional agent boundary judgement used by repository survey surfaces."""

import json
from typing import Any, Dict, Optional


def build_survey_judge(project_root, mode: str):
    """Build the configured read-only survey judge, or ``None``."""
    if mode == "off":
        return None
    from snodo.config import ConfigManager
    from snodo.recon import call_agent_chain, resolve_agent_model, resolve_recon_agents

    manager = ConfigManager()
    config = manager.load().get("llm", {}).get("recon", {})
    lanes = resolve_recon_agents(
        recon_models=config.get("models", []),
        recon_default_n=config.get("num_agents", 1),
    )
    chain = []
    for lane in lanes:
        for candidate in lane:
            model = resolve_agent_model(candidate)
            if model not in chain and (mode == "force" or manager.get_key_for_model(model)):
                chain.append(model)
    if not chain:
        return None

    def judge(dossier: Dict[str, Any]):
        result = call_agent_chain(
            str(project_root), chain, _judgement_prompt(dossier), paths=["./"],
            agent_label="survey-judge", max_turns=3,
        )
        if getattr(result, "model", None):
            judge.model = result.model
        if getattr(result, "error", None):
            return {"reason": f"the agent call failed: {result.error}"}
        verdicts = _parse_verdicts(getattr(result, "result", "") or "")
        return {"verdicts": verdicts} if verdicts is not None else {
            "reason": "the agent returned no parseable judgement verdicts"
        }

    judge.model = chain[0]
    return judge


def _judgement_prompt(dossier: Dict[str, Any]) -> str:
    return (
        "You are the judgement step of `snodo survey`, a read-only repository diagnosis tool. "
        "Classify only the listed subjects from the evidence below; do not explore or add requirements.\n\n"
        "Evidence dossier (JSON):\n" + json.dumps(dossier, indent=2, default=str) + "\n\n"
        'Reply with exactly one JSON object: {"judgements": [{"subject": "<id>", '
        '"verdict": "...", "reason": "...", "cited_files": ["<path>", ...]}]}\n'
        'Use "product" or "scaffolding" for boundary-role and "module" or "not-module" '
        "for undeclared-boundary. Cite at least one evidence file for every verdict."
    )


def _parse_verdicts(text: str) -> Optional[list[dict]]:
    try:
        start = text.index("{")
        payload = json.JSONDecoder().raw_decode(text[start:])[0]
    except (ValueError, json.JSONDecodeError):
        return None
    judgements = payload.get("judgements") if isinstance(payload, dict) else None
    return [item for item in judgements if isinstance(item, dict)] if isinstance(judgements, list) else None
