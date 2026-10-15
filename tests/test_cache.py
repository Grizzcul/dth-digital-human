"""缓存验收：临时数据库和模拟供应商，不消耗 token 或真实语音额度。"""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cache import AudioCache, CacheError, load_cases
import server
import tts


class CacheTests(unittest.TestCase):
    def test_persistence_profile_invalidation_integrity_and_lru(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audio.sqlite3"
            cache = AudioCache(path, max_bytes=12)
            profile = tts.audio_profile()
            key = server.speech_key("你好", profile)
            cache.put(key, "你好", profile, b"abcd", pinned=True)
            self.assertEqual(AudioCache(path).get(key), b"abcd")  # 新对象/重启仍能读。
            self.assertNotEqual(key, server.speech_key("另一句", profile))
            with patch.object(tts, "TTS_VOICE", "different-voice"):
                self.assertNotEqual(key, server.speech_key("你好", tts.audio_profile()))
            self.assertNotEqual(key, server.speech_key("你好", tts.audio_profile("mp3")))
            cache.put("old", "旧", profile, b"1234")
            cache.put("new", "新", profile, b"56789012")
            self.assertIsNone(cache.get("old"))
            self.assertEqual(cache.get(key), b"abcd")  # 测试音频不被淘汰。
            self.assertLessEqual(cache.stats()["bytes"], 12)
            with cache.connect() as db:
                db.execute("UPDATE audio SET audio=? WHERE key=?", (b"broken", key))
            self.assertIsNone(cache.get(key))
            self.assertEqual(cache.get("new"), b"56789012")

    def test_quota_failure_rolls_back_and_old_pins_can_be_released(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = AudioCache(Path(folder) / "audio.sqlite3", max_bytes=8)
            cache.put("one", "1", {}, b"12345678", pinned=True)
            with self.assertRaises(CacheError):
                cache.put("two", "2", {}, b"1234", pinned=True)
            self.assertEqual(cache.get("one"), b"12345678")
            self.assertIsNone(cache.get("two"))
            cache.pin([])
            cache.put("two", "2", {}, b"1234")
            self.assertIsNone(cache.get("one"))

    def test_cases_reject_ambiguous_inputs_and_revision_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test_cases.json"
            cases = [{"id": "one", "inputs": ["你好"], "reply": "原回答。"}]
            path.write_text(json.dumps(cases), encoding="utf-8")
            _, old = load_cases(path)
            cases[0]["reply"] = "新回答。"
            path.write_text(json.dumps(cases), encoding="utf-8")
            self.assertNotEqual(load_cases(path)[1], old)
            cases.append({"id": "two", "inputs": [" 你好 "], "reply": "歧义。"})
            path.write_text(json.dumps(cases), encoding="utf-8")
            with self.assertRaises(CacheError):
                load_cases(path)

    @contextmanager
    def backend(self):
        class QuietHandler(server.AssistantHandler):
            def log_message(self, *_args):
                pass
        with tempfile.TemporaryDirectory() as folder:
            backend = server.LocalServer((server.HOST, 0), QuietHandler)
            backend.api_key = backend.tts_api_key = ""
            backend.audio_cache = AudioCache(Path(folder) / "cache.sqlite3")
            backend.test_cases_path = Path(folder) / "test_cases.json"
            backend.test_cases_path.write_text(json.dumps([
                {"id": "hello", "inputs": ["你好", "你好呀"], "reply": "你好呀，很高兴又见到你。"}
            ]), encoding="utf-8")
            thread = Thread(target=backend.serve_forever, daemon=True)
            thread.start()
            self.base = f"http://{server.HOST}:{backend.server_port}"
            try:
                yield backend
            finally:
                backend.shutdown()
                backend.server_close()
                thread.join(3)

    def request(self, path, data=None, streaming=False):
        headers = {"Content-Type": "application/json"}
        if streaming:
            headers["Accept"] = "application/x-ndjson"
        request = Request(self.base + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
        with urlopen(request, timeout=4) as response:
            body = response.read()
            if streaming:
                return [json.loads(line) for line in body.splitlines()]
            return json.loads(body)

    def assert_http_error(self, code, path, data, streaming=False):
        with self.assertRaises(HTTPError) as failure:
            self.request(path, data, streaming=streaming)
        self.assertEqual(failure.exception.code, code)
        failure.exception.close()

    def manifest(self):
        config = self.request("/api/test/cases")
        return {"revision": config["revision"], "cases": [{"id": case["id"], "segments": [case["reply"]]} for case in config["cases"]]}

    def test_strict_miss_and_reply_never_touch_provider_or_conversation(self):
        with self.backend() as backend, patch.object(server, "stream_reply") as model, \
                patch.object(server, "stream_speech") as voice, patch.object(server, "synthesize_speech") as mp3:
            reply = self.request("/api/test/reply", {"message": " 你好呀 ", "conversation_id": "ignored"})
            self.assertEqual(reply["source"], "test_cache")
            self.assertNotIn("conversation_id", reply)
            self.assert_http_error(404, "/api/test/reply", {"message": "未收录"})
            self.assert_http_error(412, "/api/test/tts", {"text": "未准备", "cache_only": False})
            self.assertFalse(backend.active_tasks)
            model.assert_not_called(); voice.assert_not_called(); mp3.assert_not_called()

    def test_preparation_saves_once_and_replays_without_any_key(self):
        with self.backend() as backend:
            manifest = self.manifest()
            plan = self.request("/api/test/plan", manifest)
            self.assertFalse(plan["items"][0]["cached"])
            request = {**manifest, "plan_id": plan["plan_id"], "index": 0}
            backend.tts_api_key = "test-only"
            with patch.object(server, "stream_speech", return_value=iter([b"\x00\x40", b"\x00\xc0"])) as voice:
                events = self.request("/api/test/prepare", request, streaming=True)
                self.assertTrue(events[-1]["cache_saved"])
                self.assertEqual(backend.audio_cache.stats()["entries"], 1)
                self.assertFalse(backend.tts_lock.locked())
                backend.tts_api_key = ""  # 模拟离线/另一台电脑只拿到了缓存，没有密钥。
                repeated = self.request("/api/test/prepare", request, streaming=True)
                self.assertEqual(repeated[0]["cache"], "hit")
                cached = self.request("/api/test/tts", {"text": plan["items"][0]["text"]}, streaming=True)
                self.assertEqual(cached[0]["cache"], "hit")
                normal = self.request("/api/tts", {"text": plan["items"][0]["text"]}, streaming=True)
                self.assertEqual(normal[0]["cache"], "hit")
                self.assertEqual(voice.call_count, 1)
            self.assertTrue(self.request("/api/test/plan", manifest)["items"][0]["cached"])
            settings = self.request("/api/tts")
            self.assertFalse(settings["configured"])
            self.assertTrue(settings["cache_available"])
            self.assertEqual(settings["cache"]["entries"], 1)
            for path in ("/cache.py", "/test_cases.json", "/data/cache.sqlite3"):
                self.assert_http_error(404, path, None)

    def test_incomplete_synthesis_is_not_saved_and_plan_edits_do_not_bill(self):
        with self.backend() as backend:
            backend.tts_api_key = "test-only"
            manifest = self.manifest()
            plan = self.request("/api/test/plan", manifest)
            request = {**manifest, "plan_id": plan["plan_id"], "index": 0}
            def broken(*_args):
                yield b"\x00\x40"
                raise tts.TTSError("传输不完整")
            with patch.object(server, "stream_speech", side_effect=broken):
                events = self.request("/api/test/prepare", request, streaming=True)
                self.assertEqual(events[-1]["event"], "error")
                self.assertEqual(backend.audio_cache.stats()["entries"], 0)
                self.assertFalse(backend.tts_lock.locked())
            with patch.object(server, "stream_speech") as voice:
                with patch.object(tts, "TTS_VOICE", "changed"):
                    self.assert_http_error(409, "/api/test/prepare", request, streaming=True)
                data = json.loads(backend.test_cases_path.read_text())
                data[0]["reply"] = "现在台词变了。"
                backend.test_cases_path.write_text(json.dumps(data), encoding="utf-8")
                self.assert_http_error(409, "/api/test/prepare", request, streaming=True)
                voice.assert_not_called()

    def test_normal_stream_saves_and_cache_hit_does_not_take_synthesis_lock(self):
        with self.backend() as backend:
            backend.tts_api_key = "test-only"
            with patch.object(server, "stream_speech", return_value=iter([b"\x00\x40"])) as voice:
                events = self.request("/api/tts", {"text": "普通聊天也保存音频。"}, streaming=True)
                self.assertTrue(events[-1]["cache_saved"])
                backend.tts_lock.acquire()
                try:
                    cached = self.request("/api/tts", {"text": "普通聊天也保存音频。"}, streaming=True)
                    self.assertEqual(cached[0]["cache"], "hit")
                    self.assertTrue(backend.tts_lock.locked())
                finally:
                    backend.tts_lock.release()
                self.assertEqual(voice.call_count, 1)


if __name__ == "__main__":
    unittest.main()
