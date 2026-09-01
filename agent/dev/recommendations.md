The dispatcher’s split is right: **this process is the body; the model is the brain**. Improvements should keep that, and make the body safer, more self-describing, and cheaper for a planner to use.

## Use cases this should own

**1. On-device copilot (you, now)**  
A local/remote model asks the phone for context, then acts. Typical loop: battery + wifi + notifications + clipboard → toast/TTS/notification. High-risk SMS/keystore stay behind a tap.

**2. Hands-free / pocket operator**  
TTS + vibrate + toast as the output channel; sensors + location + camera.info as input. Good for walking, driving (read-only), or “what’s on my lock screen.”

**3. Field kit**  
Location, cell/wifi, torch, camera, USB, IR. Treat as a small robot API, not a shell.

**4. Notification secretary**  
`notification.list` → summarize → `notification.remove` / channel ops. This is the highest everyday value after battery/toast.

**5. Watchdog**  
Tier B: light/accel/location streams, mic.record. Brain polls, decides, acts (vibrate if drop, torch if dark, notify if still).

**6. Explicitly not**  
General UI automation (Tier C), “the LLM may SMS anyone,” or folding an agent loop into `daemon.py`. That would collapse the permission boundary you designed.

---

## What the tire-kick showed

| Issue | Why it matters |
|---|---|
| `brightness.get` returns usage text | CLI is write-only; catalog still advertises a perceive verb |
| `volume.get` is `parser: text` but stdout is JSON | Brain has to `json.loads` a string |
| Watch after `DELETE` is **200 + `stopped: true`**, not 404 | README smoke test is wrong; brains will retry/error |
| `sensor.read` 15s timeout; light stream empty | `-n 1` can hang; names must come from `sensor.list`, not guesses |
| Missing args → 400 with the real key (`value` vs `level`) | Good. Need that in `GET /verbs` more loudly |
| Install copied into a **pre-existing `~/agent`** | Dispatcher mixed with other trees; boot `cd ~/agent` is fragile |

Auth, route contracts, clipboard round-trip, toast/TTS/vibrate, torch, volume.set, location, notification list growth, and audit-before-execute all behaved.

---

## Recommendations (priority)

**Contract / brain UX**
- `GET /health`: pid, uptime, wake-lock, termux-api present, active watches.
- `GET /verbs` should include `route`, example `args`, `parser`, and `timeout` — not just direction/tier/risk. That’s how a model stops inventing `vibrate.once` / `level`.
- `GET /watch` list active ids; document grace reaper (60s) and `stopped`.
- Batch perceive: `POST /perceive` with `["battery.status","wifi.connectionInfo",…]` for briefings (one RTT, parallel threads you already have).
- Normalize JSON: if stdout is JSON, `parser: json` even when the script is sloppy.

**Catalog honesty**
- Drop or fix `brightness.get` (or map it to Settings via a different API).
- Add `sensor.list` → allowed `name` enum at runtime so watch/read fail 400 with “unknown sensor” instead of hang.
- Default `sensor.read` to a short `-d` duration, not unbounded wait.
- Optional `id` on `notification.post` so remove is a real loop.

**Watch / Tier B**
- First poll often empty; add `min_wait_ms` or block-up-to-N on GET.
- Cap concurrent subs; kill on daemon restart (you have pidfile — use it on boot more visibly).
- SSE or `?wait=2` long-poll so the brain isn’t a 2Hz curl loop (battery).

**Risk / privacy**
- Profiles: `strict` (confirm medium+), `daily` (confirm high only), `locked` (perceive-only). File on device, not a header the model can set.
- Coarsen location in audit (`~0.01°`) and in confirm text.
- Rate-limit high/medium verbs; one SMS confirm must not unlock a burst.
- Clipboard/SMS list: never put full bodies in audit (you redact stdin; extend that).

**Ops on a real phone**
- Dedicated dir (`~/termux-agent-dispatcher`) — don’t occupy `~/agent`.
- `termux-wake-lock` in the boot script (docstring promises it; boot script doesn’t).
- Probe `termux-battery-status` at start; refuse to listen if Termux:API is missing.
- Log rotation on `audit.log` / `daemon.out`.
- Don’t run the daemon as root/proot if Termux:API is the `u0_a*` app — we saw mixed uids; keep one Termux user.

**Tests**
- Contract tests for 401/404/400 and watch lifecycle (the 200-after-stop case).
- Fixture argv builder tests so YAML templates don’t drift from `termux-api-package` scripts.

---

## Product-shaped next verbs (still YAML-only)

Worth adding when you have a real script: `share.send`, `download`, `sms.send` already gated, `telephony.call` (high), `notification.reply` if it exists, `media-scan`. Skip calendar/contacts until you want that data in the brain’s context window.

Tier C stays out until an AccessibilityService exists; don’t fake taps with `input` over ADB.

---

## How I’d use it from a brain

One shot: `GET /verbs` → cache.  
Loop: batch perceive (battery, notifications, clipboard) → plan → 0–2 acts → if streaming, one watch with backoff poll.  
Never: raw `termux-*`, never high-risk without expecting a 2-minute confirm deny.

If you want to implement next, the highest leverage trio is **`/health` + richer `/verbs` + batch perceive**, then catalog fixes for brightness/sensors/volume parser.
