import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tests.conftest import api

PROTOCOL = "2026-07-28"


def _meta():
    return {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL,
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def mcp(base, method, params=None, token="test-token-not-for-production",
        extra_headers=None, req_id=1, name=None):
    params = dict(params or {})
    params["_meta"] = _meta()
    body = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    headers = {
        "X-Agent-Token": token,
        "Content-Type": "application/json",
        "MCP-Protocol-Version": PROTOCOL,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    elif method == "tools/call":
        headers["Mcp-Name"] = params.get("name", "")
    if extra_headers:
        headers.update(extra_headers)
    req = Request(
        base + "/mcp",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw else {}


def test_mcp_get_is_405(http_server):
    st, body = api(http_server, "GET", "/mcp")
    assert st == 405


def test_discover_and_list_tools(http_server):
    st, body = mcp(http_server, "server/discover")
    assert st == 200
    result = body["result"]
    assert result["resultType"] == "complete"
    assert PROTOCOL in result["supportedVersions"]
    assert result["capabilities"]["tools"] == {}
    assert "io.modelcontextprotocol/serverInfo" in result["_meta"]

    st, body = mcp(http_server, "tools/list")
    assert st == 200
    tools = body["result"]["tools"]
    names = [t["name"] for t in tools]
    assert "battery.status" in names
    assert "confirm.poll" in names
    assert names[-1] == "confirm.poll"
    assert names[:-1] == sorted(names[:-1])
    sms = next(t for t in tools if t["name"] == "sms.send")
    assert sms["inputSchema"]["properties"]["number"]["type"] == "string"
    assert "idempotency_key" in sms["inputSchema"]["properties"]
    assert sms["annotations"]["destructiveHint"] is True


def test_tools_call_perceive(http_server):
    from types import SimpleNamespace
    from unittest.mock import patch
    fake = SimpleNamespace(returncode=0, stdout='{"percentage": 9}', stderr="")
    with patch("dispatch.tier_a.subprocess.run", return_value=fake):
        st, body = mcp(http_server, "tools/call", {
            "name": "battery.status",
            "arguments": {},
        }, name="battery.status")
    assert st == 200
    assert body["result"]["isError"] is False
    assert body["result"]["structuredContent"]["data"]["percentage"] == 9


def test_tools_call_dry_run(http_server):
    st, body = mcp(http_server, "tools/call", {
        "name": "toast.show",
        "arguments": {"text": "hi", "dry_run": True},
    }, name="toast.show")
    assert st == 200
    sc = body["result"]["structuredContent"]
    assert sc["dry_run"] is True
    assert sc["argv"][-1] == "hi"


def test_header_mismatch(http_server):
    st, body = mcp(
        http_server, "tools/call",
        {"name": "battery.status", "arguments": {}},
        name="wrong.tool",
    )
    assert st == 400
    assert body["error"]["code"] == -32020


def test_mcp_method_not_found(http_server):
    st, body = mcp(http_server, "prompts/list")
    assert st == 404
    assert body["error"]["code"] == -32601


def test_mcp_missing_meta(http_server, token):
    req = Request(
        http_server + "/mcp",
        data=b'{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{}}',
        headers={
            "X-Agent-Token": token,
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL,
            "Mcp-Method": "server/discover",
        },
        method="POST",
    )
    try:
        urlopen(req, timeout=5)
        assert False
    except HTTPError as e:
        assert e.code == 400
        payload = json.loads(e.read())
        assert payload["error"]["code"] == -32602


def test_mcp_origin_forbidden(http_server):
    st, body = mcp(
        http_server, "server/discover",
        extra_headers={"Origin": "https://evil.example"},
    )
    assert st == 403


def test_mcp_confirm_poll_unknown(http_server):
    st, body = mcp(http_server, "tools/call", {
        "name": "confirm.poll",
        "arguments": {"confirm_id": "missing"},
    }, name="confirm.poll")
    assert st == 200
    assert body["result"]["isError"] is True
    assert body["result"]["structuredContent"]["code"] == "CONFIRM_NOT_FOUND"


def test_mcp_unknown_verb_is_tool_error(http_server):
    st, body = mcp(http_server, "tools/call", {
        "name": "nope.verb",
        "arguments": {},
    }, name="nope.verb")
    assert st == 200
    assert body["result"]["isError"] is True


def test_mcp_parse_error(http_server, token):
    req = Request(
        http_server + "/mcp",
        data=b"not-json",
        headers={
            "X-Agent-Token": token,
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL,
            "Mcp-Method": "server/discover",
        },
        method="POST",
    )
    try:
        urlopen(req, timeout=5)
        assert False
    except HTTPError as e:
        assert e.code == 400
        assert json.loads(e.read())["error"]["code"] == -32700


def test_mcp_notification_202(http_server, token):
    req = Request(
        http_server + "/mcp",
        data=json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"_meta": _meta()},
        }).encode(),
        headers={
            "X-Agent-Token": token,
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL,
            "Mcp-Method": "notifications/cancelled",
        },
        method="POST",
    )
    with urlopen(req, timeout=5) as resp:
        assert resp.status == 202


def test_unsupported_version(http_server):
    st, body = mcp(
        http_server, "server/discover",
        extra_headers={"MCP-Protocol-Version": "2025-11-25"},
    )
    assert st == 400
    assert body["error"]["code"] == -32022
