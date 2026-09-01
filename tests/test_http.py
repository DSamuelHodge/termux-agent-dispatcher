from types import SimpleNamespace
from unittest.mock import patch

from tests.conftest import api


def test_unauthorized(http_server):
    st, body = api(http_server, "GET", "/health", token="wrong")
    assert st == 401
    assert body["error"] == "unauthorized"


def test_health_and_verbs(http_server):
    st, h = api(http_server, "GET", "/health")
    assert st == 200
    assert h["ok"] is True
    assert h["verbs"] == 86
    assert "watches" in h
    assert "termux_api" in h
    assert h["host"] == "127.0.0.1"

    st, verbs = api(http_server, "GET", "/verbs")
    assert st == 200
    row = verbs["battery.status"]
    assert row["route"] == "perceive"
    assert row["parser"] == "json"
    assert "timeout" in row
    assert verbs["sensor.stream"]["route"] == "watch"
    assert "stdin" in verbs["keystore.sign"]


def test_watch_list_empty(http_server):
    st, body = api(http_server, "GET", "/watch")
    assert st == 200
    assert body["ids"] == []


def test_unknown_and_wrong_route(http_server):
    st, body = api(http_server, "POST", "/act/nosuch.verb", {"args": {}})
    assert st == 404
    st, body = api(http_server, "POST", "/act/battery.status", {"args": {}})
    assert st == 400
    st, body = api(http_server, "GET", "/nope")
    assert st == 404


def test_missing_args_400_before_run(http_server):
    st, body = api(http_server, "POST", "/act/toast.show", {"args": {}})
    assert st == 400
    assert "missing required args" in body["error"]


def test_perceive_mocked(http_server):
    fake = SimpleNamespace(returncode=0, stdout='{"percentage": 50}', stderr="")
    with patch("dispatch.tier_a.subprocess.run", return_value=fake):
        st, body = api(http_server, "POST", "/perceive/battery.status", {})
    assert st == 200
    assert body["ok"] is True
    assert body["data"]["percentage"] == 50


def test_batch_perceive(http_server):
    fake = SimpleNamespace(returncode=0, stdout="{}", stderr="")
    with patch("dispatch.tier_a.subprocess.run", return_value=fake):
        st, body = api(http_server, "POST", "/perceive", {
            "verbs": ["battery.status", {"name": "volume.get", "args": {}}],
        })
    assert st == 200
    assert [i["name"] for i in body["items"]] == ["battery.status", "volume.get"]
    assert all(i["status"] == 200 for i in body["items"])


def test_batch_rejects_act(http_server):
    st, body = api(http_server, "POST", "/perceive", {"verbs": ["toast.show"]})
    assert st == 400
    assert "batch perceive" in body["error"]


def test_batch_empty(http_server):
    st, body = api(http_server, "POST", "/perceive", {"verbs": []})
    assert st == 400


def test_batch_unknown(http_server):
    st, body = api(http_server, "POST", "/perceive", {"verbs": ["nope"]})
    assert st == 404


def test_invalid_json(http_server, token):
    import json
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    req = Request(
        http_server + "/perceive/battery.status",
        data=b"not-json",
        headers={"X-Agent-Token": token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(req, timeout=5)
        assert False
    except HTTPError as e:
        assert e.code == 400
        payload = json.loads(e.read())
        assert "invalid JSON" in payload["error"]


def test_watch_unknown_id(http_server):
    st, body = api(http_server, "GET", "/watch/deadbeef")
    assert st == 404
    st, body = api(http_server, "DELETE", "/watch/deadbeef")
    assert st == 404


def test_denied_high_risk(http_server, monkeypatch):
    from dispatch import risk_gate
    monkeypatch.setattr(risk_gate, "_confirm_on_device", lambda *a, **k: False)
    st, body = api(http_server, "POST", "/act/sms.send", {
        "args": {"number": "+1", "text": "no"},
    })
    assert st == 403
