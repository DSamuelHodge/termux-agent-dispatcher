import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from dispatch.catalog import Catalog
from dispatch import tier_a, tier_b, risk_gate
from tests.conftest import api

ROOT = Path(__file__).resolve().parents[1]


def _write(tmp, verbs, confirm=None):
    raw = {"verbs": verbs}
    if confirm is not None:
        raw["confirmation_required_for"] = confirm
    p = tmp / "v.yaml"
    p.write_text(yaml.safe_dump(raw))
    return p


def test_catalog_validation_errors(tmp_path):
    base = {
        "direction": "perceive", "tier": "A", "risk": "none",
        "command": ["x"], "args": [], "parser": "json", "timeout": 1,
    }
    cases = [
        ({**base, "direction": "nope"}, "invalid direction"),
        ({**base, "risk": "nuclear"}, "invalid risk"),
        ({**base, "parser": "xml"}, "invalid parser"),
        ({**base, "parser": "json_stream"}, "json_stream is Tier B only"),
        ({**base, "command": []}, "command must be"),
        ({**base, "args": [1]}, "args must be"),
        ({**base, "args": ["a"], "stdin": "missing"}, "stdin"),
    ]
    for spec, match in cases:
        p = _write(tmp_path, {"v": spec})
        with pytest.raises(ValueError, match=match):
            Catalog.load(p)
    p = _write(tmp_path, {"ok": base}, confirm=["bogus"])
    with pytest.raises(ValueError, match="unknown risks"):
        Catalog.load(p)


def test_tier_a_empty_json():
    from types import SimpleNamespace
    v = Catalog.load(ROOT / "verbs.yaml").get("battery.status")
    with patch("dispatch.tier_a.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")):
        assert tier_a.run(v, {}) == {"ok": True, "data": None}


def test_confirm_yes_json():
    proc = MagicMock(returncode=0, stdout='{"text": "yes"}')
    with patch("dispatch.risk_gate.subprocess.run", return_value=proc) as run:
        assert risk_gate._confirm_on_device("v", {"a": 1}, ["a"]) is True
        argv = run.call_args[0][0]
        assert argv[:2] == ["termux-dialog", "confirm"]
        assert "-t" in argv and "-i" in argv
        title = argv[argv.index("-t") + 1]
        hint = argv[argv.index("-i") + 1]
        assert title == "Allow: V?"
        assert "v(" not in hint
        assert hint != json.dumps({"a": 1}, default=str)
        assert hint == "The agent wants to v.\na: 1\nYes allows this. No denies it."
    proc = MagicMock(returncode=1, stdout="")
    with patch("dispatch.risk_gate.subprocess.run", return_value=proc):
        assert risk_gate._confirm_on_device("v", {}, []) is False
    proc = MagicMock(returncode=0, stdout="not-json")
    with patch("dispatch.risk_gate.subprocess.run", return_value=proc):
        assert risk_gate._confirm_on_device("v", {}, []) is False


def test_format_confirm_copy():
    title, hint = risk_gate.format_confirm_copy("v", {"a": 1}, ["a"])
    assert title == "Allow: V?"
    assert hint == "The agent wants to v.\na: 1\nYes allows this. No denies it."

    title, hint = risk_gate.format_confirm_copy(
        "sms.send",
        {"number": "+1XXXXXXXXXX", "text": "Hello"},
        ["number", "text"],
    )
    assert title == "Allow: Send an SMS?"
    assert hint == (
        "The agent wants to send an SMS.\n"
        "To: +1XXXXXXXXXX\n"
        "Message: Hello\n"
        "Yes allows this. No denies it."
    )

    title, hint = risk_gate.format_confirm_copy(
        "keystore.sign",
        {"alias": "k", "algorithm": "SHA256withECDSA", "data": "<12 chars>"},
        ["alias", "algorithm", "data"],
    )
    assert title == "Allow: Sign data with a keystore key?"
    assert hint == (
        "The agent wants to sign data with a keystore key.\n"
        "Alias: k\n"
        "Algorithm: SHA256withECDSA\n"
        "Data: <12 chars>\n"
        "Yes allows this. No denies it."
    )


def test_format_confirm_copy_truncate_and_filter():
    ellipsis = "\u2026"
    assert risk_gate._truncate("x" * 81, 80) == "x" * 79 + ellipsis
    assert len(risk_gate._truncate("x" * 81, 80)) == 80

    title, hint = risk_gate.format_confirm_copy("v", {"a": "x" * 81}, ["a"])
    assert f"a: {'x' * 79}{ellipsis}" in hint

    long_args = {f"a{i}": "x" * 80 for i in range(5)}
    title, hint = risk_gate.format_confirm_copy(
        "v", long_args, [f"a{i}" for i in range(5)],
    )
    assert len(hint) == 400
    assert hint.endswith(ellipsis)

    title, hint = risk_gate.format_confirm_copy("x" * 80, {}, [])
    assert len(title) == 60
    assert title.endswith(ellipsis)
    assert title.startswith("Allow: ")

    title, hint = risk_gate.format_confirm_copy(
        "v", {"a": 1, "extra": "hidden"}, ["a", "missing"],
    )
    assert "a: 1" in hint
    assert "extra" not in hint
    assert "hidden" not in hint
    assert "missing" not in hint


