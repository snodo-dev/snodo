"""TypeSafe Jev decision tool for Snodo LLM validators."""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

import httpx

from snodo.validators.tool_registry import ValidatorTool, ValidatorToolContext

_MAX_FILES = 5

def handle_decide(arguments: Mapping[str, Any], context: ValidatorToolContext) -> str:
    """Send validated questions and selected context to Jev; return safe text."""
    key_name = str(context.tool_options.get("api_key_env", "TYPESAFE_API_KEY"))
    api_key = os.environ.get(key_name)
    if not api_key:
        return f"TypeSafe Jev unavailable: environment variable {key_name} is not set."
    try:
        questions = _normalize_questions(arguments.get("questions"))
        state = _get_context(arguments, context)
        max_chars = max(1, int(context.tool_options.get("max_input_chars", 50000)))
        truncated = len(state) > max_chars
        state = state[:max_chars]
        timeout = float(context.tool_options.get("timeout_seconds", 30))
        base_url = str(context.tool_options.get("base_url", "https://api.typesafe.ai")).rstrip("/")
        model = str(context.tool_options.get("model", "jev-latest"))
        response = httpx.post(
            f"{base_url}/v1/systemone",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "state": state, "questions": questions},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
            return "TypeSafe Jev error: malformed response; expected an answers object."
        return _render(payload, questions, truncated)
    except httpx.TimeoutException:
        return "TypeSafe Jev error: request timed out. Check the endpoint or increase timeout_seconds."
    except httpx.HTTPStatusError as exc:
        return f"TypeSafe Jev error: HTTP {exc.response.status_code} from the decision API."
    except httpx.HTTPError:
        return "TypeSafe Jev error: could not complete the HTTP request. Check the endpoint and network."
    except (ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
        # Do not include exception text: paths or HTTP internals must not leak secrets.
        return f"TypeSafe Jev error: {type(exc).__name__}; check the tool arguments and options."


def _normalize_questions(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("questions must be a non-empty list")
    normalized: dict[str, dict[str, Any]] = {}
    for question in raw:
        if not isinstance(question, Mapping):
            raise ValueError("each question must be an object")
        name, kind, instructions = question.get("name"), question.get("type"), question.get("instructions")
        if not all(isinstance(value, str) and value.strip() for value in (name, kind, instructions)):
            raise ValueError("question name, type, and instructions are required")
        if name in normalized or kind not in {"noul", "choice", "score"}:
            raise ValueError("question names must be unique and types must be noul, choice, or score")
        item: dict[str, Any] = {"type": kind, "instructions": instructions}
        criteria = question.get("criteria")
        if kind == "choice":
            if not isinstance(criteria, Mapping) or not criteria:
                raise ValueError("choice questions require criteria as an option-description map")
            item["criteria"] = dict(criteria)
        elif kind == "score":
            if not isinstance(criteria, list) or not criteria:
                raise ValueError("score questions require criteria as an ordered list of levels")
            item["criteria"] = criteria
        elif criteria is not None:
            item["criteria"] = criteria
        normalized[name] = item
    return normalized


def _get_context(arguments: Mapping[str, Any], context: ValidatorToolContext) -> str:
    selected = arguments.get("context")
    if selected == "spec":
        return context.spec_text
    if selected == "diff":
        if context.diff_accessor is None:
            raise ValueError("task diff is unavailable")
        if context.diff_refs:
            return str(context.diff_accessor(*context.diff_refs))
        return str(context.diff_accessor())
    if selected != "files":
        raise ValueError("context must be diff, spec, or files")
    paths = arguments.get("files")
    if not isinstance(paths, list) or not 1 <= len(paths) <= _MAX_FILES:
        raise ValueError(f"files context requires between 1 and {_MAX_FILES} paths")
    root = context.workspace_root.resolve()
    contents = []
    for value in paths:
        if not isinstance(value, str) or not value:
            raise ValueError("file paths must be non-empty strings")
        target = (root / value).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError(f"file path is outside the workspace or unreadable: {value}")
        contents.append(f"--- {value} ---\n{target.read_text(encoding='utf-8')}")
    return "\n".join(contents)


def _render(payload: Mapping[str, Any], questions: Mapping[str, Any], truncated: bool) -> str:
    answers = payload["answers"]
    lines = []
    for name, question in questions.items():
        answer = answers.get(name)
        if not isinstance(answer, Mapping):
            return "TypeSafe Jev error: malformed response; an answer is missing or invalid."
        kind = question["type"]
        if kind == "noul":
            lines.append(f"{name} [noul]: {answer.get('noul')} (probability)")
        elif kind == "choice":
            lines.append(
                f"{name} [choice]: {answer.get('choice')} (probabilities={_compact(answer.get('probabilities'))}; "
                f"confidence={answer.get('confidence')})"
            )
        else:
            lines.append(
                f"{name} [score]: {answer.get('score')} (legend={_compact(answer.get('legend'))}; "
                f"probabilities={_compact(answer.get('probabilities'))}; confidence={answer.get('confidence')})"
            )
    usage = payload.get("usage", {})
    if not isinstance(usage, Mapping):
        usage = {}
    lines.append(f"usage: input_tokens={usage.get('input_tokens', usage.get('input', 'unknown'))}, "
                 f"output_tokens={usage.get('output_tokens', usage.get('output', 'unknown'))}")
    if truncated:
        lines.append("context truncated to max_input_chars")
    return "\n".join(lines)


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


tool = ValidatorTool(
    definition={
        "type": "function",
        "function": {
            "name": "decide",
            "description": "Ask TypeSafe Jev typed questions about the task context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "context": {"type": "string", "enum": ["diff", "spec", "files"]},
                    "files": {"type": "array", "items": {"type": "string"}, "maxItems": _MAX_FILES},
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string", "enum": ["noul", "choice", "score"]},
                                "instructions": {"type": "string"},
                                "criteria": {"type": ["object", "array", "null"]},
                            },
                            "required": ["name", "type", "instructions"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["context", "questions"],
                "additionalProperties": False,
            },
        },
    },
    handler=handle_decide,
)
