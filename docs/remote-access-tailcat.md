Keep the daemon on `127.0.0.1:8477`. Do **not** bind it to a public interface or use Tailscale Funnel. The tunnel is the exposure; `X-Agent-Token` stays the application auth.

**tailcat** is Tailscale’s data plane without a tailnet: WireGuard + NAT traversal + DERP. Phone prints a `tc…` token; desktop/cloud uses that token. Traffic is E2E encrypted. No accounts, no TUN, no root. Possession of the token is the capability unless you lock clients with `--allow`.

### Phone (server)

```sh
# once: stable server key + client identity on the desktop
tailcat genkey --fixed-region          # phone; keep ~/.config/tailcat/keys/
# on desktop:
tailcat genkey --client                # prints nodekey:…

# serve only the dispatcher port, only that client
tailcat serve 8477 --allow=nodekey:<desktop-or-agent-nodekey>
```

Newer CLI is `tailcat serve 8477`; older docs used `--serve=8477`. Share the printed `tc…` token out of band (not in git). Ephemeral keys (`--key=new`) die when the process exits — better for one-shot agent sessions.

Do **not** `serve all`, `exit-node`, or `no-auth-ssh`. That is a much larger surface than HTTP 8477.

### Desktop / cloud agent (client)

```sh
TOKEN=tcXXXXXXXXX   # from the phone
AUTH=$(cat path-to-.agent-token)

# health check through SOCKS (token as hostname; case-sensitive)
tailcat socks "$TOKEN" curl -sS -H "X-Agent-Token: $AUTH" \
  http://127.0.0.1:8477/health

# or:
tailcat socks curl -sS -H "X-Agent-Token: $AUTH" \
  "http://${TOKEN}:8477/health"
```

There is no first-class `tailcat forward` yet ([issue #14](https://github.com/tailscale/tailcat/issues/14)); SOCKS is the supported way for curl/agents. Browsers lowercase hostnames, so they are a poor fit.

### Layers that actually matter

| Layer | What it does |
|---|---|
| Daemon still loopback-only | Other apps on the phone still need the token; WAN never sees 8477 |
| WireGuard via tailcat | Transport encryption; `--allow` so a leaked `tc…` is not enough |
| `X-Agent-Token` | App auth; rotate by deleting `~/agent/.agent-token` |
| On-device risk gate | High-risk verbs still need a tap on the phone |

Public Tailscale DERP is rate-limited and not an SLA. For always-on cloud agents, run your own `derper` or use full **Tailscale Serve** (`tailscale serve 8477`) if both machines are already on a tailnet — ACLs then replace `--allow`. Funnel would put the dispatcher on the internet; skip it.

Treat the `tc…` string like a password. Saved server keys stay valid across restarts; anyone who ever had that token can reconnect unless `--allow` is set.
