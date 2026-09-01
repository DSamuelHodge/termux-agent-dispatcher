---
name: termux-agent-dispatcher-remote
description: Connect a remote desktop or cloud agent to the on-device Termux dispatcher over tailcat. Use when reaching 127.0.0.1:8477 from off-phone, Warp/Oz cloud agents, or SOCKS-wrapped curl against perceive/act/watch.
---

# Remote connect (tailcat)

Full verbatim guide: [docs/remote-access-tailcat.md](docs/remote-access-tailcat.md).
On-device contract: [AGENTS.md](AGENTS.md).

## Do

- Leave `daemon.py` on `127.0.0.1:8477`. Never Funnel, never bind `0.0.0.0`.
- Phone: `tailcat serve 8477 --allow=nodekey:<client>` only. No `all`, `exit-node`, or `no-auth-ssh`.
- Client: `tailcat socks` + `X-Agent-Token` on every request.
- Treat `tc…` tokens and `.agent-token` as secrets. Do not commit them.

## Call shape

```sh
TOKEN=tcXXXXXXXXX
AUTH=...   # from the device; not in this repo

tailcat socks "$TOKEN" curl -sS -H "X-Agent-Token: $AUTH" \
  http://127.0.0.1:8477/health

tailcat socks "$TOKEN" curl -sS -H "X-Agent-Token: $AUTH" \
  -X POST http://127.0.0.1:8477/perceive \
  -d '{"verbs":["battery.status"]}'
```

High-risk verbs still require an on-device confirm. Failure order: 401, 404, 400, 403, 500.
