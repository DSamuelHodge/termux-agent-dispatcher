"""
Loads verbs.yaml into typed Verb objects and gives the rest of the
dispatcher a single source of truth: add a verb to the YAML, it's live
everywhere (HTTP routing, risk gating, execution) with no new code.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

VALID_TIERS = {"A", "B"}  # Tier C is not representable here — see tier_c.py
VALID_DIRECTIONS = {"perceive", "act"}
VALID_RISKS = {"none", "low", "medium", "high"}
VALID_PARSERS = {"json", "text", "none", "json_stream"}


@dataclasses.dataclass(frozen=True)
class Verb:
    name: str
    direction: str          # perceive | act
    tier: str                # A | B
    risk: str                 # none | low | medium | high
    command: list[str]         # argv template
    args: list[str]              # required arg names, in order
    parser: str                    # json | text | none | json_stream
    timeout: float | None            # seconds, or None for long-running Tier B
    stdin: str | None = None         # args[] name whose value is piped to process stdin
                                     # (official scripts that read the payload from stdin:
                                     # termux-saf-write, termux-keystore sign/verify)

    def build_argv(self, supplied: dict[str, Any]) -> list[str]:
        missing = [a for a in self.args if a not in supplied]
        if missing:
            raise ValueError(f"{self.name}: missing required args {missing}")
        extra = [k for k in supplied if k not in self.args]
        if extra:
            raise ValueError(f"{self.name}: unexpected args {extra}")
        return [part.format(**supplied) for part in self.command]

    def stdin_payload(self, supplied: dict[str, Any]) -> str | None:
        """Value to pass as subprocess input, or None if this verb has no stdin hook."""
        if not self.stdin:
            return None
        value = supplied[self.stdin]
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError(
                f"{self.name}: stdin arg {self.stdin!r} must be a string, "
                f"got {type(value).__name__}"
            )
        return value

    def public_args(self, supplied: dict[str, Any]) -> dict[str, Any]:
        """Args safe to write to the audit log / confirm dialog (stdin body redacted)."""
        if not self.stdin or self.stdin not in supplied:
            return supplied
        out = dict(supplied)
        raw = out[self.stdin]
        n = len(raw) if isinstance(raw, str) else 0
        out[self.stdin] = f"<{n} chars>"
        return out


class Catalog:
    def __init__(self, verbs: dict[str, Verb], confirmation_required_for: set[str]):
        self.verbs = verbs
        self.confirmation_required_for = confirmation_required_for

    @classmethod
    def load(cls, path: str | Path) -> "Catalog":
        raw = yaml.safe_load(Path(path).read_text())
        verbs: dict[str, Verb] = {}
        for name, spec in raw["verbs"].items():
            tier = spec["tier"]
            risk = spec["risk"]
            parser = spec["parser"]
            direction = spec["direction"]
            command = spec["command"]
            if tier not in VALID_TIERS:
                raise ValueError(f"{name}: invalid tier {tier!r} (dispatcher only handles A/B)")
            if direction not in VALID_DIRECTIONS:
                raise ValueError(f"{name}: invalid direction {direction!r}")
            if risk not in VALID_RISKS:
                raise ValueError(f"{name}: invalid risk {risk!r}")
            if parser not in VALID_PARSERS:
                raise ValueError(f"{name}: invalid parser {parser!r}")
            if tier == "A" and parser == "json_stream":
                raise ValueError(f"{name}: json_stream is Tier B only")
            if not isinstance(command, list) or not command or not all(isinstance(p, str) for p in command):
                raise ValueError(f"{name}: command must be a non-empty list of strings")
            arg_names = spec.get("args", [])
            if not isinstance(arg_names, list) or not all(isinstance(a, str) for a in arg_names):
                raise ValueError(f"{name}: args must be a list of strings")
            stdin = spec.get("stdin")
            if stdin is not None and stdin not in arg_names:
                raise ValueError(
                    f"{name}: stdin {stdin!r} is not in args {arg_names}"
                )
            verbs[name] = Verb(
                name=name,
                direction=direction,
                tier=tier,
                risk=risk,
                command=command,
                args=arg_names,
                parser=parser,
                timeout=spec.get("timeout"),
                stdin=stdin,
            )
        confirm = set(raw.get("confirmation_required_for", []))
        bad_confirm = confirm - VALID_RISKS
        if bad_confirm:
            raise ValueError(f"confirmation_required_for has unknown risks {sorted(bad_confirm)}")
        return cls(verbs=verbs, confirmation_required_for=confirm)

    def get(self, name: str) -> Verb:
        if name not in self.verbs:
            raise KeyError(f"unknown verb: {name}")
        return self.verbs[name]

    def requires_confirmation(self, name: str) -> bool:
        return self.get(name).risk in self.confirmation_required_for
