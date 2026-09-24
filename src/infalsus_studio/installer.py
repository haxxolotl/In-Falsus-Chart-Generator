"""Stage and transactionally install one custom song against the live game schema.

The module deliberately owns no old-pack baseline: staging reads the installed
Addressables catalog every time and records the exact files it changed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
import functools
from pathlib import Path
from typing import Any

GAME_ASSEMBLY_SHA256 = "ab1d8fa7739078510fab5f8580095c90ea0f5e9236ac9b918e934cb9ae1b7d9c"
GAME_REL = Path("infalsus_Data")
AA_REL = GAME_REL / "StreamingAssets" / "aa" / "StandaloneWindows64"
SAM_REL = GAME_REL / "StreamingAssets" / "sam"
STATE_NAME = "installation-state.json"
MANIFEST_NAME = "install-manifest.json"


class InstallError(RuntimeError):
    """A guard failed before a live game write was made."""


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temp = Path(stream.name)
    os.replace(temp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError) as exc:
        raise InstallError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise InstallError(f"expected an object in {path}")
    return value


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise InstallError(f"unsafe pack path: {value}")
    return path


def _target(game_root: Path, relative: str) -> Path:
    root = game_root.resolve()
    target = (root / _safe_relative(relative)).resolve()
    if root not in target.parents:
        raise InstallError(f"pack path leaves game root: {relative}")
    return target


def _copy_atomic(source: Path, target: Path, expected: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(source, temporary)
        if _sha256(temporary) != expected:
            raise InstallError(f"copy hash mismatch: {target}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _game_running() -> bool:
    """Use tasklist only; this never opens, focuses, kills, or attaches to the game."""
    if os.name != "nt":
        return False
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq infalsus.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, check=False,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    return "infalsus.exe" in result.stdout.lower()


def _require_closed() -> None:
    if _game_running():
        raise InstallError("In Falsus is running; close it before install or restore.")


def discover_game() -> str | None:
    """Return a verified common Steam install root, without requiring a registry read."""
    candidates = [os.environ.get("INFALSUS_GAME_ROOT", "")]
    for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        candidates.append(f"{drive}:\\SteamLibrary\\steamapps\\common\\In Falsus")
    candidates.extend([
        r"C:\\Program Files (x86)\\Steam\\steamapps\\common\\In Falsus",
        r"C:\\Program Files\\Steam\\steamapps\\common\\In Falsus",
    ])
    for raw in candidates:
        path = Path(raw) if raw else None
        if path and (path / "infalsus.exe").is_file() and (path / "GameAssembly.dll").is_file():
            return str(path)
    return None


def _runtime() -> tuple[Any, Any]:
    try:
        import UnityPy  # type: ignore[import-not-found]
        from addressablestools import parse_binary  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise InstallError(
            "UnityPy and addressablestools must be vendored with In Falsus Studio before staging."
        ) from exc
    return UnityPy, parse_binary


def _named_object(environment: Any, name: str) -> tuple[Any, dict[str, Any]]:
    matches = []
    for obj in environment.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            data = obj.parse_as_dict()
        except Exception:
            continue
        if data.get("m_Name") == name:
            matches.append((obj, data))
    if len(matches) != 1:
        raise InstallError(f"expected exactly one {name}, found {len(matches)}")
    return matches[0]


def _other_bytes(environment: Any, edited_id: int) -> dict[int, bytes]:
    return {obj.path_id: obj.get_raw_data() for obj in environment.objects if obj.path_id != edited_id}


def _same_typetree(left: Any, right: Any) -> bool:
    """Unity serializes metadata floats as float32, while Python keeps float64."""
    if isinstance(left, float) and isinstance(right, float):
        return abs(left - right) <= 0.00001
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_typetree(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_typetree(a, b) for a, b in zip(left, right))
    return left == right


def _save_bundle(UnityPy: Any, environment: Any, obj: Any, data: dict[str, Any], name: str,
                 other_bytes: dict[int, bytes]) -> bytes:
    obj.save_typetree(data)
    raw = environment.file.save()
    if not raw.startswith(b"UnityFS\0"):
        raise InstallError(f"{name} output is not a UnityFS bundle")
    reopened = UnityPy.load(raw)
    _, restored = _named_object(reopened, name)
    if not _same_typetree(restored, data):
        raise InstallError(f"{name} failed UnityPy round-trip")
    current = {item.path_id: item.get_raw_data() for item in reopened.objects if item.path_id in other_bytes}
    if current != other_bytes:
        raise InstallError(f"{name} changed an unrelated serialized object")
    return raw


def _catalog_bundle_paths(game_root: Path, parse_binary: Any) -> dict[str, str]:
    catalog_path = game_root / GAME_REL / "StreamingAssets" / "aa" / "catalog.bin"
    if not catalog_path.is_file():
        raise InstallError(f"missing current Addressables catalog: {catalog_path}")
    catalog = parse_binary(catalog_path.read_bytes(), backend="python")
    result: dict[str, str] = {}
    for key, object_name in (
        ("release/StreamingAssetsMapping.asset", "StreamingAssetsMapping"),
        ("release/SongData.asset", "SongData"),
        ("release/PackData.asset", "PackData"),
    ):
        try:
            dependency = catalog.resources[key][0].dependencies[0].internal_id
        except (KeyError, IndexError, AttributeError) as exc:
            raise InstallError(f"current catalog has no usable {key} location") from exc
        result[object_name] = (AA_REL / Path(dependency).name).as_posix()
    return result


def _required_song(song: dict[str, Any]) -> None:
    required = ("title", "artist", "base_name", "audio_guid", "audio_bytes", "duration_seconds",
                "preview_start_seconds", "preview_end_seconds")
    missing = [field for field in required if field not in song]
    if missing:
        raise InstallError(f"song metadata missing: {', '.join(missing)}")
    if not str(song["base_name"]).startswith("custom_"):
        raise InstallError("base_name must use the custom_ namespace")
    if int(song["audio_bytes"]) < 1 or float(song["duration_seconds"]) <= 0:
        raise InstallError("audio_bytes and duration_seconds must be positive")


def _chart_rows(charts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not 1 <= len(charts) <= 4:
        raise InstallError("a song needs one to four charts")
    rows = []
    for index, chart in enumerate(charts):
        required = ("name", "guid", "rating", "level", "bytes", "payload")
        missing = [field for field in required if field not in chart]
        if missing:
            raise InstallError(f"chart {index} missing: {', '.join(missing)}")
        payload = Path(chart["payload"])
        if not payload.is_file() or payload.stat().st_size != int(chart["bytes"]):
            raise InstallError(f"chart payload size mismatch: {payload}")
        name = str(chart["name"])
        if not name.endswith(".spc"):
            name += ".spc"
        rows.append({**chart, "name": name, "payload": payload})
    if len({str(row["guid"]) for row in rows}) != len(rows):
        raise InstallError("charts use duplicate GUIDs")
    if len({row["name"] for row in rows}) != len(rows):
        raise InstallError("charts use duplicate names")
    return rows


def _song_record(song: dict[str, Any], charts: list[dict[str, Any]], song_id: int) -> dict[str, Any]:
    difficulties = (1, 2, 4, 8)
    title = str(song["title"])
    return {
        "Id": {"Value": song_id}, "BaseName": str(song["base_name"]), "CharacterIdentifier": 0,
        "ChartInfos": [{
            "Id": row["name"].removesuffix(".spc"), "Available": 1,
            "Difficulty": int(row.get("difficulty", difficulties[index])),
            "DisplayChartDesigner": str(row.get("designer", "Custom chart")),
            "DisplayJacketDesigner": str(row.get("jacket_designer", "")),
            "Rating": int(row["rating"]), "LevelSectionIndicator": str(row["rating"]),
        } for index, row in enumerate(charts)],
        "PreviewStartSeconds": float(song["preview_start_seconds"]),
        "PreviewEndSeconds": float(song["preview_end_seconds"]),
        "LocalizationToTitleReadingOverride": [title] * 6,
        "ArtistReadingOverride": str(song["artist"]), "GameplayBackground": 3, "RewardStyle": 0,
    }


def _replace_or_append(rows: list[dict[str, Any]], key: str, value: str, item: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    result = copy.deepcopy(rows)
    matches = [index for index, row in enumerate(result) if row.get(key) == value]
    if len(matches) > 1:
        raise InstallError(f"duplicate {key} in current schema: {value}")
    if matches:
        result[matches[0]] = item
        return True, result
    result.append(item)
    return False, result


def _stage_payload(source: Path, payload_root: Path, relative: str) -> dict[str, Any]:
    target = payload_root / _safe_relative(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    digest = _sha256(target)
    if digest != _sha256(source):
        raise InstallError(f"staged payload hash mismatch: {source}")
    return {"path": relative, "payload_sha256": digest, "bytes": target.stat().st_size}


def prepare_install(job_dir: Path, game_root: Path, song: dict[str, Any], charts: list[dict[str, Any],],
                    audio_payload: Path, cover: Path | None = None,
                    decorations_dir: Path | None = None) -> Path:
    """Create an install pack from the currently installed catalog, without game writes."""
    _required_song(song)
    chart_rows = _chart_rows(charts)
    game_root = Path(game_root).resolve()
    audio_payload = Path(audio_payload)
    if not audio_payload.is_file() or audio_payload.stat().st_size != int(song["audio_bytes"]):
        raise InstallError("audio payload does not match audio_bytes")
    if cover is not None and not Path(cover).is_file():
        raise InstallError("cover path is not a file")
    if decorations_dir is not None and not Path(decorations_dir).is_dir():
        raise InstallError("decorations_dir is not a directory")
    if _sha256(game_root / "GameAssembly.dll") != GAME_ASSEMBLY_SHA256:
        raise InstallError("unsupported GameAssembly.dll; rebuild the pack for this installed game version")
    UnityPy, parse_binary = _runtime()
    pack = Path(job_dir).resolve() / "install-pack"
    if pack.exists():
        raise InstallError(f"refusing to overwrite existing staging pack: {pack}")
    payload_root = pack / "payload"
    bundle_paths = _catalog_bundle_paths(game_root, parse_binary)
    files: list[dict[str, Any]] = []
    audio_rel = (SAM_REL / str(song["audio_guid"])).as_posix()
    files.append({"kind": "audio", **_stage_payload(audio_payload, payload_root, audio_rel)})
    for row in chart_rows:
        chart_rel = (SAM_REL / str(row["guid"])).as_posix()
        files.append({"kind": "chart", **_stage_payload(row["payload"], payload_root, chart_rel)})

    originals: dict[str, str | None] = {entry["path"]: _sha256(_target(game_root, entry["path"])) for entry in files}
    baseline = {"GameAssembly.dll": _sha256(game_root / "GameAssembly.dll"),
                (GAME_REL / "StreamingAssets" / "aa" / "catalog.bin").as_posix(): _sha256(game_root / GAME_REL / "StreamingAssets" / "aa" / "catalog.bin")}
    baseline.update({relative: _sha256(_target(game_root, relative)) for relative in bundle_paths.values()})

    # Map the actual bytes first. Existing entries are updated by lookup name, otherwise appended.
    map_rel = bundle_paths["StreamingAssetsMapping"]
    map_env = UnityPy.load(str(_target(game_root, map_rel)))
    map_obj, map_data = _named_object(map_env, "StreamingAssetsMapping")
    map_other = _other_bytes(map_env, map_obj.path_id)
    mapping_before = copy.deepcopy(map_data)
    entries = map_data.get("Entries")
    if not isinstance(entries, list):
        raise InstallError("StreamingAssetsMapping.Entries is not a list")
    by_lookup = {entry.get("FullLookupPath"): entry for entry in entries}
    if len(by_lookup) != len(entries):
        raise InstallError("StreamingAssetsMapping has duplicate lookup paths")
    mapping_rows = [(f"{song['base_name']}.wav", str(song["audio_guid"]), int(song["audio_bytes"]))]
    mapping_rows.extend((str(row["name"]), str(row["guid"]), int(row["bytes"])) for row in chart_rows)
    for lookup, guid, length in mapping_rows:
        prior = by_lookup.get(lookup)
        item = {"FullLookupPath": lookup, "Guid": guid, "FileLength": length}
        if prior is None:
            entries.append(item)
            by_lookup[lookup] = item
        else:
            prior.update(item)
    guids = [entry.get("Guid") for entry in entries]
    if len(set(guids)) != len(guids):
        raise InstallError("StreamingAssetsMapping would contain duplicate GUIDs")
    old_entries = {entry["FullLookupPath"]: entry for entry in mapping_before["Entries"]}
    new_entries = {entry["FullLookupPath"]: entry for entry in entries}
    changed_lookups = {lookup for lookup, _, _ in mapping_rows}
    if any(new_entries[lookup] != entry for lookup, entry in old_entries.items() if lookup not in changed_lookups):
        raise InstallError("mapping update changed an unrelated entry")
    map_raw = _save_bundle(UnityPy, map_env, map_obj, map_data, "StreamingAssetsMapping", map_other)
    map_target = payload_root / _safe_relative(map_rel); map_target.parent.mkdir(parents=True, exist_ok=True); map_target.write_bytes(map_raw)
    files.append({"path": map_rel, "kind": "mapping", "payload_sha256": _sha256(map_target), "bytes": len(map_raw)})

    song_rel = bundle_paths["SongData"]
    song_env = UnityPy.load(str(_target(game_root, song_rel)))
    song_obj, song_data = _named_object(song_env, "SongData")
    song_other = _other_bytes(song_env, song_obj.path_id)
    songs_before = copy.deepcopy(song_data["allSongInfo"])
    all_songs = song_data.get("allSongInfo")
    if not isinstance(all_songs, list):
        raise InstallError("SongData.allSongInfo is not a list")
    existing = [row for row in all_songs if row.get("BaseName") == song["base_name"]]
    if len(existing) > 1:
        raise InstallError("SongData has duplicate BaseName records")
    ids = [int(row["Id"]["Value"]) for row in all_songs]
    song_id = int(existing[0]["Id"]["Value"]) if existing else max(ids, default=0) + 1
    record = _song_record(song, chart_rows, song_id)
    _, song_data["allSongInfo"] = _replace_or_append(all_songs, "BaseName", str(song["base_name"]), record)
    before_by_name = {row["BaseName"]: row for row in songs_before}
    after_by_name = {row["BaseName"]: row for row in song_data["allSongInfo"]}
    if any(after_by_name[name] != row for name, row in before_by_name.items() if name != song["base_name"]):
        raise InstallError("SongData update changed an unrelated song record")
    song_raw = _save_bundle(UnityPy, song_env, song_obj, song_data, "SongData", song_other)
    song_target = payload_root / _safe_relative(song_rel); song_target.parent.mkdir(parents=True, exist_ok=True); song_target.write_bytes(song_raw)
    files.append({"path": song_rel, "kind": "song-registration", "payload_sha256": _sha256(song_target), "bytes": len(song_raw)})

    # Keep the current dense selector layout. Reusing its existing custom-other pack is safe;
    # cloning or extending selector pools is deliberately outside this installer.
    pack_rel = bundle_paths["PackData"]
    pack_env = UnityPy.load(str(_target(game_root, pack_rel)))
    pack_obj, pack_data = _named_object(pack_env, "PackData")
    pack_other = _other_bytes(pack_env, pack_obj.path_id)
    pack_rows = pack_data.get("PackInfo")
    if not isinstance(pack_rows, list):
        raise InstallError("PackData.PackInfo is not a list")
    packs_before = copy.deepcopy(pack_rows)
    desired_slug = str(song.get("category_slug", "custom-other"))
    target_pack = next((row for row in pack_rows if row.get("Slug") == desired_slug), None)
    if target_pack is None:
        # A fresh game has no custom categories. Use its existing first playable
        # chapter without cloning selector pools or depending on a prior pack.
        target_pack = next((p for p in pack_rows if p.get("SongIds")), None)
        if target_pack is None:
            raise InstallError("The installed game has no usable song category")
        desired_slug = target_pack.get("Slug", "existing-chapter")
    memberships = target_pack.get("SongIds")
    if not isinstance(memberships, list):
        raise InstallError("selected PackData record has no SongIds list")
    membership_ids = [int(item["Value"]) for item in memberships]
    if song_id not in membership_ids:
        memberships.append({"Value": song_id})
    if any(after != before for before, after in zip(packs_before, pack_rows) if after is not target_pack):
        raise InstallError("PackData update changed an unrelated category")
    pack_raw = _save_bundle(UnityPy, pack_env, pack_obj, pack_data, "PackData", pack_other)
    pack_target = payload_root / _safe_relative(pack_rel); pack_target.parent.mkdir(parents=True, exist_ok=True); pack_target.write_bytes(pack_raw)
    files.append({"path": pack_rel, "kind": "pack-registration", "payload_sha256": _sha256(pack_target), "bytes": len(pack_raw)})

    for entry in files:
        if entry["path"] not in originals:
            originals[entry["path"]] = _sha256(_target(game_root, entry["path"]))
        entry["original_sha256"] = originals[entry["path"]]
    if any(entry["payload_sha256"] is None for entry in files):
        raise InstallError("could not hash generated payload")
    if any(_sha256(_target(game_root, relative)) != digest for relative, digest in baseline.items()):
        raise InstallError("live game assets changed while staging; retry against the new current catalog")
    manifest = {
        "format": 2, "game_root": str(game_root), "game_assembly_sha256": GAME_ASSEMBLY_SHA256,
        "song": {key: song[key] for key in ("title", "artist", "base_name", "audio_guid", "audio_bytes", "duration_seconds", "preview_start_seconds", "preview_end_seconds")},
        "charts": [{key: row[key] for key in ("name", "guid", "rating", "level", "bytes")} for row in chart_rows],
        "files": files,
        "protected_files": {"GameAssembly.dll": GAME_ASSEMBLY_SHA256,
                            (GAME_REL / "StreamingAssets" / "aa" / "catalog.bin").as_posix(): _sha256(game_root / GAME_REL / "StreamingAssets" / "aa" / "catalog.bin")},
        "category": {"slug": desired_slug, "song_id": song_id, "strategy": "append to current existing custom PackData record; do not alter selector pools"},
        "jacket": {"mode": "fallback-existing-stock-material", "cover_supplied": cover is not None,
                   "note": "No cover bundle is generated here; SongData keeps its current fallback jacket materials."},
        "decorations": {"mode": "external-root-managed", "provided": decorations_dir is not None,
                        "note": "No BepInEx guide entry is written or replaced by this installer."},
        "source_inputs_unchanged": {"audio": _sha256(audio_payload), "charts": {str(row["guid"]): _sha256(row["payload"]) for row in chart_rows}},
    }
    _write_json(pack / MANIFEST_NAME, manifest)
    return pack


def _manifest_and_state(pack_dir: Path, game_root: Path) -> tuple[dict[str, Any], Path, dict[str, Any] | None]:
    pack_dir = Path(pack_dir).resolve(); game_root = Path(game_root).resolve()
    manifest_path = pack_dir / MANIFEST_NAME
    manifest = _read_json(manifest_path)
    if Path(str(manifest.get("game_root", ""))).resolve() != game_root:
        raise InstallError("pack was prepared for a different game root")
    manifest_hash = _sha256(manifest_path)
    state_path = pack_dir / STATE_NAME
    state = _read_json(state_path) if state_path.is_file() else None
    if state and (state.get("manifest_sha256") != manifest_hash or Path(str(state.get("game_root", ""))).resolve() != game_root):
        raise InstallError("installation receipt belongs to another pack or game root")
    return manifest, state_path, state


def _verify_manifest(pack_dir: Path, game_root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if _sha256(game_root / "GameAssembly.dll") != manifest.get("game_assembly_sha256"):
        raise InstallError("GameAssembly.dll changed; rebuild this pack for the installed game version")
    protected = manifest.get("protected_files", {})
    if not isinstance(protected, dict):
        raise InstallError("manifest protected_files is malformed")
    for relative, digest in protected.items():
        if _sha256(_target(game_root, str(relative))) != digest:
            raise InstallError(f"protected game file changed: {relative}")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise InstallError("manifest files is malformed")
    for entry in files:
        if not isinstance(entry, dict):
            raise InstallError("manifest file entry is malformed")
        relative = str(entry.get("path", "")); payload = pack_dir / "payload" / _safe_relative(relative)
        if _sha256(payload) != entry.get("payload_sha256"):
            raise InstallError(f"pack payload is missing or changed: {relative}")
    return files


def _exclusive_game(function):
    @functools.wraps(function)
    def locked(pack_dir, game_root):
        # OS locks release on crashes; a retained marker cannot strand the user.
        path=Path(game_root)/'.infalsus-studio.install.lock'
        with path.open('a+b') as stream:
            if stream.tell()==0:stream.write(b'0');stream.flush()
            stream.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as error:
                raise InstallError('Another Studio installation or restore is using this game folder.') from error
            try:return function(pack_dir,game_root)
            finally:
                stream.seek(0)
                if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
    return locked


@_exclusive_game
def install_pack(pack_dir: Path, game_root: Path) -> dict[str, Any]:
    """Copy a staged pack with verified backups, payloads first and registration last."""
    _require_closed()
    pack_dir = Path(pack_dir).resolve(); game_root = Path(game_root).resolve()
    manifest, state_path, state = _manifest_and_state(pack_dir, game_root)
    files = _verify_manifest(pack_dir, game_root, manifest)
    current = {str(entry["path"]): _sha256(_target(game_root, str(entry["path"]))) for entry in files}
    if state is None:
        for entry in files:
            if current[str(entry["path"])] != entry.get("original_sha256"):
                raise InstallError(f"game file changed since staging: {entry['path']}")
        backup_root = pack_dir / "backups" / uuid.uuid4().hex
        for entry in files:
            original = entry.get("original_sha256")
            if original is not None:
                _copy_atomic(_target(game_root, str(entry["path"])), backup_root / _safe_relative(str(entry["path"])), original)
        state = {"manifest_sha256": _sha256(pack_dir / MANIFEST_NAME), "game_root": str(game_root),
                 "backup_relative": str(backup_root.relative_to(pack_dir)), "status": "backed_up"}
        _write_json(state_path, state)
    else:
        for entry in files:
            observed = current[str(entry["path"])]
            if observed not in (entry.get("original_sha256"), entry.get("payload_sha256")):
                raise InstallError(f"refusing to overwrite unexpected game change: {entry['path']}")
    backup_root = pack_dir / str(state["backup_relative"])
    changed: list[dict[str, Any]] = []
    rank = {"audio": 0, "chart": 0, "mapping": 1, "song-registration": 2, "pack-registration": 3}
    try:
        _require_closed()
        for entry in sorted(files, key=lambda item: rank.get(str(item.get("kind")), 1)):
            relative = str(entry["path"])
            if current[relative] == entry["payload_sha256"]:
                continue
            _require_closed()
            if _sha256(_target(game_root,relative)) != current[relative]:
                raise InstallError(f'game file changed during installation: {relative}')
            _copy_atomic(pack_dir / "payload" / _safe_relative(relative), _target(game_root, relative), entry["payload_sha256"])
            changed.append(entry)
        for entry in files:
            if _sha256(_target(game_root, str(entry["path"]))) != entry["payload_sha256"]:
                raise InstallError(f"post-install hash mismatch: {entry['path']}")
        state["status"] = "installed"; _write_json(state_path, state)
        return {"status": "installed", "files": len(files), "backup": str(backup_root), "runtime": "not launched"}
    except Exception:
        if not _game_running():
            for entry in reversed(changed):
                relative = str(entry["path"]); target = _target(game_root, relative)
                if _sha256(target) != entry["payload_sha256"]:
                    continue
                original = entry.get("original_sha256")
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    _copy_atomic(backup_root / _safe_relative(relative), target, original)
            state["status"] = "rolled_back"; _write_json(state_path, state)
        raise


@_exclusive_game
def restore_pack(pack_dir: Path, game_root: Path) -> dict[str, Any]:
    """Restore only files proven to be this pack's payload; user saves are never touched."""
    _require_closed()
    pack_dir = Path(pack_dir).resolve(); game_root = Path(game_root).resolve()
    manifest, state_path, state = _manifest_and_state(pack_dir, game_root)
    files = _verify_manifest(pack_dir, game_root, manifest)
    if state is None:
        raise InstallError("no installation receipt exists for this pack")
    backup_root = pack_dir / str(state["backup_relative"])
    _require_closed()
    # Preflight the complete restore before changing its first file.
    for entry in files:
        relative=str(entry['path']);current=_sha256(_target(game_root,relative))
        if current not in (entry.get('original_sha256'),entry.get('payload_sha256')):
            raise InstallError(f'refusing to overwrite unexpected game change: {relative}')
        if current!=entry.get('original_sha256') and entry.get('original_sha256') is not None:
            if _sha256(backup_root/_safe_relative(relative)) != entry['original_sha256']:
                raise InstallError(f'backup missing or changed: {relative}')
    for entry in files:
        relative = str(entry["path"]); target = _target(game_root, relative); current = _sha256(target)
        if current == entry.get("original_sha256"):
            continue
        if current != entry.get("payload_sha256"):
            raise InstallError(f"refusing to overwrite unexpected game change: {relative}")
        original = entry.get("original_sha256")
        if original is None:
            target.unlink()
        else:
            backup = backup_root / _safe_relative(relative)
            if _sha256(backup) != original:
                raise InstallError(f"backup missing or changed: {relative}")
            _copy_atomic(backup, target, original)
    for entry in files:
        if _sha256(_target(game_root, str(entry["path"]))) != entry.get("original_sha256"):
            raise InstallError(f"post-restore hash mismatch: {entry['path']}")
    state["status"] = "restored"; _write_json(state_path, state)
    return {"status": "restored", "files": len(files), "progress": "untouched"}
