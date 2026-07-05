# Game Fix Manager — Design Document

**Status:** Draft v1 — mapped out, not yet built
**Companion tools:** `steamos_manager.sh` (prefix backups), `perf_profile_backup.sh` (perf profiles)

## 1. Problem

After a SteamOS reinstall/reimage, modded games have to be set back up one by one.
The existing Backup Manager covers Wine prefixes (`compatdata`), but mods live mostly
in the **game install directory** (`steamapps/common/...`), which Steam re-downloads
clean — plus per-game setup like launch options and systemd units that no backup
currently captures.

Goal: after a reimage → install game → run this tool → pick game → everything is
applied automatically.

## 2. Known target games (initial recipes)

| Game | Fix type | Steps needed |
|------|----------|--------------|
| L.A. Noire | Simple EXE swap | Backup original exe, copy patched exe from payload |
| Watch Dogs | Mod file set | Copy multiple mod files into game dir |
| The Crew (TCU) | Full setup | Copy TCU files, install + enable systemd unit(s), set Steam launch args |

These three cover the step types the engine needs on day one. More games get added
by writing new recipes — no code changes.

## 3. Key decisions (agreed 2026-07-06)

1. **Standalone app**, TUI first, architected so a GUI can be added later
   → strict separation: core engine (no UI code) + thin UI frontend.
2. **Game detection via Steam VDFs** (libraryfolders / appmanifests / shortcuts.vdf),
   fallback to asking the user for a path. Non-Steam games matched with
   **name aliases** (e.g. "The Crew" ≡ "The Crew Unlimited" ≡ "TCU").
3. **Hybrid fix model**: declarative per-game manifest (ordered, typed steps) +
   locally stored payload files. Nothing depends on download links staying alive.
4. **Storage**: SD card primary (`steamos_restore/game_fixes/`), mirrored to NAS
   via Syncthing.

## 4. Architecture

```
game-fix-manager/
├── DESIGN.md
├── gfm.py                  # entry point (arg parsing, wires UI to core)
├── core/
│   ├── manifest.py         # load/validate manifest.json recipes
│   ├── detect.py           # Steam VDF parsing, library scan, alias matching
│   ├── steamvdf.py         # localconfig.vdf / shortcuts.vdf read+write
│   ├── engine.py           # step runner: apply / verify / revert, dry-run
│   ├── steps/              # one module per step type (registry pattern)
│   │   ├── copy_files.py
│   │   ├── swap_exe.py
│   │   ├── launch_options.py
│   │   ├── systemd_unit.py
│   │   └── run_script.py   # escape hatch
│   └── store.py            # payload store paths, SD detection, config
└── ui/
    ├── base.py             # UI interface: choose(), confirm(), input(), progress()
    └── tui_gum.py          # v1: shells out to gum (already on device via GBM)
```

- **Language:** Python 3, stdlib-only core (SteamOS ships Python; no pip needed
  on-device). JSON manifests (no TOML dependency question).
- **UI:** v1 frontend calls the `gum` binary the Backup Manager already installs
  to `~/scripts/bin` — zero new dependencies, consistent look. All UI goes through
  `ui/base.py` so a GUI (or Textual) frontend is a drop-in later.
- **Dev environment note:** developed on Windows, runs on SteamOS. Core must use
  path abstraction + a `--dry-run` mode and a fake-filesystem test harness so logic
  is testable off-device.

## 5. Data model

Payload store layout (on SD card, Syncthing-mirrored):

```
<SD>/steamos_restore/game_fixes/
├── gfm_config.json              # user prefs, remembered custom game paths
├── la-noire/
│   ├── manifest.json
│   └── payload/
│       └── LaNoire.exe          # patched exe
├── watch-dogs/
│   ├── manifest.json
│   └── payload/...
└── the-crew/
    ├── manifest.json
    └── payload/
        ├── tcu/...              # TCU game files
        └── tcu-server.service   # systemd user unit
```

