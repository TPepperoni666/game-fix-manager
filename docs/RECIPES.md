# Writing your own recipes

A recipe is one folder with one `manifest.json` in it. There is no code to
write and nothing to register — drop the folder in and the tool picks it up
on next launch.

```
store/games/<recipe-id>/
    manifest.json          <- the whole recipe
    payload/               <- optional files it installs
```

`<recipe-id>` is lowercase-with-hyphens and is the recipe's permanent
identity. **Changing it later changes the game's appid**, which orphans its
prefix, saves and artwork — so pick it once and leave it.

---

## The smallest useful recipe

```json
{
  "id": "my-game",
  "name": "My Game",
  "detect": { "install_dir_names": ["My Game"] },
  "steps": [
    { "type": "steam_shortcut", "exe": "MyGame.exe",
      "proton": "GE-Proton10-34" }
  ]
}
```

That is enough to make a non-Steam game appear in Steam on a pinned Proton
build, with a stable appid.

A recipe with **no steps at all** is legal and useful: it declares that a game
exists, so detection, the map and prefix backups know about it.

---

## Manifest fields

| Field | Required | What it does |
|---|---|---|
| `id` | yes | permanent identity; drives the appid |
| `name` | yes | display name, and matched against folder names |
| `aliases` | | other names to match on |
| `steam_appid` | | the REAL Steam appid for games you own on Steam |
| `detect` | | how to find the install folder (below) |
| `steps` | | what to do, in order |
| `save_paths` | | saves that live in the GAME folder, not the prefix |
| `post_apply_message` | | shown after Apply — put manual steps here |
| `notes` | | for humans; never shown in the UI |
| `requires_game` | | `false` for tool recipes with no install dir |
| `remote_payloads` | | files fetched on demand instead of committed |

### Detection

Two passes, name first:

```json
"detect": {
  "install_dir_names": ["My Game", "MyGame"],
  "marker_files": ["Binaries/MyGame.exe"]
}
```

1. **Folder name** matches `install_dir_names` or any of `name`/`aliases`
2. **All** `marker_files` exist inside the folder

Name beats marker deliberately: markers get shared between games built on the
same engine, and a name match is stronger evidence.

Set `steam_appid` for a game you own on Steam and detection resolves through
the appmanifest instead — no path prompting, wherever Steam installed it.

**Verify markers against the real folder before committing.** A guessed marker
is how the Halo MCC recipe broke: the path was `mcc/` lowercase on disk, not
`MCC/`. Marker paths use forward slashes and are case-sensitive on Linux.

---

## Path templates

Usable in any step that takes a path:

| Template | Expands to |
|---|---|
| `{game_dir}` | the game's install folder |
| `{prefix}` | the Proton prefix (`compatdata/<appid>/pfx`) |
| `{prefix_localappdata}` | `drive_c/users/steamuser/AppData/Local` |
| `{localappdata}` | the prefix copy on Linux, the REAL one on Windows |
| `~` | your home directory |

Two are step-specific rather than general:

- `{home}` — **`launch_options` only**, because Steam does not expand `~`
- `{game_dir_win}` — **`wine_registry` only**, the Windows-shaped path

Anything using `{prefix}` needs the game to have run once. Mark those steps
`"optional": true` so a first Apply doesn't fail — the prefix appears after
the first launch and a re-Apply picks it up.

---

## Payloads

Files a step installs live under `payload/` in the recipe folder and are
referenced as `payload/<name>`.

**A local copy always wins.** If the same relative path exists at
`<NAS>/_recipes/<recipe-id>/payload/…` it is used instead of the committed
one. That is how large or non-redistributable files stay out of git entirely —
put them on the NAS and the recipe still resolves.

Any step can be marked `"optional": true`, which turns a failure into a
skipped-with-a-message instead of an aborted Apply.

---

## Step reference

Every step is idempotent — applying twice is the same as applying once.

**`copy_files`** — copy a payload file or tree in.
```json
{ "type": "copy_files", "from": "payload/mod", "to": "{game_dir}",
  "backup_originals": true }
```

**`swap_exe`** — replace one file with a patched copy; keeps a `.gfm-orig`.
```json
{ "type": "swap_exe", "payload": "payload/LaNoire.exe", "target": "LaNoire.exe" }
```

**`remove_files`** — take files out of play, reversibly.
```json
{ "type": "remove_files", "targets": ["Game/Disc/FMV/Win32/LEC"] }
```

**`ini_edit`** — set keys inside an INI, section-aware. Writes a `.gfm-bak`
on first change. Add `values_by_host` to override per machine.
```json
{ "type": "ini_edit", "target": "{game_dir}/Config/Engine.ini",
  "values": { "SystemSettings": { "ResX": 1920, "ResY": 1200 } },
  "values_by_host": { "steamdeck": { "SystemSettings": { "ResX": 1280 } } } }
```

**`wine_registry`** — registry values inside the prefix.
```json
{ "type": "wine_registry", "hive": "user", "key": "Software\\THQ\\Barnyard",
  "values": { "ControllerEnabled": 1, "PATH_APPLICATION": "{game_dir_win}" } }
```