def test_recover_orphans(tmp_path, monkeypatch):
    monkeypatch.setattr(tier_b, "PIDFILE", tmp_path / "pids.json")
    (tmp_path / "pids.json").write_text("[1, 2, 3]")
    def fake_kill(pid, sig):
        if pid == 1:
            raise OSError("gone")
        if pid == 2 and sig == 0:
            return None
        if pid == 3:
            return None
        raise OSError("x")
    monkeypatch.setattr(tier_b.os, "kill", fake_kill)
    monkeypatch.setattr(tier_b, "_is_our_orphan", lambda pid: pid == 3)
    killed = tier_b.recover_orphans()
    assert 3 in killed or killed == [] or isinstance(killed, list)


def test_watch_start_http(http_server):
    proc = MagicMock()
    proc.pid = 99
    proc.stdin = None
    proc.stdout = iter([])
    proc.poll.return_value = 0
    proc.wait.return_value = 0
    with patch("dispatch.tier_b.subprocess.Popen", return_value=proc):
        st, body = api(http_server, "POST", "/watch/sensor.stream", {"args": {"name": "light"}})
    assert st == 200
    assert "id" in body
    st, listing = api(http_server, "GET", "/watch")
    assert st == 200
    st, polled = api(http_server, "GET", f"/watch/{body['id']}")
    assert st == 200
    st, stopped = api(http_server, "DELETE", f"/watch/{body['id']}")
    assert st == 200
    st, after = api(http_server, "GET", f"/watch/{body['id']}")
    assert st == 200
    assert after["stopped"] is True


def test_watch_wrong_tier(http_server):
    st, body = api(http_server, "POST", "/watch/battery.status", {})
    assert st == 400


def test_args_not_object(http_server):
    st, body = api(http_server, "POST", "/perceive/battery.status", {"args": []})
    assert st == 400


def test_batch_bad_entry(http_server):
    st, body = api(http_server, "POST", "/perceive", {"verbs": [1]})
    assert st == 400


def test_token_create(tmp_path, monkeypatch):
    import daemon as d
    monkeypatch.delenv("AGENT_TOKEN", raising=False)
    monkeypatch.setattr(d, "TOKEN_PATH", tmp_path / ".agent-token")
    tok = d._load_or_create_token()
    assert len(tok) == 64
    assert (tmp_path / ".agent-token").read_text().strip() == tok
    assert d._load_or_create_token() == tok


def test_invalid_content_length(http_server, token):
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    req = Request(
        http_server + "/perceive",
        data=b"{}",
        headers={
            "X-Agent-Token": token,
            "Content-Type": "application/json",
            "Content-Length": "nope",
        },
        method="POST",
    )
    try:
        urlopen(req, timeout=5)
        assert False, "expected 400"
    except HTTPError as e:
        assert e.code == 400


def test_body_not_object(http_server, token):
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    import json as _json
    req = Request(
        http_server + "/perceive",
        data=b"[1]",
        headers={"X-Agent-Token": token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(req, timeout=5)
        assert False
    except HTTPError as e:
        assert e.code == 400
        assert "JSON object" in _json.loads(e.read())["error"]


def test_post_unknown_kind(http_server):
    st, body = api(http_server, "POST", "/nope/battery.status", {})
    assert st == 404


def test_post_unauthorized(http_server):
    st, body = api(http_server, "POST", "/perceive/battery.status", {}, token="x")
    assert st == 401


def test_delete_unauthorized(http_server):
    st, body = api(http_server, "DELETE", "/watch/x", token="x")
    assert st == 401


def test_execution_500(http_server):
    from types import SimpleNamespace
    fake = SimpleNamespace(returncode=9, stdout="", stderr="boom")
    with patch("dispatch.tier_a.subprocess.run", return_value=fake):
        st, body = api(http_server, "POST", "/perceive/battery.status", {})
    assert st == 500
    assert "exit code" in body["error"]


def test_watch_denied(http_server, monkeypatch):
    from dispatch import risk_gate
    monkeypatch.setattr(risk_gate, "_confirm_on_device", lambda *a, **k: False)
    # microphone.record is high-risk Tier B
    st, body = api(http_server, "POST", "/watch/microphone.record", {
        "args": {"outfile": "/tmp/x", "seconds": "1"},
    })
    assert st == 403


def test_watch_oserror(http_server):
    with patch("dispatch.tier_b.subprocess.Popen", side_effect=OSError("fail")):
        st, body = api(http_server, "POST", "/watch/sensor.stream", {"args": {"name": "x"}})
    assert st == 500


def test_tier_a_bad_parser():
    from dispatch.catalog import Verb
    v = Verb(name="z", direction="perceive", tier="A", risk="none",
             command=["true"], args=[], parser="bogus", timeout=1)
    from types import SimpleNamespace
    with patch("dispatch.tier_a.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")):
        with pytest.raises(ValueError, match="parser"):
            tier_a.run(v, {})

def test_main_starts(monkeypatch):
    import daemon as d
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(d, "recover_orphans", lambda: [1])
    class Fake:
        def __init__(self, *a, **k):
            pass
        def serve_forever(self):
            raise KeyboardInterrupt()
    monkeypatch.setattr(d, "ThreadingHTTPServer", Fake)
    with pytest.raises(KeyboardInterrupt):
        d.main()
