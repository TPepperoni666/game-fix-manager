# Wet Clothes Effect Restoration — payload pending

Waiting on the mod file: **Wet Clothes Effect Restoration** from ModDB
(https://www.moddb.com/mods/... — ModDB download links are tokenized, so it
can't be a remote_payload; download it manually).

Once downloaded, extract here so it looks like:

- `maleAverage/`  — contents of the mod's maleAverage.rar
  (→ injected into pak0.lp at Game/Disc/Characters/maleAverage)
- `darthVader/`   — contents of the mod's Vader.rar (optional wet Vader)
  (→ injected at Game/Disc/Characters/maleBrute/darthVader)

The pak_edit step is marked `optional`, so applies work fine before this
payload exists — it just logs a skip.
