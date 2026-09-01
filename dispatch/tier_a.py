"""
Tier A: stateless request/response. Build argv from the template, run it,
parse stdout, return a typed result. No retry logic needed beyond normal
transient-failure handling — the catalog's own note holds here: every one
of these commands has a real success/failure verdict, so a failure means
something failed, not that the read went stale.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from dispatch.catalog import Verb


class ExecutionError(Exception):
    def __init__(self, verb_name: str, message: str, stderr: str = ""):
        self.verb_name = verb_name
        self.stderr = stderr
        super().__init__(f"{verb_name}: {message}")


def run(verb: Verb, args: dict[str, Any]) -> dict[str, Any]:
    if verb.tier != "A":
        raise ValueError(f"{verb.name}: not a Tier A verb (tier={verb.tier})")

    argv = verb.build_argv(args)
    stdin_data = verb.stdin_payload(args)
    timeout = verb.timeout if verb.timeout is not None else 30

    try:
        proc = subprocess.run(
            argv,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ExecutionError(verb.name, f"timed out after {timeout}s")
    except FileNotFoundError:
        raise ExecutionError(
            verb.name,
            f"command not found: {argv[0]} (is Termux:API installed?)",
        )

    if proc.returncode != 0:
        raise ExecutionError(verb.name, f"exit code {proc.returncode}", stderr=proc.stderr)

    stdout = proc.stdout.strip()

    if verb.parser == "none":
        return {"ok": True}
    if verb.parser == "text":
        return {"ok": True, "data": stdout}
    if verb.parser == "json":
        if not stdout:
            return {"ok": True, "data": None}
        try:
            return {"ok": True, "data": json.loads(stdout)}
        except json.JSONDecodeError:
            raise ExecutionError(verb.name, "expected JSON stdout, got unparseable output",
                                  stderr=stdout[:500])

    raise ValueError(f"{verb.name}: parser {verb.parser!r} is not valid for Tier A")
