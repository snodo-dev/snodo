"""Protocol read, preview, and guarded save endpoints."""

from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import os
import tempfile
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from snodo.mcp.schema import build_protocol_schema_publication
from snodo.protocol_validation import validate_protocol
from snodo.web.protocol import protocol_view
from snodo.web.protocol_edit import ProtocolEditError, apply_protocol_edits


def _project(request: Request, name: Any) -> Any:
    return next((item for item in request.app.state.projects if item.name == name), None)


def _input(payload: Any) -> tuple[Any, str, list[dict[str, Any]]] | JSONResponse:
    if not isinstance(payload, dict):
        return JSONResponse({"error": "Request body must be a JSON object."}, status_code=400)
    project = payload.get("project")
    etag = payload.get("etag")
    edits = payload.get("edits")
    if not isinstance(project, str) or not isinstance(etag, str) or not isinstance(edits, list):
        return JSONResponse({"error": "Request requires project, etag, and edits."}, status_code=400)
    if not all(isinstance(edit, dict) for edit in edits):
        return JSONResponse({"error": "Each edit must be an object."}, status_code=400)
    return project, etag, edits


def _preview(project: Any, etag: str, edits: list[dict[str, Any]]) -> dict[str, Any] | JSONResponse:
    path = project.path / ".snodo" / "protocol.yml"
    try:
        original_bytes = path.read_bytes()
        original = original_bytes.decode("utf-8")
    except (OSError, UnicodeError):
        return JSONResponse({"error": "Protocol file not found or could not be read."}, status_code=400)
    current_etag = hashlib.sha256(original_bytes).hexdigest()
    if not hmac.compare_digest(current_etag, etag):
        return JSONResponse({"error": "Protocol file changed on disk."}, status_code=409)
    try:
        updated, changes = apply_protocol_edits(original, edits)
    except (ProtocolEditError, TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=True) as temp:
        temp.write(updated)
        temp.flush()
        findings = validate_protocol(temp.name)
    publication = build_protocol_schema_publication()
    rules = [{"id": rule["id"], "description": rule["description"], "ok": True} for rule in publication["x-snodo-cross-field-rules"]]
    result = {"diff": "".join(difflib.unified_diff(original.splitlines(keepends=True), updated.splitlines(keepends=True), fromfile=".snodo/protocol.yml", tofile=".snodo/protocol.yml")), "changes": changes, "valid": not findings, "rules": rules, "findings": findings, "text": updated}
    if findings:
        return JSONResponse({**result, "error": "Edited protocol is invalid."}, status_code=422)
    return result


async def handler(request: Request) -> JSONResponse:
    """Return protocol details for the selected project."""
    project_name = request.query_params.get("project")
    if not project_name:
        return JSONResponse({"error": "Missing required query value: project."}, status_code=400)
    project = _project(request, project_name)
    if project is None:
        return JSONResponse({"error": "Project not found."}, status_code=404)
    result = protocol_view(project)
    if "error" not in result:
        result["page_token"] = request.app.state.page_token
    return JSONResponse(result, status_code=400 if "error" in result else 200)


async def write_handler(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "Invalid JSON body."}, status_code=400)
    parsed = _input(payload)
    if isinstance(parsed, JSONResponse):
        return parsed
    name, etag, edits = parsed
    project = _project(request, name)
    if project is None:
        return JSONResponse({"error": "Project not found."}, status_code=404)
    result = _preview(project, etag, edits)
    if isinstance(result, JSONResponse):
        return result
    return JSONResponse({key: value for key, value in result.items() if key != "text"})


async def save_handler(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "Invalid JSON body."}, status_code=400)
    parsed = _input(payload)
    if isinstance(parsed, JSONResponse):
        return parsed
    name, etag, edits = parsed
    token = payload.get("page_token")
    expected = request.app.state.page_token
    if not isinstance(token, str) or not hmac.compare_digest(token, expected):
        return JSONResponse({"error": "Invalid page token."}, status_code=403)
    project = _project(request, name)
    if project is None:
        return JSONResponse({"error": "Project not found."}, status_code=404)
    result = _preview(project, etag, edits)
    if isinstance(result, JSONResponse):
        return result
    path = project.path / ".snodo" / "protocol.yml"
    try:
        # Recheck immediately before replacement to avoid overwriting intervening edits.
        current = path.read_bytes()
        if not hmac.compare_digest(hashlib.sha256(current).hexdigest(), etag):
            return JSONResponse({"error": "Protocol file changed on disk."}, status_code=409)
        fd, temp_path = tempfile.mkstemp(prefix=".protocol-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(result["text"])
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
    except OSError:
        return JSONResponse({"error": "Protocol file could not be saved."}, status_code=500)
    refreshed = protocol_view(project)
    refreshed["page_token"] = expected
    return JSONResponse(refreshed)
