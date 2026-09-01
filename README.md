# On-device agent dispatcher (Termux:API skeleton)

A loopback HTTP process that runs in Termux and is the only thing on the
phone allowed to touch Termux:API. A separate "brain" (local script or
remote model) calls typed verbs — `perceive.*`, `act.*`, `watch.*` —
instead of raw `termux-*` argv. The catalog carries direction, tier,
risk, and an optional stdin hook; high-risk verbs stop on an on-device
confirm dialog; every attempt is appended to `logs/audit.log`. This is
the device body. The model does not hold SMS, camera, or keystore
permission.

## Install on device (Termux)

From a Termux session on the phone:

    curl -sL https://raw.githubusercontent.com/DSamuelHodge/termux-agent-dispatcher/main/setup.sh | bash

That installs Python + PyYAML, copies the dispatcher into `~/agent`, and
installs `~/.termux/boot/01-start-agent`. Reboot once so Termux:Boot
picks it up, or start it by hand:

    cd ~/agent && python daemon.py

## Auth

Every route requires an `X-Agent-Token` header. The token comes from the
AGENT_TOKEN env var if set, otherwise it's generated once into
.agent-token (chmod 600) on first start. Loopback-only is not private on
Android — any app can dial 127.0.0.1 — so this token is the actual
access control. Give it to the brain with `cat ~/agent/.agent-token`;
rotate by deleting the file and restarting the daemon.

## Manual smoke test (from another Termux session, device must be unlocked
## the first time so Android's permission prompts can fire)

    TOKEN=$(cat ~/agent/.agent-token)
    curl -H "X-Agent-Token: $TOKEN" http://127.0.0.1:8477/health
    curl -H "X-Agent-Token: $TOKEN" http://127.0.0.1:8477/verbs
    curl -X POST -H "X-Agent-Token: $TOKEN" http://127.0.0.1:8477/perceive/battery.status
    curl -X POST -H "X-Agent-Token: $TOKEN" http://127.0.0.1:8477/perceive \
      -d '{"verbs": ["battery.status", "volume.get"]}'
    curl -X POST -H "X-Agent-Token: $TOKEN" http://127.0.0.1:8477/act/toast.show \
      -d '{"args": {"text": "hello from the agent"}}'

    # Tier B — start a subscription, poll it, stop it
    ID=$(curl -X POST -H "X-Agent-Token: $TOKEN" http://127.0.0.1:8477/watch/sensor.stream \
      -d '{"args": {"name": "accelerometer"}}' | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')
    curl -H "X-Agent-Token: $TOKEN" http://127.0.0.1:8477/watch/$ID
    curl -X DELETE -H "X-Agent-Token: $TOKEN" http://127.0.0.1:8477/watch/$ID

    # high-risk verb — this will pop a termux-dialog confirm on the phone
    # and block until you tap yes/no
    curl -X POST -H "X-Agent-Token: $TOKEN" http://127.0.0.1:8477/act/sms.send \
      -d '{"args": {"number": "+15551234567", "text": "test"}}'

## Adding a verb

Edit verbs.yaml only — no code changes needed for any Tier A/B command
(including ones whose official script reads the payload from stdin: set
`stdin: <arg-name>` and list that name in `args`; the dispatcher pipes
the value and redacts it in the audit log / confirm dialog).

The classified surface is in [docs/verb-catalog.md](docs/verb-catalog.md).
`verbs.yaml` is what the dispatcher loads. Copy a catalog row into the
YAML with its real argv template and it is live everywhere (routing,
risk gating, execution) on next daemon restart.

Tier C (ui.tap/ui.type/ui.gesture/ui.screen.read) is intentionally not
supported yet; see dispatch/tier_c.py for why and what it needs. The
daemon's Tier C routing branch is already wired and stays in place for
when a companion AccessibilityService app lands.

## Audit log

Every call attempt is appended to logs/audit.log as newline-delimited
JSON, written before execution so a crash still leaves a record of
intent. Lifecycle per call: `requested` (always, for every verb), then
`approved`/`denied` for confirmation-gated verbs, then `executed`/`failed`
after the attempt. Tier B subscriptions add a `stopped` event on
DELETE /watch/<id>.

## Tests

    pip install -r requirements-dev.txt
    PYTHONPATH=. pytest tests --cov=dispatch --cov=daemon --cov-fail-under=90

Live Termux:API hits are skipped unless `AGENT_LIVE=1` (no SMS / keystore).
