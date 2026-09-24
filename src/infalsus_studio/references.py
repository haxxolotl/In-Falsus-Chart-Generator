"""Bounded discovery of local and public authored chart references.

The resolver deliberately returns source files only.  It does not parse raw
OGKR, fetch audio, or use the historical ``work`` tree; callers can pass the
returned files to the existing engine adapters.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import urllib.request


PUBLIC_REPOSITORY = "https://github.com/HarumiEna/Arcaea-AFF"
_PUBLIC_API_ROOT = "https://api.github.com/repos/HarumiEna/Arcaea-AFF"
_PUBLIC_BRANCH = "main"
_PUBLIC_TREE_URL = f"{_PUBLIC_API_ROOT}/git/trees/{_PUBLIC_BRANCH}?recursive=1"
_PUBLIC_RAW_ROOT = f"https://raw.githubusercontent.com/HarumiEna/Arcaea-AFF/{_PUBLIC_BRANCH}/"
_PUBLIC_METADATA_URL = f"{_PUBLIC_RAW_ROOT}songlist/songlist"
_ALLOWED_REMOTE_HOSTS = {"api.github.com", "raw.githubusercontent.com"}

_MAX_LOCAL_FILES = 4096
_MAX_JSON_BYTES = 8 * 1024 * 1024
_MAX_METADATA_BYTES = 4 * 1024 * 1024
_MAX_TREE_BYTES = 8 * 1024 * 1024
_MAX_CHART_BYTES = 2 * 1024 * 1024
_MAX_CHARTS = 5
_HTTP_TIMEOUT = 12
_MAX_REDIRECTS = 3
_DEFAULT_URL_OPEN = urllib.request.urlopen
_CHART_SUFFIXES = {".aff", ".ds", ".json"}
_DIFFICULTY_RE = re.compile(r"(?:^|[/_-])([0-4])\.aff$", re.IGNORECASE)
_GENERIC_CHART_STEMS = {"0", "1", "2", "3", "4", "basic", "easy", "normal", "hard", "advanced", "expert", "master", "mst", "exp"}
_KNOWN_ONGEKI_FORMATS = {
    "sdvx.in ongeki visual guide",
    "canonicalongekijson",
    "canonical ongeki json",
}


class _Cancelled(Exception):
    pass


class _ReferenceError(Exception):
    pass


def _notify(progress: Callable[[str], Any] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _is_cancelled(cancel: Any) -> bool:
    if cancel is None:
        return False
    if callable(cancel):
        return bool(cancel())
    is_set = getattr(cancel, "is_set", None)
    return bool(is_set()) if callable(is_set) else bool(cancel)


def _check_cancel(cancel: Any) -> None:
    if _is_cancelled(cancel):
        raise _Cancelled()


def _norm(value: Any) -> str:
    """Use exact Unicode-normalized matching, with punctuation ignored."""

    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(char for char in text if char.isalnum())


def _title_forms(value: Any) -> set[str]:
    text = "" if value is None else str(value)
    forms = {_norm(text)} - {""}
    # These are explicit catalog suffixes, not fuzzy matching.  They cover
    # common recording labels while retaining exact title identity.
    base = re.sub(
        r"\s*\((?:feat\.?|long\s+ver\.?|chart\s+view|20\d\d\s+remaster).*?\)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    if base and _norm(base):
        forms.add(_norm(base))
    return forms


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_strings(item))
        return out
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            out.extend(_strings(item))
        return out
    return []


def _unwrap_record(record: dict[str, Any]) -> dict[str, Any]:
    nested = record.get("song_metadata")
    return nested if isinstance(nested, dict) else record


def _record_titles(record: dict[str, Any]) -> list[str]:
    record = _unwrap_record(record)
    values: list[str] = []
    for key in ("title", "name", "title_localized", "search_title"):
        values.extend(_strings(record.get(key)))
    return values


def _record_artists(record: dict[str, Any]) -> list[str]:
    record = _unwrap_record(record)
    values: list[str] = []
    for key in ("artist", "source_artist", "local_artist", "search_artist"):
        values.extend(_strings(record.get(key)))
    return values


def _record_id(record: dict[str, Any]) -> str:
    record = _unwrap_record(record)
    for key in ("id", "source_id", "song_id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _record_match(record: dict[str, Any], title: str, artist: str) -> str:
    title_keys = set().union(*(_title_forms(value) for value in _record_titles(record)))
    if not title_keys.intersection(_title_forms(title)):
        return "no"
    if not artist.strip():
        return "yes"
    artist_keys = {_norm(value) for value in _record_artists(record)} - {""}
    if not artist_keys:
        return "unknown_artist"
    return "yes" if _norm(artist) in artist_keys else "no"


def _result(
    status: str,
    paths: Iterable[Path | str] = (),
    source_game: str | None = None,
    provenance: dict[str, Any] | None = None,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    absolute = [str(Path(path).resolve()) for path in paths]
    return {
        "status": status,
        "paths": absolute,
        "source_game": source_game,
        "provenance": provenance or {},
        "warnings": list(dict.fromkeys(str(item) for item in warnings)),
    }


def _read_json(path: Path, limit: int = _MAX_JSON_BYTES) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > limit:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _canonical_ongeki(data: dict[str, Any]) -> bool:
    marker = _norm(data.get("source_format"))
    known = {_norm(value) for value in _KNOWN_ONGEKI_FORMATS}
    return marker in known and isinstance(data.get("notes"), list) and isinstance(data.get("fields"), list)


def _deemo_ds(data: dict[str, Any]) -> bool:
    return isinstance(data.get("notes"), list) and isinstance(data.get("links"), list)


def _timing_sidecar(path: Path) -> Path | None:
    candidates = (path.with_suffix(".timing.json"), path.with_name(path.name + ".timing.json"))
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size <= _MAX_JSON_BYTES:
            data = _read_json(candidate)
            if isinstance(data, dict) and isinstance(data.get("timings"), list):
                return candidate
    return None


def _metadata_sidecars(path: Path) -> list[Path]:
    candidates = (
        path.with_suffix(".json"),
        path.with_name(path.stem + ".meta.json"),
        path.with_name(path.stem + ".metadata.json"),
        path.parent / "metadata.json",
    )
    out: list[Path] = []
    for candidate in candidates:
        if candidate != path and candidate.is_file() and candidate not in out:
            out.append(candidate)
    return out


def _group_key(path: Path) -> tuple[str, str, bool]:
    match = re.match(r"^(.*?)[_-]([0-4])$", path.stem)
    if match and match.group(1):
        return str(path.parent), _norm(match.group(1)), False
    if path.stem.casefold() in _GENERIC_CHART_STEMS:
        return str(path.parent), "folder", True
    return str(path.parent), _norm(path.stem), False


def _path_title_values(path: Path, base: str, folder_only: bool) -> list[str]:
    raw_stem = path.stem
    difficulty = re.match(r"^(.*?)[_-][0-4]$", raw_stem)
    raw_base = difficulty.group(1) if difficulty and difficulty.group(1) else raw_stem
    names = [raw_base, path.parent.name]
    values = [base, *names]
    for name in names:
        for separator in (" - ", " – ", " — ", " | "):
            if separator in name:
                values.append(name.split(separator, 1)[0])
    if folder_only:
        values.append(path.parent.name)
    return [value for value in values if value]


def _path_artist_values(path: Path) -> list[str]:
    raw_stem = path.stem
    difficulty = re.match(r"^(.*?)[_-][0-4]$", raw_stem)
    names = [difficulty.group(1) if difficulty and difficulty.group(1) else raw_stem, path.parent.name]
    values = []
    for name in names:
        for separator in (" - ", " – ", " — ", " | "):
            if separator in name:
                values.append(name.split(separator, 1)[1])
    return values


def _new_group(path: Path, base: str, folder_only: bool) -> dict[str, Any]:
    return {
        "paths": [],
        "games": set(),
        "titles": set(),
        "metadata_titles": set(),
        "artists": set(),
        "metadata_files": [],
        "companions": [],
        "folder_only": folder_only,
        "base": base,
    }


def _add_metadata(group: dict[str, Any], data: dict[str, Any], source: Path | None = None) -> None:
    title_values = _record_titles(data)
    title_keys = {key for value in title_values for key in _title_forms(value)}
    group["metadata_titles"].update(title_keys)
    group["titles"].update(title_keys)
    group["artists"].update(_norm(value) for value in _record_artists(data) if _norm(value))
    if source is not None:
        group["metadata_files"].append(str(source.resolve()))


def _discover_local(
    title: str,
    artist: str,
    reference_dir: str | Path,
    progress: Callable[[str], Any] | None,
    cancel: Any,
) -> dict[str, Any]:
    root = Path(reference_dir).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise _ReferenceError(f"Reference directory does not exist: {root}")

    _notify(progress, f"Searching local chart references in {root}")
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    warnings: list[str] = []
    inspected = 0
    for path in sorted(root.rglob("*"), key=lambda value: str(value).casefold()):
        _check_cancel(cancel)
        if inspected >= _MAX_LOCAL_FILES:
            warnings.append(f"Local reference scan capped at {_MAX_LOCAL_FILES} files.")
            break
        if not path.is_file():
            continue
        inspected += 1
        if path.suffix.casefold() == ".ogkr":
            warnings.append(f"Ignored raw OGKR reference {path.name}; supply canonical Ongeki JSON instead.")
            continue
        if path.suffix.casefold() not in _CHART_SUFFIXES:
            continue
        try:
            path.resolve().relative_to(root)
        except ValueError:
            warnings.append(f"Ignored reference outside the selected directory: {path.name}")
            continue
        key_parent, base, folder_only = _group_key(path)
        group = groups.setdefault((key_parent, base), _new_group(path, base, folder_only))
        if path.suffix.casefold() == ".aff":
            group["games"].add("Arcaea")
            group["paths"].append(path)
            for value in _path_title_values(path, base, folder_only):
                group["titles"].update(_title_forms(value))
            group["artists"].update(_norm(value) for value in _path_artist_values(path) if _norm(value))
            for sidecar in _metadata_sidecars(path):
                data = _read_json(sidecar, _MAX_METADATA_BYTES)
                if data:
                    _add_metadata(group, data, sidecar)
        elif path.suffix.casefold() == ".ds":
            data = _read_json(path)
            timing = _timing_sidecar(path)
            if not data or not _deemo_ds(data):
                warnings.append(f"Ignored unsupported Deemo DS reference: {path.name}")
                continue
            if timing is None:
                warnings.append(f"Ignored {path.name}: Deemo DS requires a sibling .timing.json file.")
                continue
            group["games"].add("Deemo")
            group["paths"].append(path)
            group["companions"].append(str(timing.resolve()))
            for value in _path_title_values(path, base, folder_only):
                group["titles"].update(_title_forms(value))
            group["artists"].update(_norm(value) for value in _path_artist_values(path) if _norm(value))
            _add_metadata(group, data)
            for sidecar in _metadata_sidecars(path):
                sidecar_data = _read_json(sidecar, _MAX_METADATA_BYTES)
                if sidecar_data:
                    _add_metadata(group, sidecar_data, sidecar)
        elif path.suffix.casefold() == ".json":
            data = _read_json(path)
            if not data or not _canonical_ongeki(data):
                continue
            group["games"].add("Ongeki")
            group["paths"].append(path)
            for value in _path_title_values(path, base, folder_only):
                group["titles"].update(_title_forms(value))
            group["artists"].update(_norm(value) for value in _path_artist_values(path) if _norm(value))
            _add_metadata(group, data, path)

    usable = [group for group in groups.values() if group["paths"]]
    title_keys = _title_forms(title)
    exact = [group for group in usable if group["titles"].intersection(title_keys)]
    matching: list[dict[str, Any]] = []
    unknown_artist: list[dict[str, Any]] = []
    for group in exact:
        if artist.strip() and group["artists"]:
            if _norm(artist) in group["artists"]:
                matching.append(group)
        elif artist.strip():
            unknown_artist.append(group)
        else:
            matching.append(group)

    if not matching and len(unknown_artist) == 1:
        matching = unknown_artist
        warnings.append("Local title matched exactly, but artist metadata was unavailable.")

    if len(matching) > 1:
        return _result(
            "ambiguous",
            source_game=None,
            provenance={"kind": "local", "source": "local", "reference_dir": str(root)},
            warnings=warnings + ["Multiple local chart groups matched; no file was selected automatically."],
        )

    if not matching and len(usable) == 1:
        group = usable[0]
        if (
            group["folder_only"]
            and not group["metadata_titles"]
            and (not artist.strip() or not group["artists"] or _norm(artist) in group["artists"])
        ):
            matching = [group]
            warnings.append(
                "Accepted the only generic chart folder in the explicit reference directory; title and artist were not embedded."
            )

    if not matching:
        return _result(
            "not_found",
            source_game=None,
            provenance={"kind": "local", "source": "local", "reference_dir": str(root), "files_inspected": inspected},
            warnings=warnings,
        )

    group = matching[0]
    named_order={'easy':0,'basic':0,'pst':0,'normal':1,'advanced':1,'prs':1,
        'hard':2,'expert':2,'exp':2,'ftr':2,'master':3,'mst':3,'byd':3,'etr':4}
    def difficulty_order(value):
        stem=Path(value).stem.lower()
        suffix=re.split(r'[_\- ]',stem)[-1]
        return (int(suffix) if suffix.isdigit() else named_order.get(suffix,99),stem)
    group['paths'].sort(key=difficulty_order)
    games = sorted(group["games"])
    source_game = games[0] if len(games) == 1 else "Mixed"
    if len(games) > 1:
        warnings.append("The selected local folder contains more than one source format.")
    return _result(
        "found",
        group["paths"],
        source_game=source_game,
        provenance={
            "kind": "local",
            "source": "local",
            "reference_dir": str(root),
            "metadata_files": sorted(set(group["metadata_files"])),
            "companions": sorted(set(group["companions"])),
            "formats": games,
            "single_folder_fallback": bool(group["folder_only"] and not group["metadata_titles"]),
        },
        warnings=warnings,
    )


class _CheckedRedirectHandler(HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.hops = 0

    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Request | None:
        self.hops += 1
        if self.hops > _MAX_REDIRECTS:
            raise _ReferenceError("Public source redirect limit exceeded")
        target = urljoin(req.full_url, newurl)
        _validate_remote_url(target)
        return super().redirect_request(req, fp, code, msg, headers, target)


def _validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_REMOTE_HOSTS:
        raise _ReferenceError(f"Rejected public source URL: {url}")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise _ReferenceError("Public source URL must not contain credentials or a non-HTTPS port")


def _fetch_url(url: str, limit: int) -> bytes:
    _validate_remote_url(url)
    request = Request(url, headers={"Accept": "application/json, text/plain", "User-Agent": "in-falsus-studio-reference/1"})
    try:
        if urllib.request.urlopen is _DEFAULT_URL_OPEN:
            opener = build_opener(_CheckedRedirectHandler())
            response = opener.open(request, timeout=_HTTP_TIMEOUT)
        else:
            # Keep the stdlib entry point easy to replace in offline tests.
            response = urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT)
        try:
            geturl = getattr(response, "geturl", None)
            final_url = geturl() if callable(geturl) else url
            _validate_remote_url(final_url)
            data = response.read(limit + 1)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    except (OSError, URLError, ValueError) as exc:
        raise _ReferenceError(f"Public source request failed: {exc}") from exc
    if len(data) > limit:
        raise _ReferenceError(f"Public source response exceeded {limit} bytes")
    return data


def _cache_json(path: Path, limit: int) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size > limit:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_cache(path: Path, data: bytes, limit: int) -> None:
    if len(data) > limit:
        raise _ReferenceError(f"Public source response exceeded {limit} bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _metadata_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    songs = data.get("songs")
    if not isinstance(songs, list):
        songs = data.get("records")
    if not isinstance(songs, list):
        return []
    return [item for item in songs if isinstance(item, dict)]


def _tree_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    tree = data.get("tree")
    return [item for item in tree if isinstance(item, dict)] if isinstance(tree, list) else []


def _matching_tree_paths(entries: list[dict[str, Any]], source_id: str) -> list[dict[str, Any]]:
    prefix = f"songs/{source_id}/"
    out = []
    for entry in entries:
        path = entry.get("path")
        if not isinstance(path, str) or not path.startswith(prefix) or entry.get("type") not in (None, "blob"):
            continue
        parts = path.replace("\\", "/").split("/")
        if any(part in ("", ".", "..") for part in parts) or "\\" in path:
            continue
        match = _DIFFICULTY_RE.search(path)
        if not match:
            continue
        try:
            if int(entry.get("size", 0)) > _MAX_CHART_BYTES:
                continue
        except (TypeError, ValueError):
            continue
        out.append(dict(entry, difficulty=int(match.group(1))))
    return sorted(out, key=lambda item: (item["difficulty"], str(item.get("path"))))[:_MAX_CHARTS]


def _remote_discover(
    title: str,
    artist: str,
    job_dir: str | Path,
    progress: Callable[[str], Any] | None,
    cancel: Any,
) -> dict[str, Any]:
    if job_dir is None or not str(job_dir).strip():
        raise _ReferenceError("A job directory is required for bounded public reference caching")
    job = Path(job_dir).expanduser().resolve()
    if job.exists() and not job.is_dir():
        raise _ReferenceError(f"Job directory is not a directory: {job}")
    job.mkdir(parents=True, exist_ok=True)
    cache = job / "references" / "arcaea"
    metadata_cache = cache / "songlist.json"
    tree_cache = cache / "aff-tree.json"
    warnings: list[str] = []

    _check_cancel(cancel)
    _notify(progress, "Checking the verified public Arcaea AFF catalog")
    metadata = _cache_json(metadata_cache, _MAX_METADATA_BYTES)
    metadata_cached = metadata is not None
    if metadata is None:
        raw = _fetch_url(_PUBLIC_METADATA_URL, _MAX_METADATA_BYTES)
        metadata = json.loads(raw.decode("utf-8"))
        if not isinstance(metadata, dict):
            raise _ReferenceError("Public Arcaea metadata has an unexpected shape")
        _write_cache(metadata_cache, raw, _MAX_METADATA_BYTES)

    matches = []
    unknown_artist = []
    for record in _metadata_records(metadata):
        match = _record_match(record, title, artist)
        if match == "yes":
            matches.append(record)
        elif match == "unknown_artist":
            unknown_artist.append(record)

    if not matches and len(unknown_artist) == 1:
        matches = unknown_artist
        warnings.append("Public title matched exactly, but artist metadata was unavailable.")
    if len(matches) > 1:
        return _result(
            "ambiguous",
            source_game="Arcaea",
            provenance={"kind": "public", "source": "public", "repository": PUBLIC_REPOSITORY, "metadata_cached": metadata_cached},
            warnings=warnings + ["Multiple public Arcaea songs matched; no chart was selected automatically."],
        )
    if not matches:
        return _result(
            "not_found",
            source_game=None,
            provenance={"kind": "public", "source": "public", "repository": PUBLIC_REPOSITORY, "metadata_cached": metadata_cached},
            warnings=warnings,
        )

    record = matches[0]
    source_id = _record_id(record)
    if not source_id or not re.fullmatch(r"[A-Za-z0-9._-]+", source_id) or source_id in {".", ".."}:
        raise _ReferenceError("Public Arcaea metadata did not provide a safe song id")

    _check_cancel(cancel)
    tree = _cache_json(tree_cache, _MAX_TREE_BYTES)
    tree_cached = tree is not None
    if tree is None:
        _notify(progress, "Reading the public Arcaea chart tree")
        raw_tree = _fetch_url(_PUBLIC_TREE_URL, _MAX_TREE_BYTES)
        tree = json.loads(raw_tree.decode("utf-8"))
        if not isinstance(tree, dict):
            raise _ReferenceError("Public Arcaea chart tree has an unexpected shape")
        _write_cache(tree_cache, raw_tree, _MAX_TREE_BYTES)
    if tree.get("truncated"):
        raise _ReferenceError("Public Arcaea chart tree was truncated; no chart was selected")

    entries = _matching_tree_paths(_tree_entries(tree), source_id)
    if not entries:
        return _result(
            "not_found",
            source_game="Arcaea",
            provenance={"kind": "public", "source": "public", "repository": PUBLIC_REPOSITORY, "source_id": source_id},
            warnings=warnings + ["The exact public song matched, but no difficulty AFF files were present."],
        )

    output: list[Path] = []
    files: list[dict[str, Any]] = []
    song_cache = cache / "songs" / source_id
    for entry in entries:
        _check_cancel(cancel)
        remote_path = str(entry["path"])
        filename = Path(remote_path).name
        destination = song_cache / filename
        if destination.is_file() and destination.stat().st_size <= _MAX_CHART_BYTES:
            data = destination.read_bytes()
            cached = True
        else:
            _notify(progress, f"Downloading public chart {filename}")
            url = urljoin(_PUBLIC_RAW_ROOT, quote(remote_path, safe="/"))
            data = _fetch_url(url, _MAX_CHART_BYTES)
            _write_cache(destination, data, _MAX_CHART_BYTES)
            cached = False
        if not data:
            warnings.append(f"Public chart was empty: {remote_path}")
            continue
        output.append(destination)
        files.append(
            {
                "path": remote_path,
                "url": urljoin(_PUBLIC_RAW_ROOT, quote(remote_path, safe="/")),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "cached": cached,
                "git_blob_sha": entry.get("sha"),
            }
        )
    if not output:
        return _result(
            "error",
            source_game="Arcaea",
            provenance={"kind": "public", "source": "public", "repository": PUBLIC_REPOSITORY, "source_id": source_id},
            warnings=warnings + ["No usable public chart bytes were downloaded."],
        )

    return _result(
        "found",
        output,
        source_game="Arcaea",
        provenance={
            "kind": "public",
            "source": "public",
            "repository": PUBLIC_REPOSITORY,
            "branch": _PUBLIC_BRANCH,
            "revision": tree.get("sha"),
            "metadata_url": _PUBLIC_METADATA_URL,
            "tree_url": _PUBLIC_TREE_URL,
            "source_id": source_id,
            "metadata_cached": metadata_cached,
            "tree_cached": tree_cached,
            "files": files,
            "audio_downloaded": False,
        },
        warnings=warnings,
    )


def find_references(
    title: str,
    artist: str,
    reference_dir: str | Path | None,
    job_dir: str | Path,
    progress: Callable[[str], Any] | None,
    cancel: Any = None,
) -> dict[str, Any]:
    """Find exact local or public authored references for one song.

    ``status`` is ``found``, ``not_found``, ``ambiguous``, ``error``, or
    ``cancelled``.  Public responses are cached below ``job_dir/references``;
    no repository or game files are written.
    """

    if not str(title or "").strip():
        return _result("error", warnings=["A song title is required for source discovery."])

    warnings: list[str] = []
    try:
        _check_cancel(cancel)
        if reference_dir is not None and str(reference_dir).strip():
            local = _discover_local(title, artist or "", reference_dir, progress, cancel)
            warnings.extend(local["warnings"])
            if local["status"] == "found":
                return _result(
                    "found",
                    local["paths"],
                    local["source_game"],
                    dict(local["provenance"], search_order="local"),
                    warnings,
                )
            if local["status"] == "ambiguous":
                warnings.append("Local references were ambiguous; trying the verified public catalog.")

        remote = _remote_discover(title, artist or "", job_dir, progress, cancel)
        warnings.extend(remote["warnings"])
        if remote["status"] == "found":
            return _result(
                "found",
                remote["paths"],
                remote["source_game"],
                dict(remote["provenance"], search_order="public" if reference_dir is None else "local_then_public"),
                warnings,
            )
        return _result(
            remote["status"],
            source_game=remote["source_game"],
            provenance=remote["provenance"],
            warnings=warnings,
        )
    except _Cancelled:
        return _result("cancelled", warnings=warnings + ["Source discovery was cancelled."])
    except _ReferenceError as exc:
        return _result("error", warnings=warnings + [str(exc)])
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return _result("error", warnings=warnings + [f"Source discovery failed: {exc}"])


__all__ = ["find_references"]
