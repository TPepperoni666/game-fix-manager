# GFM — To Do / Test Checklist

Last updated 2026-07-13. Deeper context lives in the Claude memory files
(`MEMORY.md` index); this is the actionable list.

---

## 1. Test on the Deck — a lot of new code has never touched real hardware

Everything below is written, unit-tested (159 smoke checks) and pushed, but
**only ever run on a Windows dev box**. Highest-value first.

- [ ] **Run 🔍 Scan once.** Single highest-value action. It:
  - captures **The Crew's `data.bin`** — a real data-loss risk right now,
    covered by no other backup
  - creates NAS `_state/localconfig-<uid>.vdf`, which **unblocks the per-game
    perf/TDP restore** (blocked purely because that file has never existed —
    you've never run capture)
  - exercises the new Scan bundle end-to-end (SD → Steam → reconcile →
    capture-all)
- [ ] **Apply Fixes multi-select.** The arrow-key picker (←→ toggle, Enter
      confirm) has *never actually run* — it existed but was never wired up.
      Confirm the D-pad toggles and that several games apply in one pass.
- [ ] **New menu structure** — Scan / Save Restore / Settings / Advanced all
      navigate, Back works from both submenus.
- [ ] **`import-prefixes`** — first real exercise of `symlinks=True`. Windows
      blocks symlink creation (WinError 1314) so that line is guarded by a
      flag-assertion only. A prefix contains `dosdevices/z: -> /`; if it were
      ever followed it would try to copy the whole root filesystem. **Watch the
      file count / duration on the first import.**
- [ ] **`deploy`** — the BF3 proof of concept (see §2).
- [ ] **Save Restore** bundle (import prefixes → restore all game saves).

### Recipes awaiting a Deck test
- [ ] **Heroes of the Pacific** — it *runs* (needs GE-Proton11-1) but the
      **widescreen fix is unverified**. Set scaling to **Fit**: fills the 16:10
      panel = working; 4:3 pillarbox = ASI not hooking, still 800×600.
      Expected regardless: no AA (game has none) and a hard **30 FPS cap** (no
      patch exists — don't chase it).
- [ ] **GTA IV + FusionFix** — verify `dinput8`-only override is enough. If
      FusionFix's in-game menu doesn't appear, fall back to also overriding
      `d3d9`.
- [ ] **Halo MCC + AlphaRing** — remember MCC must launch with **anti-cheat
      disabled** or the DLL is inert.
- [ ] **Transformers 2 (ROTF)**
- [ ] **Heroes Over Europe** — you're doing the `graphicssettings.exe` hex fix
      manually (path A). Ping me if you want it automated after.

---

## 2. Blocked on you

- [ ] **Stage BF3** → copy `F:\games\Battlefield 3\` (all 12.36 GB, nothing
      excluded) to `\\192.168.1.33\Game Fixes\_games\Battlefield 3\`, then
      ⬇️ Deploy → 📁 Scan → 🔧 Apply. This is the deploy PoC.
- [ ] **HAWX 1** — install to the SD + rescan, then I build it (research done:
      use `HAWX.exe`, the DX9 one; `HAWX_dx10.exe` black-screens).
- [ ] **PitCrew / The Crew mods** — need to know **where PitCrew writes its
      compiled output** before I can spec capture/restore. Either point me at
      its docs or compile once and tell me what changed in the game folder.
- [ ] **Eclipse** — 5 pak-only recipes are dormant; none of those games are
      installed on the Deck.

---

## 3. Ready to build (say the word)

- [ ] **BF3 UI Scaling Fix** — BF3's UI doesn't scale past 720p, so at
      1920×1200 the HUD/menus are tiny. `Engine.BuildInfo_Win32_Retail_dll.dll`
      is already in the install; the fix swaps it. MP-anticheat-incompatible,
      irrelevant for campaign-only.
- [ ] **Tier 2 widescreen classics** (all on the NAS, none on the SD yet):
      NFS Most Wanted (2005) · NFS Underground 2 · NFS Carbon · Ultimate
      Spider-Man · 007 Nightfire · Midnight Club II · True Crime: Streets of
      L.A. · Marvel Ultimate Alliance · Spider-Man 3 / Web of Shadows ·
      Blazing Angels 2
- [ ] **BF2 → Steam-native migration** — plan is settled: copy `mods/zcf` (it
      bundles the custom levels) + ReShade/GEM root files from the NAS zip onto
      Steam appid **24860**, migrate prefix 3564688670 → compatdata/24860, keep
      the non-Steam copy as fallback. Launch:
      `%command% +modPath mods/zcf +menu 1 +fullscreen 1 +szx 1920 +szy 1200`.
- [ ] **EA WRC v1.8.1 downgrade** — the legit route to playing it on Linux
      (pre-anti-cheat build, your own copy, via DepotDownloader + freeze
      updates). Offered; not taken up.
- [ ] **Per-game perf/TDP restore** — unblocked the moment `_state/` exists
      (see §1, run Scan). TDP is APU-specific (Deck ≠ Legion Go 2) so it should
      be opt-in per device; scaling/VRR/framerate are portable.

---

## 4. Parked / dropped

- **V8 Supercars 3** + **Colin McRae Rally 2005** — both Codemasters, both need
  the exe to run from a C:/D: drive-root *inside* the prefix (SD/Z:\ path broke
  the disc check). V8's `steam_shortcut` was removed for this.
- **Skate 3** — a "recomp", tackle later.
- **HAWX 2** — dropped. It's stuck behind the Uplay launcher (`orbit/uplay.exe`
  in the scan), same dead-launcher trap as Driver SF.
- **Denuvo/EA-anticheat workaround** — declined (piracy-sourced toolchain).

---

## 5. Known caveats in the code

- `prefiximport.restore` is **not atomic**: if the copy dies midway your live
  prefix is safe in `<appid>.gfm-prefixbak`, but the target is left partial.
  Recoverable, not transactional.
- `smoke_test.py`'s remote-payload extraction check **fails under Git Bash on
  Windows** (GNU tar reads `C:\...` as a hostname). Pre-existing, environment
  only — passes under PowerShell/bsdtar and on the Deck.
- Deck-verified already (don't re-test): **L.A. Noire, The Crew, Simpsons**.
