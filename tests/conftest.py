import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

os.environ.setdefault("AGENT_TOKEN", "test-token-not-for-production")

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def token():
    return os.environ["AGENT_TOKEN"]


@pytest.fixture
def http_server(monkeypatch, tmp_path):
    import daemon as d
    from dispatch import risk_gate

    monkeypatch.setattr(risk_gate, "LOG_PATH", tmp_path / "audit.log")
    server = ThreadingHTTPServer(("127.0.0.1", 0), d.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    yield base
    server.shutdown()
    thread.join(timeout=5)


def api(base, method, path, body=None, token="test-token-not-for-production", timeout=10):
    data = None if body is None else json.dumps(body).encode()
    headers = {"X-Agent-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(base + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw.decode(errors="replace")}
        return e.code, payload
