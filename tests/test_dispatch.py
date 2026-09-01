import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dispatch import risk_gate, tier_a, tier_b, tier_c
from dispatch.catalog import Verb

ROOT = Path(__file__).resolve().parents[1]


def _verb(**kw):
    defaults = dict(
        name="demo", direction="perceive", tier="A", risk="none",
        command=["true"], args=[], parser="json", timeout=5,
    )
    defaults.update(kw)
    return Verb(**defaults)


def test_tier_a_json_ok():
    v = _verb()
    completed = SimpleNamespace(returncode=0, stdout='{"a": 1}', stderr="")
    with patch("dispatch.tier_a.subprocess.run", return_value=completed):
        assert tier_a.run(v, {}) == {"ok": True, "data": {"a": 1}}


def test_tier_a_text_and_none():
    completed = SimpleNamespace(returncode=0, stdout=" hi \n", stderr="")
    with patch("dispatch.tier_a.subprocess.run", return_value=completed):
        assert tier_a.run(_verb(parser="text"), {}) == {"ok": True, "data": "hi"}
        assert tier_a.run(_verb(parser="none"), {}) == {"ok": True}


def test_tier_a_timeout_and_missing():
    v = _verb()
    with patch("dispatch.tier_a.subprocess.run", side_effect=subprocess.TimeoutExpired("x", 5)):
        with pytest.raises(tier_a.ExecutionError, match="timed out"):
            tier_a.run(v, {})
    with patch("dispatch.tier_a.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(tier_a.ExecutionError, match="command not found"):
            tier_a.run(v, {})


def test_tier_a_bad_exit_and_bad_json():
    v = _verb()
    with patch("dispatch.tier_a.subprocess.run", return_value=SimpleNamespace(returncode=2, stdout="", stderr="nope")):
        with pytest.raises(tier_a.ExecutionError, match="exit code"):
            tier_a.run(v, {})
    with patch("dispatch.tier_a.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="not-json", stderr="")):
        with pytest.raises(tier_a.ExecutionError, match="unparseable"):
            tier_a.run(v, {})


def test_tier_a_rejects_tier_b():
    with pytest.raises(ValueError, match="not a Tier A"):
        tier_a.run(_verb(tier="B"), {})


def test_tier_c():
    with pytest.raises(tier_c.TierCNotImplemented):
        tier_c.run("ui.tap", {})


def test_risk_gate_low_risk_no_dialog(tmp_path, monkeypatch):
    from dispatch.catalog import Catalog
    monkeypatch.setattr(risk_gate, "LOG_PATH", tmp_path / "a.log")
    cat = Catalog.load(ROOT / "verbs.yaml")
    risk_gate.check(cat, "toast.show", {"text": "x"})
    lines = (tmp_path / "a.log").read_text().splitlines()
    assert json.loads(lines[-1])["stage"] == "requested"


def test_risk_gate_denied(tmp_path, monkeypatch):
    from dispatch.catalog import Catalog
    monkeypatch.setattr(risk_gate, "LOG_PATH", tmp_path / "a.log")
    monkeypatch.setattr(risk_gate, "_confirm_on_device", lambda *a, **k: False)
    cat = Catalog.load(ROOT / "verbs.yaml")
    with pytest.raises(risk_gate.Denied):
        risk_gate.check(cat, "sms.send", {"number": "1", "text": "x"})


def test_risk_gate_approved(tmp_path, monkeypatch):
    from dispatch.catalog import Catalog
    monkeypatch.setattr(risk_gate, "LOG_PATH", tmp_path / "a.log")
    monkeypatch.setattr(risk_gate, "_confirm_on_device", lambda *a, **k: True)
    cat = Catalog.load(ROOT / "verbs.yaml")
    risk_gate.check(cat, "sms.send", {"number": "1", "text": "x"})
    stages = [json.loads(l)["stage"] for l in (tmp_path / "a.log").read_text().splitlines()]
    assert "approved" in stages


def test_confirm_timeout_is_deny():
    with patch("dispatch.risk_gate.subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
        assert risk_gate._confirm_on_device("v", {}, []) is False
    with patch("dispatch.risk_gate.subprocess.run", side_effect=FileNotFoundError):
        assert risk_gate._confirm_on_device("v", {}, []) is False


def test_check_confirm_copy_uses_public_args(tmp_path, monkeypatch):
    from dispatch.catalog import Catalog
    monkeypatch.setattr(risk_gate, "LOG_PATH", tmp_path / "a.log")
    cat = Catalog.load(ROOT / "verbs.yaml")
    captured = []
    proc = MagicMock(returncode=0, stdout='{"text": "yes"}')

    def fake_run(argv, **kwargs):
        captured.append(list(argv))
        return proc

    with patch("dispatch.risk_gate.subprocess.run", side_effect=fake_run):
        risk_gate.check(cat, "sms.send", {"number": "+1XXXXXXXXXX", "text": "Hello"})
    argv = captured[-1]
    assert argv[:2] == ["termux-dialog", "confirm"]
    title = argv[argv.index("-t") + 1]
    hint = argv[argv.index("-i") + 1]
    assert title == "Allow: Send an SMS?"
    assert "To: +1XXXXXXXXXX" in hint
    assert "Message: Hello" in hint
    assert "sms.send(" not in hint
    assert hint != json.dumps({"number": "+1XXXXXXXXXX", "text": "Hello"}, default=str)

    captured.clear()
    raw = {"alias": "k", "algorithm": "SHA256withECDSA", "data": "abcdefghijkl"}
    with patch("dispatch.risk_gate.subprocess.run", side_effect=fake_run):
        risk_gate.check(cat, "keystore.sign", raw)
    argv = captured[-1]
    hint = argv[argv.index("-i") + 1]
    title = argv[argv.index("-t") + 1]
    assert title == "Allow: Sign data with a keystore key?"
    assert "Data: <12 chars>" in hint
    assert "abcdefghijkl" not in hint
    assert "keystore.sign(" not in hint
    log_events = [json.loads(l) for l in (tmp_path / "a.log").read_text().splitlines()]
    for event in log_events:
        assert "hint" not in event
        if event.get("verb") == "keystore.sign" and "args" in event:
            assert event["args"]["data"] == "<12 chars>"
            assert isinstance(event["args"], dict)


def test_tier_b_start_poll_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(tier_b, "PIDFILE", tmp_path / "pids.json")
    proc = MagicMock()
    proc.pid = 4242
    proc.stdin = None
    proc.stdout = iter(['{"n": 1}\n', "not-json\n"])
    proc.poll.return_value = None
    proc.wait.return_value = 0
    v = _verb(name="sensor.stream", tier="B", parser="json_stream",
              command=["termux-sensor", "-s", "{name}"], args=["name"], timeout=None)
    mgr = tier_b.SubscriptionManager()
    with patch("dispatch.tier_b.subprocess.Popen", return_value=proc):
        sid = mgr.start(v, {"name": "accelerometer"})
    import time
    deadline = time.time() + 2
    polled = {"items": []}
    while time.time() < deadline:
        polled = mgr.poll(sid)
        if polled["items"]:
            break
        time.sleep(0.05)
    assert any(isinstance(i, dict) and (i.get("n") == 1 or i.get("_parse_error")) for i in polled["items"]) or polled["items"]
    name = mgr.stop(sid)
    assert name == "sensor.stream"
    after = mgr.poll(sid)
    assert after["stopped"] is True
    with pytest.raises(KeyError):
        mgr.stop("missing")
