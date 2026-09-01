# AGENTS.md — on-device dispatcher

This file is for anyone (human or model) changing this tree. The running
program is `daemon.py`. The contract the brain calls is `verbs.yaml`.

## What this is

A Termux loopback server (`127.0.0.1:8477`) that turns a typed verb
into a Termux:API process. It is the phone's hands. It is not the
planner. Do not fold an LLM loop into this process.

## Layout

- `daemon.py` — HTTP, auth, route kind vs tier/direction
- `verbs.yaml` — only place a new Tier A/B verb should be added
- `dispatch/catalog.py` — loads YAML; `stdin` field pipes an arg
- `dispatch/tier_a.py` / `tier_b.py` / `tier_c.py` — execute
- `dispatch/risk_gate.py` — confirm dialog + `logs/audit.log`
- `docs/verb-catalog.md` — human catalog (direction / tier / risk)
- `boot/01-start-agent` — Termux:Boot unit

## Rules

1. New Termux:API capability: edit `verbs.yaml` only. No Python unless
   the dispatcher itself is missing a mechanism (stdin was one).
2. Do not put AutoTask-only / Tier C rows in the YAML.
   `VALID_TIERS` is `{A, B}`.
3. Match official `termux-api-package` `scripts/*.in` argv. If a script
   reads the payload from stdin, set `stdin: <arg>` and list that arg
   in `args`. Do not stuff the body into argv.
4. Risk `high` is gated on-device. Do not add a back door that skips
   `risk_gate.check`.
5. Audit events use `Verb.public_args` so stdin bodies are not logged.
6. Leave `__pycache__` and `.agent-token` off the device copy when
   you can. Tokens are per-device.
7. After YAML or dispatch changes, load-test:

       python -c "from dispatch.catalog import Catalog; c=Catalog.load('verbs.yaml'); print(len(c.verbs))"

## HTTP contract

- `GET /health` — pid, uptime, verb count, active watches, termux-api probe
- `GET /verbs` — each row includes direction, tier, risk, args, parser, timeout, route, optional stdin
- `POST /perceive` `{"verbs":[...]}` — batch Tier A perceive (names or `{name, args}`)
- `POST /perceive/<verb>` `{"args":{...}}` — Tier A, direction perceive
- `POST /act/<verb>` `{"args":{...}}` — Tier A, direction act
- `POST /watch/<verb>` — Tier B start → `{"id"}`
- `GET /watch` — `{ids}` of active subscriptions
- `GET /watch/<id>` / `DELETE /watch/<id>` — poll stays 200 with `stopped: true` until reaped
- Header `X-Agent-Token` on every call

Failure order is fixed: 401 auth, 404 unknown verb, 400 route/args,
403 risk deny, 500 execution.

## Deploy

On-device Termux only:

    curl -sL https://raw.githubusercontent.com/DSamuelHodge/termux-agent-dispatcher/main/setup.sh | bash

Smoke `GET /health`, `battery.status`, and `toast.show` before anything
with risk `high`.
