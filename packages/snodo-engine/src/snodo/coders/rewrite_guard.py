"""Guard against implausibly short whole-file coder rewrites."""

import re
from pathlib import Path
from typing import Any, Dict, List


def implausible_rewrite_feedback(files: List[Dict], workspace: Any, prompt: str) -> Dict[str, str]:
    """Refuse drastic accidental shrinkage before a body enters staged writes."""
    root = getattr(workspace, "project_root", None)
    if not isinstance(root, (str, Path)):
        return {}
    shortening_requested = bool(re.search(r"\b(shorten|condense|reduce|trim|remove\s+sections?)\b", prompt, re.I))
    refusals: Dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict) or item.get("action", "write") != "write":
            continue
        path, content = item.get("path"), item.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            continue
        if item.get("acknowledge_short_rewrite") is True or shortening_requested:
            continue
        try:
            target = (Path(root) / path).resolve()
            target.relative_to(Path(root).resolve())
            if not target.is_file():
                continue
            old_size, new_size = target.stat().st_size, len(content.encode("utf-8"))
        except (OSError, ValueError):
            continue
        delta = old_size - new_size
        if delta >= 1024 and new_size <= old_size * 0.4:
            refusals[path] = f"{path}: submitted {new_size} bytes, {delta} bytes smaller than existing {old_size}-byte file"
    return refusals

