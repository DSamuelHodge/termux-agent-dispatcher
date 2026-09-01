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


def _confirm_on_device(verb_name: str, args: dict) -> bool:
    """
    Blocks on a termux-dialog confirm prompt shown on the device itself.
    This runs synchronously — the daemon thread handling this request
    waits for a human to tap yes/no on the phone.
    """
    hint = f"{verb_name}({json.dumps(args, default=str)})"
    try:
        proc = subprocess.run(
            ["termux-dialog", "confirm", "-t", "Agent action requires approval", "-i", hint],
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

    approved = _confirm_on_device(verb_name, logged_args)
    audit({
        "verb": verb_name,
        "risk": verb.risk,
        "args": logged_args,
        "stage": "approved" if approved else "denied",
    })

    if not approved:
        raise Denied(f"{verb_name}: declined on-device (risk={verb.risk})")
