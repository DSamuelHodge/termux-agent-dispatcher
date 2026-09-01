"""
Tier B: stateful or streaming verbs — termux-sensor without -n,
termux-location -r updates, termux-microphone-record for its duration.
These need an explicit start/stop lifecycle rather than a single call,
so the daemon doesn't leave `termux-sensor -s ...` running unmonitored
forever, and so a caller can drain results as they arrive instead of
blocking until the process exits (which, for -r updates, is never).
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dispatch.catalog import Verb

# Bounded per-subscription buffer. If the brain polls slower than the
# stream produces, the oldest items are dropped instead of growing RAM
# without limit; poll() reports the drop count so the brain can react
# (poll faster, re-subscribe, etc.).
QUEUE_MAX = 500

# A stopped subscription stays poll-able this long (final drain) before
# the reaper removes it from the registry.
REAP_GRACE_S = 60.0
REAP_INTERVAL_S = 30.0

PIDFILE = Path(__file__).resolve().parent.parent / "logs" / "subscriptions.pids"


@dataclass
class Subscription:
    id: str
    verb_name: str
    process: subprocess.Popen
    queue: "queue.Queue[Any]" = field(default_factory=lambda: queue.Queue(maxsize=QUEUE_MAX))
    reader_thread: threading.Thread | None = None
    stopped: bool = False
    stopped_at: float | None = None
    dropped: int = 0


class SubscriptionManager:
    def __init__(self):
        self._subs: dict[str, Subscription] = {}
        self._lock = threading.Lock()
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True)
        self._reaper.start()

    def start(self, verb: Verb, args: dict[str, Any]) -> str:
        if verb.tier != "B":
            raise ValueError(f"{verb.name}: not a Tier B verb (tier={verb.tier})")

        argv = verb.build_argv(args)
        stdin_data = verb.stdin_payload(args)
        sub_id = uuid.uuid4().hex[:12]

        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if stdin_data is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # unread PIPE stderr can deadlock a noisy child
            text=True,
            bufsize=1,  # line-buffered
        )
        if stdin_data is not None and proc.stdin is not None:
            # Official stdin-reading scripts consume the body and then
            # either exit (Tier A) or wait on a device event (Tier B NFC
            # write does not use this hook today). Close so the child
            # sees EOF.
            proc.stdin.write(stdin_data)
            proc.stdin.close()
        sub = Subscription(id=sub_id, verb_name=verb.name, process=proc)

        def _read_lines():
            q = sub.queue
            if proc.stdout is None:
                self._mark_stopped(sub)
                return
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                if verb.parser == "json_stream":
                    try:
                        item: Any = json.loads(line)
                    except json.JSONDecodeError:
                        item = {"_raw": line, "_parse_error": True}
                else:
                    item = line
                # bounded queue, drop-oldest (single producer: this thread)
                while True:
                    try:
                        q.put_nowait(item)
                        break
                    except queue.Full:
                        try:
                            q.get_nowait()
                            sub.dropped += 1
                        except queue.Empty:
                            pass
            self._mark_stopped(sub)

        sub.reader_thread = threading.Thread(target=_read_lines, daemon=True)
        sub.reader_thread.start()

        with self._lock:
            self._subs[sub_id] = sub
        self._pids_add(proc.pid)

        return sub_id

    def poll(self, sub_id: str, max_items: int = 50) -> dict[str, Any]:
        sub = self._get(sub_id)
        items = []
        while len(items) < max_items:
            try:
                items.append(sub.queue.get_nowait())
            except queue.Empty:
                break
        return {"items": items, "stopped": sub.stopped, "dropped": sub.dropped}

    def stop(self, sub_id: str) -> str:
        """
        Terminate the subscription's process. Returns the verb name so the
        dispatcher can audit the stop.
        """
        with self._lock:
            sub = self._subs.get(sub_id)
            if sub is None:
                raise KeyError(f"unknown subscription: {sub_id}")
        if sub.process.poll() is None:
            sub.process.terminate()
            try:
                sub.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sub.process.kill()
        self._mark_stopped(sub)
        self._pids_remove(sub.process.pid)
        return sub.verb_name

    def list_active(self) -> list[str]:
        with self._lock:
            return [sid for sid, s in self._subs.items() if not s.stopped]

    # -- internals ----------------------------------------------------------

    def _mark_stopped(self, sub: Subscription) -> None:
        if not sub.stopped:
            sub.stopped = True
            sub.stopped_at = time.time()

    def _reap_loop(self) -> None:
        while True:
            time.sleep(REAP_INTERVAL_S)
            now = time.time()
            with self._lock:
                dead = [
                    sid for sid, s in self._subs.items()
                    if s.stopped and s.stopped_at is not None
                    and now - s.stopped_at > REAP_GRACE_S
                ]
                for sid in dead:
                    del self._subs[sid]

    def _get(self, sub_id: str) -> Subscription:
        with self._lock:
            if sub_id not in self._subs:
                raise KeyError(f"unknown subscription: {sub_id}")
            return self._subs[sub_id]

    # -- pid tracking (crash recovery) ---------------------------------------

    def _pids_add(self, pid: int) -> None:
        with self._lock:
            PIDFILE.parent.mkdir(parents=True, exist_ok=True)
            pids = _pids_load()
            pids.append(pid)
            _pids_save(pids)

    def _pids_remove(self, pid: int) -> None:
        with self._lock:
            _pids_save([p for p in _pids_load() if p != pid])


def _pids_load() -> list[int]:
    try:
        return [int(p) for p in json.loads(PIDFILE.read_text())]
    except (OSError, ValueError):
        return []


def _pids_save(pids: list[int]) -> None:
    PIDFILE.write_text(json.dumps(pids))


def _is_our_orphan(pid: int) -> bool:
    """
    Pidfile entries can be stale (pid reuse). Only kill processes whose
    cmdline is a termux-* binary — nothing else in this deployment should
    be spawning those, so this keeps a recycled pid from nuking an
    unrelated process.
    """
    try:
        parts = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    if not parts or not parts[0]:
        return False
    return Path(os.fsdecode(parts[0])).name.startswith("termux-")


def recover_orphans() -> list[int]:
    """
    Called once at daemon startup: terminate any termux-* processes left
    running by a previous crash (tracked in logs/subscriptions.pids).
    """
    killed: list[int] = []
    for pid in _pids_load():
        try:
            os.kill(pid, 0)
        except OSError:
            continue  # already gone
        if not _is_our_orphan(pid):
            continue  # pid reused by something we don't own
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            killed.append(pid)
        except OSError:
            continue
    _pids_save([])
    return killed
