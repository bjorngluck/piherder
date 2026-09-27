"""Hosted MCP on the herder (Streamable HTTP, stateless JSON).

Same process as the web app. Clients send ``POST /mcp`` with
``Authorization: Bearer ph_…``. Tools mirror the public ``piherder-mcp``
stdio adapter and call the existing ``/api/v1`` route functions in-process.

Transport choice: MCP Streamable HTTP (spec 2025-03-26 and later), stateless,
preferring a single ``application/json`` response. That is what Cursor, Claude
Code, and VS Code use for a remote ``url`` in 2026. A long-lived SSE session
and OAuth discovery are not required for this cut. Legacy SSE-only clients
use the stdio ``uvx`` fallback.

The token is never logged and is not accepted in the query string.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlmodel import Session
from starlette.background import BackgroundTasks

from ..version_info import APP_VERSION
from . import api_tokens as tok_svc

logger = logging.getLogger(__name__)


class ToolArgError(ValueError):
    """Caller sent a tool argument the schema does not accept."""

PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
PREFERRED_PROTOCOL = "2025-03-26"
MAX_BODY_BYTES = 1024 * 1024
MAX_FILE_BYTES = 256 * 1024
MCP_JOB_TYPES = (
    "backup",
    "retention",
    "os_patch",
    "container_patch",
    "os_update_check",
    "container_update_check",
)
_QUERY_SECRET_KEYS = frozenset(
    {"token", "access_token", "api_key", "api_token", "secret", "authorization"}
)
_SERVER_INSTRUCTIONS = (
    "Call summary before changing anything. "
    "trigger_job only starts backup, retention, os_patch, container_patch, "
    "os_update_check, or container_update_check. "
    "On HTTP 409 poll get_job and do not start another. "
    "Files stay in the fleet jail. "
    "Do not invent SSH, Move, a console, compose stack actions, or token admin. "
    "Tools appear only for scopes on this token. A token without read has no tools."
)

_READ_ANN = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}
_WRITE_ANN = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}
_EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def _obj_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _int_prop(description: str) -> dict[str, str]:
    return {"type": "integer", "description": description}


def _str_prop(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


def tool_catalog() -> list[dict[str, Any]]:
    """Public adapter tools. Order matches piherder-mcp registration."""
    sid = _int_prop("Server id")
    return [
        {
            "name": "health",
            "description": "Token health, scopes, and allowed features.",
            "scope": tok_svc.SCOPE_READ,
            "inputSchema": _EMPTY_SCHEMA,
            "annotations": _READ_ANN,
        },
        {
            "name": "summary",
            "description": "Fleet heartbeat from the database. Does not SSH.",
            "scope": tok_svc.SCOPE_READ,
            "inputSchema": _EMPTY_SCHEMA,
            "annotations": _READ_ANN,
        },
        {
            "name": "list_servers",
            "description": "List servers. limit default 100, max 100.",
            "scope": tok_svc.SCOPE_READ,
            "inputSchema": _obj_schema(
                {
                    "q": _str_prop("Optional name/host filter"),
                    "limit": _int_prop("Page size (default 100, max 100)"),
                    "offset": _int_prop("Page offset"),
                },
                [],
            ),
            "annotations": _READ_ANN,
        },
        {
            "name": "get_server",
            "description": "One server, including feature flags.",
            "scope": tok_svc.SCOPE_READ,
            "inputSchema": _obj_schema({"server_id": sid}, ["server_id"]),
            "annotations": _READ_ANN,
        },
        {
            "name": "inventory",
            "description": "Stored Docker inventory. Omit server_id for the fleet. Does not SSH.",
            "scope": tok_svc.SCOPE_READ,
            "inputSchema": _obj_schema(
                {"server_id": _int_prop("Host id. Omit for the whole fleet.")},
                [],
            ),
            "annotations": _READ_ANN,
        },
        {
            "name": "services",
            "description": "Stored service up/down chips. Does not poll Kuma or NPM.",
            "scope": tok_svc.SCOPE_READ,
            "inputSchema": _EMPTY_SCHEMA,
            "annotations": _READ_ANN,
        },
        {
            "name": "list_jobs",
            "description": "List jobs. Pass server_id to limit the list to one host.",
            "scope": tok_svc.SCOPE_READ,
            "inputSchema": _obj_schema(
                {
                    "server_id": _int_prop("Optional host id"),
                    "status_filter": _str_prop("Optional job status"),
                    "job_type": _str_prop("Optional job type"),
                    "active_only": {"type": "boolean", "description": "Only active jobs"},
                    "limit": _int_prop("Page size (default 50)"),
                    "offset": _int_prop("Page offset"),
                },
                [],
            ),
            "annotations": _READ_ANN,
        },
        {
            "name": "get_job",
            "description": "One job. detail=true includes a longer log tail.",
            "scope": tok_svc.SCOPE_READ,
            "inputSchema": _obj_schema(
                {
                    "job_id": _int_prop("Job id"),
                    "detail": {"type": "boolean", "description": "Include a longer log tail"},
                },
                ["job_id"],
            ),
            "annotations": _READ_ANN,
        },
        {
            "name": "set_features",
            "description": "Toggle backup, os_patch, or docker. Omit a field to leave it unchanged.",
            "scope": tok_svc.SCOPE_EDIT,
            "inputSchema": _obj_schema(
                {
                    "server_id": sid,
                    "backup": {"type": "boolean", "description": "Backups feature"},
                    "os_patch": {"type": "boolean", "description": "OS patch feature"},
                    "docker": {"type": "boolean", "description": "Docker / containers feature"},
                },
                ["server_id"],
            ),
            "annotations": _WRITE_ANN,
        },
        {
            "name": "trigger_job",
            "description": (
                "Start one of backup, retention, os_patch, container_patch, "
                "os_update_check, container_update_check. "
                "HTTP 202 means accepted. HTTP 409 means that job is already active: "
                "poll get_job and do not start another."
            ),
            "scope": tok_svc.SCOPE_JOBS,
            "inputSchema": _obj_schema(
                {
                    "server_id": sid,
                    "job_type": {
                        "type": "string",
                        "enum": list(MCP_JOB_TYPES),
                        "description": "One of the six MCP job types",
                    },
                    "source_filter": _str_prop("Optional backup source name"),
                    "os_steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional OS patch steps",
                    },
                },
                ["server_id", "job_type"],
            ),
            "annotations": _WRITE_ANN,
        },
        {
            "name": "list_files",
            "description": "List a fleet-jail directory. p is jail-relative.",
            "scope": tok_svc.SCOPE_FILES,
            "inputSchema": _obj_schema(
                {"server_id": sid, "p": _str_prop("Jail-relative directory")},
                ["server_id"],
            ),
            "annotations": _READ_ANN,
        },
        {
            "name": "read_file",
            "description": (
                "Download one fleet-jail file. Result is capped around 256 KiB "
                "and says when it was cut."
            ),
            "scope": tok_svc.SCOPE_FILES,
            "inputSchema": _obj_schema(
                {"server_id": sid, "p": _str_prop("Jail-relative file path")},
                ["server_id", "p"],
            ),
            "annotations": _READ_ANN,
        },
        {
            "name": "write_file",
            "description": (
                "Upload text into the fleet jail. p is the directory. "
                "name is the file basename. Cap 256 KiB."
            ),
            "scope": tok_svc.SCOPE_FILES,
            "inputSchema": _obj_schema(
                {
                    "server_id": sid,
                    "p": _str_prop("Jail-relative directory"),
                    "name": _str_prop("File basename"),
                    "text": _str_prop("UTF-8 file body"),
                },
                ["server_id", "name", "text"],
            ),
            "annotations": _WRITE_ANN,
        },
        {
            "name": "mkdir",
            "description": "Create a directory in the fleet jail. p is the parent. name is the new directory.",
            "scope": tok_svc.SCOPE_FILES,
            "inputSchema": _obj_schema(
                {
                    "server_id": sid,
                    "p": _str_prop("Jail-relative parent"),
                    "name": _str_prop("New directory name"),
                },
                ["server_id", "name"],
            ),
            "annotations": _WRITE_ANN,
        },
        {
            "name": "rename_file",
            "description": "Rename inside the current fleet-jail directory.",
            "scope": tok_svc.SCOPE_FILES,
            "inputSchema": _obj_schema(
                {
                    "server_id": sid,
                    "p": _str_prop("Jail-relative directory"),
                    "src": _str_prop("Current basename"),
                    "dest": _str_prop("New basename"),
                },
                ["server_id", "src", "dest"],
            ),
            "annotations": _WRITE_ANN,
        },
        {
            "name": "delete_file",
            "description": "Delete one file or an empty directory in the fleet jail. Not recursive.",
            "scope": tok_svc.SCOPE_FILES,
            "inputSchema": _obj_schema(
                {"server_id": sid, "p": _str_prop("Jail-relative path")},
                ["server_id", "p"],
            ),
            "annotations": _WRITE_ANN,
        },
    ]


def tools_for_scopes(scopes: set[str]) -> list[dict[str, Any]]:
    """Tools the token may see. Missing ``read`` fails closed (empty list)."""
    if tok_svc.SCOPE_READ not in scopes:
        return []
    return [dict(tool) for tool in tool_catalog() if tool["scope"] in scopes]


def public_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """MCP tools/list entry (no internal scope key)."""
    return {
        "name": tool["name"],
        "description": tool["description"],
        "inputSchema": tool["inputSchema"],
        "annotations": tool["annotations"],
    }


def _host_of(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].split("?", 1)[0]
    return text.lower()


def origin_allowed(request: Request) -> bool:
    """Block browser DNS-rebinding origins. IDE clients usually omit Origin.

    ``Origin: null`` is rejected. An http(s) Origin must match the request Host
    or ``PIHERDER_PUBLIC_URL``. A Bearer request from another http(s) origin is
    still allowed: browsers cannot attach ``Authorization`` cross-origin unless
    ``CORS_ORIGINS`` already permits that origin (same rule as ``/api/v1``).
    Non-http schemes (editor app origins) are allowed.
    """
    origin = (request.headers.get("origin") or "").strip()
    if not origin:
        return True
    if origin.lower() == "null":
        return False
    scheme = origin.split("://", 1)[0].lower() if "://" in origin else ""
    if scheme not in ("http", "https"):
        return True
    origin_host = _host_of(origin)
    request_host = (request.headers.get("host") or "").split(",")[0].strip().lower()
    if origin_host and request_host and origin_host == request_host:
        return True
    try:
        from .password_reset import configured_public_origin

        public_host = _host_of(configured_public_origin())
    except Exception:
        public_host = ""
    if origin_host and public_host and origin_host == public_host:
        return True
    auth = (request.headers.get("authorization") or "").strip().lower()
    if auth.startswith("bearer "):
        return True
    return False


def query_carries_secret(request: Request) -> bool:
    for key in request.query_params.keys():
        if key.lower() in _QUERY_SECRET_KEYS:
            return True
    return False


def redact_query_string(request: Request) -> None:
    """Drop the query from the ASGI scope so access logs do not keep a secret."""
    try:
        request.scope["query_string"] = b""
    except Exception:
        pass


def negotiated_protocol(raw: str | None) -> str:
    version = (raw or "").strip()
    if version in PROTOCOL_VERSIONS:
        return version
    return PREFERRED_PROTOCOL


def header_protocol_ok(raw: str | None) -> bool:
    version = (raw or "").strip()
    if not version:
        return True
    return version in PROTOCOL_VERSIONS


def _rpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def tool_text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, default=str)
    out: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }
    if not is_error and isinstance(payload, dict):
        out["structuredContent"] = payload
    return out


def _http_error_payload(exc: HTTPException) -> dict[str, Any]:
    detail = exc.detail
    if not isinstance(detail, str):
        detail = json.dumps(detail, default=str)
    return {"ok": False, "status": int(exc.status_code), "detail": detail}


def _require_int(args: dict[str, Any], key: str) -> int:
    if key not in args or args[key] is None:
        raise ToolArgError(f"Missing {key}")
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolArgError(f"{key} must be an integer")
    return value


def _opt_int(args: dict[str, Any], key: str) -> int | None:
    if key not in args or args[key] is None or args[key] == "":
        return None
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolArgError(f"{key} must be an integer")
    return value


def _opt_str(args: dict[str, Any], key: str, default: str = "") -> str:
    if key not in args or args[key] is None:
        return default
    value = args[key]
    if not isinstance(value, str):
        raise ToolArgError(f"{key} must be a string")
    return value


def _opt_bool(args: dict[str, Any], key: str, default: bool = False) -> bool:
    if key not in args or args[key] is None:
        return default
    value = args[key]
    if not isinstance(value, bool):
        raise ToolArgError(f"{key} must be a boolean")
    return value


def _opt_bool_or_none(args: dict[str, Any], key: str) -> bool | None:
    if key not in args or args[key] is None:
        return None
    value = args[key]
    if not isinstance(value, bool):
        raise ToolArgError(f"{key} must be a boolean")
    return value


def present_file(data: bytes, *, truncated: bool) -> dict[str, Any]:
    """Capped file body, same shape as the stdio adapter."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "encoding": "base64",
            "truncated": truncated,
            "bytes": len(data),
            "content_base64": base64.b64encode(data).decode("ascii"),
        }
    return {
        "encoding": "utf-8",
        "truncated": truncated,
        "bytes": len(data),
        "text": text,
    }


