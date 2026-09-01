from pathlib import Path

import pytest
import yaml

from dispatch.catalog import Catalog, Verb

ROOT = Path(__file__).resolve().parents[1]


def test_load_real_catalog():
    c = Catalog.load(ROOT / "verbs.yaml")
    assert len(c.verbs) == 73
    assert "battery.status" in c.verbs
    assert c.verbs["sensor.stream"].tier == "B"
    spec = c.verbs["battery.status"].public_spec()
    assert spec["route"] == "perceive"
    assert spec["parser"] == "json"
    assert "timeout" in spec
    watch = c.verbs["sensor.stream"].public_spec()
    assert watch["route"] == "watch"
    stdin = c.verbs["keystore.sign"].public_spec()
    assert stdin["stdin"] == "data"


def test_build_argv_missing_and_extra():
    v = Verb(
        name="t", direction="act", tier="A", risk="none",
        command=["cmd", "{x}"], args=["x"], parser="none", timeout=1,
    )
    with pytest.raises(ValueError, match="missing"):
        v.build_argv({})
    with pytest.raises(ValueError, match="unexpected"):
        v.build_argv({"x": "1", "y": "2"})
    assert v.build_argv({"x": "1"}) == ["cmd", "1"]


def test_stdin_and_public_args():
    v = Verb(
        name="t", direction="act", tier="A", risk="high",
        command=["cmd"], args=["data"], parser="text", timeout=1, stdin="data",
    )
    assert v.stdin_payload({"data": "hello"}) == "hello"
    assert v.stdin_payload({"data": None}) == ""
    with pytest.raises(ValueError, match="must be a string"):
        v.stdin_payload({"data": 1})
    redacted = v.public_args({"data": "secret"})
    assert redacted["data"] == "<6 chars>"


def test_load_rejects_bad_yaml(tmp_path):
    p = tmp_path / "v.yaml"
    p.write_text(yaml.safe_dump({
        "verbs": {
            "bad": {
                "direction": "perceive", "tier": "C", "risk": "none",
                "command": ["x"], "args": [], "parser": "json", "timeout": 1,
            }
        }
    }))
    with pytest.raises(ValueError, match="invalid tier"):
        Catalog.load(p)


def test_unknown_verb():
    c = Catalog.load(ROOT / "verbs.yaml")
    with pytest.raises(KeyError):
        c.get("nope.verb")


def test_requires_confirmation():
    c = Catalog.load(ROOT / "verbs.yaml")
    assert c.requires_confirmation("sms.send") is True
    assert c.requires_confirmation("toast.show") is False
