"""Shared perceive/act/watch execution used by HTTP and MCP."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from dispatch import risk_gate, store as verb_store, tier_a, tier_c
from dispatch.catalog import Catalog, Verb
from dispatch.confirm import ConfirmManager, webhook_ok
from dispatch.errors import (
    CIRCUIT_OPEN,
    CONFIRM_NOT_FOUND,
    EXECUTION_FAILED,
    IDEMPOTENCY_CONFLICT,
    INVALID_ARGS,
    INVALID_BODY,
    INVALID_ROUTE,
    MISSING_IDEMPOTENCY_KEY,
    TIMEOUT,
    TIER_C_UNAVAILABLE,
    UNKNOWN_VERB,
    error_payload,
)
from dispatch.tier_b import SubscriptionManager


def args_hash(args: dict[str, Any]) -> str:
    blob = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _idempotency_required(verb: Verb) -> bool:
    return verb.risk == "high" and (verb.direction == "act" or verb.tier == "B")


def dry_run_payload(catalog: Catalog, verb: Verb, args: dict[str, Any]) -> dict[str, Any]:
    argv = verb.build_argv(args)
    return {
        "ok": True,
        "dry_run": True,
        "verb": verb.name,
        "direction": verb.direction,
        "tier": verb.tier,
        "risk": verb.risk,
        "route": "watch" if verb.tier == "B" else verb.direction,
        "argv": argv,
        "stdin": verb.public_args(args).get(verb.stdin) if verb.stdin else None,
        "confirmation_required": catalog.requires_confirmation(verb.name),
        "idempotency_required": _idempotency_required(verb),
    }


def execute_kind(
    kind: str,
    verb: Verb,
    args: dict[str, Any],
    *,
    subs: SubscriptionManager,
) -> tuple[int, dict[str, Any]]:
    logged = verb.public_args(args)
    if kind == "watch":
        try:
            sub_id = subs.start(verb, args)
        except OSError as e:
            risk_gate.audit({
                "verb": verb.name, "risk": verb.risk, "args": logged,
                "stage": "failed", "error": str(e),
            })
            return 500, error_payload(EXECUTION_FAILED, f"{verb.name}: {e}")
        risk_gate.audit({
            "verb": verb.name, "risk": verb.risk, "args": logged,
            "stage": "executed", "subscription": sub_id,
        })
        return 200, {"id": sub_id}

    try:
        result = tier_a.run(verb, args)
    except tier_a.ExecutionError as e:
        stage = "timeout" if "timed out" in str(e) else "failed"
        code = TIMEOUT if stage == "timeout" else EXECUTION_FAILED
        risk_gate.audit({
            "verb": verb.name, "risk": verb.risk, "args": logged,
            "stage": stage, "error": str(e), "stderr": e.stderr[:500],
        })
        status = 500
        return status, error_payload(code, str(e), stderr=e.stderr)
    except ValueError as e:
        risk_gate.audit({
            "verb": verb.name, "risk": verb.risk, "args": logged,
            "stage": "failed", "error": str(e),
        })
        return 400, error_payload(INVALID_ARGS, str(e))
    risk_gate.audit({
        "verb": verb.name, "risk": verb.risk, "args": logged, "stage": "executed",
    })
    return 200, result


def dispatch(
    catalog: Catalog,
    subs: SubscriptionManager,
    confirms: ConfirmManager,
    verb_name: str,
    args: dict[str, Any],
    *,
    kind: str,
    dry_run: bool = False,
    idempotency_key: str | None = None,
    webhook_url: str | None = None,
) -> tuple[int, dict[str, Any]]:
    try:
        verb = catalog.get(verb_name)
    except KeyError as e:
        return 404, error_payload(UNKNOWN_VERB, str(e))

    if verb.tier not in ("A", "B"):
        try:
            tier_c.run(verb_name, args)
        except tier_c.TierCNotImplemented as e:
            return 501, error_payload(TIER_C_UNAVAILABLE, str(e))

    if kind in ("perceive", "act"):
        if verb.tier != "A" or verb.direction != kind:
            return 400, error_payload(
                INVALID_ROUTE,
                f"{verb_name}: tier {verb.tier} direction "
                f"{verb.direction!r} does not support route kind {kind!r}",
            )
    elif kind == "watch":
        if verb.tier != "B":
            return 400, error_payload(
                INVALID_ROUTE,
                f"{verb_name}: tier {verb.tier} does not support route kind {kind!r}",
            )
    else:
        return 404, error_payload(INVALID_ROUTE, "not found")

    try:
        verb.validate_args(args)
    except ValueError as e:
        return 400, error_payload(INVALID_ARGS, str(e))

    if webhook_url and not webhook_ok(webhook_url):
        return 400, error_payload(INVALID_BODY, "webhook_url must be http(s)")

    if dry_run:
        return 200, dry_run_payload(catalog, verb, args)

    if _idempotency_required(verb) and not idempotency_key:
        return 400, error_payload(
            MISSING_IDEMPOTENCY_KEY,
            f"{verb_name}: Idempotency-Key is required for risk={verb.risk} act/watch",
        )

    digest = args_hash(args)
    st = verb_store.get_store()
    if idempotency_key:
        if not isinstance(idempotency_key, str) or not (1 <= len(idempotency_key) <= 256):
            return 400, error_payload(INVALID_BODY, "idempotency_key must be 1–256 chars")
        prior = st.get_idempotency(verb_name, idempotency_key)
        if prior:
            if prior["args_hash"] != digest:
                return 409, error_payload(
                    IDEMPOTENCY_CONFLICT,
                    f"{verb_name}: idempotency key reused with different args",
                )
            cid = prior.get("confirm_id")
            if cid:
                from dispatch.confirm import public_job
                job = confirms.get(cid)
                if job:
                    body = public_job(job)
                    if job["status"] == "pending":
                        return 202, body
                    stored = job.get("result")
                    if isinstance(stored, dict):
                        return int(job.get("http_status") or 200), stored
                    return int(job.get("http_status") or 200), body
            return prior["http_status"], prior["response"]

    try:
        risk_gate.precheck(catalog, verb_name, args)
    except verb_store.CircuitOpen as e:
        return 500, error_payload(CIRCUIT_OPEN, str(e))

    if catalog.requires_confirmation(verb_name):
        payload = confirms.submit(
            verb, args, kind=kind,
            webhook_url=webhook_url,
            idempotency_key=idempotency_key,
        )
        if idempotency_key:
            st.put_idempotency(
                verb=verb_name, key=idempotency_key, args_hash=digest,
                http_status=202, response=payload, confirm_id=payload["confirm_id"],
            )
        return 202, payload

    status, payload = execute_kind(kind, verb, args, subs=subs)
    if idempotency_key:
        st.put_idempotency(
            verb=verb_name, key=idempotency_key, args_hash=digest,
            http_status=status, response=payload,
        )
    return status, payload


def poll_confirm(confirms: ConfirmManager, job_id: str) -> tuple[int, dict[str, Any]]:
    from dispatch.confirm import public_job

    job = confirms.get(job_id)
    if not job:
        return 404, error_payload(CONFIRM_NOT_FOUND, f"unknown confirm id: {job_id}")
    body = public_job(job)
    # Poll stays 200 so clients can loop on one contract; terminal HTTP
    # status of the verb is in result / http_status of the stored job.
    body["verb_http_status"] = job.get("http_status")
    return 200, body
