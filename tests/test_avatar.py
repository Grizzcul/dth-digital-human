"""固定素材入口：只访问临时文件和本地服务，不调用模型。"""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class AvatarTests(unittest.TestCase):
    def test_detects_added_removed_and_preferred_video(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            assets = root / "png"
            assets.mkdir()
            with patch.object(server, "PROJECT_DIR", root), patch.object(server, "ASSET_DIR", assets):
                self.assertEqual(server.avatar_assets(), {"image": None, "video": None})
                (assets / "avatar.png").write_bytes(b"picture")
                self.assertEqual(server.avatar_assets(), {"image": "png/avatar.png", "video": None})
                (assets / "avatar.mp4").write_bytes(b"mp4")
                self.assertEqual(server.avatar_assets()["video"], "png/avatar.mp4")
                (assets / "avatar.webm").write_bytes(b"webm")
                self.assertEqual(server.avatar_assets()["video"], "png/avatar.webm")
                (assets / "avatar.webm").unlink()
                self.assertEqual(server.avatar_assets()["video"], "png/avatar.mp4")
                (assets / "avatar.mp4").unlink()
                self.assertIsNone(server.avatar_assets()["video"])

    def test_fixed_assets_change_without_server_restart(self):
        class QuietHandler(server.AssistantHandler):
            def log_message(self, *_args):
                pass

        with TemporaryDirectory() as folder:
            root = Path(folder)
            assets = root / "png"
            assets.mkdir()
            with patch.object(server, "PROJECT_DIR", root), patch.object(server, "ASSET_DIR", assets):
                backend = server.LocalServer((server.HOST, 0), QuietHandler)
                worker = Thread(target=backend.serve_forever, daemon=True)
                worker.start()
                base = f"http://{server.HOST}:{backend.server_port}"
                try:
                    with urlopen(base + "/api/avatar", timeout=3) as response:
                        self.assertEqual(json.load(response), {"image": None, "video": None})
                        self.assertEqual(response.headers["Cache-Control"], "no-store")
                    (assets / "avatar.png").write_bytes(b"first picture")
                    (assets / "avatar.webm").write_bytes(b"0123456789")
                    with urlopen(base + "/api/avatar", timeout=3) as response:
                        self.assertEqual(json.load(response)["video"], "png/avatar.webm")
                    with urlopen(base + "/png/avatar.png", timeout=3) as response:
                        self.assertEqual(response.read(), b"first picture")
                    (assets / "avatar.png").write_bytes(b"replacement picture")
                    with urlopen(base + "/png/avatar.png", timeout=3) as response:
                        self.assertEqual(response.read(), b"replacement picture")
                    request = Request(base + "/png/avatar.webm", headers={"Range": "bytes=2-4"})
                    with urlopen(request, timeout=3) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.read(), b"234")
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base + "/local_config.py", timeout=3)
                    self.assertEqual(error.exception.code, 404)
                    (assets / "avatar.webm").unlink()
                    with urlopen(base + "/api/avatar", timeout=3) as response:
                        self.assertIsNone(json.load(response)["video"])
                finally:
                    backend.shutdown()
                    backend.server_close()
                    worker.join(3)


if __name__ == "__main__":
    unittest.main()
