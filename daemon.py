"""
Daemon entrypoint. Started from ~/.termux/boot/ so it survives reboot.
Holds a wake-lock so Doze doesn't kill it, and exposes a 127.0.0.1-only
HTTP interface so the "brain" (wherever your LLM decision loop actually
runs — same process, a separate script, or a remote call) can dispatch
verbs without knowing anything about tiers, risk, or subprocess mechanics.

Routes:
  POST /perceive/<verb>   {"args": {...}}   -> Tier A perceive call
  POST /act/<verb>         {"args": {...}}   -> Tier A act call (risk-gated)
  POST /watch/<verb>       {"args": {...}}   -> Tier B: start subscription, returns {"id": ...}
  GET  /watch/<id>                              -> Tier B: poll queued results
  DELETE /watch/<id>                            -> Tier B: stop subscription
  GET  /verbs                                    -> list catalog entries (for the brain to introspect)

Auth: every route requires an X-Agent-Token header. Token comes from the
AGENT_TOKEN env var if set, otherwise it is generated once into
.agent-token (chmod 600). Loopback-only is NOT private on Android — any
app can dial 127.0.0.1 — so this token is the actual access control.

Concurrency: ThreadingHTTPServer — each request gets its own thread, so a
high-risk verb blocking on a termux-dialog confirm never stalls watch
polls or other verbs.

Deliberately NOT exposed on any interface but loopback — this process
should never be reachable from anywhere but the device itself.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dispatch import risk_gate, tier_a, tier_c
from dispatch.catalog import Catalog
from dispatch.tier_b import SubscriptionManager, recover_orphans

HOST = "127.0.0.1"
PORT = 8477
ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / "verbs.yaml"
TOKEN_PATH = ROOT / ".agent-token"


def _load_or_create_token() -> str:
    env = os.environ.get("AGENT_TOKEN")
    if env:
        return env
    if TOKEN_PATH.exists():
        token = TOKEN_PATH.read_text().strip()
        if token:
            return token
    token = secrets.token_hex(32)
    TOKEN_PATH.write_text(token + "\n")
    TOKEN_PATH.chmod(0o600)
    return token


AUTH_TOKEN = _load_or_create_token()

catalog = Catalog.load(CATALOG_PATH)
subs = SubscriptionManager()


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


_MAX_BODY = 1_000_000  # 1 MiB — stdin payloads are text; refuse unbounded reads


def _read_body(handler: BaseHTTPRequestHandler) -> dict:
    raw_len = handler.headers.get("Content-Length", "0")
    try:
        length = int(raw_len)
    except ValueError:
        raise ValueError("invalid Content-Length")
    if length < 0:
        raise ValueError("invalid Content-Length")
    if length > _MAX_BODY:
        raise ValueError(f"body too large ({length} bytes, max {_MAX_BODY})")
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON body: {e}")
    if not isinstance(payload, dict):
        raise ValueError("body must be a JSON object")
    return payload


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass  # audit.log via risk_gate is the record of truth, not stdout

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Agent-Token", "")
        return hmac.compare_digest(supplied, AUTH_TOKEN)

    def do_GET(self):
        if not self._authorized():
            return _json_response(self, 401, {"error": "unauthorized"})
        path = self.path.split("?", 1)[0]
        parts = path.strip("/").split("/")

        if path.rstrip("/") == "/verbs":
            listing = {
                name: {
                    "direction": v.direction,
                    "tier": v.tier,
                    "risk": v.risk,
                    "args": v.args,
                    **({"stdin": v.stdin} if v.stdin else {}),
                }
                for name, v in catalog.verbs.items()
            }
            return _json_response(self, 200, listing)

        if len(parts) == 2 and parts[0] == "watch":
            try:
                result = subs.poll(parts[1])
                return _json_response(self, 200, result)
            except KeyError as e:
                return _json_response(self, 404, {"error": str(e)})

        return _json_response(self, 404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return _json_response(self, 401, {"error": "unauthorized"})

        parts = self.path.split("?", 1)[0].strip("/").split("/")
        if len(parts) != 2:
            return _json_response(self, 404, {"error": "not found"})

        kind, verb_name = parts
        if kind not in ("perceive", "act", "watch"):
            return _json_response(self, 404, {"error": "not found"})

        try:
            body = _read_body(self)
        except ValueError as e:
            return _json_response(self, 400, {"error": str(e)})
        args = body.get("args", {})
        if not isinstance(args, dict):
            return _json_response(self, 400, {"error": "'args' must be an object"})

        # Failure ordering is deliberate:
        #   404 unknown verb -> 400 route/direction/tier contract -> 400
        #   payload -> 403 risk gate -> 500 execution. Never make a human
        #   tap through a confirm dialog for a call the machine can already
        #   prove is malformed.
        try:
            verb = catalog.get(verb_name)
        except KeyError as e:
            return _json_response(self, 404, {"error": str(e)})

        # Tier C routing stays wired even though Catalog.load currently
        # hard-rejects tier C entries, which makes this branch unreachable
        # today. Kept on purpose: when the companion AccessibilityService
        # app lands and tier C becomes a representable tier, this path
        # starts working with no daemon changes. See dispatch/tier_c.py.
        if verb.tier not in ("A", "B"):
            try:
                tier_c.run(verb_name, args)
            except tier_c.TierCNotImplemented as e:
                return _json_response(self, 501, {"error": str(e)})

        if kind in ("perceive", "act"):
            if verb.tier != "A" or verb.direction != kind:
                return _json_response(
                    self, 400,
                    {"error": f"{verb_name}: tier {verb.tier} direction "
                              f"{verb.direction!r} does not support route kind {kind!r}"},
                )
        elif verb.tier != "B":
            # watch: direction deliberately unchecked — Tier B spans
            # perceive (sensor/location streams) and act (mic.record);
            # the tier B start/stop lifecycle is the contract here.
            return _json_response(
                self, 400,
                {"error": f"{verb_name}: tier {verb.tier} does not support route kind {kind!r}"},
            )

        # Validate the payload BEFORE the risk gate, so a malformed
        # high-risk call 400s instead of popping a confirm dialog.
        # tier_a.run / tier_b start build argv again from the same spec.
        try:
            verb.build_argv(args)
            verb.stdin_payload(args)
        except ValueError as e:
            return _json_response(self, 400, {"error": str(e)})

        try:
            risk_gate.check(catalog, verb_name, args)
        except risk_gate.Denied as e:
            return _json_response(self, 403, {"error": str(e)})

        if kind == "watch":
            logged = verb.public_args(args)
            try:
                sub_id = subs.start(verb, args)
            except OSError as e:
                risk_gate.audit({"verb": verb_name, "risk": verb.risk, "args": logged,
                                 "stage": "failed", "error": str(e)})
                return _json_response(self, 500, {"error": f"{verb_name}: {e}"})
            risk_gate.audit({"verb": verb_name, "risk": verb.risk, "args": logged,
                             "stage": "executed", "subscription": sub_id})
            return _json_response(self, 200, {"id": sub_id})

        logged = verb.public_args(args)
        try:
            result = tier_a.run(verb, args)
        except tier_a.ExecutionError as e:
            risk_gate.audit({"verb": verb_name, "risk": verb.risk, "args": logged,
                             "stage": "failed", "error": str(e), "stderr": e.stderr[:500]})
            return _json_response(self, 500, {"error": str(e), "stderr": e.stderr})
        except ValueError as e:
            risk_gate.audit({"verb": verb_name, "risk": verb.risk, "args": logged,
                             "stage": "failed", "error": str(e)})
            return _json_response(self, 400, {"error": str(e)})
        risk_gate.audit({"verb": verb_name, "risk": verb.risk, "args": logged, "stage": "executed"})
        return _json_response(self, 200, result)

    def do_DELETE(self):
        if not self._authorized():
            return _json_response(self, 401, {"error": "unauthorized"})
        parts = self.path.split("?", 1)[0].strip("/").split("/")
        if len(parts) == 2 and parts[0] == "watch":
            try:
                verb_name = subs.stop(parts[1])
            except KeyError as e:
                return _json_response(self, 404, {"error": str(e)})
            risk_gate.audit({"verb": verb_name, "stage": "stopped", "subscription": parts[1]})
            return _json_response(self, 200, {"ok": True})
        return _json_response(self, 404, {"error": "not found"})


def main():
    subprocess.run(["termux-wake-lock"], check=False)
    # Kill any termux-sensor / termux-location / termux-microphone-record
    # left running by a previous crash, via logs/subscriptions.pids.
    orphans = recover_orphans()
    if orphans:
        print(f"recovered {len(orphans)} orphaned subscription process(es): {orphans}")
    print(f"agent daemon listening on {HOST}:{PORT} — {len(catalog.verbs)} verbs loaded")
    print(f"auth token: AGENT_TOKEN env var, or {TOKEN_PATH} (created on first run)")
    try:
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    finally:
        subprocess.run(["termux-wake-unlock"], check=False)


if __name__ == "__main__":
    main()
