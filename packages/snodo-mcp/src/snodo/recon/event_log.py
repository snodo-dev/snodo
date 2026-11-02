"""Append-only progress event log for a single recon."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Mapping

_logger = logging.getLogger(__name__)
_MAX_SUMMARY = 160


@dataclass(frozen=True)
class ReconEvent:
    timestamp: str
    lane: int
    agent: str
    model: str
    kind: str
    detail: dict[str, Any]


class ReconEventLog:
    """Read and append JSONL events associated with one recon directory."""

    def __init__(self, recon_dir: str | Path):
        self.path = Path(recon_dir) / "events.jsonl"

    @staticmethod
    def summarize_arguments(arguments: Mapping[str, Any]) -> dict[str, str]:
        """Return bounded argument descriptions, never raw structured/file data."""
        summaries = {}
        for key, value in arguments.items():
            if isinstance(value, str):
                summary = value
            elif isinstance(value, (int, float, bool)) or value is None:
                summary = str(value)
            else:
                summary = f"<{type(value).__name__}>"
            if len(summary) > _MAX_SUMMARY:
                summary = summary[:_MAX_SUMMARY - 1] + "…"
            summaries[str(key)[:40]] = summary
        return summaries

    def append(self, lane: int, agent: str, model: str, kind: str,
               detail: Mapping[str, Any] | None = None) -> None:
        """Append one event atomically at the line level; logging failures are non-fatal."""
        event = ReconEvent(datetime.now(timezone.utc).isoformat(), lane, agent,
                           model, kind, dict(detail or {}))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = (json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            with self.path.open("ab", buffering=0) as stream:
                stream.write(line)
        except Exception as exc:  # progress reporting must not interrupt a recon
            _logger.debug("Could not append recon event to %s: %s", self.path, exc)

    def read(self, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        """Read complete records after byte offset; preserve an incomplete tail."""
        try:
            with self.path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read()
        except FileNotFoundError:
            return [], offset
        complete = data.rfind(b"\n") + 1
        events = []
        for line in data[:complete].splitlines():
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, UnicodeDecodeError):
                _logger.debug("Ignoring malformed recon event line in %s", self.path)
        return events, offset + complete

    @staticmethod
    def format(event: Mapping[str, Any]) -> str:
        """Format a record as one readable line, including unknown future kinds."""
        lane, agent, model = event.get("lane", "?"), event.get("agent", "?"), event.get("model", "?")
        kind, detail = event.get("kind", "event"), event.get("detail") or {}
        if kind == "start":
            text = "started"
        elif kind == "tool_turn":
            tools = detail.get("tools", [])
            text = "tools: " + ", ".join(str(t) for t in tools)
            if detail.get("summary"):
                text += " — " + str(detail["summary"])
        elif kind == "failover":
            text = f"failed over to {detail.get('model', '?')}"
        elif kind == "finish":
            text = "finished"
        elif kind == "failure":
            text = "failed" + (f": {detail['error']}" if detail.get("error") else "")
        else:
            text = f"{kind}: {json.dumps(detail, ensure_ascii=False, sort_keys=True)}"
        return f"[{event.get('timestamp', '?')}] lane {lane} {agent} ({model}): {text}".replace("\n", " ")
