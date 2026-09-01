import json
from pathlib import Path

import pytest

from dispatch.store import CircuitOpen, Store, StoreConfig


def test_durability_pragmas(tmp_path):
    st = Store(StoreConfig(path=tmp_path / "agent.db", offline=True))
    assert st.durability == {"journal_mode": "wal", "synchronous": 2}
    st.close()


def test_append_stages_and_reject_unknown(tmp_path):
    st = Store(StoreConfig(path=tmp_path / "agent.db", offline=True))
    for stage in ("requested", "approved", "denied", "timeout", "executed", "failed"):
        st.append(verb="toast.show", stage=stage, risk="low", args={"text": "x"})
    rows = st.recent("toast.show", limit=20)
    assert {r[3] for r in rows} >= {"requested", "executed", "failed"}
    with pytest.raises(ValueError, match="unknown stage"):
        st.append(verb="toast.show", stage="stopped")
    st.close()


def test_circuit_trips_and_blocks(tmp_path):
    cfg = StoreConfig(
        path=tmp_path / "agent.db",
        offline=True,
        failure_limit=3,
        window_s=60,
        cooldown_s=30,
    )
    st = Store(cfg)
    for _ in range(3):
        st.record_outcome(verb="sms.send", stage="failed", error="boom")
    assert st.is_open("sms.send")
    stages = [r[3] for r in st.recent("sms.send", limit=20)]
    assert "circuit_open" in stages
    with pytest.raises(CircuitOpen):
        st.guard("sms.send")
    st.close()


def test_committed_row_survives_reopen(tmp_path):
    path = tmp_path / "agent.db"
    st = Store(StoreConfig(path=path, offline=True))
    rowid = st.append(verb="battery.status", stage="executed", risk="none")
    st.close()
    st2 = Store(StoreConfig(path=path, offline=True))
    rows = st2.recent("battery.status")
    assert rows[0][0] == rowid
    assert json.loads(rows[0][5]) == {}
    st2.close()


def test_guard_closed_and_get_store(tmp_path, monkeypatch):
    from dispatch import store as verb_store

    path = tmp_path / "g.db"
    st = Store(StoreConfig(path=path, offline=True))
    st.guard("battery.status")
    st.append(verb="battery.status", stage="timeout")
    assert st.failure_count("battery.status") == 1
    assert st.recent("battery.status", stage="timeout")
    st.close()
    monkeypatch.setenv("AGENT_DB_PATH", str(path))
    verb_store.reset_store()
    s2 = verb_store.get_store()
    assert s2.durability["journal_mode"] == "wal"
    verb_store.reset_store()


def test_from_env_offline_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.delenv("LIBSQL_URL", raising=False)
    cfg = StoreConfig.from_env()
    assert cfg.offline is True
    assert cfg.sync_url is None
    monkeypatch.setenv("LIBSQL_URL", "libsql://example.turso.io")
    monkeypatch.setenv("LIBSQL_AUTH_TOKEN", "t")
    cfg2 = StoreConfig.from_env()
    assert cfg2.sync_url.startswith("libsql://")
    assert cfg2.offline is False
