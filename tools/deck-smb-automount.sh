#!/bin/bash
# Set up a systemd AUTOMOUNT for the Game Fixes SMB share on SteamOS / Linux.
#
# Why automount (not a plain mount): it mounts on-demand the moment something
# reads the path, and unmounts when idle. That means:
#   * survives reboots (the .automount unit is enabled)
#   * NEVER hangs boot if the NAS is offline (lazy — only tries on access)
#   * away from home the path is simply empty until Tailscale is up, then works
#
# Run once (it'll ask for sudo). Re-run after a reimage to restore it.
#
# Override any default with an env var, e.g.:
#   SMB_HOST=192.168.1.33 SHARE="Game Fixes" ./deck-smb-automount.sh
set -euo pipefail

SMB_HOST="${SMB_HOST:-192.168.1.33}"
SHARE="${SHARE:-Game Fixes}"
MOUNT_POINT="${MOUNT_POINT:-$HOME/mnt/game-fixes}"
CRED_FILE="${CRED_FILE:-/etc/gfm-smb.cred}"

echo "Game Fixes SMB automount setup"
echo "  server : //$SMB_HOST/$SHARE"
echo "  mount  : $MOUNT_POINT"
echo

# --- preflight: cifs mount helper present? ---
if ! command -v mount.cifs >/dev/null 2>&1 && [ ! -e /sbin/mount.cifs ]; then
    echo "WARNING: mount.cifs (cifs-utils) not found."
    echo "SteamOS usually has it; if the mount later fails with 'wrong fs type',"
    echo "install cifs-utils (you may need: sudo steamos-readonly disable) and"
    echo "re-run this script."
    echo
fi

# --- credentials (root-only file, keeps user/pass out of the unit) ---
read -rp "SMB username (blank for guest): " SMB_USER
SMB_PASS=""
if [ -n "$SMB_USER" ]; then
    read -rsp "SMB password: " SMB_PASS; echo
fi

if [ -n "$SMB_USER" ]; then
    sudo tee "$CRED_FILE" >/dev/null <<EOF
username=$SMB_USER
password=$SMB_PASS
EOF
    sudo chmod 600 "$CRED_FILE"
    CRED_OPT="credentials=$CRED_FILE"
else
    CRED_OPT="guest"
fi

# --- mount point (under $HOME so it's writable + persistent) ---
mkdir -p "$MOUNT_POINT"

# systemd unit names are derived from the mount path
UID_N=$(id -u)
GID_N=$(id -g)
MOUNT_UNIT=$(systemd-escape -p --suffix=mount "$MOUNT_POINT")
AUTO_UNIT=$(systemd-escape -p --suffix=automount "$MOUNT_POINT")

sudo tee "/etc/systemd/system/$MOUNT_UNIT" >/dev/null <<EOF
[Unit]
Description=Game Fixes SMB share (GFM local payloads)
After=network-online.target
Wants=network-online.target

[Mount]
What=//$SMB_HOST/$SHARE
Where=$MOUNT_POINT
Type=cifs
Options=$CRED_OPT,uid=$UID_N,gid=$GID_N,ro,iocharset=utf8,vers=3.0,_netdev,nofail
TimeoutSec=20
EOF

sudo tee "/etc/systemd/system/$AUTO_UNIT" >/dev/null <<EOF
[Unit]
Description=Automount for Game Fixes SMB share

[Automount]
Where=$MOUNT_POINT
TimeoutIdleSec=300

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "$AUTO_UNIT"

echo
echo "Done. The share now automounts on access and after every reboot."
echo "Test it:   ls '$MOUNT_POINT'    (first access triggers the mount)"
echo "Point GFM: python3 ~/game-fix-manager/gfm.py --local-payloads '$MOUNT_POINT'"
echo
echo "To remove later:"
echo "  sudo systemctl disable --now '$AUTO_UNIT'"
echo "  sudo rm /etc/systemd/system/$MOUNT_UNIT /etc/systemd/system/$AUTO_UNIT $CRED_FILE"
