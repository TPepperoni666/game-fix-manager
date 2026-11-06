"""Did the weekly backup do its job, and does anybody know?

In October a SteamOS update removed the NAS automount unit. The weekly job
noticed, skipped its NAS steps and said so in gfm.log, correctly, twice. Nobody
read the log, so two weeks of saves went unprotected in silence. The job was
never the problem; the log was its only output.

So every run leaves a small record here, and everything that has to tell a
person reads that one record: the main menu, Self-Check, the job's own desktop
notification, and a login-time retry for the runs that fired in Game Mode.

The record lives in the config dir, on the internal disk, because of what it
reports on. It cannot be on the NAS, which is the thing that went missing, and
gfm.log sits on the SD card, which is the other. Nor does it go in config.json:
an open menu holds that file in memory and would write the job's update away
when it next saved.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import store

# Weekly plus a couple of days' grace. Persistent=true means a Sunday with the
# device off runs on the next boot, so a few days late is normal.
OVERDUE = timedelta(days=9)
# The longest real run so far took 30 minutes. A started run with no finish
# this much later died, which is how the 27 Sep crash looked from outside.
STUCK_AFTER = timedelta(hours=6)
# Fewer than half of the expected prefixes counts as a drop, not as a game or
# two being removed. The set shrinks from 11 to 1 when the managed list can't
# be read off the NAS.
DROP_RATIO = 2
# Enough for Plasma to start answering after a login.
NOTIFY_TRIES = 3
NOTIFY_WAIT = 20

TITLE = "Game Fix Manager: backup incomplete"
NAS_FIX = "Fix: Settings > 🔌 Connect NAS Payloads (needs sudo)."
NAS_BACK = "The NAS is back. This clears after the next complete weekly run."
SD_FIX = "Fix: put the SD card back in."
LOG_FIX = "Details are in gfm.log."
TIMER_FIX = "Fix: Settings > 📅 Weekly Backup Timer."
PREFIX_CHECK = "Check 🩺 Self-Check, then gfm.log."

AUTOSTART_NAME = "gfm-backup-warning.desktop"


def health_path() -> Path:
    # Read at call time, not import time, so the tests can point it elsewhere.
    return store.CONFIG_DIR / "weekly_health.json"


def load(path=None) -> dict:
    """The record, or {} without one. An unclean shutdown can truncate this
    file, and that must never stop the menu opening: load_or_seed rebuilds it
    from gfm.log."""
    try:
        data = json.loads(Path(path or health_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(rec: dict, path=None) -> None:
    path = Path(path or health_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _stamp(when=None) -> str:
    return (when or datetime.now()).isoformat(timespec="seconds")


def _parse(text) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def day(text) -> str:
    when = _parse(text)
    return when.strftime("%d %b %Y").lstrip("0") if when else "an unknown date"


def new_run(failed=(), skipped=(), nas_skipped=False, sd_skipped=False,
            prefixes=None, os_build="", at=None) -> dict:
    """One finished run, in the shape the record keeps."""
    return {"at": _stamp() if at is None else at,
            "complete": not failed and not skipped,
            "failed": list(failed), "skipped": list(skipped),
            "nas_skipped": bool(nas_skipped), "sd_skipped": bool(sd_skipped),
            "prefixes": prefixes, "os_build": os_build}


def _track_prefixes(rec: dict, run: dict) -> None:
    """Keep the prefix count a normal run should reach.

    Tracked on complete runs as well: on 4 Oct the share was mounted read-only,
    capture did nothing, and the run still reported all 6 steps OK. The prefix
    count falling from 10 to 1 was the only sign. With the NAS up, the same low
    count twice running is the owner's doing (games removed), so it becomes the
    new normal rather than a warning that never clears."""
    count = run.get("prefixes")
    if count is None:
        return
    base = rec.get("prefix_baseline")
    low = bool(base) and count * DROP_RATIO < base
    settled = low and not run["nas_skipped"] and rec.get("prefix_low_runs", 0)
    if not low or settled:
        rec["prefix_baseline"] = count
        rec["prefix_low_runs"] = 0
    elif not run["nas_skipped"]:
        rec["prefix_low_runs"] = rec.get("prefix_low_runs", 0) + 1


def _apply_run(rec: dict, run: dict) -> None:
    rec["last_run"] = run
    if run["complete"]:
        rec["last_ok"] = run
    streaks = rec.setdefault("streaks", {})
    for key, hit in (("nas", run["nas_skipped"]), ("sd", run["sd_skipped"]),
                     ("failed", run["failed"])):
        if hit:
            streak = streaks.setdefault(key, {"count": 0, "since": run["at"]})
            streak["count"] += 1
        else:
            streaks.pop(key, None)
    _track_prefixes(rec, run)


_LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d) (.*)$")
_LOG_PREFIXES = re.compile(r"\[info\] Backing up (\d+) remembered prefix")
_LOG_STEP = re.compile("\[warn\]   (✗|⏭️?) +(.+?): ")


def runs_from_log(text: str) -> list[dict]:
    """Rebuild past runs from gfm.log, oldest first.

    Without this a device upgrading mid-outage would claim its first skip was
    the next Sunday, and show nothing until then. A run is the block between
    its `start:` line and the next one; a block with no closing summary (one
    still running, or one that crashed) says nothing about the outcome, so it
    is left out.

    The step lines are only read BELOW the summary line, because the health
    preamble at the top of a run prints `✗ NAS mounted` in the same format."""
    runs: list[dict] = []
    cur = None

    def close():
        nonlocal cur
        if cur is not None and cur["at"]:
            runs.append(cur)
        cur = None

    for raw in text.splitlines():
        m = _LOG_LINE.match(raw)
        if not m:
            continue
        stamp, body = f"{m[1]}T{m[2]}", m[3]
        if body.startswith("start: cmd="):
            close()
            if body.startswith("start: cmd=weekly-backup"):
                cur = new_run(at="")
            continue
        if cur is None:
            continue
        if cur["at"]:
            step = _LOG_STEP.match(body)
            if step:
                cur["failed" if step[1] == "✗" else "skipped"].append(step[2])
        elif body.startswith("[error]") and "NAS STILL DOWN" in body:
            cur["nas_skipped"] = True
        elif body.startswith("[error]") and "NO SD CARD" in body:
            cur["sd_skipped"] = True
        elif body.startswith("[success] Weekly backup complete"):
            cur["at"] = stamp
        elif body.startswith("[error] Weekly backup finished with"):
            cur["at"] = stamp
            cur["complete"] = False
        else:
            count = _LOG_PREFIXES.match(body)
            if count:
                cur["prefixes"] = int(count[1])
    close()
    return runs


def _log_text(log_file=None) -> str | None:
    try:
        if log_file is None:
            from . import sdmap
            log_file = sdmap.log_path()
        return Path(log_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def load_or_seed(path=None, log_file=None) -> dict:
    """The record, backfilled from gfm.log the first time. With the log out of
    reach (no SD card) it is left unseeded, to try again next time."""
    rec = load(path)
    if rec.get("seeded"):
        return rec
    text = _log_text(log_file)
    if text is None:
        return rec
    rec = {"seeded": True}
    for run in runs_from_log(text):
        _apply_run(rec, run)
    save(rec, path)
    return rec


def begin_run(path=None, log_file=None, now=None) -> None:
    """Mark a run as started. The job can die before it reaches record_run,
    and a run that never finished must not look like one that never started."""
    rec = load_or_seed(path, log_file)
    rec["running_since"] = _stamp(now)
    save(rec, path)


def record_run(run: dict, path=None, log_file=None, held_back=0) -> dict:
    rec = load_or_seed(path, log_file)
    _apply_run(rec, run)
    rec["seeded"] = True
    rec["held_back"] = held_back
    rec.pop("running_since", None)
    save(rec, path)
    return rec


@dataclass
class Alert:
    problems: list = field(default_factory=list)
    detail: list = field(default_factory=list)
    fixes: list = field(default_factory=list)
    cautions: list = field(default_factory=list)

    def banner(self) -> list:
        """(style, text) lines. Each has to fit 78 columns: the main menu on
        an 80x24 console has two rows to spare and no room for wrapping."""
        first, *rest = self.problems
        return ([("error", f"⚠ BACKUP INCOMPLETE: {first}")]
                + [("error", p) for p in rest]
                + [("warn", t) for t in self.detail + self.fixes + self.cautions])

    def body(self) -> str:
        return "\n".join(self.problems + self.detail + self.fixes + self.cautions)


def _runs(count: int) -> str:
    return f"{count} weekly run{'' if count == 1 else 's'}"


def _prefix_drop(rec: dict):
    count = (rec.get("last_run") or {}).get("prefixes")
    base = rec.get("prefix_baseline")
    if count is not None and base and count * DROP_RATIO < base:
        return count, base
    return None


def reconnect_warning(count: int) -> str:
    """The cost of the repair. Saying only "run Connect NAS Payloads" would
    send people straight into it, and a weekly job that deletes without asking
    is held back only by the share being missing."""
    return (f"Up to {count} deployed game(s) have lost their Steam shortcut. "
            "The weekly job keeps them only while the NAS is down: once it is "
            "back, that job can delete them with no prompt. Settings > "
            "🧹 Reclaim lists them. Decide on each one before reconnecting.")


def summarise(rec: dict, now=None, nas_mounted=None, held_back=None,
              expect_timer=None) -> Alert | None:
    """What a person needs to be told, or None when nothing is wrong.

    nas_mounted is the live state (None = unknown). The streak only ends with a
    complete run, so "mounted again, not yet proven" gets its own wording
    rather than sending someone to repair a share that is already back."""
    now = now or datetime.now()
    if expect_timer is None:
        expect_timer = os.name != "nt"     # the timer is Linux only
    if held_back is None:
        held_back = rec.get("held_back", 0)
    streaks = rec.get("streaks") or {}
    last = rec.get("last_run") or {}
    out = Alert()

    nas = streaks.get("nas")
    if nas:
        out.problems.append(f"{_runs(nas['count'])} skipped the NAS steps, "
                            f"since {day(nas['since'])}.")
        out.fixes.append(NAS_BACK if nas_mounted else NAS_FIX)
    elif nas_mounted is False and last and expect_timer:
        out.problems.append("The NAS is not mounted right now.")
        out.fixes.append(NAS_FIX)
    if NAS_FIX in out.fixes and held_back:
        out.cautions.append("⚠ First read 🩺 Self-Check: reconnecting puts "
                            f"up to {held_back} game(s) at risk.")

    sd = streaks.get("sd")
    if sd:
        out.problems.append(f"{_runs(sd['count'])} skipped the SD card steps, "
                            f"since {day(sd['since'])}.")
        out.fixes.append(SD_FIX)

    bad = streaks.get("failed")
    if bad:
        out.problems.append(f"{_runs(bad['count'])} had failed steps.")
        out.fixes.append(LOG_FIX)

    started = _parse(rec.get("running_since"))
    if started and now - started > STUCK_AFTER:
        out.problems.append(f"The run of {day(rec['running_since'])} never "
                            "finished.")
        out.fixes.append(LOG_FIX)

    seen = _parse(last.get("at"))
    if expect_timer and seen and now - seen > OVERDUE:
        out.problems.append(f"No weekly run for {(now - seen).days} days.")
        out.fixes.append(TIMER_FIX)

    drop = _prefix_drop(rec)
    if drop and not out.problems:
        out.problems.append(f"The last run backed up {drop[0]} prefix(es), "
                            f"down from {drop[1]}.")
        out.fixes.append(PREFIX_CHECK)
        drop = None

    if not out.problems:
        return None
    ok = rec.get("last_ok")
    summary = (f"Last complete run: {day(ok['at'])}." if ok
               else "No complete run on record.")
    if drop:
        summary += f" Prefixes backed up: {drop[0]}, was {drop[1]}."
    out.detail.append(summary)
    if bad and last.get("failed"):
        out.detail.append("Latest: " + ", ".join(last["failed"]) + ".")
    out.fixes = list(dict.fromkeys(out.fixes))
    return out


def _bus_env(environ) -> dict | None:
    """An environment that reaches the session bus, or None without one. A
    user unit normally inherits the address. If it didn't, the socket is in a
    fixed place."""
    env = dict(environ)
    if env.get("DBUS_SESSION_BUS_ADDRESS"):
        return env
    runtime = env.get("XDG_RUNTIME_DIR")
    if not runtime and hasattr(os, "getuid"):
        runtime = f"/run/user/{os.getuid()}"
    if runtime and (Path(runtime) / "bus").exists():
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={Path(runtime) / 'bus'}"
        return env
    return None


def notify(title: str, body: str, run=subprocess.run, which=shutil.which,
           env=None) -> str | None:
    """Raise a desktop notification. Returns which tool delivered it, or None.

    None is the expected answer in Game Mode, though that has not been tested
    on hardware. Steam's own toasts live inside its UI process, where Decky
    reaches them through a plugin and a Python process cannot, and nothing
    documents a desktop notification service in that session. A call can also
    sit waiting on D-Bus activation for a daemon that never comes, hence the
    timeout: this must never hold up the job."""
    if env is None:
        if os.name == "nt":
            return None
        env = _bus_env(os.environ)
    if env is None:
        return None
    attempts = (
        ("notify-send", ["notify-send", "--app-name=Game Fix Manager",
                         "--urgency=critical", "--icon=dialog-warning",
                         title, body]),
        ("gdbus", ["gdbus", "call", "--session",
                   "--dest", "org.freedesktop.Notifications",
                   "--object-path", "/org/freedesktop/Notifications",
                   "--method", "org.freedesktop.Notifications.Notify",
                   "Game Fix Manager", "0", "dialog-warning", title, body,
                   "[]", "{'urgency': <byte 2>}", "0"]),
    )
    for name, argv in attempts:
        if not which(name):
            continue
        try:
            r = run(argv, capture_output=True, text=True, timeout=15, env=env)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            return name
    return None


def autostart_dir(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_CONFIG_HOME") or ""
    # The spec says a relative XDG_* value is invalid and must be ignored.
    if not os.path.isabs(base):
        base = str(Path.home() / ".config")
    return Path(base) / "autostart"


def sync_autostart(active: bool, directory=None, python=None,
                   app_dir=None) -> None:
    """Keep the login retry in step with the alert: present while there is
    something to say, gone once there isn't.

    The job usually fires in Game Mode, where its notification probably has
    nowhere to land. This entry says it again at the next Desktop Mode login,
    and notify()'s return value can't stand in for that decision: being
    accepted is not the same as being seen. The job
    writes it rather than the timer setup, so a device that only pulled this
    code gets it without anybody re-running Settings."""
    if directory is None and os.name == "nt":
        return
    entry = Path(directory or autostart_dir()) / AUTOSTART_NAME
    if not active:
        entry.unlink(missing_ok=True)
        return
    text = ("[Desktop Entry]\nType=Application\n"
            "Name=Game Fix Manager backup warning\n"
            "Comment=Removed automatically once the weekly backup is healthy\n"
            f'Exec="{python or sys.executable}" -m core.weeklyhealth\n'
            f"Path={app_dir or store.APP_DIR}\n"
            "Terminal=false\n")
    if entry.is_file() and entry.read_text(encoding="utf-8") == text:
        return
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(text, encoding="utf-8")


def _mounted_now() -> bool | None:
    where = store.load_config().get("local_payloads_dir")
    if not where:
        return None
    try:
        return os.path.ismount(where)
    except OSError:
        return None


def main(tries=NOTIFY_TRIES, wait=NOTIFY_WAIT, sleep=time.sleep,
         directory=None) -> int:
    """`python -m core.weeklyhealth`, run at Desktop Mode login by the entry
    above. It stays out of gfm.py on purpose: gfm.py's main() wakes the NAS
    automount before doing anything, and a login reminder has no business
    touching the mount, or waiting 25 seconds on a dead one."""
    alert = summarise(load_or_seed(), nas_mounted=_mounted_now())
    sync_autostart(alert is not None, directory=directory)
    if alert is None:
        return 0
    for attempt in range(tries):
        if attempt:
            sleep(wait)
        if notify(TITLE, alert.body()):
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
