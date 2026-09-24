from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from infalsus_studio import calibration  # noqa: E402


class CalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.game = self.root / "game"; self.game.mkdir(); (self.game / "infalsus.exe").write_bytes(b"game"); (self.game / "GameAssembly.dll").write_bytes(b"assembly")
        self.cache = self.root / "cache"; self.events: list[str] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_heldout_split_is_stable_and_twenty_percent(self) -> None:
        songs = [{"BaseName": f"song_{index}", "ChartInfos": [{"Available": 1}]} for index in range(20)]
        self.assertEqual(calibration._heldout_songs(songs), calibration._heldout_songs(list(reversed(songs))))
        self.assertEqual(len(calibration._heldout_songs(songs)), 4)

    def test_first_run_writes_bound_manifest_then_reuses_models(self) -> None:
        songs = [{"BaseName": f"song_{index}", "ChartInfos": [{"Id": f"song_{index}0", "Available": 1, "Difficulty": 1, "Rating": 1}]} for index in range(4)]
        calls = {"extract": 0, "build": 0}

        def extract(*_args, **_kwargs): calls["extract"] += 1
        def build(cache, *_args, **_kwargs):
            calls["build"] += 1
            for artifact in calibration.ARTIFACTS:
                path = cache / artifact; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"model")
            return {"ok": True}

        with patch.object(calibration, "_live_roster", return_value=(songs, {})), patch.object(calibration, "_extract_corpus", side_effect=extract), patch.object(calibration, "_build_models", side_effect=build):
            self.assertEqual(calibration.ensure_models(self.game, self.cache, self.events.append), self.cache.resolve())
            self.assertEqual(calibration.ensure_models(self.game, self.cache, self.events.append), self.cache.resolve())
        self.assertEqual(calls, {"extract": 1, "build": 1})
        manifest = json.loads((self.cache / "calibration-manifest.json").read_text(encoding="utf8"))
        self.assertEqual(manifest["game_assembly_sha256"], calibration._sha256(self.game / "GameAssembly.dll"))
        self.assertEqual(manifest["training_source_sha256"], calibration._training_source_sha())
        self.assertEqual(len(manifest["heldout_songs"]), 1)


if __name__ == "__main__":
    unittest.main()