**`pak_edit`** — replace members inside a zip-format game archive.
```json
{ "type": "pak_edit", "archive": "LevelPacks/pak0.lp",
  "insert": [ { "from": "payload/wet/maleAverage",
                "into": "Game/Disc/Characters/maleAverage" } ] }
```

**`symlink`** — make two paths share one real folder (save-path fixes).
```json
{ "type": "symlink", "link": "{prefix}/drive_c/.../SKIDROW",
  "target": "TPepperoni666", "optional": true }
```

**`steam_shortcut`** — create or update the non-Steam Steam entry.
```json
{ "type": "steam_shortcut", "exe": "Binaries/Game.exe",
  "start_dir": "{game_dir}", "proton": "GE-Proton10-34" }
```

**`proton_version`** — force a compat tool for a game you own on Steam.
```json
{ "type": "proton_version", "tool": "GE-Proton10-34" }
```

**`install_runner`** — make sure a GE build is present; mark it optional.
```json
{ "type": "install_runner", "name": "GE-Proton10-34", "optional": true }
```

**`launch_options`** — set Steam launch options.
```json
{ "type": "launch_options", "value": "{home}/gfm-wrappers/game.sh %command%" }
```

**`systemd_unit`** — install and enable a unit from the payload.
```json
{ "type": "systemd_unit", "unit": "payload/tcu-network.service",
  "scope": "system", "enable": true, "start": false }
```

**`pitcrew_compile`** — The Crew mods, via PitCrewCompiler.
```json
{ "type": "pitcrew_compile", "payload": "racesaplenty",
  "package_version": 5 }
```

A `type` not in this list fails at **load** time, not apply time — so a typo
shows up immediately rather than half way through installing something.

---

## Appids — the one that keeps biting

Non-Steam games get `crc32("gfm:<recipe-id>") | 0x80000000`. It is derived
from the id, not the path, so it survives reimages, drive moves and re-adds.

**But if you deploy a folder BEFORE writing its recipe**, Deploy has already
made a generic shortcut at `crc32("gfm:deploy:<folder name>")` — a different
number. Applying the recipe then creates a *second* Steam entry and orphans
the prefix and artwork attached to the first.

This has happened three times (Gears of War 2 - Hollow, Skate 3, Banjo). The
fix is to pin the existing id in `store/prefix_registry.json`:

```json
{ "appid": 3121635969, "name": "Banjo-Kazooie: Recompiled",
  "kind": "non_steam", "recipe_id": "banjo-kazooie-recomp" }
```

To find the id a game already has, look in
`<NAS>/_state/shortcuts/<hostname>.json`.

Pins you write by hand go in `prefix_registry.json` (curated, in git). Pins
the tool discovers go in `<NAS>/_state/adopted_appids.json` — never in git,
so updates never conflict and they survive a reimage.

---

## Testing before you trust it

```bash
python3 gfm.py list          # does it load and detect?
python3 tests/smoke_test.py  # does the store still validate?
```

`list` shows every recipe, whether the game was found, and whether the fix
reads as applied. If your recipe doesn't appear, the manifest failed to load;
if it appears but the game doesn't, detection is wrong.

Apply prints each step as it runs, and everything is logged to `gfm.log`.
Reverting restores `.gfm-orig` / `.gfm-bak` backups — steps that can't be
safely undone say so instead of pretending.

---

## Worked example

Non-Steam UE3 game: pin a runner, fix the resolution, add the shortcut.

```json
{
  "id": "example-ue3-game",
  "name": "Example Game",
  "aliases": ["ExampleGame"],
  "detect": {
    "install_dir_names": ["Example Game"],
    "marker_files": ["Binaries/ExampleGame.exe"]
  },
  "save_paths": ["{game_dir}/saves"],
  "steps": [
    { "type": "install_runner", "name": "GE-Proton10-34", "optional": true },
    { "type": "ini_edit",
      "target": "{game_dir}/ExampleGame/Config/DefaultEngine.ini",
      "values": {
        "SystemSettings": { "ResX": 1920, "ResY": 1200,
                            "Fullscreen": "True" }
      } },
    { "type": "steam_shortcut", "exe": "Binaries/ExampleGame.exe",
      "start_dir": "{game_dir}/Binaries", "proton": "GE-Proton10-34" }
  ],
  "post_apply_message": "Set Graphics API to Vulkan in the in-game options.",
  "notes": "Marker verified on disk 2026-08-05. UNTESTED on hardware."
}
```

Note the booleans written as the **strings** `"True"`/`"False"`. `ini_edit`
turns a JSON `true` into `1`, which UE3 accepts but which won't match the
file's own spelling — worth matching whatever the game already writes.

---

## Habits worth copying

- **Verify every path against the real folder** before committing. Most broken
  recipes are a guessed filename or the wrong case.
- **Say what's untested.** Recipe `notes` are where "UNTESTED on hardware"
  belongs, so nobody later assumes it was proven.
- **Put manual steps in `post_apply_message`.** Anything that can't be
  scripted — an in-game setting, a licence prompt — should be told to the
  person right after Apply, not buried in notes they'll never open.
- **Prefer `optional: true`** for anything touching a prefix that may not
  exist yet.
