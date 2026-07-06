#!/bin/bash
# GFM wrapper for Transformers: War for Cybertron — starts Psych's FPS
# unlocker trainer inside the game's Proton prefix, runs the game, cleans up.
#
# Steam calls this via launch options:  twfc.sh %command%
# %command% is the full launch stack (Steam Linux Runtime entry point ->
# proton -> game exe). To run the trainer in the SAME prefix we reuse that
# whole stack, swapping the game exe for the trainer and the verb
# 'waitforexitandrun' for 'run' (second-instance verb).
#
# In game: press Numpad 0 to unlock the FPS cap (bind a back button to
# Num0 in Steam Input). Cap the game at 60 in the QAM — it glitches above.

TRAINER="$HOME/TWFC_Unlocker/TWFC_FPS_Unlocker.exe"

# Build the trainer command from the game command
TRAINER_CMD=()
for arg in "${@:1:$#-1}"; do
    [ "$arg" = "waitforexitandrun" ] && arg="run"
    TRAINER_CMD+=("$arg")
done
TRAINER_CMD+=("$TRAINER")

# Give the game (and the prefix's wineserver) time to come up, then attach
if [ -f "$TRAINER" ]; then
    ( sleep 20; exec "${TRAINER_CMD[@]}" ) &
    HELPER_PID=$!
else
    echo "gfm/twfc: trainer not found at $TRAINER — launching game plain" >&2
    HELPER_PID=""
fi

"$@"
STATUS=$?

# Game exited — stop the delayed launcher and any lingering trainer process
[ -n "$HELPER_PID" ] && kill "$HELPER_PID" 2>/dev/null
pkill -f "TWFC_FPS_Unlocker" 2>/dev/null

exit $STATUS
