"""Headless checks for the stable CLI/UI option contract."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from infalsus_studio.cli import build_parser, namespace_to_options  # noqa: E402


class UiOptionTests(unittest.TestCase):
    def test_defaults_match_ui(self) -> None:
        namespace = build_parser().parse_args(["track.mp3"])
        self.assertEqual(
            namespace_to_options(namespace),
            {
                "media": "track.mp3",
                "game_root": None,
                "title": None,
                "artist": None,
                "reference_dir": None,
                "cover": None,
                "source_search": True,
                "decorations": False,
                "normalize_audio": True,
                "scroll_effects": True,
                "install_after": True,
            },
        )

    def test_flags_and_optional_inputs(self) -> None:
        namespace = build_parser().parse_args(
            [
                "https://example.test/song",
                "--game",
                "D:/Games/In Falsus",
                "--reference-dir",
                "refs",
                "--cover",
                "cover.png",
                "--title",
                "A title",
                "--artist",
                "An artist",
                "--no-install",
                "--no-source-search",
                "--decorations",
                "--no-normalize",
                "--no-scroll-effects",
            ]
        )
        options = namespace_to_options(namespace)
        self.assertEqual(options["game_root"], "D:/Games/In Falsus")
        self.assertEqual(options["reference_dir"], "refs")
        self.assertEqual(options["cover"], "cover.png")
        self.assertEqual(options["title"], "A title")
        self.assertEqual(options["artist"], "An artist")
        self.assertFalse(options["install_after"])
        self.assertFalse(options["source_search"])
        self.assertTrue(options["decorations"])
        self.assertFalse(options["normalize_audio"])
        self.assertFalse(options["scroll_effects"])

    def test_self_test_does_not_require_media(self) -> None:
        namespace = build_parser().parse_args(["--self-test"])
        self.assertTrue(namespace.self_test)
        self.assertIsNone(namespace.media)


if __name__ == "__main__":
    unittest.main()
