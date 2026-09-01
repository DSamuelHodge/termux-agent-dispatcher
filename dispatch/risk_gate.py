"""
Risk gate: sits in front of every verb execution. This is deliberately
NOT something the brain can route around — it's middleware the dispatcher
calls unconditionally, keyed off the catalog's own risk field.

For risk levels in `confirmation_required_for`, the gate blocks and shows
a termux-dialog confirm prompt on-device. Only an explicit human "yes"
lets execution proceed. Every attempt — approved, denied, or bypassed by
a lower risk tier — is written to the audit log first, before the verb runs,
so a crash mid-execution still leaves a record of intent.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

from dispatch.catalog import Catalog
from dispatch import store as verb_store

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "audit.log"
_AUDIT_LOCK = threading.Lock()

_VALUE_MAX = 80
_HINT_MAX = 400
_TITLE_MAX = 60
_FOOTER = "Yes allows this. No denies it."

_INTENT = {
    "sms.send": "Send an SMS",
    "call.place": "Place a phone call",
    "camera.photo": "Take a photo",
    "microphone.record": "Record from the microphone",
    "fingerprint.auth": "Use the fingerprint sensor",
    "keystore.list": "List hardware keystore keys",
    "keystore.generate": "Create a hardware keystore key",
    "keystore.delete": "Delete a hardware keystore key",
    "keystore.sign": "Sign data with a keystore key",
    "keystore.verify": "Verify a keystore signature",
}

_ARG_LABEL = {
    "sms.send": {"number": "To", "text": "Message"},
    "call.place": {"number": "Number"},
    "camera.photo": {"camera_id": "Camera", "outfile": "Save as"},
    "microphone.record": {"outfile": "File", "seconds": "Seconds"},
    "keystore.generate": {"alias": "Alias"},
    "keystore.delete": {"alias": "Alias"},
    "keystore.sign": {"alias": "Alias", "algorithm": "Algorithm", "data": "Data"},
    "keystore.verify": {
        "alias": "Alias", "algorithm": "Algorithm",
        "signature": "Signature", "data": "Data",
    },
}


class Denied(Exception):
    """Raised when a confirmation-gated verb is declined on-device."""


def audit(event: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": time.time(), **event}
    line = json.dumps(event, default=str) + "\n"
    with _AUDIT_LOCK:
        with LOG_PATH.open("a") as f:
            f.write(line)
    stage = event.get("stage")
    if stage in verb_store.STAGES:
        try:
            verb_store.get_store().record_outcome(
                verb=event.get("verb") or "",
                stage=stage,
                risk=event.get("risk"),
                args=event.get("args") if isinstance(event.get("args"), dict) else {},
                error=event.get("error"),
            )
        except Exception:
            pass


def _truncate(s: str, n: int) -> str:
    if n <= 0:
        return ""
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _fallback_intent(name: str) -> str:
    return " ".join(p[:1].upper() + p[1:] for p in name.split("."))


def format_confirm_copy(verb_name: str, public_args: dict, arg_names: list) -> tuple[str, str]:
    intent = _INTENT.get(verb_name) or _fallback_intent(verb_name)
    title = _truncate(f"Allow: {intent}?", _TITLE_MAX)
    lead = f"The agent wants to {intent[:1].lower() + intent[1:]}."
    labels = _ARG_LABEL.get(verb_name, {})
    lines = []
    for key in arg_names:
        if key not in public_args:
            continue
        label = labels.get(key, key)
        lines.append(f"{label}: {_truncate(str(public_args[key]), _VALUE_MAX)}")
    hint = _truncate("\n".join([lead, *lines, _FOOTER]), _HINT_MAX)
    return title, hint


def _confirm_on_device(verb_name: str, public_args: dict, arg_names: list) -> bool:
    """
    Blocks on a termux-dialog confirm prompt shown on the device itself.
    This runs synchronously — the daemon thread handling this request
    waits for a human to tap yes/no on the phone.
    """
    title, hint = format_confirm_copy(verb_name, public_args, arg_names)
    try:
        proc = subprocess.run(
            ["termux-dialog", "confirm", "-t", title, "-i", hint],
            capture_output=True,
            text=True,
            timeout=120,  # human has 2 minutes to respond before this counts as a denial
        )
    except subprocess.TimeoutExpired:
        return False
    except FileNotFoundError:
        return False
    if proc.returncode != 0:
        return False
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False
    return result.get("text") == "yes"


def check(catalog: Catalog, verb_name: str, args: dict) -> None:
    """
    Raises Denied if this call should not proceed. Returns None (silently)
    if it's clear to execute. Always logs first.
    """
    verb = catalog.get(verb_name)
    needs_confirmation = catalog.requires_confirmation(verb_name)
    logged_args = verb.public_args(args)

    audit({
        "verb": verb_name,
        "risk": verb.risk,
        "args": logged_args,
        "confirmation_required": needs_confirmation,
        "stage": "requested",
    })

    verb_store.get_store().guard(verb_name, risk=verb.risk, args=logged_args)

    if not needs_confirmation:
        return

    approved = _confirm_on_device(verb_name, logged_args, verb.args)
    audit({
        "verb": verb_name,
        "risk": verb.risk,
        "args": logged_args,
        "stage": "approved" if approved else "denied",
    })

    if not approved:
        raise Denied(f"{verb_name}: declined on-device (risk={verb.risk})")
