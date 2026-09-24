from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from infalsus_studio import installer


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class InstallerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name); self.game = self.root / "game"; self.pack = self.root / "pack"
        (self.game / "infalsus_Data/StreamingAssets/aa").mkdir(parents=True)
        (self.game / "GameAssembly.dll").write_bytes(b"assembly")
        (self.game / "infalsus_Data/StreamingAssets/aa/catalog.bin").write_bytes(b"catalog")
        self.target = self.game / "infalsus_Data/StreamingAssets/sam/custom"; self.target.parent.mkdir(parents=True); self.target.write_bytes(b"before")
        payload = self.pack / "payload/infalsus_Data/StreamingAssets/sam/custom"; payload.parent.mkdir(parents=True); payload.write_bytes(b"after")
        manifest = {"format": 2, "game_root": str(self.game), "game_assembly_sha256": digest(self.game / "GameAssembly.dll"),
                    "protected_files": {"GameAssembly.dll": digest(self.game / "GameAssembly.dll"), "infalsus_Data/StreamingAssets/aa/catalog.bin": digest(self.game / "infalsus_Data/StreamingAssets/aa/catalog.bin")},
                    "files": [{"path": "infalsus_Data/StreamingAssets/sam/custom", "kind": "chart", "original_sha256": digest(self.target), "payload_sha256": digest(payload), "bytes": 5}]}
        (self.pack / "install-manifest.json").write_text(json.dumps(manifest), encoding="utf8")
        self.running = False
        self.old_running = installer._game_running
        installer._game_running = lambda: self.running

    def tearDown(self) -> None:
        installer._game_running = self.old_running
        self.temp.cleanup()

    def test_install_and_restore_are_hash_bound_and_preserve_unrelated_files(self) -> None:
        unrelated = self.game / "unrelated.txt"; unrelated.write_text("keep", encoding="utf8")
        self.assertEqual(installer.install_pack(self.pack, self.game)["status"], "installed")
        self.assertEqual(self.target.read_bytes(), b"after")
        self.assertEqual(unrelated.read_text(encoding="utf8"), "keep")
        self.assertEqual(installer.restore_pack(self.pack, self.game)["status"], "restored")
        self.assertEqual(self.target.read_bytes(), b"before")

    def test_refuses_live_game_and_changed_precondition_without_writes(self) -> None:
        self.running = True
        with self.assertRaises(installer.InstallError):
            installer.install_pack(self.pack, self.game)
        self.assertEqual(self.target.read_bytes(), b"before")
        self.running = False
        (self.game / "GameAssembly.dll").write_bytes(b"changed")
        with self.assertRaises(installer.InstallError):
            installer.install_pack(self.pack, self.game)
        self.assertEqual(self.target.read_bytes(), b"before")

    def test_restore_refuses_an_unrelated_post_install_change(self) -> None:
        installer.install_pack(self.pack, self.game)
        self.target.write_bytes(b"someone else")
        with self.assertRaises(installer.InstallError):
            installer.restore_pack(self.pack, self.game)
        self.assertEqual(self.target.read_bytes(), b"someone else")

    def test_rejects_path_escape(self) -> None:
        manifest = json.loads((self.pack / "install-manifest.json").read_text(encoding="utf8"))
        manifest["files"][0]["path"] = "../outside"
        (self.pack / "install-manifest.json").write_text(json.dumps(manifest), encoding="utf8")
        with self.assertRaises(installer.InstallError):
            installer.install_pack(self.pack, self.game)
