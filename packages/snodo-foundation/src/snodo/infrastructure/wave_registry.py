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
import re
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

        When classification fails (unparseable, missing fields, exception)
        the task is left unwaved: ``wave_id`` is ``None``.  No wave is ever
        fabricated.  A warning is logged.

        Returns:
            dict with keys ``flow_type``, ``wave_id`` (str or None),
            ``task_summary`` (str or None).
        """
        lock = FileLock(str(self._lock_path))
        with lock:
            waves = self._read_waves()
            open_waves = self._filter_open(waves)
            prompt = self._build_prompt(task_spec, open_waves)
            result = self._call_classifier(prompt, completion_fn, model)

            flow_type = result.get("flow_type", "feature")
            task_summary = result.get("task_summary")
            wave_id = result.get("wave_id")

            if flow_type not in FLOW_TYPES:
                flow_type = "feature"

            # No valid classification — leave task unwaved (R3)
            if not wave_id:
                return {"flow_type": flow_type, "wave_id": None, "task_summary": task_summary}

            existing_ids = {w.wave_id for w in waves}

            # Match existing wave
            if wave_id != "new":
                matched = next((w for w in waves if w.wave_id == wave_id), None)
                if matched:
                    self._assign_to_wave(matched, task_id, task_summary)
                    self._write_waves(waves)
                    return {
                        "flow_type": flow_type,
                        "wave_id": wave_id,
                        "task_summary": task_summary,
                    }

            # Mint new wave
            feature_description = result.get("feature_description", "") or task_summary or "Unclassified"
            new_wave = WaveEntry(
                wave_id=_generate_wave_id(existing_ids),
                feature_description=feature_description,
                anchor_summaries=[task_summary] if task_summary else [],
                created=_now(),
                last_activity=_now(),
                task_ids=[task_id],
            )
            waves.append(new_wave)
            self._write_waves(waves)
            return {
                "flow_type": flow_type,
                "wave_id": new_wave.wave_id,
                "task_summary": task_summary,
            }

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
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            _logger.warning("Corrupt wave.json (%s) — returning empty", e)
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
        self, wave: WaveEntry, task_id: str, task_summary: Optional[str]
    ) -> None:
        wave.last_activity = _now()
        if task_id not in wave.task_ids:
            wave.task_ids.append(task_id)
        if task_summary and len(wave.anchor_summaries) < 3:
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
            "- Evaluate the task against each open wave's description and "
            "anchor summaries.\n"
            "- If the task shares a feature area with an open wave, return that "
            "wave's id.\n"
            "- If it does not match any open wave, return \"new\".\n"
            "- Make a clear evidence-based decision — do not bias toward either "
            "matching or minting.\n\n"
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
        """Call LLM classifier, validate required fields, retry once on failure.

        Returns a dict with at minimum ``flow_type``.  On total failure
        after retry, ``wave_id`` and ``task_summary`` are None — the caller
        must not mint a wave.
        """
        for attempt in range(2):
            try:
                if completion_fn is None:
                    from litellm import completion as completion_fn

                kwargs: dict = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": self._config.max_tokens,
                    "temperature": self._config.temperature,
                }

                # Request structured JSON when the provider supports it (R4)
                try:
                    from litellm import supports_response_format
                    if supports_response_format(
                        model, {"type": "json_object"}
                    ):
                        kwargs["response_format"] = {"type": "json_object"}
                except Exception:
                    pass

                response = completion_fn(**kwargs)
                content = response.choices[0].message.content
                if not content:
                    continue

                # Primary: direct JSON parse
                parsed = _parse_json(content)

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

        _logger.warning(
            "Classifier failed after 2 attempts — leaving task unwaved"
        )
        return _fallback()


def _parse_json(content: str) -> Optional[dict]:
    """Parse JSON from LLM response.  Tries direct parse first, then
    extracts from markdown fences, then bare ``{...}`` as backstop."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Backstop: extract from markdown fence
    m = re.search(r'```(?:json)?\s*\n(.*?)```', content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Last resort: bare JSON object anywhere in the text
    m = re.search(r'\{.*\}', content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return None


def _fallback() -> dict:
    """Return a result that leaves the task unwaved — no fabricated wave_id."""
    return {
        "flow_type": "feature",
        "wave_id": None,
        "task_summary": None,
        "feature_description": None,
    }
