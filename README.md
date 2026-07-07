# Game Fix Manager

Re-applies game mods and fixes after a SteamOS reinstall or reimage.
Companion to the SteamOS Backup Manager: that tool saves your Wine prefixes,
this one puts the mods, launch options, and system setup back.

**The mental model:** every game has a *recipe* (what to do) and a *payload*
(the mod files). Apply a recipe and it converges the game to the fixed state —
run it twice, nothing breaks. Every original file is kept next to the modified
one as `<name>.gfm-orig`, so **Revert always gets you back to stock.**

## Quick start (Steam Deck / SteamOS)

One line in Konsole — clones on first run, updates after that, then opens the
menu:

```bash
if [ -d ~/game-fix-manager ]; then git -C ~/game-fix-manager pull; else git clone https://github.com/TPepperoni666/game-fix-manager.git ~/game-fix-manager; fi; python3 ~/game-fix-manager/gfm.py
```

Install the game from Steam first (exception: The Crew — see its row below),
then apply its fix. For couch use, pick **🖥️ Install Shortcut** once — see
[Controller support](#controller-support).

## The menu — what each entry does

| Entry | What it does |
|-------|--------------|
| **🔧 Apply Fixes** | Pick one or more games (TAB toggles, ENTER confirms) and install their fixes. Downloads any missing payloads first, backs up originals, then — if launch options changed — asks to restart Steam once at the end. |
| **📋 Status** | Read-only health check: shows every game with the icons below. Never changes anything. |
| **↩️ Revert a Game** | Puts a game back to stock from the `.gfm-orig` backups. Asks before each game. |
| **🔗 Reconcile Prefixes** | For non-Steam shortcuts whose Steam-picked appid points at an empty prefix while your real prefix sits under an old appid in `compatdata/`. Identifies the right one (GBM CSV, then a `drive_c` folder scan), then rewrites the shortcut's appid and its `CompatToolMapping` entry to point at the existing prefix — so your saves/registry/mods are back in play. Steam closes for the write; nothing on disk moves. |
| **💾 Mirror Store** | Downloads any payloads you don't have yet, then copies the complete store to the SD card (or a path you give it, e.g. the NAS). After this, a reimage can restore **every** game with no internet — the app finds the SD copy automatically when there's no git clone. Run it again any time; it only copies what changed. |
| **🖥️ Install Shortcut** | Creates the desktop launcher (auto-updates recipes on every launch) and prints the Game Mode setup steps. |

### Status icons

| Icon | Meaning |
|------|---------|
| ✅ | Fix fully applied and verified (file contents actually checked, not guessed) |
| ☐ | Game found, fix not applied (or payload not downloaded yet — apply fetches it) |
| 🟡 | PARTIAL — some steps applied, some not. Common after a game update overwrote the mod, or launch options got cleared. Re-apply fixes it. |
| ❓ | Game not found. Install it, or apply and type the path once (it's remembered). |

## The games — what each fix does

| Game | What the fix does | Manual steps left |
|------|-------------------|-------------------|
| **L.A. Noire** | V-Patch 2.0: uncaps 30 FPS, widescreen/ultrawide fixes. Sets the required `WINEDLLOVERRIDES` launch option itself. | None |
| **Watch_Dogs** | Living City mod v2.9 (fuel, events, Black Market **weapon unlocks**) + your tuned config. Downloads the 274MB mod from this repo's release on first apply. | None |
| **The Crew (TCU)** | Downloads the TCU patch from thecrewunlimited.com, installs the boot network service (needs sudo), stages the TCU launcher, sets launch options. **Add TheCrew.exe to Steam FIRST** — the tool finds the game through your shortcut. Keep the game folder on the SD card: the game is unobtainable and your save (`data.bin`) lives in it. | Force Proton on the shortcut (one-time) |
| **Shift 2 Unleashed** | Xbox 360 controller button prompts (single pak swap). | None (untested recipe — first run will tell) |
| **Force Unleashed 1** | Raises the 30 FPS cap to **40** (above 40 the grip physics break — a hard blocker in the Jedi Temple DLC; the manifest explains switching to 60). Downloads patched exes from PCGamingWiki. | None |
| **Force Unleashed 2** | 60 FPS exe, skips the 7 logo intros, sets physics-taming launch options. Wet-clothes mod slot is ready but waiting on its ModDB file. | None |
| **Transformers: WFC** | Stages the FPS-unlock trainer + a wrapper so it launches with the game, sets the launch option. Trainer is runtime memory patching — **press Num0 in game each session** (bind a back button). Untested under Proton. | Num0 per session; 60 FPS cap in QAM |
| **DeckOps (COD setup)** | Puts the official [DeckOps](https://github.com/GalvarinoDev/DeckOps) installer on the desktop — a tool recipe, no game needed. Run it from Desktop Mode; it self-updates and installs the Call of Duty clients (CoD4x, IW4x, Plutonium, T7X, AlterWare…). | Run the installer; provide game files yourself |

## Controller support

- **Desktop Mode** — works out of the box: Steam's desktop layout maps
  D-pad → arrows, A → Enter, B → Esc.
- **Game Mode** — right-click the desktop shortcut → *Add to Steam*, then in
  its controller settings pick the official *Keyboard (WASD) and Mouse*
  template (or map D-pad → arrows, A → Enter, B → Esc, plus a button for TAB
  to toggle multi-select).

## Terminal commands (same actions as the menu)

| Command | What it does |
|---------|--------------|
| `python3 gfm.py` | the interactive menu |
| `python3 gfm.py list` | the Status screen |
| `python3 gfm.py apply [id ...]` | apply fixes — ids like `la-noire`, `the-crew`; none = pick from list |
| `python3 gfm.py revert <id>` | back to stock |
| `python3 gfm.py install` | desktop/Game Mode launcher |
| `python3 gfm.py mirror [--dest PATH]` | offline copy of the store (default: SD card) |
| `python3 gfm.py reconcile` | wire shortcuts to existing compatdata prefixes |
| `--dry-run` | print the full plan, touch nothing |
| `--store PATH`, `--steam-root PATH` | overrides (mostly for development) |

## Adding a new game

1. Create `store/games/<id>/manifest.json` — copy `la-noire` (simple) or
   `the-crew` (downloads + systemd) as a template.
2. Mod files go in `store/games/<id>/payload/`; write where they came from in
   the manifest's `notes`.
3. Commit. No code changes needed.

Step types: `copy_files` (files/trees, `executable` flag for scripts),
`swap_exe` (single-file swap), `remove_files` (reversible delete — intro
videos), `launch_options` (Steam AND non-Steam shortcuts, batched behind one
Steam restart), `systemd_unit` (boot services, user/system scope),
`pak_edit` (edit inside zip-format game archives). Any step can be
`"optional": true` (skip-with-warning instead of failing the recipe).

**Payload rules:** repo is public — payloads must be redistributable (keep
licenses in the recipe's `docs/`). Actual game files / patched exes: fetch
them from their original source via `remote_payloads` (TCU, Force Unleashed)
or host big redistributable ones as GitHub release assets (Watch Dogs).
GitHub blocks repo files >100MB either way.

## Where the store can live

Search order: `--store` → `GFM_STORE` env → remembered config
(`~/.config/gfm/config.json`) → `store/` next to the app (the git clone) →
SD card (`/run/media/*/steamos_restore/game_fixes`).

## Development

Stdlib-only Python 3.11+; developed on Windows, targets SteamOS. Core logic
(`core/`) has no UI code; frontends implement `ui/base.py` so a GUI can drop
in later. Note the gum UI only runs where gum exists (the Deck) — the plain
fallback is what you get on a dev box.

```bash
python tests/smoke_test.py   # 51-check end-to-end suite, fake Steam library
```
