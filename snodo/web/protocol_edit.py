"""Comment-preserving edits for protocol YAML validator settings."""

from __future__ import annotations

import difflib
import re
from typing import Any

import yaml


class ProtocolEditError(ValueError):
    """A protocol edit is invalid or cannot be verified."""


def _quoted(value: str) -> str:
    """Encode a string as a YAML double-quoted scalar."""
    return yaml.safe_dump(value, allow_unicode=True, default_style='"',
                          default_flow_style=True, width=10**9).strip()


def _lines(text: str) -> tuple[list[str], str]:
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.splitlines(keepends=True), newline


def _span(lines: list[str], key: str, identity_key: str, identity: str) -> tuple[int, int]:
    root = re.compile(rf"^  - {re.escape(identity_key)}:\s*['\"]?([^'\"\s]+)")
    starts = [i for i, line in enumerate(lines) if root.match(line.rstrip("\r\n"))]
    for pos, start in enumerate(starts):
        if root.match(lines[start].rstrip("\r\n")).group(1) != identity:
            continue
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        # The next root list item or top-level key ends this block.
        for i in range(start + 1, end):
            if lines[i].startswith(("  - ", "modes:", "validators:", "disagreement_policy:", "initial_mode:")):
                end = i
                break
        return start, end
    raise ProtocolEditError(f"unknown {identity_key.removesuffix('_id')} id {identity!r} (field {key})")


def _edit_field(lines: list[str], start: int, end: int, field: str, value: Any, newline: str) -> None:
    key_line = re.compile(rf"^    {re.escape(field)}:")
    found = next((i for i in range(start + 1, end) if key_line.match(lines[i])), None)
    if found is not None:
        finish = found + 1
        while finish < end and lines[finish].startswith("      "):
            finish += 1
        # Empty tools and cleared caps are represented by an absent key.
        if (field == "tools" and value == []) or (field == "severity_cap" and value is None):
            lines[found:finish] = []
            return
        # Preserve comments and unrelated lines within a criteria sequence using item matching.
        if field == "criteria" and isinstance(value, list):
            item_re = re.compile(r"^      - (.*?)(?:\r?\n)?$")
            original_lines = lines[found + 1:finish]
            items: list[tuple[str, list[str], str]] = []
            comments: list[str] = []
            for line in original_lines:
                match = item_re.match(line)
                if match:
                    try:
                        parsed_item = yaml.safe_load(match.group(1))
                    except yaml.YAMLError:
                        parsed_item = None
                    items.append((str(parsed_item), comments, line))
                    comments = []
                elif line.lstrip().startswith("#"):
                    comments.append(line)
            matcher = difflib.SequenceMatcher(a=[item for item, _, _ in items], b=value, autojunk=False)
            output: list[str] = []
            for tag, a1, a2, b1, b2 in matcher.get_opcodes():
                if tag == "equal":
                    output.extend(comment for _, attached, _ in items[a1:a2] for comment in attached)
                    output.extend(line for _, _, line in items[a1:a2])
                elif tag in {"replace", "insert"}:
                    output.extend(f"      - {_quoted(str(item))}{newline}" for item in value[b1:b2])
            # Keep comment-only content when the list is emptied, then emit [] inline.
            if not value:
                comments_only = [line for line in original_lines if line.lstrip().startswith("#")]
                lines[found:finish] = [f"    {field}: []{newline}"] + comments_only
            else:
                lines[found + 1:finish] = output
            return
        if isinstance(value, list):
            if not value:
                replacement = [f"    {field}: []{newline}"]
            else:
                replacement = [f"    {field}:{newline}"] + [f"      - {_quoted(str(item))}{newline}" for item in value]
        elif value is None:
            replacement = [f"    {field}: null{newline}"]
        elif isinstance(value, bool):
            replacement = [f"    {field}: {str(value).lower()}{newline}"]
        else:
            replacement = [f"    {field}: {_quoted(str(value))}{newline}"]
        lines[found:finish] = replacement
    else:
        # Insert before the first existing field at/after the canonical location.
        order = ["validator_type", "evaluation_phase", "severity_cap", "tools", "criteria"]
        rank = order.index(field) if field in order else len(order)
        insert = end
        for i in range(start + 1, end):
            match = re.match(r"^    ([a-z_]+):", lines[i])
            if match and match.group(1) in order and order.index(match.group(1)) > rank:
                insert = i
                break
        if isinstance(value, list):
            replacement = [f"    {field}: []{newline}"] if not value else [f"    {field}:{newline}"] + [f"      - {_quoted(str(x))}{newline}" for x in value]
        elif value is None:
            replacement = [f"    {field}: null{newline}"]
        else:
            replacement = [f"    {field}: {_quoted(str(value))}{newline}"]
        lines[insert:insert] = replacement


