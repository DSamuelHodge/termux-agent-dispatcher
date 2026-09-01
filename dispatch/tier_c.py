"""
Tier C: UI automation (ui.screen.read, ui.tap, ui.type, ui.gesture).

Deliberately NOT implemented here. Termux:API has no equivalent for any
of this — it requires an AccessibilityService, which a Termux shell
process cannot host. This file exists so the dispatcher fails loudly and
specifically, instead of a Tier C verb silently 404ing or being confused
with a missing Tier A/B catalog entry.

To implement this tier, you need a separate companion Android app that:
  1. Registers an AccessibilityService
  2. Exposes a local interface (Unix domain socket or loopback HTTP)
     for read (screen tree) and act (tap/type/gesture) calls
  3. Re-resolves targets by selector at act-time rather than trusting
     a tree read earlier in the loop, since nodes can move or vanish
     between perceive() and act() — this is the one tier where those
     two are entangled, per the design note in the catalog.

Once that companion app exists, this module should proxy to it rather
than reimplement anything — the daemon here should stay a pure dispatcher.
"""


class TierCNotImplemented(Exception):
    def __init__(self, verb_name: str):
        super().__init__(
            f"{verb_name}: Tier C (UI automation) requires a companion "
            f"AccessibilityService app that does not exist yet in this "
            f"deployment. Termux:API cannot provide ui.tap/ui.type/"
            f"ui.gesture/ui.screen.read — see dispatch/tier_c.py."
        )


def run(verb_name: str, args: dict) -> None:
    raise TierCNotImplemented(verb_name)
