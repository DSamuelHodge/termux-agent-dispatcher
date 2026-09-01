# Verb catalog

Human classification of the Termux:API surface used by this dispatcher
(direction, tier, risk). The machine-readable wiring is `verbs.yaml`
(73 verbs: 65 Tier A / 8 Tier B).

Copy a row into `verbs.yaml` with a real argv template and it is live on
the next daemon restart. AutoTask-only / Tier C rows (`notification.listen`,
`ui.*`, `foreground.app.get`) stay out of the YAML.

See the root README for install, auth, smoke tests, and the stdin hook.
