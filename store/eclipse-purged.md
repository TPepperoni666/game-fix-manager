# Eclipse mods — PURGED (exe-swap, no longer hosted)

These Eclipse performance mods **replace the game `.exe`**, which pins them to a
specific game build and breaks saves when the game updates past the mod. Per
Tony (2026-07-09) we keep **only the pak-only** Eclipse mods and purge every
exe-swap one: recipes removed from the tool (hidden), payloads no longer hosted
on the NAS / local-payloads. This file is the written record of what was dropped
so we remember these games have an Eclipse mod out there if we ever want it back
(git history still holds the deleted recipes).

## Kept (pak-only — save-safe, build-tolerant)

- Avowed (`eclipse-avowed`)
- Final Fantasy VII Remake (`eclipse-ff7-remake`)
- Gothic 1 Remake (`eclipse-gothic-1-remake`)
- Oblivion Remastered (`eclipse-oblivion-remastered`)
- Stellar Blade (`eclipse-stellar-blade`)

## Purged (exe-swap) — 22 recipes

| game | recipe id | steam appid |
|------|-----------|-------------|
| Alan Wake 2 | eclipse-alan-wake-2 | |
| The Callisto Protocol | eclipse-callisto-protocol | |
| Clair Obscur: Expedition 33 | eclipse-clair-obscur | |
| Cronos: The New Dawn | eclipse-cronos | |
| Cyberpunk 2077 | eclipse-cyberpunk-2077 | 1091500 |
| Death Stranding 2: On the Beach | eclipse-death-stranding-2 | |
| DOOM: The Dark Ages | eclipse-doom-dark-ages | |
| Final Fantasy VII Rebirth | eclipse-ff7-rebirth | |
| Hell Is Us | eclipse-hell-is-us | |
| Hogwarts Legacy | eclipse-hogwarts-legacy | |
| Indiana Jones and the Great Circle | eclipse-indiana-jones | |
| Star Wars Jedi: Survivor | eclipse-jedi-survivor | |
| LEGO Batman: Legacy of the Dark Knight | eclipse-lego-batman | 2215200 |
| Mafia: The Old Country | eclipse-mafia-old-country | |
| MGS Delta: Snake Eater | eclipse-mgs-delta | |
| MindsEye | eclipse-mindseye | |
| Silent Hill 2 | eclipse-silent-hill-2 | |
| Marvel's Spider-Man 2 | eclipse-spider-man-2 | |
| S.T.A.L.K.E.R. 2 | eclipse-stalker-2 | |
| Starfield | eclipse-starfield | |
| Styx: Blades of Greed | eclipse-styx-blades | |
| Wuchang: Fallen Feathers | eclipse-wuchang | |

## Still to do (manual)

- Delete the corresponding exe-swap payload folders from the NAS / local-payloads
  (they're gitignored, so not in this repo — GFM just stopped referencing them).
- `tools/stage-eclipse.py` now stages only the 5 kept pak-only mods (index pruned).