async def _collect_capped(resp: StreamingResponse, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    truncated = False
    iterator = resp.body_iterator

    def _take(chunk: Any) -> bool:
        nonlocal total, truncated
        if isinstance(chunk, str):
            raw = chunk.encode("utf-8")
        else:
            raw = bytes(chunk)
        if total >= limit:
            truncated = True
            return False
        room = limit - total
        if len(raw) > room:
            chunks.append(raw[:room])
            total += room
            truncated = True
            return False
        chunks.append(raw)
        total += len(raw)
        return True

    if hasattr(iterator, "__aiter__"):
        async for chunk in iterator:
            if not _take(chunk):
                break
    else:
        for chunk in iterator:
            if not _take(chunk):
                break
    return b"".join(chunks), truncated


def _unwrap_json(value: Any) -> tuple[int, Any]:
    if isinstance(value, JSONResponse):
        raw = bytes(value.body or b"")
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {"ok": True, "status": value.status_code}
        return int(value.status_code), data
    return 200, value


async def _drain_background(tasks: BackgroundTasks) -> None:
    for task in list(tasks.tasks):
        try:
            await task()
        except Exception:
            logger.warning("hosted MCP background task failed")


async def call_tool(
    name: str,
    args: dict[str, Any],
    session: Session,
    auth: Any,
) -> dict[str, Any]:
    """Run one tool against the in-process API. Unknown names are tool errors."""
    from ..routers import api_v1

    visible = {tool["name"] for tool in tools_for_scopes(auth.scopes)}
    if name not in visible:
        if tok_svc.SCOPE_READ not in auth.scopes:
            return tool_text_result(
                _http_error_payload(
                    HTTPException(403, detail=tok_svc.missing_scope_message(tok_svc.SCOPE_READ))
                ),
                is_error=True,
            )
        return tool_text_result(
            {"ok": False, "detail": f"Unknown tool: {name}"},
            is_error=True,
        )
    try:
        if name == "health":
            payload = api_v1.api_health(auth)
        elif name == "summary":
            payload = api_v1.api_summary(session, auth)
        elif name == "list_servers":
            payload = api_v1.list_servers(
                session,
                auth,
                q=_opt_str(args, "q"),
                limit=_opt_int(args, "limit") if "limit" in args and args.get("limit") is not None else 100,
                offset=_opt_int(args, "offset") or 0,
            )
        elif name == "get_server":
            payload = api_v1.get_server(_require_int(args, "server_id"), session, auth)
        elif name == "inventory":
            server_id = _opt_int(args, "server_id")
            if server_id is None:
                payload = api_v1.api_inventory(session, auth)
            else:
                payload = api_v1.api_server_inventory(server_id, session, auth)
        elif name == "services":
            payload = api_v1.api_services(session, auth)
        elif name == "list_jobs":
            payload = api_v1.list_jobs(
                server_id=_opt_int(args, "server_id"),
                status_filter=_opt_str(args, "status_filter") or None,
                job_type=_opt_str(args, "job_type") or None,
                active_only=_opt_bool(args, "active_only"),
                limit=_opt_int(args, "limit") if args.get("limit") is not None else 50,
                offset=_opt_int(args, "offset") or 0,
                session=session,
                auth=auth,
            )
        elif name == "get_job":
            payload = api_v1.get_job(
                _require_int(args, "job_id"),
                detail=_opt_bool(args, "detail"),
                session=session,
                auth=auth,
            )
        elif name == "set_features":
            fields = {
                key: _opt_bool_or_none(args, key)
                for key in ("backup", "os_patch", "docker")
            }
            if all(value is None for value in fields.values()):
                return tool_text_result(
                    {
                        "ok": False,
                        "detail": "No feature fields to change. Pass backup, os_patch, or docker.",
                    },
                    is_error=True,
                )
            body = api_v1.ServerFeaturesBody(**fields)
            payload = api_v1.patch_server_features(
                _require_int(args, "server_id"), body, session, auth
            )
        elif name == "trigger_job":
            job_type = _opt_str(args, "job_type").strip().lower()
            if job_type not in MCP_JOB_TYPES:
                allowed = ", ".join(MCP_JOB_TYPES)
                return tool_text_result(
                    {"ok": False, "detail": f"job_type must be one of {allowed}"},
                    is_error=True,
                )
            body_fields: dict[str, Any] = {"job_type": job_type}
            source = _opt_str(args, "source_filter")
            if source:
                body_fields["source_filter"] = source
            if "os_steps" in args and args["os_steps"] is not None:
                steps = args["os_steps"]
                if not isinstance(steps, list) or not all(isinstance(item, str) for item in steps):
                    raise ToolArgError("os_steps must be an array of strings")
                body_fields["os_steps"] = steps
            background = BackgroundTasks()
            raw = await api_v1.create_server_job(
                _require_int(args, "server_id"),
                api_v1.JobCreateBody(**body_fields),
                background,
                session,
                auth,
            )
            status_code, payload = _unwrap_json(raw)
            if isinstance(payload, dict) and status_code in (202, 409):
                payload = dict(payload)
                payload["http_status"] = status_code
            await _drain_background(background)
        elif name == "list_files":
            payload = api_v1.api_files_list(
                _require_int(args, "server_id"),
                _opt_str(args, "p"),
                session,
                auth,
            )
        elif name == "read_file":
            resp = api_v1.api_files_download(
                _require_int(args, "server_id"),
                _opt_str(args, "p"),
                session,
                auth,
            )
            if not isinstance(resp, StreamingResponse):
                payload = resp
            else:
                data, truncated = await _collect_capped(resp, MAX_FILE_BYTES)
                payload = present_file(data, truncated=truncated)
        elif name == "write_file":
            from io import BytesIO

            from starlette.datastructures import UploadFile

            if "text" not in args:
                raise ToolArgError("Missing text")
            if "name" not in args or not _opt_str(args, "name"):
                raise ToolArgError("Missing name")
            text = _opt_str(args, "text")
            raw_bytes = text.encode("utf-8")
            if len(raw_bytes) > MAX_FILE_BYTES:
                return tool_text_result(
                    {
                        "ok": False,
                        "detail": f"file is {len(raw_bytes)} bytes; cap is {MAX_FILE_BYTES}",
                    },
                    is_error=True,
                )
            upload = UploadFile(file=BytesIO(raw_bytes), filename=_opt_str(args, "name"))
            payload = await api_v1.api_files_upload(
                _require_int(args, "server_id"),
                upload,
                _opt_str(args, "p"),
                session,
                auth,
            )
        elif name == "mkdir":
            payload = api_v1.api_files_mkdir(
                _require_int(args, "server_id"),
                api_v1.FilesMkdirBody(
                    p=_opt_str(args, "p"),
                    name=_opt_str(args, "name"),
                ),
                session,
                auth,
            )
        elif name == "rename_file":
            payload = api_v1.api_files_rename(
                _require_int(args, "server_id"),
                api_v1.FilesRenameBody(
                    p=_opt_str(args, "p"),
                    src=_opt_str(args, "src"),
                    dest=_opt_str(args, "dest"),
                ),
                session,
                auth,
            )
        elif name == "delete_file":
            payload = api_v1.api_files_delete(
                _require_int(args, "server_id"),
                _opt_str(args, "p"),
                session,
                auth,
            )
        else:
            return tool_text_result(
                {"ok": False, "detail": f"Unknown tool: {name}"},
                is_error=True,
            )
    except ToolArgError as exc:
        return tool_text_result({"ok": False, "detail": str(exc)}, is_error=True)
    except HTTPException as exc:
        return tool_text_result(_http_error_payload(exc), is_error=True)
    except Exception:
        logger.warning("hosted MCP tool %s failed", name)
        return tool_text_result(
            {"ok": False, "detail": "Tool call failed"},
            is_error=True,
        )
    return tool_text_result(payload, is_error=False)


async def dispatch_rpc(
    message: dict[str, Any],
    session: Session,
    auth: Any,
) -> dict[str, Any] | None:
    """Return a JSON-RPC response, or None when the message is a notification."""
    if "id" not in message:
        return None
    req_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str) or not method:
        return _rpc_error(req_id, -32600, "Invalid Request")
    params = message.get("params") or {}
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _rpc_error(req_id, -32602, "Invalid params")

    if method == "initialize":
        if tok_svc.SCOPE_READ not in auth.scopes:
            return _rpc_error(
                req_id,
                -32001,
                tok_svc.missing_scope_message(tok_svc.SCOPE_READ),
            )
        version = negotiated_protocol(
            params.get("protocolVersion") if isinstance(params.get("protocolVersion"), str) else None
        )
        return _rpc_result(
            req_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "piherder", "version": APP_VERSION},
                "instructions": _SERVER_INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _rpc_result(req_id, {})
    if method == "tools/list":
        if tok_svc.SCOPE_READ not in auth.scopes:
            return _rpc_error(
                req_id,
                -32001,
                tok_svc.missing_scope_message(tok_svc.SCOPE_READ),
            )
        tools = [public_tool(tool) for tool in tools_for_scopes(auth.scopes)]
        return _rpc_result(req_id, {"tools": tools})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _rpc_error(req_id, -32602, "tools/call requires a tool name")
        arguments = params.get("arguments") or {}
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _rpc_error(req_id, -32602, "tools/call arguments must be an object")
        result = await call_tool(name, arguments, session, auth)
        return _rpc_result(req_id, result)
    if method in ("resources/list", "resources/templates/list"):
        key = "resourceTemplates" if method.endswith("templates/list") else "resources"
        return _rpc_result(req_id, {key: []})
    if method == "prompts/list":
        return _rpc_result(req_id, {"prompts": []})
    if method.startswith("notifications/"):
        return _rpc_result(req_id, {})
    return _rpc_error(req_id, -32601, f"Method not found: {method}")


