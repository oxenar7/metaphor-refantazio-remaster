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
- **Eht Ria is repainted** (1.0.3). Its ground, sand, moss, bridge planks, beams, scaffolding, poles and tree trunks
  are upscaled from the originals to 2048 px and given a painted finish: the blotches become clean patches of colour
  with crisp edges and soft brush strokes, like the game's painted skies, instead of photo detail. Its well and church
  door, which fill the screen, are 8x, with stone crevices baked from the game's own normal map.
- **Close-up props at 4x** (1.0.1). About 440 small atlases that cutscenes and shops show up close (market stalls,
  furniture, crates, barrels, food) get a second AI pass. Where they are wood, the blocky painting is rebuilt with photo
  grain that follows each plank. Food, cloth and metal are only sharpened.
- **Akademeia bookshelves** (1.0.1): every book spine gets a rounded look, so the shelves no longer read as a flat
  painted wall.
- **Althabury** (1.0.2): shopfronts and the close-up paintings at 8x, about 240 more props and building textures at
  4x, and sharper rusted steel on the iron bridge.
- **Eht Ria details** (1.0.2): church pillars at 8x; the grass patches on the ground rebuilt from a real photo;
  sharper posts, stall, rocks and dragon statue.
- **The 600 blurriest surfaces up to 4096 px** (1.0.4). Every field model was measured against how close the camera
  gets to it from the walkable areas, to find the surfaces whose texture is stretched the most on screen: walls, floors,
  roofs, stairs, doors, shopfronts, rock faces. The 600 worst are rebuilt from the originals with 4x-UltraSharpV2 and
  back-projection, up to 4096 px, and their normal maps get the matching fine relief. Signs, paintings and ornaments
  get a gentler AI step so it does not invent detail.
- **Nothing stays at 256 px or less** (1.0.4). About 470 more small colour textures that were still at 128 or 256 px
  are upscaled to at least 512.
- **Althabury inn and similar** (1.0.4): the azaleas, the ground and the maple trunks around the inn, plus the same
  kinds of flowers, leaves, bark and ground elsewhere, are upscaled and given the painted finish of Eht Ria. The
  castle-style block walls (the inn's brick wall among them) are rebuilt at 2048 px with real block shapes, bevels and
  deep relief.
- **Normal-map fix** (1.0.4): the photo relief that earlier versions added to about 1,600 normal maps had its vertical
  direction flipped (OpenGL style instead of the game's DirectX style), so some bumps read as dents. Fixed.

About 36,500 textures in 6,592 game files are changed.

## Requirements

- Metaphor: ReFantazio on Steam, Windows 10/11. The release was made from Steam build 18330018.
- [Reloaded-II](https://github.com/Reloaded-Project/Reloaded-II/releases) with
  [Metaphor Essentials](https://gamebanana.com/mods/548481) by DeathChaos. Reloaded-II also installs its
  dependency CRI FileSystem V2 Hook.
- **Force 4K Assets** turned on in Metaphor Essentials (Reloaded-II, right click it, then *Configure Mod*). Most of
  the changed textures are in the game's 4K set.
- **About 170 GB of free disk space** for a new install: 134 GB for the mod and 35.7 GB for the download. Updating
  from 1.0.3 needs about 100 GB free. You can delete the download afterwards.
- Tested on an RTX 4070 (12 GB) at 3440x1440. Hundreds of environment surfaces are now 4096 px. GPUs with less
  video memory have not been tested.

## Install

1. Install Reloaded-II and Metaphor Essentials, and launch the game once through Reloaded-II.
2. Download `MetaphorRemaster-<version>-installer.zip` from [Releases](https://github.com/oxenar7/metaphor-refantazio-remaster/releases)
   and extract it anywhere.
3. Double-click **`Install.bat`**. It:
   - finds the game and your Reloaded-II folder;
   - downloads the data parts from this repository's releases (parts that did not change come from the release that
     first published them), checks their SHA-256, and resumes if interrupted;
   - rebuilds the mod's texture files from **your own** `base.cpk` plus the new textures, and checks every file it
     writes against its SHA-1.
4. In Reloaded-II, tick **Metaphor: ReFantazio Remaster** for Metaphor and start the game from there.

The installer only writes the new mod folder (`Reloaded-II\Mods\oxenar7.metaphor.remaster`) and its data folder. It
changes nothing in the game folder. To uninstall, delete the mod in Reloaded-II.

- **Update from an earlier version:** download the new installer zip and run its `Install.bat` the same way. Files
  that did not change are kept and the textures already in your installed files are reused, so going from 1.0.3 to
  1.0.4 downloads 20.6 GB.
- **Manual download:** put the `.bin` files into the installer's `data` folder before running it. It uses them
  instead of downloading. A full install of 1.0.4 needs the 10 parts of release 1.0.0, the 2 of release 1.0.1, the 1 of release 1.0.2, the
  1 of release 1.0.3 and the 15 of release 1.0.4.
  If any are missing, `Install.bat -NoDownload` lists their links.
- **Leave characters and enemies original:** pick option 2 in the installer. That saves 7.9 GB.
- **Game update:** run the installer again. Files whose game original changed are skipped and stay vanilla until a
  new release.
- **Command line:** `Install.bat -GameDir <folder with base.cpk> -ModsDir <Reloaded-II Mods> -Skip character,enemy -All`.

## Compatibility

- Works with [MetaphorFix](https://github.com/Lyall/MetaphorFix) and [Luma](https://github.com/Filoppi/Luma-Framework)
  (DLSS/HDR).
- Other mods that replace the same `.TEX` files conflict with this one. The mod lower in Reloaded-II's load order
  wins.

## Credits

- **Kim2091**: [2x-AnimeSharpV4 Fast RCAN PU](https://openmodeldb.info/models/2x-AnimeSharpV4-Fast-RCAN-PU) and
  [4x-UltraSharpV2 Lite](https://huggingface.co/Kim2091/UltraSharpV2) upscaling models (both CC BY-NC-SA 4.0).
- **[Poly Haven](https://polyhaven.com)** (CC0): rough_wood, weathered_planks, weathered_brown_planks,
  forest_ground_04, leafy_grass, mud_forest, forest_leaves_02, coast_sand_04, rocky_terrain_02, rock_face_03,
  painted_plaster_wall, green_metal_rust, grass_ground.
- **DeathChaos**: Metaphor Essentials. **Sewer56**: Reloaded-II and CRI FileSystem V2 Hook.
- **Microsoft**: [DirectXTex](https://github.com/microsoft/DirectXTex) texconv, used to build the textures.
- **ATLUS / SEGA**: Metaphor: ReFantazio and all of its original art.

## License

- **Code** (installer, release tools, `pipeline/`): MIT, see [LICENSE](LICENSE).
- **Texture data** in the releases: derived from the game's assets, which belong to ATLUS/SEGA, using models
  licensed CC BY-NC-SA 4.0. It is shared free of charge for non-commercial use. You may share and adapt it under
  the same terms with credit. Never sell it or put it behind a paywall.
- This is a fan project, not affiliated with or endorsed by ATLUS or SEGA. You need your own copy of the game.

## How it was made

The full pipeline (upscaling, material grain, cavity baking, best-source matching, validation) is in
[`pipeline/`](pipeline/README.md). The installer is plain PowerShell with a small C# core compiled on your PC. There is
no executable to trust.
