# GFM — To Do / Test Checklist

Last updated 2026-07-13. Deeper context lives in the Claude memory files
(`MEMORY.md` index); this is the actionable list.

---

## 0. NEXT SESSION — start here (Tony's pick, 2026-07-13)

> **Staging source is now `E:\Games`** (was `F:\games`). Use E:\Games for any
> future robocopy to NAS `_games/`. Recipe *notes* still cite F:\games as where
> they were built — harmless, historical; recipes reference the SD game_dir, not
> the source drive.


1. **Make the NAS writable on the Deck.** ⬆️ Update, then ⚙️ Settings →
   🔌 Connect NAS Payloads (needs sudo) to reinstall the mount unit as `rw`.
   **Why it matters:** the share is mounted `ro`, and capture WRITES there —
   so right now nothing is backing up **The Crew's `data.bin`** (game-folder
   save, covered by no prefix backup — the one live data-loss risk), your
   custom art, or `_state/localconfig.vdf` (which is the *only* thing blocking
   the per-game perf/TDP restore). The share itself is fine — 35GB was written
   to it from Windows today; it's purely the Deck's mount option, and it was
   our bug (fixed in a34f25c). Old installs keep the `ro` unit until this is
   re-run.
2. **Test Halo MCC + AlphaRing.** Remember MCC must launch with **anti-cheat
   disabled** or the DLL is inert. Untested.
3. **Stage the big four** (~280 GB): Death Stranding 2, RE Requiem, College
   Football 27, Pragmata. DS2 is ~17 GB in from an aborted run — robocopy
   resumes, doesn't restart.
4. **Make the NAS `Game Fixes` share visible from the Windows machine.**
   Likely cause found today: the share is **guest**, and Windows 10/11 blocks
   insecure guest logons by default ("your organization's security policies
   block unauthenticated guest access"). Evidence: Python hit *"Encryption is
   not supported for guest access"* and a later listing got *Permission
   denied*, while robocopy/bash worked — i.e. the SMB session is guest and
   flaky. Right fix = create a real SMB user on TrueNAS and map the share with
   credentials (a drive letter), rather than re-enabling guest logons on
   Windows. That'd fix Tony's Explorer visibility AND the intermittent access
   from tooling.

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
- [ ] **Weekly background scan → reclaim SD space for BIG games removed from
      Steam.** Tony's idea, refined 2026-07-13: a timer (systemd, like
      tcu-network.service) notices when the Steam shortcut for a
      **tool-deployed** game disappears from `shortcuts.vdf` (i.e. Tony deleted
      it), and if that game is **larger than 50 GB**, removes it from the SD.

      **The >50 GB floor is the key safety feature, not just convenience.** Every
      game whose save lives in the GAME FOLDER (The Crew's `data.bin` 24.6GB,
      Simpsons' `Save1` 2.2GB, HOTP — all retro titles) is well under 50 GB, so
      the threshold makes them **ineligible by construction**. What's left over
      50 GB is big modern AAA (Death Stranding 2 122GB, RE Requiem 75GB) whose
      saves live in the PREFIX and survive deletion (hazard 4). So the threshold
      excludes the only case that could actually lose data. Keep the save_paths
      guard anyway as defence-in-depth, but the floor is what makes this safe.

      **The trigger, precisely:** a game is a reclaim candidate only if ALL of:
      (a) it was DEPLOYED by the tool from NAS `_games/` — needs a deployed-games
      record (write one when `deploy` runs); (b) it has a recipe with a
      steam_shortcut + pinned gospel appid; (c) that appid was in LAST week's
      `shortcuts.vdf` snapshot but is ABSENT now; (d) it's still staged in NAS
      `_games/` (so deletion is a re-copy away, never a loss); (e) its on-SD size
      is > 50 GB. Non-Steam shortcuts live in `shortcuts.vdf` (binary — we
      already parse it via `core/shortcutsvdf.py`), NOT the `appmanifest_*.acf`
      files (those are Steam-owned games).

      Remaining hazards to design around, all still real:

      1. **"No shortcut" ≠ "removed".** A freshly deployed game has no shortcut
         until you Apply — the gap between ⬇️ Deploy and 🔧 Apply is exactly
         that state. A rule of "on the SD, not in Steam → delete" wipes the game
         you just spent 10 minutes copying.
      2. **So it needs a SNAPSHOT.** Removal is only detectable as *was in the
         last snapshot, absent now*. Store the known shortcut appids (we already
         pin gospel appids, and `sdmap` already records `shortcut_appid`).
      3. **An empty/rewritten `shortcuts.vdf` looks like "user deleted
         everything".** A reimage, a Steam reset, a botched write — including
         our OWN close-Steam→write→restart dance — could present as every game
         vanishing at once. On a timer, unattended, that's a mass delete. Guard:
         refuse to act if more than 1–2 disappear at once, or if the vdf failed
         to parse, or if the snapshot is stale/missing. Fail closed.
      4. **NEVER TOUCH `compatdata/`. Delete only `<SD>/Games/<name>/`.**
         Most games keep their saves in the PREFIX
         (`compatdata/<appid>/pfx/drive_c/users/steamuser/...`), not the game
         folder. Steam does not remove a non-Steam shortcut's prefix when you
         delete the shortcut — it just becomes an orphan (which is exactly what
         🔗 Reconcile exists to adopt). So for prefix-save games this whole
         feature is **safe and reversible by design**: the prefix survives, and
         because we pin the **gospel appid**, re-deploying + re-applying later
         recreates the shortcut with the SAME appid, which marries straight back
         up to the untouched prefix. Saves intact, nothing lost but the copy
         time. That's the pinned-appid design paying off.
      5. **The exception: GAME-FOLDER saves die with the folder.** The Crew's
         `data.bin`, Simpsons' `Save1`, Heroes of the Pacific's `save/` live
         *inside* the game dir — that's the whole reason `save_paths` exists.
         For any recipe with `save_paths`, deletion MUST refuse unless those
         saves are already captured (`saves.read_index()` non-empty), or capture
         them first. This is the ONLY case where reclaiming space can actually
         lose data.
      6. **Only delete what's restorable.** Require the game to be staged in NAS
         `_games/` first, so a false positive costs a re-copy, not the game.
      7. **Confirm, don't auto-delete.** Better shape: the timer *detects* and
         queues, the tool shows "these 2 look removed, reclaim 19GB?" next time
         you open it. Unattended deletion of user data on a heuristic is how you
         lose trust in a tool permanently.

      Also worth asking whether it's needed: the SD is 2TB with ~1.8TB free, so
      this is tidiness, not pressure. Low priority, high blast radius.

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
