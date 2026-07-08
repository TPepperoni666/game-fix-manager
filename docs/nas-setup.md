# NAS-hosted local payloads (Game Fixes share)

Big or copyrighted payloads (Eclipse perf mods, patched exes) live off git on
a NAS share and are read on-demand at apply time — nothing permanent lands on
the Deck. This is how it's wired up.

## 1. TrueNAS — the share

- Create a dataset and an **SMB** share named `Game Fixes` (SMB mounts easily
  on SteamOS; NFS is fiddlier on the read-only rootfs).
- Optional: uncheck **Browsable to Network Clients** to hide it from Explorer
  network browsing — it still works by direct path.
- Access control is by SMB user (or enable guest). Away-from-home access is
  gated by Tailscale, so LAN permissions can stay simple.

## 2. Stage the payloads onto it (from Windows)

Windows has 7-Zip and the mod archives, so stage there straight to the share:

```
python tools\stage-eclipse.py "<folder of Eclipse .rar/.zip files>" "\\NAS-IP\Game Fixes"
```

This extracts each mod, picks the right variant (For Licence / v1), and lays
it out as `<recipe-id>/payload/mod/…` — the exact structure GFM expects. Re-run
when a mod updates.

## 3. Deck — automount that survives reboots

```
bash ~/game-fix-manager/tools/deck-smb-automount.sh
```

Prompts for the SMB user/pass (or blank for guest), then installs a systemd
**automount**: mounts on-demand at `~/mnt/game-fixes`, survives reboots, and
never hangs boot if the NAS is offline. Re-run after a reimage.

Then point GFM at it once (persists to config):

```
python3 ~/game-fix-manager/gfm.py --local-payloads ~/mnt/game-fixes
```

## 4. Away from home — Tailscale

Install the Tailscale app on TrueNAS and the client on the Deck. The NAS is
then reachable from anywhere as if you're on the home LAN — the same automount
and apply flow work unchanged. (You only need the NAS at *apply* time; once a
mod is applied its files are in the game dir and the game plays offline.)

## Flow

Install game from Steam → (NAS reachable: home LAN or Tailscale) →
`gfm.py apply eclipse-<game>` → files copy from the NAS mount into the game
dir, originals backed up → play. Revert restores the stock files.
