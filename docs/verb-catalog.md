# Verb catalog

Human classification of the Termux:API and termux-tools surface used by
this dispatcher (direction, tier, risk). The machine-readable wiring is
`verbs.yaml` (86 verbs: 78 Tier A / 8 Tier B).

Most rows wrap Termux:API (`termux-*` scripts from `termux-api-package`).
`url.open`, `url.open.in`, and `file.open*` wrap termux-tools
(`termux-open-url`, `termux-open`) so a brain can open a URL or file in
another app without a new dispatcher mechanism.

Copy a row into `verbs.yaml` with a real argv template and it is live on
the next daemon restart. AutoTask-only / Tier C rows (`notification.listen`,
`ui.*`, `foreground.app.get`) stay out of the YAML.

See the root README for install, auth, smoke tests, and the stdin hook.

On-device confirm is prose (`Allow: {intent}?` plus labeled public args), not JSON.

## Share and open

- `share.send` is a legacy frozen default VIEW (`termux-share {file}` with
  no `-a`). Prefer `share.file.view` / `share.file.send` / `share.file.edit`
  for explicit ACTION_VIEW / ACTION_SEND / ACTION_EDIT.
- `share.text` is ACTION_SEND with the body on stdin (`stdin: text`), risk
  low. Do not put the text on argv.
- `file.open` is VIEW via `termux-open {path}`. `file.open.send` adds
  `--send`. `file.open.chooser` is VIEW-only in v1 (`--chooser`); there is
  no send+chooser sibling.

## USB

- `usb.list` is `-l`. `usb.request` uses `-r` (`termux-usb -r {device}`),
  risk medium, parser text.

## Official argv notes

- `termux-brightness` always **sets** `0–255|auto`. `brightness.set` is
  `termux-brightness {value}`. The script has no query mode, so
  `brightness.get` cannot read current brightness (no-arg prints usage).
- `infrared.transmit` is `termux-infrared-transmit -f {frequency} {pattern}`.
