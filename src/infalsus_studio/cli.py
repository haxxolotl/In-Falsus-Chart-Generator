"""Command line entry point for In Falsus Studio."""

from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path
from typing import Any, Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and optionally install an In Falsus song pack.")
    parser.add_argument("media", nargs="?", help="local media file or supported media URL")
    parser.add_argument("--game", dest="game_root", help="In Falsus game folder")
    parser.add_argument("--reference-dir", help="optional folder containing reference material")
    parser.add_argument("--cover", help="optional cover image")
    parser.add_argument("--title", help="optional song title")
    parser.add_argument("--artist", help="optional artist name")
    parser.add_argument("--no-install", dest="install_after", action="store_false", default=True)
    parser.add_argument("--no-source-search", dest="source_search", action="store_false", default=True)
    parser.add_argument("--decorations", action="store_true", default=False)
    parser.add_argument("--no-normalize", dest="normalize_audio", action="store_false", default=True)
    parser.add_argument("--no-scroll-effects", dest="scroll_effects", action="store_false", default=True)
    parser.add_argument("--self-test", action="store_true", help="validate the CLI without running the backend")
    parser.add_argument('--self-test-report', help='write a dependency check report without opening a window')
    parser.add_argument('--install-pack', help='install an already prepared pack')
    parser.add_argument('--restore-pack', help='restore a pack backup')
    parser.add_argument('--qa-window', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument("--gui", action="store_true", help="launch the graphical interface")
    return parser


def namespace_to_options(namespace: argparse.Namespace) -> dict[str, Any]:
    """Map CLI names to the stable pipeline options contract."""
    return {
        "media": namespace.media,
        "game_root": namespace.game_root,
        "title": namespace.title,
        "artist": namespace.artist,
        "reference_dir": namespace.reference_dir,
        "cover": namespace.cover,
        "source_search": namespace.source_search,
        "decorations": namespace.decorations,
        "normalize_audio": namespace.normalize_audio,
        "scroll_effects": namespace.scroll_effects,
        "install_after": namespace.install_after,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Public parser helper for launchers and headless tests."""
    return build_parser().parse_args(argv)


def parse_options(argv: Sequence[str] | None = None) -> tuple[argparse.Namespace, dict[str, Any]]:
    namespace = parse_args(argv)
    return namespace, namespace_to_options(namespace)


def _error_text(error: Exception) -> str:
    detail = str(error).strip() or error.__class__.__name__
    return f"{detail} Check the media and game paths, then try again."


def main(argv: Sequence[str] | None = None) -> int:
    namespace, options = parse_options(argv)
    if namespace.qa_window:
        from .gui import main as gui_main
        gui_main(automation=True)
        return 0
    if namespace.gui:
        from .gui import main as gui_main

        gui_main()
        return 0
    if namespace.self_test_report:
        from .engine.runtime import configure
        from .media import ffmpeg
        from .installer import _runtime
        import tkinter,unicorn,numpy,scipy,soundfile,sklearn
        from unicorn.unicorn_py3.arch import intel
        configure(Path.cwd(),Path.cwd())
        _runtime()
        Path(namespace.self_test_report).write_text(json.dumps(dict(status='passed',ffmpeg_exists=Path(ffmpeg()).is_file(),
            numpy=numpy.__version__,sklearn=sklearn.__version__,frozen=bool(getattr(sys,'frozen',False)))),encoding='utf8')
        return 0
    if namespace.self_test:
        print("infalsus-studio CLI self-test: ok")
        return 0
    if namespace.install_pack or namespace.restore_pack:
        from .installer import install_pack,restore_pack
        path=Path(namespace.install_pack or namespace.restore_pack)
        game=Path(namespace.game_root or json.loads((path/'install-manifest.json').read_text(encoding='utf8'))['game_root'])
        receipt=(install_pack if namespace.install_pack else restore_pack)(path,game)
        print(json.dumps(receipt));return 0
    if not namespace.media:
        build_parser().error("media is required unless --self-test or --gui is used")

    try:
        from .pipeline import run_job

        result = run_job(options, progress=lambda message: print(message, flush=True), cancel=None)
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Error: {_error_text(error)}", file=sys.stderr)
        return 1

    print(f"Pack: {result.get('pack_dir') or result.get('job_dir', 'unknown')}")
    print(f"Installed: {'yes' if result.get('installed') else 'no'}")
    if result.get("songs") is not None:
        try:
            print(f"Songs: {len(result['songs'])}")
        except TypeError:
            print(f"Songs: {result['songs']}")
    for warning in result.get("warnings", []) or []:
        print(f"Warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
