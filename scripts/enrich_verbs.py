#!/usr/bin/env python3
"""One-shot: add description + args_schema to verbs.yaml. Source of truth after run is the YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "verbs.yaml"

DESCRIPTIONS = {
    "battery.status": "Battery percentage, charging state, health, and temperature.",
    "system.info": "Termux environment and package info (termux-info).",
    "audio.info": "Current audio output device and session info.",
    "brightness.get": "Official termux-brightness has no query mode; prints usage. Prefer Settings, not this verb.",
    "volume.get": "Current volume levels per stream (music, ring, alarm, …).",
    "wifi.connectionInfo": "Current Wi-Fi SSID, BSSID, IP, and RSSI.",
    "wifi.scanInfo": "Nearby Wi-Fi scan results.",
    "telephony.deviceInfo": "Telephony device identifiers and network operator.",
    "telephony.cellInfo": "Serving and neighbor cell tower info.",
    "usb.list": "List attached USB devices (termux-usb -l).",
    "usb.request": "Request access to a USB device path (termux-usb -r).",
    "location.get": "One-shot location from gps, network, or passive provider.",
    "sensor.list": "List sensors this device exposes.",
    "sensor.read": "Read a single sample from one named sensor.",
    "nfc.read": "Stream NFC tag reads until the watch is stopped.",
    "infrared.frequencies": "IR blaster carrier frequencies this device supports.",
    "contacts.list": "Address-book contacts. Medium privacy risk.",
    "sms.list": "SMS inbox/sent/draft/outbox messages. Medium privacy risk.",
    "call.log": "Call log entries. Medium privacy risk.",
    "notification.list": "Notifications currently in the shade.",
    "camera.info": "Available camera IDs and capabilities.",
    "tts.engines": "Installed text-to-speech engines.",
    "clipboard.get": "Current clipboard text.",
    "saf.dirs": "Storage Access Framework tree roots Termux may use.",
    "saf.ls": "List a SAF directory.",
    "saf.read": "Read a SAF document URI as text.",
    "saf.stat": "Stat a SAF path.",
    "brightness.set": "Set screen brightness 0–255 or auto. Write-only CLI.",
    "volume.set": "Set volume for a named stream.",
    "wifi.enable": "Enable or disable Wi-Fi (true/false).",
    "torch.toggle": "Turn the camera flash torch on or off.",
    "vibrate": "Vibrate the device for the given milliseconds.",
    "toast.show": "Show a short on-screen toast.",
    "wallpaper.set": "Set wallpaper from a local file.",
    "wallpaper.set.url": "Set wallpaper from an HTTP(S) URL.",
    "fingerprint.auth": "Prompt for fingerprint. High risk; on-device confirm.",
    "keystore.list": "List Android keystore aliases. High risk; on-device confirm.",
    "keystore.generate": "Generate a hardware keystore key. High risk; on-device confirm.",
    "keystore.delete": "Delete a keystore alias. High risk; on-device confirm.",
    "keystore.sign": "Sign stdin bytes with a keystore key. High risk; on-device confirm.",
    "keystore.verify": "Verify a signature against stdin bytes. High risk; on-device confirm.",
    "nfc.write": "Write an NDEF text record; watch until the tag is presented.",
    "infrared.transmit": "Transmit an IR pattern at a carrier frequency.",
    "clipboard.set": "Replace clipboard contents.",
    "notification.post": "Post a status-bar notification.",
    "notification.channel.create": "Create a notification channel.",
    "notification.channel.delete": "Delete a notification channel.",
    "notification.remove": "Remove a posted notification by id.",
    "media.info": "Current termux-media-player state.",
    "media.play": "Play a local media file.",
    "media.pause": "Pause playback.",
    "media.resume": "Resume playback (termux-media-player play).",
    "media.stop": "Stop playback.",
    "media.scan": "Ask MediaStore to scan a file.",
    "tts.speak": "Speak text with the default TTS engine.",
    "sms.send": "Send an SMS. High risk; on-device confirm; requires Idempotency-Key.",
    "call.place": "Place a phone call. High risk; on-device confirm; requires Idempotency-Key.",
    "camera.photo": "Capture a still image. High risk; on-device confirm; requires Idempotency-Key.",
    "storage.get": "Android storage picker; writes the chosen file to the given path.",
    "saf.managedir": "Prompt the user to grant a SAF directory.",
    "saf.mkdir": "Create a directory under a SAF parent URI.",
    "saf.create": "Create a document in a SAF folder.",
    "saf.write": "Write stdin content to a SAF document URI.",
    "saf.rm": "Remove a SAF path.",
    "share.send": "Legacy ACTION_VIEW share of a file (prefer share.file.*).",
    "share.file.send": "ACTION_SEND a file.",
    "share.file.view": "ACTION_VIEW a file.",
    "share.file.edit": "ACTION_EDIT a file.",
    "share.text": "ACTION_SEND text (body on stdin).",
    "url.open": "Open a URL in the default handler.",
    "url.open.in": "Open a URL in a specific package.",
    "file.open": "Open a path with termux-open (VIEW).",
    "file.open.send": "Open a path with --send.",
    "file.open.chooser": "Open a path with --chooser (VIEW only).",
    "download.fetch": "Enqueue a download of a URL.",
    "sensor.stream": "Stream named-sensor samples until the watch is stopped.",
    "location.watch": "Stream location updates until the watch is stopped.",
    "microphone.record": "Record microphone audio. High risk; on-device confirm; requires Idempotency-Key.",
    "microphone.stop": "Stop an in-progress microphone recording.",
    "microphone.info": "Microphone recorder status.",
    "dialog.show": "Show a termux-dialog widget and stream the result.",
    "stt.listen": "Speech-to-text until the watch is stopped.",
    "job.schedule": "Schedule a Termux job for a script path.",
    "job.pending": "List pending Termux jobs.",
    "job.cancel": "Cancel a job by id.",
    "job.cancel.all": "Cancel all scheduled jobs.",
}

ARG_DESC = {
    "text": "Message body or toast/TTS/share/NFC text.",
    "value": "Numeric level, or 'auto' for brightness.",
    "stream": "Volume stream: music, alarm, notification, ring, system, call.",
    "state": "On/off or true/false as the official script expects.",
    "ms": "Duration in milliseconds.",
    "file": "Local filesystem path.",
    "url": "HTTP(S) URL.",
    "alias": "Android keystore alias.",
    "algorithm": "Signature algorithm (e.g. SHA256withECDSA).",
    "data": "Bytes piped on stdin (redacted in audit/confirm).",
    "signature": "Detached signature to verify.",
    "frequency": "IR carrier frequency in Hz.",
    "pattern": "Comma-separated on/off IR pulse pattern.",
    "title": "Notification title or channel title.",
    "content": "Notification body, or SAF write payload on stdin.",
    "id": "Notification, channel, or job identifier.",
    "number": "E.164 or local phone number.",
    "camera_id": "Camera id from camera.info (usually 0 or 1).",
    "outfile": "Destination file path.",
    "path": "Filesystem or SAF path.",
    "uri": "SAF document URI.",
    "parent_uri": "SAF parent directory URI.",
    "folder_uri": "SAF folder URI.",
    "name": "Sensor, file, directory, or dialog name.",
    "package": "Android package name.",
    "provider": "Location provider: gps, network, or passive.",
    "type": "SMS folder: inbox, sent, draft, outbox, or all.",
    "device": "USB device path from usb.list.",
    "detail": "NFC read detail: full or info.",
    "seconds": "Recording length in seconds.",
    "widget": "termux-dialog widget (confirm, text, radio, …).",
    "script": "Script path for termux-job-scheduler.",
    "job_id": "termux-job-scheduler job id.",
}

ENUMS = {
    "provider": ["gps", "network", "passive"],
    "type": ["all", "inbox", "sent", "draft", "outbox"],
    "stream": ["music", "alarm", "notification", "ring", "system", "call"],
    "state": None,  # per-verb
    "detail": ["full", "info"],
}

VERB_ENUMS = {
    "wifi.enable": {"state": ["true", "false"]},
    "torch.toggle": {"state": ["on", "off"]},
}


def schema_for(name: str, args: list[str], stdin: str | None) -> dict:
    props = {}
    for a in args:
        spec: dict = {"type": "string", "description": ARG_DESC.get(a, a)}
        if stdin == a:
            spec["description"] = spec["description"] + " Piped on stdin; redacted in logs."
        enums = (VERB_ENUMS.get(name) or {}).get(a)
        if enums is None and a in ENUMS and ENUMS[a]:
            enums = ENUMS[a]
        if enums:
            spec["enum"] = list(enums)
        props[a] = spec
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": props,
        "required": list(args),
    }


class FlowList(list):
    pass


def represent_flow_list(dumper, data):
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


def represent_str(dumper, data):
    if any(c in data for c in ":#{}[]&*!|>%@`") or data == "" or data.lower() in {"true", "false", "null", "yes", "no"}:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="'")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(FlowList, represent_flow_list)


def main() -> None:
    raw = yaml.safe_load(PATH.read_text())
    verbs = raw["verbs"]
    missing = set(verbs) - set(DESCRIPTIONS)
    extra = set(DESCRIPTIONS) - set(verbs)
    if missing or extra:
        raise SystemExit(f"description mismatch missing={sorted(missing)} extra={sorted(extra)}")

    ordered = {}
    for name, spec in verbs.items():
        args = list(spec.get("args") or [])
        stdin = spec.get("stdin")
        entry = {
            "direction": spec["direction"],
            "tier": spec["tier"],
            "risk": spec["risk"],
            "command": FlowList(spec["command"]),
            "args": FlowList(args),
        }
        if stdin:
            entry["stdin"] = stdin
        entry["parser"] = spec["parser"]
        entry["timeout"] = spec.get("timeout")
        entry["description"] = DESCRIPTIONS[name]
        entry["args_schema"] = schema_for(name, args, stdin)
        ordered[name] = entry

    # Hand-emit to keep a stable, grep-friendly layout.
    lines = ["# Compact machine catalog. Comments live in docs/verb-catalog.md.", "verbs:"]
    for name, spec in ordered.items():
        lines.append(f"  {name}:")
        for key in ("direction", "tier", "risk"):
            lines.append(f"    {key}: {spec[key]}")
        cmd = spec["command"]
        cmd_s = "[" + ", ".join(_q(x) for x in cmd) + "]"
        lines.append(f"    command: {cmd_s}")
        args_s = "[" + ", ".join(args) + "]" if spec["args"] else "[]"
        # quote arg names that need it — none do
        if spec["args"]:
            lines.append("    args: [" + ", ".join(spec["args"]) + "]")
        else:
            lines.append("    args: []")
        if "stdin" in spec:
            lines.append(f"    stdin: {spec['stdin']}")
        lines.append(f"    parser: {spec['parser']}")
        timeout = spec["timeout"]
        lines.append(f"    timeout: {timeout if timeout is not None else 'null'}")
        lines.append(f"    description: {_q(spec['description'])}")
        schema = spec["args_schema"]
        lines.append("    args_schema:")
        lines.append("      type: object")
        lines.append("      additionalProperties: false")
        if schema["properties"]:
            lines.append("      properties:")
            for pname, pspec in schema["properties"].items():
                lines.append(f"        {pname}:")
                lines.append(f"          type: {pspec['type']}")
                lines.append(f"          description: {_q(pspec['description'])}")
                if "enum" in pspec:
                    enums = ", ".join(_q(x) for x in pspec["enum"])
                    lines.append(f"          enum: [{enums}]")
        else:
            lines.append("      properties: {}")
        if schema["required"]:
            lines.append("      required: [" + ", ".join(schema["required"]) + "]")
        else:
            lines.append("      required: []")
    confirm = raw.get("confirmation_required_for") or ["high"]
    lines.append("confirmation_required_for: [" + ", ".join(confirm) + "]")
    lines.append("")
    PATH.write_text("\n".join(lines))
    print(f"wrote {PATH} ({len(ordered)} verbs)")


def _q(s: str) -> str:
    if s is None:
        return "null"
    escaped = s.replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


if __name__ == "__main__":
    main()
