# How the textures were made

These are the scripts that produced the released textures. They are here for transparency and for anyone who wants
to build on them. You do **not** need them to install the mod (use the installer from the
[Releases](https://github.com/oxenar7/metaphor-refantazio-remaster/releases) page).

They are working research scripts, not a polished tool. Every step caches its results and can be resumed. Expect to
read the code.

## Requirements

- Windows, Python 3.11+, an NVIDIA GPU with CUDA PyTorch, plus `numpy`, `Pillow`, `spandrel` and `safetensors`
- `texconv.exe` from [DirectXTex](https://github.com/microsoft/DirectXTex/releases) (BC7 encode/decode)
- the model [2x-AnimeSharpV4_Fast_RCAN_PU](https://openmodeldb.info/models/2x-AnimeSharpV4-Fast-RCAN-PU) by Kim2091
- the Poly Haven materials listed in the main README, 2K JPG (`<id>_diff_2k.jpg`, `<id>_nor_gl_2k.jpg`), in one folder
- about 150 GB of free disk space for the work folder

Copy `config.example.json`, fix the paths, then run `python -I cpk_toc.py <base.cpk> <toc.tsv>` once to list the archive.

## Order

All scripts take the config as their first argument (`python -I <script> <cfg.json> <phase>`).

| Step | Script and phases | What it does |
|---|---|---|
| 1 | `upscale_all.py` `index`, `decode`, `up`, `encode` | Finds every colour texture in `COMMON/model` plus the `XBOX/model` files that have no `COMMON` copy. Upscales 2x on the GPU with 3 steps of back-projection, so that downscaling the result gives the original again (PSNR ≥ 69 dB). Field and vehicle textures up to 2048 px go to 4096, in tiles. |
| 2 | `grain_all.py` `select`, `decode`, `build`, `encode` | Sorts field textures by name into ground, wood, stone, plaster, floor and roof tile. Adds the high-pass detail of a matching photo to the luminance, keeping the local mean and the original colours. Wood grain is rotated to follow the texture. Normal maps get the photo's relief (whiteout blend). |
| 3 | `carriage.py` `build`, `encode` | The Grand Trad carriage atlas: 4x and plank-oriented wood grain. |
| 4 | `cavity.py` `select`, `decode`, `build`, `encode` | Grand Trad stone and paving: bakes cavity and ambient occlusion from the game's height maps, or from the normal map. |
| 5 | `best_source.py` `scan`, `build` | The game often ships the same atlas at 1/2 to 1/8 of its size in other object packs. Each small copy gets the biggest one's final texture after a layout check: global correlation, a per-block band-pass test, and hybrid or patch modes for variants that differ. |
| 6 | `cavity_bigs.py` `add`, `encode` | Gives cavity to the big copies that step 5 now uses everywhere. |
| 7 | `upscale_all.py` `pack` | Writes the .TEX files. Layer priority: best > cavity > special > grain > upscale. |
| 8 | `make_recipe_ehtria.py`, `remaster.py`, `build_ground.py` | Eht Ria ground and wood retexture, by entry name in every field file that uses them. The ground job files are not included; see the code. |
| 9 | `validate_mod.py`, `audit_*.py` | Checks entry names and sizes against base.cpk, and compares every changed texture with the original (luminance, alpha, wrong-texture detection). |

`../tools/release/make_release.py` then turns the finished mod folders into the installer's manifest and data parts.

## Format notes

- `.TEX` is a big-endian u32 count, then for each entry a 256-byte name, a big-endian u32 size and a DDS (mostly
  BC7, DX10 header). The game zero-pads each DDS to 32 bytes.
- `base.cpk` is a CRI CPK with a plain `@UTF` table of contents. The files are stored uncompressed.
- The game reads `COMMON/model` (the "4K" set) only when Metaphor Essentials' *Force 4K Assets* is on. Otherwise it
  reads `XBOX/model`.
