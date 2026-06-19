"""Wave registry — emergent value-grouping + flow_type classification.

FILE: snodo/infrastructure/wave_registry.py

Manages .snodo/wave.json with file-locked read->classify->write.
Every task gets a one-shot classification at governance time:
  - flow_type: feature / defect / debt / risk
  - wave_id:  matched existing wave or newly minted

Waves expire after max_age_days or max_idle_days (configurable).

All feature_description and task_summary values MUST be LLM-generated
summaries. Raw task_spec slices are NEVER assigned to these fields.
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from filelock import FileLock

from snodo.infrastructure.config import WaveConfig

_logger = logging.getLogger(__name__)

FLOW_TYPES = {"feature", "defect", "debt", "risk"}


@dataclass
class WaveEntry:
    wave_id: str
    feature_description: str
    anchor_summaries: list[str] = field(default_factory=list)
    created: float = 0.0
    last_activity: float = 0.0
    task_ids: list[str] = field(default_factory=list)


def _now() -> float:
    return time.time()


def _generate_wave_id(existing: set[str]) -> str:
    for i in range(1, 100000):
        wid = f"w_{i:04x}"
        if wid not in existing:
            return wid
    return f"w_{_now():x}"


class WaveRegistry:
    """File-locked wave registry for a project.

    Thread-safe for same-machine concurrency via ``filelock``.
    Cross-machine divergence is resolved cloud-side (separate).
    """

    def __init__(
        self,
        project_root: str,
        config: Optional[WaveConfig] = None,
    ):
        self._project_root = Path(project_root)
        self._snodo_dir = self._project_root / ".snodo"
        self._wave_path = self._snodo_dir / "wave.json"
        self._lock_path = self._snodo_dir / "wave.json.lock"
        self._config = config or WaveConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify_task(
        self,
        task_spec: str,
        task_id: str,
        completion_fn,
        model: str,
    ) -> dict:
        """Read wave registry, classify via LLM, update wave.json, return result.

        Returns:
            dict with keys ``flow_type``, ``wave_id``, ``task_summary``.
        """
        lock = FileLock(str(self._lock_path))
        with lock:
            waves = self._read_waves()
            open_waves = self._filter_open(waves)
            prompt = self._build_prompt(task_spec, open_waves)
            result = self._call_classifier(prompt, completion_fn, model)
            flow_type = result["flow_type"]
            task_summary = result["task_summary"]
            existing_ids = {w.wave_id for w in waves}

            # Match existing wave
            if result["wave_id"] != "new":
                wave_id = result["wave_id"]
                matched = next((w for w in waves if w.wave_id == wave_id), None)
                if matched:
                    self._assign_to_wave(matched, task_id, task_summary)
                    self._write_waves(waves)
                    return {"flow_type": flow_type, "wave_id": wave_id, "task_summary": task_summary}

            # Mint new wave
            feature_description = result.get("feature_description", "") or "Unclassified feature"
            new_wave = WaveEntry(
                wave_id=_generate_wave_id(existing_ids),
                feature_description=feature_description,
                anchor_summaries=[task_summary],
                created=_now(),
                last_activity=_now(),
                task_ids=[task_id],
            )
            waves.append(new_wave)
            self._write_waves(waves)
            return {"flow_type": flow_type, "wave_id": new_wave.wave_id, "task_summary": task_summary}

    def open_waves(self) -> list[WaveEntry]:
        """Return waves that are still OPEN (non-expired)."""
        lock = FileLock(str(self._lock_path))
        with lock:
            return self._filter_open(self._read_waves())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_waves(self) -> list[WaveEntry]:
        if not self._wave_path.exists():
            return []
        try:
            raw = json.loads(self._wave_path.read_text())
            if not isinstance(raw, list):
                return []
            return [WaveEntry(**e) for e in raw if isinstance(e, dict)]
        except (json.JSONDecodeError, TypeError, KeyError):
            _logger.warning("Corrupt wave.json — returning empty")
            return []

    def _write_waves(self, waves: list[WaveEntry]) -> None:
        self._snodo_dir.mkdir(parents=True, exist_ok=True)
        raw = [asdict(w) for w in waves]
        tmp = self._wave_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, indent=2))
        tmp.replace(self._wave_path)

    def _filter_open(self, waves: list[WaveEntry]) -> list[WaveEntry]:
        now = _now()
        result = []
        for w in waves:
            age_ok = (now - w.created) < self._config.max_age_days * 86400
            idle_ok = (now - w.last_activity) < self._config.max_idle_days * 86400
            if age_ok and idle_ok:
                result.append(w)
        return result

    def _assign_to_wave(
        self, wave: WaveEntry, task_id: str, task_summary: str
    ) -> None:
        wave.last_activity = _now()
        if task_id not in wave.task_ids:
            wave.task_ids.append(task_id)
        if len(wave.anchor_summaries) < 3:
            if task_summary not in wave.anchor_summaries:
                wave.anchor_summaries.append(task_summary)

    def _build_prompt(
        self, task_spec: str, open_waves: list[WaveEntry]
    ) -> str:
        parts = [
            "You are a software-task classifier. Given a task spec and a list of "
            "open feature waves, respond with JSON.\n",
            "\n## Task Spec\n",
            task_spec[:2000],
            "\n\n## Open Waves\n",
        ]
        if not open_waves:
            parts.append("  (none — this task will start a new wave)")
        else:
            for w in open_waves:
                parts.append(f"\n- wave_id: {w.wave_id}")
                parts.append(f"  description: {w.feature_description}")
                anchors = w.anchor_summaries or []
                if anchors:
                    parts.append(f"  anchors ({len(anchors)}/3):")
                    for a in anchors:
                        parts.append(f"    - {a}")

        parts.append(
            "\n\n## Instructions\n"
            "Classify this task's flow_type and decide whether it belongs to an "
            "open wave or starts a new one.\n\n"
            "flow_type (one of feature, defect, debt, risk):\n"
            "  feature — new capability or enhancement\n"
            "  defect  — bug fix or regression\n"
            "  debt    — refactor, tech-debt cleanup, test improvement\n"
            "  risk    — security, compliance, or reliability\n\n"
            "## Required output fields — ALL REQUIRED\n"
            "- flow_type\n"
            "- wave_id: an existing wave_id from the list above, or \"new\"\n"
            "- task_summary: ONE LINE describing THIS task "
            "(e.g. \"Migrate Team page to SvelteKit\"). "
            "NEVER copy the spec literally — write a concise label.\n"
            "- feature_description: REQUIRED when wave_id=\"new\". "
            "A short feature label for the body of work, broader than one task "
            "(e.g. \"SvelteKit dashboard migration\"). "
            "NEVER copy the spec literally.\n\n"
            "## Wave matching\n"
            "- Assign to an existing wave when this task is part of the same "
            "feature or effort as that wave's description.\n"
            "- Only return \"new\" if this is genuinely a different feature.\n"
            "- If uncertain, lean toward the closest existing wave — "
            "over-grouping is preferable to fragmentation.\n\n"
            "## Examples\n"
            'Good: {"flow_type": "feature", "wave_id": "w_0001", '
            '"task_summary": "Migrate Team page to SvelteKit"}\n'
            'Good: {"flow_type": "feature", "wave_id": "new", '
            '"task_summary": "Migrate Dashboard page to SvelteKit", '
            '"feature_description": "SvelteKit dashboard migration"}\n'
            'Bad: {"flow_type": "feature", "wave_id": "new", '
            '"task_summary": "VALIDATION TOKEN: ..."}\n\n'
            "Respond with ONLY the JSON object. All listed fields are required.\n"
        )
        return "".join(parts)

    def _call_classifier(
        self, prompt: str, completion_fn, model: str
    ) -> dict:
        """Call LLM classifier, validate required fields, retry once on failure."""
        for attempt in range(2):
            try:
                if completion_fn is None:
                    from litellm import completion as completion_fn
                kwargs = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 400,
                    "temperature": 0.0,
                }
                response = completion_fn(**kwargs)
                content = response.choices[0].message.content
                if not content:
                    continue
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    import re
                    m = re.search(r'\{.*\}', content, re.DOTALL)
                    if m:
                        parsed = json.loads(m.group(0))
                if isinstance(parsed, dict):
                    flow_type = parsed.get("flow_type", "")
                    wave_id = parsed.get("wave_id", "")
                    task_summary = parsed.get("task_summary", "")
                    if (
                        flow_type in FLOW_TYPES
                        and wave_id
                        and task_summary
                        and (wave_id != "new" or parsed.get("feature_description"))
                    ):
                        return parsed
            except Exception as e:
                _logger.warning(
                    "Classifier LLM call attempt %d failed: %s",
                    attempt + 1, e,
                )
        _logger.error("Classifier LLM failed after 2 attempts — using fallback")
        return _fallback()


def _fallback() -> dict:
    return {
        "flow_type": "feature",
        "wave_id": "new",
        "task_summary": "Unclassified task",
        "feature_description": "Unclassified feature",
    }