def apply_protocol_edits(text: str, edits: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Apply validator/mode edits, returning updated YAML and a change summary.

    Validator edits use ``validator_id`` plus any of ``type``, ``phase``,
    ``severity_cap``, ``tools`` or ``criteria``. Mode edits use ``mode_id``
    and either ``add_validator`` or ``remove_validator``.
    """
    if not edits:
        return text, []
    try:
        original = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProtocolEditError(f"protocol YAML does not parse: {exc}") from exc
    if not isinstance(original, dict):
        raise ProtocolEditError("protocol YAML must be a mapping")
    lines, newline = _lines(text)
    changes: list[dict[str, Any]] = []
    mapping = {"type": "validator_type", "phase": "evaluation_phase", "severity_cap": "severity_cap", "tools": "tools", "criteria": "criteria"}
    for edit in edits:
        if "validator_id" in edit:
            vid = str(edit["validator_id"])
            validator = next((v for v in original.get("validators", []) if v.get("validator_id") == vid), None)
            if validator is None:
                raise ProtocolEditError(f"unknown validator id {vid!r}")
            start, end = _span(lines, "validator", "validator_id", vid)
            for input_key, field in mapping.items():
                if input_key not in edit:
                    continue
                value = edit[input_key]
                if input_key == "phase" and value not in {"pre_execute", "post_execute"}:
                    raise ProtocolEditError(f"validator {vid!r} field {field}: invalid phase")
                before = validator.get(field)
                _edit_field(lines, start, end, field, value, newline)
                changes.append({"validator": vid, "field": field, "before": before, "after": value})
                # Recompute span after potential size change.
                start, end = _span(lines, "validator", "validator_id", vid)
        elif "mode_id" in edit:
            mid = str(edit["mode_id"])
            mode = next((m for m in original.get("modes", []) if m.get("mode_id") == mid), None)
            if mode is None:
                raise ProtocolEditError(f"unknown mode id {mid!r}")
            action = "add_validator" if "add_validator" in edit else "remove_validator"
            vid = str(edit.get(action))
            if not any(v.get("validator_id") == vid for v in original.get("validators", [])):
                raise ProtocolEditError(f"mode {mid!r} field validators: unknown validator id {vid!r}")
            before = list(mode.get("validators") or [])
            after = before.copy()
            if action == "add_validator" and vid not in after:
                after.append(vid)
            if action == "remove_validator":
                after = [item for item in after if item != vid]
            block = next((i for i, line in enumerate(lines) if re.match(rf'^  - mode_id: ["\']?{re.escape(mid)}(?:["\']?)$', line.rstrip("\r\n"))), None)
            if block is None:
                raise ProtocolEditError(f"unknown mode id {mid!r}")
            finish = next((i for i in range(block + 1, len(lines)) if lines[i].startswith("  - mode_id:")), len(lines))
            key = next((i for i in range(block + 1, finish) if re.match(r"^    validators:", lines[i])), None)
            if key is None:
                raise ProtocolEditError(f"mode {mid!r} field validators is missing")
            end = key + 1
            while end < finish and lines[end].startswith("      "):
                end += 1
            repl = [f"    validators: []{newline}"] if not after else [f"    validators:{newline}"] + [f"      - {_quoted(item)}{newline}" for item in after]
            lines[key:end] = repl
            changes.append({"validator": vid, "field": f"mode:{mid}.validators", "before": before, "after": after})
        else:
            raise ProtocolEditError("each edit must name validator_id or mode_id")
    result = "".join(lines)
    try:
        parsed = yaml.safe_load(result)
    except yaml.YAMLError as exc:
        last = changes[-1]
        raise ProtocolEditError(f"validator {last['validator']!r} field {last['field']}: result does not parse: {exc}") from exc
    for change in changes:
        if change["field"].startswith("mode:"):
            mid = change["field"].split(":", 1)[1].split(".", 1)[0]
            target = next(m for m in parsed["modes"] if m["mode_id"] == mid)
            actual = target.get("validators") or []
        else:
            target = next(v for v in parsed["validators"] if v["validator_id"] == change["validator"])
            actual = target.get(change["field"])
            if change["field"] == "tools" and change["after"] == [] and actual is None:
                actual = []
        if actual != change["after"]:
            raise ProtocolEditError(f"validator {change['validator']!r} field {change['field']}: requested change is not visible")
    return result, changes
