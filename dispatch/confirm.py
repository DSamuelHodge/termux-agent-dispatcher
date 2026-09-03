"""Async on-device confirmation.

High-risk verbs return 202 + a confirm_id instead of blocking the HTTP/MCP
request on termux-dialog. The phone user still taps Yes/No; the brain polls
GET /confirm/<id> (or MCP tool confirm.poll) or receives a webhook.

Application handles live in logs/agent.db — MCP itself stays stateless
(2026-07-28): the client passes confirm_id as an ordinary argument.
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from dispatch import risk_gate, store as verb_store
from dispatch.catalog import Catalog, Verb
from dispatch.errors import CONFIRM_DENIED, CONFIRM_NOT_FOUND, CONFIRM_PENDING, error_payload

ExecuteFn = Callable[[str, Verb, dict[str, Any]], tuple[int, dict[str, Any]]]

_WEBHOOK_TIMEOUT = 5
_ALLOWED_WEBHOOK_SCHEMES = {"http", "https"}


def webhook_ok(url: str | None) -> bool:
    if not url:
        return True
    if not isinstance(url, str) or len(url) > 2048:
        return False
    parsed = urlparse(url)
    return parsed.scheme in _ALLOWED_WEBHOOK_SCHEMES and bool(parsed.netloc)


def _post_webhook(url: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, default=str).encode()
    req = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=_WEBHOOK_TIMEOUT) as resp:
            resp.read()
    except Exception:
        pass


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "confirm_id": job["id"],
        "verb": job["verb"],
        "kind": job["kind"],
        "status": job["status"],
        "poll": f"/confirm/{job['id']}",
    }
    if job.get("risk"):
        out["risk"] = job["risk"]
    if job["status"] == "pending":
        out["code"] = CONFIRM_PENDING
    if job.get("result") is not None:
        out["result"] = job["result"]
    if job.get("error"):
        out["error"] = job["error"]
        out["code"] = job.get("error_code") or out.get("code")
    return out


class ConfirmManager:
    def __init__(self, catalog: Catalog, execute: ExecuteFn):
        self.catalog = catalog
        self._execute = execute
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()

    def get(self, job_id: str) -> dict[str, Any] | None:
        return verb_store.get_store().get_confirm_job(job_id)

    def fail_orphans(self) -> int:
        """Pending rows from a previous process cannot resume a dialog."""
        st = verb_store.get_store()
        n = 0
        for job_id in st.list_pending_confirms():
            job = st.get_confirm_job(job_id)
            if not job:
                continue
            st.put_confirm_job(
                job_id=job_id,
                verb=job["verb"],
                kind=job["kind"],
                risk=job["risk"],
                args=job["args"],
                status="failed",
                http_status=500,
                error="confirm job orphaned on daemon restart",
                error_code="EXECUTION_FAILED",
                webhook_url=job.get("webhook_url"),
                idempotency_key=job.get("idempotency_key"),
            )
            n += 1
        return n

    def submit(
        self,
        verb: Verb,
        args: dict[str, Any],
        *,
        kind: str,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:16]
        st = verb_store.get_store()
        st.put_confirm_job(
            job_id=job_id,
            verb=verb.name,
            kind=kind,
            risk=verb.risk,
            args=verb.public_args(args),
            status="pending",
            http_status=202,
            webhook_url=webhook_url,
            idempotency_key=idempotency_key,
        )
        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, verb, args, kind, webhook_url),
            daemon=True,
            name=f"confirm-{job_id}",
        )
        with self._lock:
            self._in_flight.add(job_id)
        thread.start()
        job = st.get_confirm_job(job_id)
        assert job is not None
        return public_job(job)

    def _run_job(
        self,
        job_id: str,
        verb: Verb,
        args: dict[str, Any],
        kind: str,
        webhook_url: str | None,
    ) -> None:
        st = verb_store.get_store()
        logged = verb.public_args(args)
        try:
            approved = risk_gate._confirm_on_device(verb.name, logged, verb.args)
            risk_gate.record_decision(verb.name, verb.risk, logged, approved)
            if not approved:
                denied = error_payload(
                    CONFIRM_DENIED,
                    f"{verb.name}: declined on-device (risk={verb.risk})",
                )
                st.put_confirm_job(
                    job_id=job_id,
                    verb=verb.name,
                    kind=kind,
                    risk=verb.risk,
                    args=logged,
                    status="denied",
                    http_status=403,
                    result=denied,
                    error=denied["error"],
                    error_code=CONFIRM_DENIED,
                    webhook_url=webhook_url,
                )
                self._notify(job_id)
                return

            http_status, payload = self._execute(kind, verb, args)
            failed = http_status >= 400
            st.put_confirm_job(
                job_id=job_id,
                verb=verb.name,
                kind=kind,
                risk=verb.risk,
                args=logged,
                status="failed" if failed else "executed",
                http_status=http_status,
                result=payload,
                error=payload.get("error") if failed else None,
                error_code=payload.get("code") if failed else None,
                webhook_url=webhook_url,
            )
            self._notify(job_id)
        except Exception as e:
            st.put_confirm_job(
                job_id=job_id,
                verb=verb.name,
                kind=kind,
                risk=verb.risk,
                args=logged,
                status="failed",
                http_status=500,
                error=str(e),
                error_code="EXECUTION_FAILED",
                webhook_url=webhook_url,
            )
            self._notify(job_id)
        finally:
            with self._lock:
                self._in_flight.discard(job_id)

    def _notify(self, job_id: str) -> None:
        job = verb_store.get_store().get_confirm_job(job_id)
        if not job or not job.get("webhook_url"):
            return
        _post_webhook(job["webhook_url"], public_job(job))
