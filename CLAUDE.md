# Working on Game Fix Manager

Read this before changing anything. It is not a description of the code — the
README covers what the tool does and `docs/RECIPES.md` covers how to author a
recipe. This file is the list of things that have already gone wrong, so they
go wrong at most once.

GFM re-applies game fixes/mods and backs up saves so a SteamOS reimage can be
survived. **For some saves it is the only copy.** That one fact decides how to
weigh every trade-off here: a crash is visible and recoverable, a backup that
reports success while writing nothing is neither.

## Non-negotiables

- **Standard library only.** No third-party imports, ever — the Windows build
  is a single PyInstaller exe and the Deck runs it from a git checkout.
- **Run the suite under PowerShell on Windows**, not Git Bash:
  `python tests\smoke_test.py`. Git Bash's `tar` reads `C:` as a remote host
  and the payload-fetch tests fail for reasons that have nothing to do with
  your change.
- **`manifest.KNOWN_STEP_TYPES` must equal `engine._REGISTRY`.** A name in one
  and not the other means either a recipe dies mid-apply or a valid step is
  rejected at startup. The suite asserts both directions.
- **Large payloads never go in git.** The F1 Manager paks are 12 GB and
  PinyonShift is 15 GB; both live only on the NAS under
  `_recipes/<id>/payload/` or `_games/<name>/`. `payload_path()` prefers the
  NAS copy anyway.

## Read the machine. Never guess a path

Two recipes silently backed up nothing for months because a path was inferred
rather than read:

- Halo MCC — wrong case in a directory name.
- Simpsons Hit & Run — recipe said `{game_dir}/Save1`; the file is
  `{game_dir}/game/save1`. Lowercase *and* one level deeper. On btrfs that is
  simply a different path, and capture skips missing entries silently.

So: SSH into the device and look. `gfm.log` Syncthing-mirrors to the
workstation, so the log is readable there without anyone transcribing it.

When a `save_paths` entry is inferred from PCGamingWiki rather than confirmed
against a live install, **say so in the recipe notes**. A capture that comes
back empty is the only symptom you get.

## Tests must execute the code, not grep it

`_repair_nas_mount` shipped with a `NameError` on its first real call. Several
checks had been written for it and **all of them passed**, because every one
used `inspect.getsource()` and searched for a substring. The crash then
aborted an entire unattended backup.

Asserting on source text is fine for wiring ("is this menu entry present"),
but every function needs at least one check that **calls** it. A stub object
and a temp directory are usually enough.

## Appid identity — the deploy-before-recipe trap

Caught three times. Two different formulas:

```
recipe shortcut:  crc32(f"gfm:{recipe.id}")          | 0x80000000
generic deploy:   crc32(f"gfm:deploy:{folder_name}") | 0x80000000
```

Deploy a game *before* its recipe exists and it is pinned under the deploy
appid; the recipe then looks for the other one and finds no prefix, no
artwork, no saves. Create the recipe first, or re-adopt afterwards.

A shortcut that predates its recipe fails more quietly. `ensure_shortcut`
matches by name and rewrites the entry's appid, so an unpinned recipe moves the
game onto a new, empty prefix without adding a visible duplicate. Pin the live
appid with `"appid"` on the `steam_shortcut` step. `steam_appid` is not a
substitute: it is for real Steam installs.

## State that must survive a reimage

Adopted appid pins go to `<NAS>/_state/adopted_appids.json`, falling back to
the config dir when there is no NAS. They used to be appended to
`store/prefix_registry.json`, which is **tracked** — that wedged every later
`git pull --ff-only` and put reimage-survival data inside the one directory a
reimage destroys. Curated pins stay in git; discovered pins do not.

## Path templates: one template, both platforms

A save captured on the Deck must restore on Windows, so:

- `{localappdata}` `{appdata}` `{documents}` `{savedgames}` `{programfilesx86}`
  → inside the Proton prefix on Linux, the real folder on Windows.
- `{xdg_data}` `{xdg_config}` → **never** rewritten into a prefix. These are
  for native Linux builds, which have no prefix.
- `~` expands **only** as a leading character. A blanket replace also ate the
  `~` in `settings.ini~` and in short paths like `C:\PROGRA~2`.