### manifest.json (schema sketch)

```json
{
  "id": "the-crew",
  "name": "The Crew",
  "aliases": ["The Crew Unlimited", "TCU", "TheCrew"],
  "steam_appid": 241560,
  "detect": {
    "install_dir_names": ["TheCrew"],
    "marker_files": ["TheCrew.exe"]
  },
  "steps": [
    { "type": "copy_files", "from": "payload/tcu", "to": "{game_dir}",
      "backup_originals": true },
    { "type": "systemd_unit", "unit": "payload/tcu-server.service",
      "scope": "user", "enable": true, "start": true },
    { "type": "launch_options", "value": "...", "merge": "prepend" }
  ],
  "notes": "Free-text: where the mod came from, version, quirks."
}
```

Step contract — every step type implements:
- **apply(ctx)** — do the thing; idempotent (safe to re-run)
- **verify(ctx)** — is it currently applied? (drives ✅/☐ status in the menu)
- **revert(ctx)** — undo using `.gfm-orig` backups where possible

`ctx` carries resolved paths: `game_dir`, `prefix_dir` (compatdata), `payload_dir`,
`steam_root`, plus dry-run flag and logger.

## 6. Game detection flow

1. Parse `libraryfolders.vdf` → all Steam library roots (internal + SD card).
2. For recipes with `steam_appid`: find `appmanifest_<id>.acf` → `installdir`.
3. Non-Steam: parse `shortcuts.vdf`, fuzzy-match shortcut names against
   `name` + `aliases` (case/punctuation-insensitive).
4. Fallback: scan library `common/` dirs for `install_dir_names` / `marker_files`.
5. Still not found → prompt user for the path, remember it in `gfm_config.json`.

## 7. Steam-write safety

Launch options live in `localconfig.vdf` (Steam games) or `shortcuts.vdf`
(non-Steam). Both require **Steam to be closed** for safe writes — reuse the
proven pattern from `perf_profile_backup.sh` / `safe_vdf_edit`: warn about
controller dropout → `steam -shutdown` → wait/kill → edit → relaunch.
Batch all VDF writes so Steam is bounced **once per session**, not per game.
Always write a `.bak` before modifying (existing pattern).

## 8. Main user flows

- **Apply fixes** (the post-reimage flow): list recipes with detected/installed/
  applied status → multi-select → engine runs steps, VDF writes batched last.
- **Add / capture a game**: wizard — pick game, select modded files to pull into
  payload, capture current launch options, add systemd/other steps → writes
  manifest.json. (This is how recipes get built from the *currently working*
  install, before it's ever lost.)
- **Verify**: re-run all `verify()` — shows what's applied, drifted, or missing.
- **Revert**: undo a game's fixes from backups.

## 9. Roadmap

- **Phase 1 (MVP):** manifest format, engine with `copy_files` + `swap_exe`,
  VDF detection, gum TUI apply-flow. Hand-author L.A. Noire + Watch Dogs recipes.
- **Phase 2:** `launch_options` + `systemd_unit` steps (Steam-close batching)
  → The Crew fully automated.
- **Phase 3:** capture wizard, `verify`/status display, revert.
- **Phase 4:** Syncthing mirror setup helper; optional hook so GBM's
  "Full System Restore" can chain into "Apply all fixes".
- **Later:** GUI frontend (Game-Mode friendly), more step types (ini edits,
  registry, winetricks) as new games demand them.

## 10. Open items

- [ ] Capture the exact TCU Linux setup from the current working install
      (which files, unit contents, launch args) **before** the next reimage.
- [ ] Confirm Watch Dogs mod file list + whether it also needs launch options.
- [ ] Confirm L.A. Noire patched exe source/version for the notes field.
- [ ] Decide non-Steam prefix handling: recipes may need `prefix_dir` for
      shortcuts whose compatdata ID changes across reinstalls (GBM's remap
      logic already solves the ID problem — reuse, don't duplicate).