def _wants_sse(accept: str) -> bool:
    parts = [p.split(";", 1)[0].strip().lower() for p in (accept or "").split(",")]
    parts = [p for p in parts if p]
    if not parts or "*/*" in parts:
        return False
    if "application/json" in parts:
        return False
    return "text/event-stream" in parts


def _accept_ok(accept: str) -> bool:
    if not (accept or "").strip():
        return True
    parts = [p.split(";", 1)[0].strip().lower() for p in accept.split(",")]
    if "*/*" in parts or "application/json" in parts or "text/event-stream" in parts:
        return True
    return False


def _encode_rpc(payload: dict[str, Any], *, sse: bool) -> Response:
    raw = json.dumps(payload, default=str)
    headers = {"Cache-Control": "no-store"}
    if sse:
        body = f"event: message\ndata: {raw}\n\n"
        headers["Cache-Control"] = "no-cache"
        return Response(content=body, media_type="text/event-stream", headers=headers)
    return JSONResponse(content=json.loads(raw), headers=headers)


async def handle_mcp_http(request: Request, session: Session) -> Response:
    """Streamable HTTP entrypoint. Auth matches ``/api/v1`` Bearer tokens."""
    if query_carries_secret(request):
        redact_query_string(request)
        return JSONResponse(
            status_code=400,
            content={
                "detail": "Pass the API token in the Authorization header, not the query string.",
            },
        )
    if not origin_allowed(request):
        return JSONResponse(
            status_code=403,
            content={"detail": "Origin is not allowed for this MCP endpoint"},
        )

    method = (request.method or "GET").upper()
    allow = "POST, OPTIONS"
    if method == "OPTIONS":
        return Response(status_code=204, headers={"Allow": allow, "Cache-Control": "no-store"})
    if method in ("GET", "DELETE", "HEAD"):
        # Stateless server: no SSE listen channel and no session to delete.
        return JSONResponse(
            status_code=405,
            content={"detail": "Hosted MCP accepts POST only (stateless Streamable HTTP)."},
            headers={"Allow": allow},
        )
    if method != "POST":
        return JSONResponse(
            status_code=405,
            content={"detail": "Method not allowed"},
            headers={"Allow": allow},
        )

    accept = request.headers.get("accept") or ""
    if not _accept_ok(accept):
        return JSONResponse(
            status_code=406,
            content={"detail": "Accept must include application/json or text/event-stream"},
        )
    proto_header = request.headers.get("mcp-protocol-version")
    if not header_protocol_ok(proto_header):
        return JSONResponse(
            status_code=400,
            content={"detail": "Unsupported MCP-Protocol-Version"},
        )

    ctype = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if ctype and ctype != "application/json":
        return JSONResponse(
            status_code=415,
            content={"detail": "Content-Type must be application/json"},
        )

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "MCP request body is too large"})
    try:
        message = json.loads(raw.decode("utf-8")) if raw else None
    except Exception:
        return JSONResponse(
            status_code=400,
            content=_rpc_error(None, -32700, "Parse error"),
        )
    if isinstance(message, list):
        return JSONResponse(
            status_code=400,
            content=_rpc_error(None, -32600, "JSON-RPC batches are not supported"),
        )
    if not isinstance(message, dict):
        return JSONResponse(
            status_code=400,
            content=_rpc_error(None, -32600, "Invalid Request"),
        )

    from ..routers.api_v1 import get_api_auth

    try:
        auth = get_api_auth(request, session, request.headers.get("authorization"))
    except HTTPException as exc:
        headers = {}
        if exc.headers:
            headers.update(dict(exc.headers))
        detail = exc.detail if isinstance(exc.detail, str) else "Unauthorized"
        return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=headers)

    rpc_method = message.get("method")
    if isinstance(rpc_method, str) and rpc_method.startswith("notifications/") and "id" not in message:
        return Response(status_code=202, headers={"Cache-Control": "no-store"})
    if "id" not in message:
        return Response(status_code=202, headers={"Cache-Control": "no-store"})

    try:
        payload = await dispatch_rpc(message, session, auth)
    except Exception:
        logger.warning("hosted MCP request failed")
        payload = _rpc_error(message.get("id"), -32603, "Internal error")
    if payload is None:
        return Response(status_code=202, headers={"Cache-Control": "no-store"})
    return _encode_rpc(payload, sse=_wants_sse(accept))
