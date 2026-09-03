from types import SimpleNamespace
from unittest.mock import patch

from tests.conftest import api


def test_dry_run_act(http_server):
    st, body = api(http_server, "POST", "/act/toast.show", {
        "args": {"text": "hello"},
        "dry_run": True,
    })
    assert st == 200
    assert body["dry_run"] is True
    assert body["argv"] == ["termux-toast", "hello"]
    assert body["confirmation_required"] is False


def test_dry_run_high_risk_skips_confirm_and_idempotency(http_server, monkeypatch):
    from dispatch import risk_gate
    called = []
    monkeypatch.setattr(risk_gate, "_confirm_on_device", lambda *a, **k: called.append(1) or True)
    st, body = api(http_server, "POST", "/act/sms.send", {
        "args": {"number": "+1", "text": "x"},
        "dry_run": True,
    })
    assert st == 200
    assert body["dry_run"] is True
    assert body["confirmation_required"] is True
    assert called == []


def test_high_risk_requires_idempotency_key(http_server):
    st, body = api(http_server, "POST", "/act/sms.send", {
        "args": {"number": "+1", "text": "x"},
    })
    assert st == 400
    assert body["code"] == "MISSING_IDEMPOTENCY_KEY"


def test_idempotency_replay_and_conflict(http_server):
    fake = SimpleNamespace(returncode=0, stdout="", stderr="")
    with patch("dispatch.tier_a.subprocess.run", return_value=fake):
        st1, b1 = api(http_server, "POST", "/act/toast.show", {
            "args": {"text": "a"},
            "idempotency_key": "k1",
        })
        st2, b2 = api(http_server, "POST", "/act/toast.show", {
            "args": {"text": "a"},
            "idempotency_key": "k1",
        })
        st3, b3 = api(http_server, "POST", "/act/toast.show", {
            "args": {"text": "b"},
            "idempotency_key": "k1",
        })
    assert st1 == 200 and b1["ok"] is True
    assert st2 == 200 and b2 == b1
    assert st3 == 409
    assert b3["code"] == "IDEMPOTENCY_CONFLICT"


def test_structured_unknown_verb(http_server):
    st, body = api(http_server, "POST", "/act/nosuch.verb", {"args": {}})
    assert st == 404
    assert body["code"] == "UNKNOWN_VERB"


def test_schema_enum_rejected(http_server):
    st, body = api(http_server, "POST", "/act/torch.toggle", {"args": {"state": "maybe"}})
    assert st == 400
    assert body["code"] == "INVALID_ARGS"
    assert "one of" in body["error"]


def test_confirm_unknown(http_server):
    st, body = api(http_server, "GET", "/confirm/nope")
    assert st == 404
    assert body["code"] == "CONFIRM_NOT_FOUND"


def test_bad_webhook_url(http_server):
    st, body = api(http_server, "POST", "/act/toast.show", {
        "args": {"text": "x"},
        "webhook_url": "file:///etc/passwd",
    })
    assert st == 400
    assert body["code"] == "INVALID_BODY"


def test_idempotency_header(http_server):
    fake = SimpleNamespace(returncode=0, stdout="", stderr="")
    from urllib.request import Request, urlopen
    import json, os
    with patch("dispatch.tier_a.subprocess.run", return_value=fake):
        req = Request(
            http_server + "/act/toast.show",
            data=json.dumps({"args": {"text": "h"}}).encode(),
            headers={
                "X-Agent-Token": os.environ["AGENT_TOKEN"],
                "Content-Type": "application/json",
                "Idempotency-Key": "hdr-1",
            },
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            assert resp.status == 200


def test_circuit_open_structured(http_server, _isolated_verb_store):
    st = _isolated_verb_store
    st.append(verb="toast.show", stage="circuit_open", risk="none")
    st2, body = api(http_server, "POST", "/act/toast.show", {"args": {"text": "x"}})
    assert st2 == 500
    assert body["code"] == "CIRCUIT_OPEN"


def test_agent_error_payload():
    from dispatch.errors import AgentError
    e = AgentError("INVALID_ARGS", "nope", http_status=400, extra="1")
    assert e.payload()["code"] == "INVALID_ARGS"
    assert e.payload()["extra"] == "1"


def test_fail_orphans(tmp_path):
    from dispatch.catalog import Catalog
    from dispatch.confirm import ConfirmManager
    from dispatch import store as verb_store
    from pathlib import Path
    cat = Catalog.load(Path(__file__).resolve().parents[1] / "verbs.yaml")
    st = verb_store.get_store()
    st.put_confirm_job(
        job_id="orphan1", verb="sms.send", kind="act", risk="high",
        args={}, status="pending",
    )
    mgr = ConfirmManager(cat, lambda *a, **k: (200, {"ok": True}))
    assert mgr.fail_orphans() >= 1
    job = st.get_confirm_job("orphan1")
    assert job["status"] == "failed"


def test_high_risk_approved_and_webhook(http_server, monkeypatch):
    import time
    from dispatch import risk_gate, confirm as confirm_mod
    monkeypatch.setattr(risk_gate, "_confirm_on_device", lambda *a, **k: True)
    posted = []

    class FakeResp:
        def read(self):
            return b"ok"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        posted.append((req.full_url, req.data))
        return FakeResp()

    monkeypatch.setattr(confirm_mod, "urlopen", fake_urlopen)
    fake = SimpleNamespace(returncode=0, stdout="", stderr="")
    with patch("dispatch.tier_a.subprocess.run", return_value=fake):
        st, body = api(http_server, "POST", "/act/sms.send", {
            "args": {"number": "+1", "text": "ok"},
            "idempotency_key": "sms-ok",
            "webhook_url": "https://example.com/hook",
        })
    assert st == 202
    cid = body["confirm_id"]
    job = None
    for _ in range(80):
        st, job = api(http_server, "GET", f"/confirm/{cid}")
        if job["status"] != "pending":
            break
        time.sleep(0.05)
    assert job["status"] == "executed"
    assert posted and posted[0][0] == "https://example.com/hook"

    # replay same key after completion
    st, replay = api(http_server, "POST", "/act/sms.send", {
        "args": {"number": "+1", "text": "ok"},
        "idempotency_key": "sms-ok",
    })
    assert st == 200
    assert replay.get("ok") is True


def test_build_argv_stringifies():
    from dispatch.catalog import Verb
    v = Verb(
        name="t", direction="act", tier="A", risk="none",
        command=["cmd", "{x}"], args=["x"], parser="none", timeout=1,
    )
    assert v.build_argv({"x": 2}) == ["cmd", "2"]
    assert v.build_argv({"x": True}) == ["cmd", "true"]


def test_webhook_ok():
    from dispatch.confirm import webhook_ok
    assert webhook_ok(None)
    assert webhook_ok("https://example.com/hook")
    assert not webhook_ok("javascript:alert(1)")
    assert not webhook_ok("not a url")
