from pathlib import Path

import pytest
import yaml

from dispatch.catalog import Catalog, Verb

ROOT = Path(__file__).resolve().parents[1]


def test_load_real_catalog():
    c = Catalog.load(ROOT / "verbs.yaml")
    assert len(c.verbs) == 86
    assert sum(1 for v in c.verbs.values() if v.tier == "A") == 78
    assert sum(1 for v in c.verbs.values() if v.tier == "B") == 8
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


def test_share_open_sibling_argv():
    c = Catalog.load(ROOT / "verbs.yaml")
    assert c.verbs["url.open"].command == ["termux-open-url", "{url}"]
    assert c.verbs["url.open"].args == ["url"]
    assert c.verbs["url.open.in"].command == ["termux-open-url", "{url}", "{package}"]
    assert c.verbs["url.open.in"].args == ["url", "package"]
    assert c.verbs["file.open"].command == ["termux-open", "{path}"]
    assert c.verbs["file.open"].args == ["path"]
    assert c.verbs["share.send"].command == ["termux-share", "{file}"]
    assert c.verbs["share.file.send"].command == ["termux-share", "-a", "send", "{file}"]
    assert c.verbs["share.file.view"].command == ["termux-share", "-a", "view", "{file}"]
    assert c.verbs["share.file.edit"].command == ["termux-share", "-a", "edit", "{file}"]
    share_text = c.verbs["share.text"]
    assert share_text.command == ["termux-share", "-a", "send"]
    assert share_text.stdin == "text"
    assert share_text.risk == "low"
    assert share_text.args == ["text"]
    assert c.verbs["file.open.send"].command == ["termux-open", "--send", "{path}"]
    assert c.verbs["file.open.chooser"].command == ["termux-open", "--chooser", "{path}"]
    assert c.verbs["media.resume"].command == ["termux-media-player", "play"]
    assert c.verbs["media.resume"].args == []
    assert c.verbs["job.cancel.all"].command == ["termux-job-scheduler", "--cancel-all"]
    assert c.verbs["job.cancel.all"].args == []
    assert c.verbs["wallpaper.set.url"].command == ["termux-wallpaper", "-u", "{url}"]
    assert c.verbs["usb.request"].command == ["termux-usb", "-r", "{device}"]
    assert c.verbs["usb.request"].args == ["device"]
    assert c.verbs["usb.request"].parser == "text"
    assert c.verbs["usb.request"].risk == "medium"


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
