"""Portable, read-only calibration from the currently installed stock roster."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable


FORMAT = 1
ARTIFACTS = ("revision2/phrase-library.joblib", "revision2/salience-model.joblib", "revision2/stock-metrics.json", "revision3/phrase-structure-study.json", "revision4/arrangement-model.joblib")


class CalibrationError(RuntimeError):
    """The live roster cannot safely produce a portable model cache."""


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _training_source_sha() -> str:
    engine = Path(__file__).resolve().parent / "engine"
    paths = [Path(__file__), *(engine / name for name in ("learn_phrase_library.py", "learn_audio_salience.py", "learn_arrangements.py", "study_phrase_structure.py", "generate_phrase_charts.py", "arrangement_metrics.py", "audio_features.py", "decode_corpus.py", "chart_emulator.py"))]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf8")); digest.update(path.read_bytes())
    return digest.hexdigest()


def _identity(game_root: Path) -> dict[str, str]:
    game_root = Path(game_root).resolve(); assembly = game_root / "GameAssembly.dll"
    if not (game_root / "infalsus.exe").is_file() or not assembly.is_file():
        raise CalibrationError(f"not an In Falsus installation: {game_root}")
    return {"game_root": str(game_root), "game_assembly_sha256": _sha256(assembly), "training_source_sha256": _training_source_sha()}


def _heldout_songs(songs: list[dict[str, Any]]) -> list[str]:
    names = sorted({str(song["BaseName"]) for song in songs if song.get("BaseName")})
    count = max(1, math.ceil(len(names) * .20))
    return sorted(names, key=lambda name: hashlib.sha256(f"infalsus-calibration-v1:{name}".encode("utf8")).digest())[:count]


def _progress(progress, stage: str, current: int | None = None, total: int | None = None) -> None:
    if progress:
        progress(stage if current is None else f"{stage}: {current}/{total}")


@contextmanager
def _exclusive_lock(cache_root: Path):
    lock = cache_root / "calibration.lock"; cache_root.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise CalibrationError(f"calibration is already running: {lock}") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii")); yield
    finally:
        os.close(descriptor); lock.unlink(missing_ok=True)


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write("\n"); temporary = Path(stream.name)
    os.replace(temporary, path)


def _cache_matches(cache_root: Path, identity: dict[str, str]) -> bool:
    manifest = _read_manifest(cache_root / "calibration-manifest.json")
    return bool(manifest and all(manifest.get(key) == value for key, value in identity.items())
        and manifest.get("format") == FORMAT and all((cache_root / item).is_file()
        and _sha256(cache_root / item) == manifest.get("artifact_sha256", {}).get(item) for item in ARTIFACTS))


def _official_songs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("BaseName") or "") and not str(row["BaseName"]).lower().startswith("custom_") and any(chart.get("Available") for chart in row.get("ChartInfos", []))]


def _live_roster(game_root: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    from . import installer
    unity, parse_binary = installer._runtime()
    bundles = installer._catalog_bundle_paths(game_root, parse_binary)
    song_env = unity.load(str(game_root / bundles["SongData"])); _, song_data = installer._named_object(song_env, "SongData")
    map_env = unity.load(str(game_root / bundles["StreamingAssetsMapping"])); _, mapping_data = installer._named_object(map_env, "StreamingAssetsMapping")
    songs = _official_songs(song_data.get("allSongInfo", []))
    if len(songs) < 4:
        raise CalibrationError("current SongData has fewer than four official playable songs")
    mapping = {str(row["FullLookupPath"]): str(row["Guid"]) for row in mapping_data.get("Entries", []) if row.get("FullLookupPath") and row.get("Guid")}
    return songs, mapping


def _atomic_npz(path: Path, **values: Any) -> None:
    import numpy as np
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.tmp.npz")
    np.savez_compressed(temporary, **values); os.replace(temporary, path)


def _extract_corpus(game_root: Path, cache_root: Path, songs: list[dict[str, Any]], mapping: dict[str, str], progress=None, cancel=None) -> None:
    import soundfile as sf
    from .engine.audio_features import extract
    from .engine.chart_emulator import Decoder
    from .engine.decode_corpus import decode_audio

    corpus = cache_root / "corpus"; charts = corpus / "charts"; features = corpus / "features"; charts.mkdir(parents=True, exist_ok=True); features.mkdir(parents=True, exist_ok=True)
    sam = game_root / "infalsus_Data" / "StreamingAssets" / "sam"; decoder = Decoder(game_root); chart_count = 0
    receipts_path = corpus / 'extraction-receipts.json'
    receipts = _read_manifest(receipts_path) or {}
    feature_code = _sha256(Path(__file__).parent / 'engine/audio_features.py')
    decoder_code = _sha256(Path(__file__).parent / 'engine/chart_emulator.py')
    for song_index, song in enumerate(songs, 1):
        if cancel and cancel(): raise CalibrationError("calibration cancelled")
        base = str(song["BaseName"]); audio_path = features / f"{base}.npz"
        guid = mapping.get(f"{base}.wav")
        source = sam / str(guid) if guid else None
        if source is None or not source.is_file(): raise CalibrationError(f"missing current audio mapping for {base}")
        source_hash = _sha256(source)
        key = f'audio:{base}'
        previous = receipts.get(key, {})
        if not audio_path.is_file() or previous.get('source') != source_hash or previous.get('code') != feature_code or previous.get('output') != _sha256(audio_path):
            wave, sample_rate = sf.read(io.BytesIO(decode_audio(source.read_bytes())), dtype="float32", always_2d=True)
            with tempfile.NamedTemporaryFile(suffix=".wav", dir=corpus, delete=False) as stream:
                temporary = Path(stream.name)
            try:
                sf.write(temporary, wave, sample_rate); extracted = extract(temporary)
            finally:
                temporary.unlink(missing_ok=True)
            _atomic_npz(audio_path, t=extracted["t"], features=extracted["features"], peaks=extracted["peaks"])
            receipts[key] = dict(source=source_hash, code=feature_code, output=_sha256(audio_path))
        for chart in song["ChartInfos"]:
            if not chart.get("Available"): continue
            chart_count += 1; target = charts / f"{chart['Id']}.json"
            guid = mapping.get(f"{chart['Id']}.spc")
            source = sam / str(guid) if guid else None
            if source is None or not source.is_file(): raise CalibrationError(f"missing current chart mapping for {chart['Id']}")
            source_hash = _sha256(source); key = f"chart:{chart['Id']}"; previous=receipts.get(key,{})
            if target.is_file() and previous.get('source')==source_hash and previous.get('code')==decoder_code and previous.get('output')==_sha256(target): continue
            _write_json(target, decoder.decode(source.read_bytes(), str(chart["Id"])))
            receipts[key] = dict(source=source_hash, code=decoder_code, output=_sha256(target))
        _write_json(receipts_path, receipts)
        _progress(progress, "extracting official charts", song_index, len(songs))
    if chart_count < 16: raise CalibrationError("current roster has fewer than 16 playable official charts")


def _build_models(cache_root: Path, songs: list[dict[str, Any]], heldout: list[str], progress=None, cancel=None) -> dict[str, Any]:
    from .engine import learn_arrangements, learn_audio_salience, learn_phrase_library, study_phrase_structure
    corpus = cache_root / "corpus"
    report = lambda stage, current, total: _progress(progress, stage, current, total)
    return {"phrase_library": learn_phrase_library.train(corpus, songs, heldout, cache_root, report, cancel),
            "salience": learn_audio_salience.train(corpus, songs, heldout, cache_root, report, cancel),
            "phrase_structure": study_phrase_structure.study(corpus, songs, cache_root, report, cancel),
            "arrangements": learn_arrangements.train(corpus, songs, heldout, cache_root, report, cancel)}


def ensure_models(game_root, cache_root, progress, cancel=None) -> Path:
    """Return a hash-bound model cache, extracting only the installed official roster."""
    if hasattr(cancel, 'is_set'): cancel = cancel.is_set
    game_root, cache_root = Path(game_root).resolve(), Path(cache_root).resolve(); identity = _identity(game_root)
    if _cache_matches(cache_root, identity):
        _progress(progress, "using calibrated official-chart models"); return cache_root
    with _exclusive_lock(cache_root):
        if _cache_matches(cache_root, identity): return cache_root
        if cancel and cancel(): raise CalibrationError("calibration cancelled")
        _progress(progress, "reading current official roster")
        songs, mapping = _live_roster(game_root); heldout = _heldout_songs(songs)
        _extract_corpus(game_root, cache_root, songs, mapping, progress, cancel)
        receipt = _build_models(cache_root, songs, heldout, progress, cancel)
        if not all((cache_root / item).is_file() for item in ARTIFACTS): raise CalibrationError("calibration did not produce every required model artifact")
        _write_json(cache_root / "calibration-manifest.json", {"format": FORMAT, **identity, "songs": len(songs), "charts": sum(1 for song in songs for chart in song["ChartInfos"] if chart.get("Available")), "heldout_songs": heldout, "training": receipt,
            "artifact_sha256": {item: _sha256(cache_root / item) for item in ARTIFACTS}})
    return cache_root
