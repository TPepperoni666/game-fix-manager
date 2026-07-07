#!/bin/bash
# GFM wrapper for Driver: San Francisco.
#
# Ubisoft's servers for this game shut down Oct 2022. The bundled
# DriverSanFranciscoLauncher.exe still tries to authenticate on start, which
# under Proton either times out or hangs. PCGW confirms the game runs
# standalone from Driver.exe with no functional loss (single-player only —
# multiplayer's dead regardless).
#
# Steam calls this via launch options:  dsf-launch.sh %command%
# %command% expands to the full Proton launch stack whose LAST arg is the
# launcher exe. We keep everything else intact and swap that last arg for
# Driver.exe from the same directory.

new_args=()
last_i=$(($# - 1))
i=0
for arg in "$@"; do
    if [ "$i" -eq "$last_i" ] && [[ "$arg" == *.exe ]]; then
        dir="$(dirname "$arg")"
        # Both capitalisations exist across depot revisions — try each
        if [ -f "$dir/Driver.exe" ]; then
            new_args+=("$dir/Driver.exe")
        elif [ -f "$dir/driver.exe" ]; then
            new_args+=("$dir/driver.exe")
        else
            echo "gfm/dsf: no Driver.exe next to $arg — falling back to launcher" >&2
            new_args+=("$arg")
        fi
    else
        new_args+=("$arg")
    fi
    i=$((i + 1))
done

exec "${new_args[@]}"
