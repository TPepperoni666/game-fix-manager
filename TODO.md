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

- [x] **Stage BF3** — done. It's on the SD, mapped, and Apply works. (The
      documented `singleplayer` launch arg turned out to BREAK it; recipe now
      sets no launch options.)
- [x] **HAWX 1** — recipe built off the Windows install (no SD scan needed) and
      staged to the NAS.
- [ ] **Deploy the newly staged games** — FUEL, PURE (CD), Blur and HAWX 1 are
      all on the NAS at `_games/` **with recipes built**. Just needs
      ⬆️ Update → ⬇️ Deploy → 🔧 Apply on the Deck.
- [ ] **Stage the big four** (~280 GB): Death Stranding 2, RE Requiem, College
      Football 27, Pragmata. DS2 is ~17 GB in from an aborted run — robocopy
      resumes rather than restarts. Only worth it for what you'd actually play
      on a handheld.
- [ ] **Move the prefix backups to the SD** — the scan reported `GBM sources: 0
      SD backups, 0 CSV entries`, so `import-prefixes` has nothing to do until
      they're at `<SD>/steamos_restore/prefix_backups/<name>/<appid>/`. You said
      you'd done this — the next Scan's log line will confirm (count > 0).
- [ ] **PitCrew / The Crew mods** — need to know **where PitCrew writes its
      compiled output** before I can spec capture/restore. Either point me at
      its docs or compile once and tell me what changed in the game folder.
- [ ] **Eclipse** — 5 pak-only recipes are dormant; none of those games are
      installed on the Deck.

---

## 3. Feature ideas (Tony's, 2026-07-13)

- [ ] **"Add shortcuts for games on this device that aren't in Steam."** A menu
      option that finds games present on the SD/disk with no Steam shortcut and
      offers to create them. Today you only get a shortcut if a recipe exists
      and you Apply it — a game with no recipe is invisible. Would lean on the
      SD scan (which already knows folder + exes) rather than needing a recipe.
      Open question: which exe, when a folder has several (FUEL is the warning —
      the obvious `FUEL.exe` is the WRONG one). Probably: propose the best guess,
      let the user confirm/override.
- [ ] **Auto-create the shortcut when a game is deployed from the NAS.** i.e.
      ⬇️ Deploy → offer to Scan + Apply in one go, so a copied game lands
      playable. Deploy is currently recipe-agnostic (it just copies
      `_games/<name>` → `Games/<name>`), so this means teaching it to look for a
      matching recipe afterwards. Sensible: it's the natural end of the
      deploy → scan → apply chain.
- [x] **GE-Proton10-34 as the standard runner** — done. Every new recipe pins it
      unless the game needs otherwise (only Heroes of the Pacific deviates: it
      needs GE-Proton11-1 to launch at all).

## 4. Ready to build (say the word)

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

## 5. Parked / dropped

- **V8 Supercars 3** + **Colin McRae Rally 2005** — both Codemasters, both need
  the exe to run from a C:/D: drive-root *inside* the prefix (SD/Z:\ path broke
  the disc check). V8's `steam_shortcut` was removed for this.
- **Skate 3** — a "recomp", tackle later.
- **HAWX 2** — dropped. It's stuck behind the Uplay launcher (`orbit/uplay.exe`
  in the scan), same dead-launcher trap as Driver SF.
- **Denuvo/EA-anticheat workaround** — declined (piracy-sourced toolchain).

---

## 6. Known caveats in the code

- `prefiximport.restore` is **not atomic**: if the copy dies midway your live
  prefix is safe in `<appid>.gfm-prefixbak`, but the target is left partial.
  Recoverable, not transactional.
- `smoke_test.py`'s remote-payload extraction check **fails under Git Bash on
  Windows** (GNU tar reads `C:\...` as a hostname). Pre-existing, environment
  only — passes under PowerShell/bsdtar and on the Deck.
- Deck-verified already (don't re-test): **L.A. Noire, The Crew, Simpsons**.
