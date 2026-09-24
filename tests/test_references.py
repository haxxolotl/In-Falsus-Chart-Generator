from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from infalsus_studio import references


class ReferenceDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.refs = self.root / "refs"
        self.job = self.root / "job"
        self.refs.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_local_aff_uses_unicode_normalized_metadata(self) -> None:
        song = self.refs / "Cafe Song"
        song.mkdir()
        chart = song / "cafe_song_0.aff"
        chart.write_text("AudioOffset:0;\n", encoding="utf-8")
        (song / "cafe_song_0.json").write_text(
            json.dumps({"title": "Cafe\u0301 Song", "artist": "Ärtist"}), encoding="utf-8"
        )

        result = references.find_references(
            "Café Song", "ÄRTIST", self.refs, self.job, progress=None
        )

        self.assertEqual(result["status"], "found")
        self.assertEqual(result["source_game"], "Arcaea")
        self.assertEqual(result["paths"], [str(chart.resolve())])
        self.assertEqual(result["provenance"]["kind"], "local")

    def test_local_filename_artist_mismatch_is_not_selected(self) -> None:
        chart = self.refs / "Title - Correct Artist_0.aff"
        chart.write_text("AudioOffset:0;\n", encoding="utf-8")

        with patch.object(
            references,
            "_remote_discover",
            return_value=references._result("not_found", provenance={"kind": "public"}),
        ):
            result = references.find_references("Title", "Different Artist", self.refs, self.job, progress=None)

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["paths"], [])

    def test_local_deemo_requires_and_returns_timing_sidecar(self) -> None:
        chart = self.refs / "Dream.ds"
        timing = self.refs / "Dream.timing.json"
        chart.write_text(json.dumps({"notes": [], "links": [], "title": "Dream", "artist": "N"}), encoding="utf-8")
        timing.write_text(json.dumps({"timings": [{"time": 0, "bpm": 120}]}), encoding="utf-8")

        result = references.find_references("Dream", "N", self.refs, self.job, progress=None)

        self.assertEqual(result["status"], "found")
        self.assertEqual(result["source_game"], "Deemo")
        self.assertEqual(result["paths"], [str(chart.resolve())])
        self.assertEqual(result["provenance"]["companions"], [str(timing.resolve())])

    def test_local_canonical_ongeki_json_is_accepted(self) -> None:
        chart = self.refs / "Lift Off.json"
        chart.write_text(
            json.dumps(
                {
                    "source_format": "SDVX.in Ongeki visual guide",
                    "title": "Lift Off",
                    "artist": "DJ Test",
                    "notes": [],
                    "fields": [],
                }
            ),
            encoding="utf-8",
        )

        result = references.find_references("Lift Off", "DJ Test", self.refs, self.job, progress=None)

        self.assertEqual(result["status"], "found")
        self.assertEqual(result["source_game"], "Ongeki")
        self.assertEqual(result["paths"], [str(chart.resolve())])

    def test_numeric_only_explicit_folder_is_a_warned_fallback(self) -> None:
        (self.refs / "0.aff").write_text("AudioOffset:0;\n", encoding="utf-8")
        (self.refs / "1.aff").write_text("AudioOffset:0;\n", encoding="utf-8")

        result = references.find_references("User supplied song", "Unknown", self.refs, self.job, progress=None)

        self.assertEqual(result["status"], "found")
        self.assertEqual(result["source_game"], "Arcaea")
        self.assertEqual(len(result["paths"]), 2)
        self.assertTrue(any("generic chart folder" in warning for warning in result["warnings"]))

    def test_public_mock_downloads_matching_aff_only_and_caches_in_job(self) -> None:
        metadata = {"songs": [{"id": "remote-song", "title_localized": {"en": "Remote Song"}, "artist": "Remote Artist"}]}
        tree = {
            "sha": "tree-sha",
            "truncated": False,
            "tree": [
                {"path": "songs/remote-song/0.aff", "type": "blob", "size": 5, "sha": "a"},
                {"path": "songs/remote-song/1.aff", "type": "blob", "size": 5, "sha": "b"},
                {"path": "songs/remote-song/2.aff", "type": "blob", "size": 5, "sha": "c"},
                {"path": "songs/remote-song/audio.ogg", "type": "blob", "size": 7, "sha": "music"},
            ],
        }
        metadata_url = references._PUBLIC_METADATA_URL
        tree_url = references._PUBLIC_TREE_URL

        def fetch(url: str, limit: int) -> bytes:
            if url == metadata_url:
                return json.dumps(metadata).encode("utf-8")
            if url == tree_url:
                return json.dumps(tree).encode("utf-8")
            if url.endswith(".aff"):
                return b"AudioOffset:0;"
            raise AssertionError(url)

        with patch.object(references, "_fetch_url", side_effect=fetch):
            result = references.find_references("Remote Song", "Remote Artist", None, self.job, progress=None)

        self.assertEqual(result["status"], "found")
        self.assertEqual(result["source_game"], "Arcaea")
        self.assertEqual(len(result["paths"]), 3)
        self.assertTrue(all(Path(path).is_absolute() for path in result["paths"]))
        self.assertTrue(all("audio.ogg" not in path for path in result["paths"]))
        self.assertTrue(all(str(self.job / "references") in path for path in result["paths"]))
        self.assertFalse((self.job / "references" / "arcaea" / "audio.ogg").exists())

        with patch.object(references, "_fetch_url", side_effect=AssertionError("cache miss")):
            cached = references.find_references("Remote Song", "Remote Artist", None, self.job, progress=None)
        self.assertEqual(cached["status"], "found")
        self.assertEqual(cached["paths"], result["paths"])

    def test_public_ambiguity_does_not_select_a_chart(self) -> None:
        metadata = {
            "songs": [
                {"id": "one", "title": "Same", "artist": "Artist"},
                {"id": "two", "title": "Same", "artist": "Artist"},
            ]
        }
        with patch.object(references, "_fetch_url", return_value=json.dumps(metadata).encode("utf-8")):
            result = references.find_references("Same", "Artist", None, self.job, progress=None)

        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["paths"], [])
        self.assertTrue(any("Multiple public Arcaea songs" in warning for warning in result["warnings"]))

    @unittest.skipUnless(os.environ.get("IN_FALSUS_PUBLIC_CANARY"), "opt-in live public source canary")
    def test_live_public_canary_one_song(self) -> None:
        result = references.find_references("Altale", "Sakuzyo", None, self.job, progress=None)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["source_game"], "Arcaea")
        self.assertGreaterEqual(len(result["paths"]), 1)
        self.assertTrue(all(Path(path).suffix.lower() == ".aff" for path in result["paths"]))


if __name__ == "__main__":
    unittest.main()
