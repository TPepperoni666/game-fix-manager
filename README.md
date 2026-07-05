# Game Fix Manager

Re-applies game mods/fixes after a SteamOS reinstall or reimage. Companion to
the SteamOS Backup Manager (which covers Wine prefixes — this covers game-dir
mods, and later launch options + systemd units).

**Keep this repo private** — payloads contain patched game executables.

## Fresh-install bootstrap (Steam Deck / SteamOS)

One-time prep: create a **fine-grained personal access token** (GitHub →
Settings → Developer settings → Personal access tokens → Fine-grained tokens)
scoped to *only this repo* with *Contents: read-only*. Keep it somewhere you
can paste from.

Then this single line in Konsole clones (or updates) and applies:

```bash
TOKEN=<paste-token>; if [ -d ~/game-fix-manager ]; then git -C ~/game-fix-manager pull; else git clone https://$TOKEN@github.com/TPepperoni666/game-fix-manager.git ~/game-fix-manager; fi; python3 ~/game-fix-manager/gfm.py apply la-noire
```

The token is saved into the clone's remote URL, so after the first run plain
`git pull` works without pasting it again. The app finds its `store/` folder
inside the clone, so this works before the SD card or Syncthing are set up.
Install the game(s) from Steam first, then apply.

## Commands

| Command | What it does |
|---------|--------------|
| `python3 gfm.py` | interactive menu (uses gum if present, plain menus otherwise) |
| `python3 gfm.py list` | every recipe + detected/applied status |
| `python3 gfm.py apply [id ...]` | apply fixes (prompts for game path if not auto-found) |
| `python3 gfm.py revert <id>` | restore originals from `.gfm-orig` backups |
| `--dry-run` | show what would happen, touch nothing |
| `--store PATH` / `--steam-root PATH` | overrides |

## Adding a game

1. Create `store/games/<id>/manifest.json` (copy `la-noire` as a template).
2. Put mod files in `store/games/<id>/payload/`.
3. Commit. No code changes needed.

Step types available: `copy_files`, `swap_exe` (more coming: `launch_options`,
`systemd_unit` for The Crew/TCU).

Payload size note: GitHub blocks files >100MB — anything bigger needs Git LFS
or stays SD-card-only.

## Store resolution order

`--store` → `GFM_STORE` env → remembered in `~/.config/gfm/config.json` →
`store/` next to the app (git clone) → SD card scan
(`/run/media/*/steamos_restore/game_fixes`).

## Development

Stdlib-only Python 3.11+. Core logic (`core/`) has no UI code; frontends
implement `ui/base.py` so a GUI can drop in later.

```bash
python tests/smoke_test.py   # end-to-end test with a fake Steam library
```
