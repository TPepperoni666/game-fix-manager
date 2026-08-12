# Ghost Recon Wildlands — cross-save between Windows and SteamOS

Ubisoft Connect's cloud save does not work for Wildlands under Proton. It's a known
dead end, not something to fix: Ubisoft never supported the Deck and post-launch
support for Wildlands ended. So Connect's cloud sync gets turned **off on both
machines** and Syncthing becomes the single source of truth for the save folder.

`wl_save_sync.py` does the fiddly parts: finds the save folder inside the Proton
prefix, flips the Connect setting, creates the Syncthing folder, and wraps the game
launch so you never start a session on a stale save.

## The save folder

Same Ubisoft account on both machines, so the account GUID is the same. `3559` is the
Ubisoft product id for the Steam release of Wildlands.

| | path |
|---|---|
| Windows | `C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\savegames\85c08d6d-eddd-43bd-86a0-aab4ed214fa0\3559` |
| SteamOS | `~/.local/share/Steam/steamapps/compatdata/460930/pfx/drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/savegames/<same GUID>/3559` |

Connect's cloud-sync toggle is `user: syncsavegames:` in `settings.yaml`
(`%LOCALAPPDATA%\Ubisoft Game Launcher\` on Windows, the same path under
`pfx/drive_c/users/steamuser/AppData/Local/` in the prefix).

## Status — live as of 2026-08-11

Four nodes, full mesh, all idle and 100%: **TONY_MAIN_PC**, **Legion Go 2 SteamOS**,
**HTPC**, **NAS**. All carrying the same 5 `.save` files / 279,620 bytes.

| | |
|---|---|
| Connect cloud sync | off on all three gaming machines (`settings.yaml.wlsync-bak` holds each original) |
| Syncthing folder | `wildlands-save`, send-receive, versioning simple/keep 10 everywhere |
| Sharing | full mesh — every node shares with every other |

The NAS (`NKM5YMB`, TrueNAS Docker app, Syncthing v2.1.2) holds a replica at
`~/Wildlands Save` inside its config ixVolume. It's the always-on member: quit on any
machine with the others off and the save still reaches somewhere immediately, so
nothing can come up later and find only its own stale copy. It also gives the guard a
peer to confirm against when the other machines are down.

### Which save won, and why

The HTPC already had its own `3559` folder from a 10 Aug session, diverged from the
Legion's 11 Aug one (they shared an identical `5.save`, so common history). Tony
confirmed the Legion had carried his HTPC progress forward — Connect *downloads* from
the cloud fine under Proton, it's only the upload that fails — so the Legion's copy is
a strict continuation and nothing was lost by seeding from it.

The HTPC's pre-sync save is preserved at
`C:\Users\tonyf\Wildlands-save-backup-HTPC-20260811` on that machine. Its live folder
was cleared before joining, so it seeded clean rather than sitting in Syncthing's
"locally changed" limbo.

### HTPC install notes

Syncthing was **not** installed — the `BAZZITE HTPC` device on the NAS was a leftover
from a wiped Bazzite install, so the machine joined with a brand-new device ID
(`WPUQ7PW…`). Installed via `syncthing-windows-setup.exe` v2.0.2 to match the main PC.

Two things the silent install skipped, both fixed manually:

- **Firewall rule** — `cscript //nologo SyncthingFirewallRule.js /elevated /create /silent`
- **Logon task** — the bundled `SyncthingLogonTask.js` fails over SSH with `0x80070534`
  (no account-name mapping); registered the task directly from the main PC's exported
  XML instead, with the local SID substituted. Action matches: `stctl.exe --start`.

Worth re-checking both after any Syncthing upgrade on that box.

Two **stale handheld device entries** live on the NAS — `steamOS LeGo 2` (BSEPNFH) and
`Legion Go 2` (62APWHS). Neither is the current device ID; they're leftovers from past
SteamOS reimages. Harmless, but worth pruning in the NAS's Syncthing UI sometime, and
worth expecting again after the next reimage.

### What isn't synced, on purpose

`.stignore` in the save folder (both machines) excludes:

- `*.save.upload` — Connect's stuck cloud-upload markers. These are the visible
  symptom of the original problem: zero-byte queue files that never cleared. They're
  machine-local state, not save data.
- `uplay_backup/` — Connect keeps its own rolling backup per machine under the same
  filenames. Syncing it makes the two machines fight over identical names for
  unrelated content. Syncthing's own versioning covers recovery instead.

## The launch guard

The guard does **one** thing: it waits until this machine has the newest save before
letting the game start. That's the only gap Syncthing can't close for itself — nothing
else sits between "press Play" and "the game opens the save file". Wake the handheld,
press Play before Syncthing has reconnected, and the game reads a stale save; Syncthing
then pulls the newer one *while you play*, which is exactly how you get
`.sync-conflict-*` files.

**It deliberately does nothing after the game exits.** An earlier version forced a
rescan and waited for peers on the way out. That was dropped on 2026-08-12 because it
earned nothing:

- Syncthing's watcher already pushes a finished save within seconds (`fsWatcherDelayS:
  5`), which is what actually delivered the 12 Aug HTPC session.
- The guard **cannot prevent you suspending the machine**. It doesn't inhibit sleep, so
  the "handheld goes down before the save is pushed" case it was supposed to cover was
  never actually covered.
- It carried a hand-off race that made it a no-op anyway: `GRW.exe` starts, hands off
  to Ubisoft Connect and dies within ~2 seconds, so a post-exit wait keyed on that
  process saw "not running" in the gap and pushed a save the game hadn't written yet.
  Observed on the HTPC: guard finished 11:59:05, save files written 12:00:15–12:01:17.

Less code, and honest about what it covers.

**SteamOS** — Wildlands → Properties → Launch Options. You already run Lossless
Scaling there (`~/lsfg %command%`), so the guard goes *in front* of it and keeps it:

```
python3 /home/deck/wildlands-save-sync/wl_save_sync.py guard -- ~/lsfg %command%
```

**Main PC** — Wildlands → Properties → Launch Options:

```
"D:\Linux Prefix Manager\wildlands-save-sync\wl-guard.bat" %command%
```

**HTPC** — same, but the tool lives in the user profile there:

```
"C:\Users\tonyf\wildlands-save-sync\wl-guard.bat" %command%
```

**Set as of 2026-08-11 on all three** — main PC verified through a full Steam
load/save cycle (Steam read the value and wrote it back verbatim on exit); HTPC and
Legion written with Steam closed and read back clean.

Writing these means editing `userdata/<id>/config/localconfig.vdf` with Steam closed,
since Steam rewrites it from memory on exit. Use Game Fix Manager's
`core/steamvdf.py` (`get_launch_options` / `set_launch_options`) rather than any new
parser — it round-trips losslessly and makes its own `.vdf.gfm-bak`.

**Game Mode blocks this on the Legion.** `steam -shutdown` works, but gamescope
respawns Steam in **under a second** (observed: pid 20535 → 22956 within 3s), and the
fresh instance reads the file at startup — so even a write that lands gets clobbered
on its next exit. Switch the handheld to Desktop Mode first, where a shutdown sticks.
Same caveat applies to GFM's `launch_options` step: its "one Steam bounce" flush is
only safe in Desktop Mode.

The guard never blocks the game. If Syncthing is down, the peer is offline, or
anything else fails, it logs the problem to `wl-save-sync.log` and launches anyway.
It also re-asserts Connect's cloud sync = off and warns about any conflict files it
finds on the way past.

## Commands

```
setup                     find/create the save folder, turn Connect cloud sync off
status                    sync state + peer completion + conflict-file check
push                      force a scan, wait until peers have the save
add-folder --peer NAME    create the Syncthing folder (--type to change mode later)
guard -- <command>        wait for the newest save, then run the game
```

Env overrides if auto-detection misses: `WL_PREFIX`, `WL_ACCOUNT_ID`, `WL_ST_CONFIG`.

## Rules that keep this working

- **Never play both machines while one is offline.** Run `status` first if unsure —
  it shows peer completion and any conflict files.
- **Connect cloud sync stays off.** If you toggle it back on in the Connect UI, the
  guard flips it off again at next launch (`--no-cloud-guard` to opt out). With cloud
  sync on, Connect can pull its stale server-side copy over a good synced save.
- **The prefix is the sync path on Linux.** A reimage or prefix wipe destroys it.
  Syncthing refuses to sync a folder whose `.stfolder` marker is gone, which is what
  saves you — re-run `setup` and re-add rather than pointing it at a fresh prefix.
- **Versioning is on** (simple, keep 10) — old versions land in `.stversions/` inside
  the save folder, so a bad sync is recoverable.

## If a conflict happens anyway

`status` lists the `.sync-conflict-*` files. Pick the one you want by timestamp/size,
rename it over the real name on **one** machine with the game closed, let it sync,
and delete the rest.
