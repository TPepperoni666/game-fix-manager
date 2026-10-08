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

So: `ssh deck@192.168.1.28` and look. `gfm.log` Syncthing-mirrors to
`C:\Users\tonyf\SD Card Map\gfm.log`, so the log is readable from the
workstation without anyone transcribing it.

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

## Destructive operations

- Reclaim removes a deployed game only when it is over the **34 GB** floor
  (`reclaim.DEFAULT_MIN_BYTES`) *and* its Steam shortcut is gone. It deleted a
  34 GB Jak deploy once because "no shortcut" was read as "shortcut deleted";
  that rule is now deliberate, not a bug.
- Order is **copy → verify → delete**, always. Never delete before the
  replacement is confirmed readable.

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
