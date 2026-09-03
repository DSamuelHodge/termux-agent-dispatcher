"""MCP 2026-07-28 Streamable HTTP wrapper around the verb catalog.

Stateless by spec: no initialize handshake, no Mcp-Session-Id, no GET stream.
Each POST is self-describing via MCP-Protocol-Version + params._meta.
Cross-call state (confirm jobs, watches, idempotency) is an explicit handle
the client passes back — never inferred from a session.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import urlparse

from dispatch.catalog import Catalog, Verb, split_envelope
from dispatch.confirm import ConfirmManager
from dispatch.engine import dispatch, poll_confirm
from dispatch.errors import ORIGIN_FORBIDDEN, error_payload
from dispatch.tier_b import SubscriptionManager

PROTOCOL = "2026-07-28"
SERVER_INFO = {"name": "termux-agent-dispatcher", "version": "1.0.0"}
TTL_MS = 60_000

JSONRPC_PARSE = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL = -32603
MCP_HEADER_MISMATCH = -32020
MCP_UNSUPPORTED_VERSION = -32022

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _server_meta() -> dict[str, Any]:
    return {META_SERVER_INFO: SERVER_INFO}


def jsonrpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def jsonrpc_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    out = dict(result)
    out.setdefault("resultType", "complete")
    meta = dict(out.get("_meta") or {})
    meta.setdefault(META_SERVER_INFO, SERVER_INFO)
    out["_meta"] = meta
    return {"jsonrpc": "2.0", "id": req_id, "result": out}


def origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    parsed = urlparse(origin)
    host = (parsed.hostname or "").lower()
    return host in _LOCAL_HOSTS


def decode_header_value(raw: str | None) -> str | None:
    if raw is None:
        return None
    if raw.startswith("=?base64?") and raw.endswith("?="):
        inner = raw[len("=?base64?"):-2]
        try:
            return base64.b64decode(inner).decode()
        except Exception:
            return raw
    return raw


def _header(headers, *names: str) -> str | None:
    for name in names:
        value = headers.get(name)
        if value is not None and value != "":
            return value
    # Case-insensitive fallback
    lower = {k.lower(): v for k, v in headers.items()}
    for name in names:
        value = lower.get(name.lower())
        if value is not None and value != "":
            return value
    return None


def tool_title(name: str) -> str:
    return " ".join(p[:1].upper() + p[1:] for p in name.split("."))


def tool_annotations(verb: Verb) -> dict[str, Any]:
    return {
        "readOnlyHint": verb.direction == "perceive",
        "destructiveHint": verb.risk == "high",
        "openWorldHint": verb.name.startswith(("url.", "share.", "download.")),
        "idempotentHint": False,
    }


def envelope_properties() -> dict[str, Any]:
    return {
        "dry_run": {
            "type": "boolean",
            "description": "Validate and return argv without executing or confirming.",
        },
        "idempotency_key": {
            "type": "string",
            "minLength": 1,
            "maxLength": 256,
            "description": "Replay key for act verbs. Required when risk is high.",
        },
        "webhook_url": {
            "type": "string",
            "description": "Optional http(s) URL posted when an async confirm job finishes.",
        },
    }


def verb_input_schema(verb: Verb) -> dict[str, Any]:
    schema = json.loads(json.dumps(verb.args_schema or {"type": "object", "properties": {}}))
    props = dict(schema.get("properties") or {})
    props.update(envelope_properties())
    schema["properties"] = props
    schema["type"] = "object"
    schema["additionalProperties"] = False
    return schema


def list_tools(catalog: Catalog) -> list[dict[str, Any]]:
    tools = []
    for name in sorted(catalog.verbs):
        verb = catalog.verbs[name]
        spec = verb.public_spec(catalog.confirmation_required_for)
        description = verb.description or f"{verb.direction} verb {name} (risk {verb.risk})."
        if spec["confirmation_required"]:
            description += (
                " High-risk: returns a pending confirm_id; poll confirm.poll. "
                "On-device Yes/No; does not block the MCP request."
            )
        tools.append({
            "name": name,
            "title": tool_title(name),
            "description": description,
            "inputSchema": verb_input_schema(verb),
            "annotations": tool_annotations(verb),
            "_meta": {
                "dev.termux-agent/verb": spec,
            },
        })
    tools.append({
        "name": "confirm.poll",
        "title": "Poll confirmation",
        "description": (
            "Poll an async on-device confirm job by confirm_id. "
            "MCP is stateless; pass the handle from the original tools/call."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "confirm_id": {
                    "type": "string",
                    "description": "Handle returned by a pending high-risk tools/call.",
                },
            },
            "required": ["confirm_id"],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
            "idempotentHint": True,
        },
    })
    return tools


def _tool_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=str)


def _call_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": _tool_text(payload)}],
        "structuredContent": payload,
        "isError": is_error,
    }


class McpHandler:
    def __init__(
        self,
        catalog: Catalog,
        subs: SubscriptionManager,
        confirms: ConfirmManager,
    ):
        self.catalog = catalog
        self.subs = subs
        self.confirms = confirms

    def handle(
        self,
        method: str,
        headers,
        raw_body: bytes,
    ) -> tuple[int, dict[str, Any] | None]:
        """Return (http_status, json_body). Body is None for 202 notification."""
        if method == "GET" or method == "DELETE":
            return 405, error_payload(
                "INVALID_ROUTE",
                "MCP 2026-07-28 has no GET/DELETE session stream",
            )

        origin = _header(headers, "Origin")
        if not origin_allowed(origin):
            return 403, jsonrpc_error(
                None, JSONRPC_INVALID_REQUEST, "invalid Origin",
                {"code": ORIGIN_FORBIDDEN},
            )

        proto = _header(headers, "MCP-Protocol-Version")
        mcp_method = _header(headers, "Mcp-Method")
        mcp_name = decode_header_value(_header(headers, "Mcp-Name"))

        try:
            body = json.loads(raw_body.decode() or "null")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 400, jsonrpc_error(None, JSONRPC_PARSE, "Parse error")

        if not isinstance(body, dict):
            return 400, jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "Invalid Request")

        if body.get("jsonrpc") != "2.0":
            return 400, jsonrpc_error(
                body.get("id"), JSONRPC_INVALID_REQUEST, "jsonrpc must be 2.0",
            )

        req_id = body.get("id")
        rpc_method = body.get("method")
        params = body.get("params") if isinstance(body.get("params"), dict) else {}

        # Notification: no id
        if "id" not in body:
            return 202, None

        if not isinstance(rpc_method, str):
            return 400, jsonrpc_error(req_id, JSONRPC_INVALID_REQUEST, "method required")

        if proto != PROTOCOL:
            return 400, jsonrpc_error(
                req_id, MCP_UNSUPPORTED_VERSION,
                f"unsupported protocol version {proto!r}",
                {"supported": [PROTOCOL], "requested": proto},
            )

        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else None
        if not isinstance(meta, dict):
            return 400, jsonrpc_error(
                req_id, JSONRPC_INVALID_PARAMS,
                f"params._meta.{META_VERSION} and {META_CLIENT_CAPS} are required",
            )
        meta_ver = meta.get(META_VERSION)
        if meta_ver != proto:
            return 400, jsonrpc_error(
                req_id, MCP_HEADER_MISMATCH,
                "Header mismatch: MCP-Protocol-Version does not match params._meta",
            )
        if META_CLIENT_CAPS not in meta:
            return 400, jsonrpc_error(
                req_id, JSONRPC_INVALID_PARAMS,
                f"params._meta.{META_CLIENT_CAPS} is required",
            )

        if mcp_method != rpc_method:
            return 400, jsonrpc_error(
                req_id, MCP_HEADER_MISMATCH,
                f"Header mismatch: Mcp-Method {mcp_method!r} != method {rpc_method!r}",
            )

        if rpc_method in ("tools/call",) and mcp_name != params.get("name"):
            return 400, jsonrpc_error(
                req_id, MCP_HEADER_MISMATCH,
                f"Header mismatch: Mcp-Name {mcp_name!r} != params.name {params.get('name')!r}",
            )

        if rpc_method == "server/discover":
            return 200, jsonrpc_result(req_id, self._discover())
        if rpc_method == "tools/list":
            return 200, jsonrpc_result(req_id, self._tools_list())
        if rpc_method == "tools/call":
            return self._tools_call(req_id, params)

        return 404, jsonrpc_error(
            req_id, JSONRPC_METHOD_NOT_FOUND, f"Method not found: {rpc_method}",
        )

    def _discover(self) -> dict[str, Any]:
        return {
            "resultType": "complete",
            "supportedVersions": [PROTOCOL],
            "capabilities": {"tools": {}},
            "instructions": (
                "On-device Termux:API catalog. Perceive tools are read-only. "
                "High-risk act tools return a confirm_id (on-device Yes/No) — "
                "poll confirm.poll. Pass idempotency_key on high-risk calls. "
                "dry_run validates without executing. No MCP session; every "
                "request must carry protocol version and client capabilities."
            ),
            "ttlMs": TTL_MS,
            "cacheScope": "public",
        }

    def _tools_list(self) -> dict[str, Any]:
        return {
            "resultType": "complete",
            "tools": list_tools(self.catalog),
            "ttlMs": TTL_MS,
            "cacheScope": "public",
        }

    def _tools_call(self, req_id: Any, params: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        name = params.get("name")
        if not isinstance(name, str):
            return 400, jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, "params.name required")
        raw_args = params.get("arguments") or {}
        if not isinstance(raw_args, dict):
            return 400, jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, "arguments must be an object")

        if name == "confirm.poll":
            confirm_id = raw_args.get("confirm_id")
            if not isinstance(confirm_id, str) or not confirm_id:
                return 400, jsonrpc_error(
                    req_id, JSONRPC_INVALID_PARAMS, "confirm_id required",
                )
            _status, payload = poll_confirm(self.confirms, confirm_id)
            return 200, jsonrpc_result(
                req_id,
                _call_result(
                    payload,
                    is_error=_status >= 400 or payload.get("status") in {"denied", "failed"},
                ),
            )

        try:
            verb = self.catalog.get(name)
        except KeyError:
            return 200, jsonrpc_result(
                req_id,
                _call_result(
                    error_payload("UNKNOWN_VERB", f"unknown verb: {name}"),
                    is_error=True,
                ),
            )

        args, envelope = split_envelope(raw_args)
        kind = "watch" if verb.tier == "B" else verb.direction
        dry_run = bool(envelope.get("dry_run"))
        idem = envelope.get("idempotency_key")
        webhook = envelope.get("webhook_url")
        http_status, payload = dispatch(
            self.catalog, self.subs, self.confirms, name, args,
            kind=kind,
            dry_run=dry_run,
            idempotency_key=idem if isinstance(idem, str) else None,
            webhook_url=webhook if isinstance(webhook, str) else None,
        )
        is_error = http_status >= 400 and http_status != 202
        # 202 pending is a successful MCP tool result with a handle.
        return 200, jsonrpc_result(req_id, _call_result(payload, is_error=is_error))
