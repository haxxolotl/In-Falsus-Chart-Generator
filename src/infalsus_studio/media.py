"""Local/direct-link media preparation; every child process stays hidden."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.parse
import urllib.request


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError("Cancelled; the game has not been changed.")


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args, cancel=None):
    check_cancel(cancel)
    result = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-y", *map(str, args)],
        capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    check_cancel(cancel)
    stderr = result.stderr.decode("utf8", errors="replace")
    if result.returncode:
        raise ValueError("Media conversion failed: " + stderr[-1800:])
    return stderr


def acquire(value, job, cancel=None):
    """Accept a file or a direct HTTP(S) media link, not a streaming-site page."""
    url = urllib.parse.urlsplit(value)
    if url.scheme.lower() in ("http", "https"):
        if url.username or url.password:
            raise ValueError("Use a media URL without embedded credentials.")
        path = job / "input.media"
        req = urllib.request.Request(value, headers={"User-Agent": "InFalsusStudio/0.1"})
        with urllib.request.urlopen(req, timeout=45) as source, path.open("wb") as target:
            if urllib.parse.urlsplit(source.url).scheme not in ("http", "https"):
                raise ValueError("The media link redirected to an unsupported protocol.")
            if "text/html" in source.headers.get("Content-Type", ""):
                raise ValueError("This is a webpage. Use a direct MP3/MP4/audio-file link or a downloaded file.")
            total = 0
            while block := source.read(1024 * 1024):
                check_cancel(cancel)
                total += len(block)
                if total > 2 * 1024**3:
                    raise ValueError("Media exceeds the 2 GB input limit.")
                target.write(block)
        return path, Path(urllib.parse.unquote(url.path)).stem
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError("Choose an existing audio/video file or a direct HTTP(S) link.")
    if path.stat().st_size > 2 * 1024**3:
        raise ValueError("Media exceeds the 2 GB input limit.")
    return path, path.stem


def prepare(value, job: Path, normalize=True, cancel=None):
    import numpy as np
    import soundfile as sf
    source, stem = acquire(value, job, cancel)
    metadata = job / "metadata.txt"
    run_ffmpeg(["-i", source, "-f", "ffmetadata", metadata], cancel)
    tags = {}
    for line in metadata.read_text(encoding="utf8", errors="replace").splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            tags[key.lower()] = val
    raw = job / "decoded.wav"
    run_ffmpeg(["-i", source, "-map", "0:a:0", "-vn", "-ar", "44100", "-ac", "2", "-c:a", "pcm_f32le", raw], cancel)
    info = sf.info(raw)
    input_sha256=hashlib.sha256(raw.read_bytes()).hexdigest()
    if not 15 <= info.duration <= 1200:
        raise ValueError("Use a song between 15 seconds and 20 minutes long.")
    measurement = run_ffmpeg(["-i", raw, "-af", "loudnorm=I=-12:TP=-1:LRA=11:print_format=json", "-f", "null", "-"], cancel)
    measured = json.loads(measurement[measurement.rfind("{"):measurement.rfind("}") + 1])
    integrated = float(measured["input_i"])
    if not math.isfinite(integrated) or integrated < -50:
        raise ValueError("The input is silent or too quiet to map reliably.")
    gain = min(12., max(0., -11.0 - integrated)) if normalize else 0.
    wav = job / "prepared.wav"
    # Compensate limiter look-ahead, retain the exact decoded sample count, then
    # add a two-second lead-in. Never stretch/trim music to fit a chart.
    filters = []
    if gain > 0:
        filters += [f"volume={gain:.6f}dB", "alimiter=limit=.89:attack=5:release=100:level=false:latency=true"]
    filters += [f"atrim=end_sample={info.frames}", "asetpts=N/SR/TB", "adelay=2000:all=1"]
    run_ffmpeg(["-i", raw, "-af", ",".join(filters), "-ar", "44100", "-c:a", "pcm_f32le", wav], cancel)
    if sf.info(wav).frames != info.frames + 88200:
        raise ValueError("Audio preparation changed the expected sample count.")
    ogg = job / "audio.ogg"
    run_ffmpeg(["-i", wav, "-c:a", "libvorbis", "-q:a", "6", ogg], cancel)
    if sf.info(ogg).frames != sf.info(wav).frames:
        raise ValueError("Encoded audio duration differs from the chart clock.")
    digest = hashlib.sha256(ogg.read_bytes()).hexdigest()
    output_log=run_ffmpeg(['-i',ogg,'-af','loudnorm=I=-12:TP=-1:LRA=11:print_format=json','-f','null','-'],cancel)
    output_measure=json.loads(output_log[output_log.rfind('{'):output_log.rfind('}')+1])
    return dict(wav=wav, ogg=ogg, title=tags.get("title") or stem,
        artist=tags.get("artist") or tags.get("album_artist") or "Unknown artist",
        duration_seconds=sf.info(wav).duration, sha256=digest,input_sha256=input_sha256,
        audio_metrics=dict(input_lufs=integrated, output_lufs=float(output_measure['input_i']),
            output_true_peak_dbtp=float(output_measure['input_tp']), gain_db=gain, lead_in_seconds=2,
            original_frames=info.frames, prepared_frames=sf.info(wav).frames, sample_rate=44100))
