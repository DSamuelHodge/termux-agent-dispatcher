"""Device-gated tests. Skipped unless AGENT_LIVE=1.

Never sends SMS, never opens fingerprint/keystore. Perceive + toast only.
"""

import os

import pytest

from tests.conftest import api

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_LIVE") != "1",
    reason="set AGENT_LIVE=1 to hit Termux:API on device",
)


def test_live_health(http_server):
    st, body = api(http_server, "GET", "/health")
    assert st == 200
    assert body["ok"] is True


def test_live_battery(http_server):
    st, body = api(http_server, "POST", "/perceive/battery.status")
    assert st == 200
    assert body["ok"] is True


def test_live_batch(http_server):
    st, body = api(http_server, "POST", "/perceive", {
        "verbs": ["battery.status", "volume.get"],
    })
    assert st == 200
    assert len(body["items"]) == 2
