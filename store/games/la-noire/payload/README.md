# L.A. Noire payload — V-Patch 2.0

`vpatch/` mirrors the patch's `L.A.Noire/` folder and is copied straight into
the game directory:

- `dinput8.dll` — Ultimate ASI Loader (ThirteenAG)
- `plugins/lanvp.asi` — the V-Patch itself (VaanaCZ)
- `plugins/lanvp.ini` — config (FPS cap, FoV, borderless, logo skip)

On Proton the game also needs the launch option
`WINEDLLOVERRIDES="dinput8=n,b" %command%` — the recipe prints this after
applying (automated once the launch_options step lands).

Upstream readme + licenses: `../docs/`.