Choose by *what runs the game*, not what it was written for: a Windows recomp
under Proton wants `{localappdata}`; a native build wants `{xdg_data}`.

## The unattended weekly backup

It runs from a timer with nobody watching, and the log is the only thing read
afterwards.

- **Nothing outside `step()`'s try/except.** One failed step must never abort
  the rest. A raw call in the health preamble took the whole job down once.
- **Skipped is not success.** Steps that write to the NAS are gated on
  `needs_nas`; skips are their own outcome and count toward the exit code, or
  systemd records `Result=success` for a run that backed up nothing.
- **Test the mount with `ismount`, not "are there files here".** A shadowed
  mountpoint reads as a perfectly good directory — that is the entire
  mechanism by which a week of saves landed on the internal disk.
- **The log is not a way of telling anyone.** In October an OS update removed
  the automount unit, and two runs skipped the NAS and said so correctly in a
  log nobody opened. Each run now leaves a record (`core/weeklyhealth.py`, in
  the config dir, because the NAS and the SD card are what can go missing).
  The menu banner, Self-Check, the desktop notification and the Desktop Mode
  login retry all read that one record. A new way for the job to fall short
  belongs in `summarise()`, or nobody hears about it.
- **No route into Game Mode is known, and that is untested on hardware.**
  Steam's toasts live inside its UI process, where Decky reaches them through a
  plugin and a Python process can't. So a device that stays in Game Mode and
  never opens GFM may still hear nothing. `notify()` returning a tool name means
  the message was accepted, not that it was seen.
- **`runs_from_log` parses the job's own messages.** Rewording `NAS STILL DOWN`,
  `NO SD CARD`, `Weekly backup complete` or `Weekly backup finished with`
  breaks the backfill. The suite feeds real job output through it to catch that.
- **A repair may diagnose; it must not get cleverer.** `_repair_nas_mount` gives
  up when the automount is gone, and that stays. Starting the surviving `.mount`
  would bring the share back from a timer, and the share being down is
  currently all that stops reclaim acting on games that lost their shortcut.
  The owner decides when it comes back, so the warning names the cost beside
  the repair step, and every reconnect flow calls `_warn_before_reconnect`.

## Destructive operations

- Reclaim removes a deployed game only when it is over the **34 GB** floor
  (`reclaim.DEFAULT_MIN_BYTES`) *and* its Steam shortcut is gone. It deleted a
  34 GB Jak deploy once because "no shortcut" was read as "shortcut deleted";
  that rule is now deliberate, not a bug.
- Order is **copy → verify → delete**, always. Never delete before the
  replacement is confirmed readable.
- **The weekly backup deletes without asking.** `cmd_weekly_backup` does
  `setattr(self.args, "auto", True)` and its step 6/6 calls `cmd_reclaim`,
  which then takes the `if auto:` branch and removes up to
  `MAX_AUTO_BATCH` (3) candidates with no prompt. The unit file carries no
  `--auto`, so reading only the ExecStart line says the opposite — do not
  conclude the unattended job is safe from the command line alone.
- **Reclaim's identity check is name-based, and names drift.** The deployed
  record is keyed by the SD *folder* name while a recipe has its own name;
  `_norm` strips non-alphanumerics and the comparison is exact, so
  `forzahorizon2 != forzahorizon2azimuth`. Anything that renames a shortcut
  — `steam_shortcut` on Apply does — can therefore make a tracked game look
  un-shortcutted. Worse, a failed recipe lookup also disarms the save guard,
  because `_saves_safe(recipe=None)` returns True before reading
  `save_paths`. `by_name` is keyed on `all_names` for this reason; keep it
  that way.

## Working on the Legion

- It tracks `master` via `git pull --ff-only` and there is **no CI**. Do not
  have two committers going at once; a wedged pull has already cost a day.
- An unclean shutdown truncated files that a `git pull` had just written —
  ext4 delayed allocation leaves the names on disk without the contents. If
  the tool dies on an `ImportError` for something that obviously exists, check
  for zero-length files before debugging the code.

## Editing and committing

- Do not rewrite whole files through PowerShell `Set-Content` — it adds a BOM
  and mangles the emoji the UI depends on. Use targeted edits.
- Commit messages explain **why**, including the incident that motivated the
  change. That history is the reason this file could be written.
