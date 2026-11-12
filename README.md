# Metaphor: ReFantazio Remaster

A game-wide texture remaster for **Metaphor: ReFantazio** (PC, Steam) that keeps the original art style. It is a
Reloaded-II mod built on Metaphor Essentials.

- **Sharper textures everywhere.** Every low-resolution colour texture (environments, characters, enemies,
  weapons, items, vehicles) is upscaled 2x with an anime-tuned AI model. Large environment and vehicle atlases go to
  4096 px. A back-projection step keeps colours and shapes faithful: shrinking the new texture back down gives the
  original again.
- **Real material detail.** Wood, ground, stone, plaster, floors and roof tiles get fine grain from real photo
  materials (Poly Haven, CC0). The game's own colours stay, and the normal maps get the matching relief.
- **Grand Trad stone** gets baked cavity and ambient occlusion, made from the game's own height maps.
- **Best source everywhere.** The game often ships the same atlas at 1/2 to 1/8 of its size in another place. Every
  copy now uses the sharpest one, for example the shopfronts of Via Sunlumeo.
- **Eht Ria** gets a new ground (packed earth, grass, jungle soil, sand) and new wood for planks, beams and scaffolding.

About 35,000 textures in 6,592 game files are changed.

## Requirements

- Metaphor: ReFantazio on Steam, Windows 10/11. The release was made from Steam build 18330018.
- [Reloaded-II](https://github.com/Reloaded-Project/Reloaded-II/releases) with
  [Metaphor Essentials](https://gamebanana.com/mods/548481) by DeathChaos. Reloaded-II also installs its
  dependency CRI FileSystem V2 Hook.
- **Force 4K Assets** turned on in Metaphor Essentials (Reloaded-II, right click it, then *Configure Mod*). Most of
  the changed textures are in the game's 4K set.
- **About 65 GB of free disk space** during installation: 52 GB for the mod and 12.6 GB for the download. You can
  delete the download afterwards.
- Tested on an RTX 4070 (12 GB) at 3440x1440. Some environment textures are now 4096 px. GPUs with less video
  memory have not been tested.

## Install

1. Install Reloaded-II and Metaphor Essentials, and launch the game once through Reloaded-II.
2. Download `MetaphorRemaster-<version>-installer.zip` from [Releases](https://github.com/oxenar7/metaphor-refantazio-remaster/releases)
   and extract it anywhere.
3. Double-click **`Install.bat`**. It:
   - finds the game and your Reloaded-II folder;
   - downloads the 10 data parts from this repository's release, checks their SHA-256, and resumes if interrupted;
   - rebuilds the mod's texture files from **your own** `base.cpk` plus the new textures, and checks every file it
     writes against its SHA-1.
4. In Reloaded-II, tick **Metaphor: ReFantazio Remaster** for Metaphor and start the game from there.

The installer only writes the new mod folder (`Reloaded-II\Mods\oxenar7.metaphor.remaster`) and its data folder. It
changes nothing in the game folder. To uninstall, delete the mod in Reloaded-II.

- **Manual download:** put the `.bin` files from the release into the installer's `data` folder before running it.
  It uses them instead of downloading.
- **Leave characters and enemies original:** pick option 2 in the installer. That saves 7.8 GB.
- **Game update:** run the installer again. Files whose game original changed are skipped and stay vanilla until a
  new release.
- **Command line:** `Install.bat -GameDir <folder with base.cpk> -ModsDir <Reloaded-II Mods> -Skip character,enemy -All`.

## Compatibility

- Works with [MetaphorFix](https://github.com/Lyall/MetaphorFix) and [Luma](https://github.com/Filoppi/Luma-Framework)
  (DLSS/HDR).
- Other mods that replace the same `.TEX` files conflict with this one. The mod lower in Reloaded-II's load order
  wins.

## Credits

- **Kim2091**: [2x-AnimeSharpV4 Fast RCAN PU](https://openmodeldb.info/models/2x-AnimeSharpV4-Fast-RCAN-PU) upscaling
  model (CC BY-NC-SA 4.0).
- **[Poly Haven](https://polyhaven.com)** (CC0): rough_wood, weathered_planks, weathered_brown_planks,
  forest_ground_04, leafy_grass, mud_forest, forest_leaves_02, coast_sand_04, rocky_terrain_02, rock_face_03,
  painted_plaster_wall.
- **DeathChaos**: Metaphor Essentials. **Sewer56**: Reloaded-II and CRI FileSystem V2 Hook.
- **Microsoft**: [DirectXTex](https://github.com/microsoft/DirectXTex) texconv, used to build the textures.
- **ATLUS / SEGA**: Metaphor: ReFantazio and all of its original art.

## License

- **Code** (installer, release tools, `pipeline/`): MIT, see [LICENSE](LICENSE).
- **Texture data** in the releases: derived from the game's assets, which belong to ATLUS/SEGA, using a model
  licensed CC BY-NC-SA 4.0. It is shared free of charge for non-commercial use. You may share and adapt it under
  the same terms with credit. Never sell it or put it behind a paywall.
- This is a fan project, not affiliated with or endorsed by ATLUS or SEGA. You need your own copy of the game.

## How it was made

The full pipeline (upscaling, material grain, cavity baking, best-source matching, validation) is in
[`pipeline/`](pipeline/README.md). The installer is plain PowerShell with a small C# core compiled on your PC. There is
no executable to trust.
