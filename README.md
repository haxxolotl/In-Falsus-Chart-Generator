# In Falsus Chart Generator (Studio)

A Windows app and Python scripts for turning local audio/video or a **direct media-file URL** into four custom In Falsus charts, then installing them with a reversible backup.

## Use the Windows app

Extract the complete portable folder and open `InFalsusStudio.exe`. Keep its `runtime`, `src` and `scripts` folders alongside it; no Python installation is needed.

For the command line, use `InFalsusStudio-CLI.exe --help`. This separate launcher prints progress/errors, returns the backend exit code, and uses the same bundled runtime. With no arguments it prints help. The graphical executable opens the app with no arguments.

1. Choose an MP3, MP4, WAV, OGG or other FFmpeg-readable file, or paste a direct HTTP(S) file link. Streaming-site webpage URLs are not file links.
2. Confirm the detected game folder. Supply title/artist when the file has no tags; optionally choose cover art and a reference folder.
3. Choose the options and press **Create & install**. Close the game for installation. If it is still running, creation finishes and **Install prepared pack** installs the checked result later.

The first run calibrates against the installed official songs. This takes several minutes; subsequent jobs reuse a verified local cache. Audio, charts, cache, job results and backups stay under `%LOCALAPPDATA%\InFalsusStudio`. The app does not upload media or launch/close the game.

| Option | Behavior |
| --- | --- |
| Search for source chart | Exact-title/artist discovery in the supplied folder, then the public Arcaea AFF repository. Arcaea AFF, Deemo DS with a `.timing.json` sidecar, and canonical Ongeki visual-guide JSON are accepted locally. Raw OGKR and arbitrary chart videos are not parsed. |
| Include decorations | Adds aligned AFF trace/non-input arcs and supported lane/camera scene cues, or visual Ongeki field traces, through the optional SourceGuides plugin. Local AFF timing groups can move these lines independently; playable notes still use the game's native global scroll. Requires **BepInEx 6 for IL2CPP** already installed. |
| Normalize audio | Measures integrated loudness and lifts quiet input with a limiter. Preserves every decoded audio frame and adds a two-second lead-in. |
| Add scroll effects | Translates authored source scroll changes, with rewind-crossing and readability checks. Does not invent random BPM changes. |
| Install after creating | Installs against the exact current catalog, with an exclusive install lock, backups, changed-file checks and rollback on failure. |

**Ordinary charts require only files and registration changes, not BepInEx or a gameplay DLL patch.** Decorations are a separate render-only DLL and chart-hash-bound `guides.json`; they create no judged notes and do not alter scoring. The app merges its guide records with existing ones.

Guides use the full AFF playfield coordinate range. Explicit source arctap cues follow the final surviving note positions and onset times after simplification; unrelated decorative arcs retain their authored shapes. Notes removed during adaptation acquire no cue target. Guides remain visible through contact with the judge line. Supported AFF widening controls draw animated rails around the game's existing four central input lanes and two side targets; they do not add native input lanes or alter the camera. Source timing-group motion affects guide art only, so independently reversing or waterfalling playable notes remains unsupported.

Four source difficulties map to the four target tiers. With three sources, the bottom three map directly, ULT is eased and FBD uses the full highest source. With fewer sources, easier tiers are explicitly derived. An uncertain alignment or a reference for a shorter music edit falls back to the audio arranger and reports the reason.

Charts are automatic arrangements, not human-authored or playtest-certified. Each accepted output is checked for native encoding, time bounds, cursor continuity, input widths, capacity and effect crossings. Generated difficulty labels remain estimates. The source-backed and audio-only routes have different musical fidelity; receipts identify which one ran.

Keyboard playability checks always run **after** source recovery and beat snapping. They compare short bursts, sustained density and per-key recovery against the installed official FBD charts, reserve held keys, and reduce excessive chord layers without shifting surviving notes. FBD-only wide targets provide alternative fingers for repeated-key bottlenecks; clean alternating patterns remain narrow. The measured stock envelope is a conservative automated guard, not a substitute for playing the result. Packs with unresolved violations cannot be installed through generation.

## Installation and recovery

The current release supports the hash-checked game build specified in `installer.py`; a game update with a different executable is rejected before installation. Do not disable that guard. Revalidate the native codec and metadata schema before supporting another build.

Song names and artists are registered in the displayed localization tables. Supplied art is registered for both the song browser and chart/gameplay screens. Without art, the game’s native fallback is registered explicitly. Existing matching custom categories are reused; a clean game uses an existing playable chapter without expanding its selector pools.

Use **Restore backup** to reverse an installation. Restore refuses to overwrite later unrelated changes. Keep the job’s `install-pack` directory and its backups. No save files are included in a pack or modified by the installer.

## Run from source

Use Windows x64 and Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
.\.venv\Scripts\python.exe -m infalsus_studio
```

```powershell
# Create a pack without changing the game.
.\.venv\Scripts\python.exe -m infalsus_studio "song.mp3" --title "Song" --artist "Artist" --no-install

# Source references and optional decorations.
.\.venv\Scripts\python.exe -m infalsus_studio "https://example.org/song.mp4" --reference-dir "C:\Charts\Song" --decorations

# Install or restore an existing prepared pack.
.\.venv\Scripts\python.exe -m infalsus_studio --install-pack "C:\MyJob\install-pack"
.\.venv\Scripts\python.exe -m infalsus_studio --restore-pack "C:\MyJob\install-pack"

# Tests and portable executable.
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\build_windows.ps1
```

`INFALSUS_GAME_ROOT` can specify the game path. `INFALSUS_STUDIO_HOME` relocates local cache/jobs. The reusable entry point is `infalsus_studio.pipeline.run_job(options, progress, cancel)`; CLI and GUI use that same path.

The Windows builder downloads the checksum-verified official CPython 3.12 runtime, copies only declared production dependencies, and compiles a small launcher using Windows' .NET Framework compiler. It writes `dist/InFalsusStudio-Portable` and refuses to overwrite an existing build. The runtime is deliberately not frozen: the native chart codec needs a normal CPython callback environment.

## Project layout

- `src/infalsus_studio`: app, media preparation, calibration, discovery, generation, transactional installation.
- `src/infalsus_studio/engine`: adopted charting, alignment, geometry and native-codec algorithms. Revision suffixes preserve their provenance; historical bulk-pack entry points are not used.
- `decorations`: SourceGuides source. Build using `dotnet build decorations/SourceGuides.csproj -c Release -p:GameRoot="..."` against your own loader/interop installation. Game and loader DLLs are not included in the source project.
- `scripts`: launch/build entry points. `tests`: offline regression checks. `third-party`: dependency notices.

## Repository and release boundaries

Commit source, tests, build files, notices and the small compiled **our-own** SourceGuides plugin. Do not commit user media, reference chart downloads, extracted game assets, learned phrase/model caches, game binaries, job directories, or installation backups. `.gitignore` excludes these and the portable build output. Attach the portable ZIP to a release rather than committing the executable tree.

This is an unofficial local tool, not affiliated with the game developers. Distribute only material you have permission to share; the release contains no game soundtrack or stock chart data. The portable build is for local use; review the FFmpeg redistribution requirements in `third-party/NOTICE.md` before publishing a binary release.

## Verification limits

See `RESULT.md` for the checks performed on this repository. A portable dependency check and offline regression tests do not establish that a newly generated song loads or plays correctly in the game. This repository preparation does not install a song or modify the game. A compatible local game installation is required even for `--no-install`, because calibration, native encoding, and pack staging read it. No official chart-import API is claimed.
