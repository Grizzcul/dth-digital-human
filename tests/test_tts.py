"""语音请求和 HTTP 接口测试：模拟供应商，不使用真实密钥、不产生费用。"""
import base64
from io import BytesIO
import json
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
import tts


def chunk(code=0, data=None):
    return (json.dumps({"code": code, "data": data}) + "\n").encode()


class TTSTests(unittest.TestCase):
    def test_cancel_during_headers_releases_stream_lock(self):
        from threading import Lock
        handler = object.__new__(server.AssistantHandler)
        handler.server = SimpleNamespace(tts_api_key="test-only", tts_lock=Lock())
        handler.server.tts_lock.acquire()
        with patch.object(handler, "send_response"), patch.object(handler, "send_header"), \
                patch.object(handler, "end_headers", side_effect=BrokenPipeError), patch.object(tts, "urlopen") as provider:
            handler.send_speech_stream("测试取消。")
            provider.assert_not_called()
        self.assertFalse(handler.server.tts_lock.locked())

    def test_pcm_generator_yields_before_completion_and_closes_upstream(self):
        response = BytesIO(chunk(data=base64.b64encode(b"\x00\x40").decode()) + chunk(20000000))
        with patch.object(tts, "urlopen", return_value=response) as call:
            audio = tts.stream_speech("流式测试。", "test-only")
            self.assertEqual(next(audio), b"\x00\x40")
            self.assertLess(response.tell(), len(response.getvalue()))
            self.assertEqual(json.loads(call.call_args.args[0].data)["req_params"]["audio_params"]["format"], "pcm")
            audio.close()
            self.assertTrue(response.closed)

    def test_http_pcm_reaches_client_before_provider_finishes(self):
        class QuietHandler(server.AssistantHandler):
            def log_message(self, *_args):
                pass
        backend = server.LocalServer((server.HOST, 0), QuietHandler)
        backend.audio_cache = None  # 老接口回归不写入项目的真实缓存。
        backend.tts_api_key = "test-only"
        worker = Thread(target=backend.serve_forever, daemon=True)
        worker.start()
        release = Event()
        def provider(*_args):
            yield b"\x00\x40"
            if not release.wait(3):
                raise RuntimeError("Client did not receive first chunk")
            yield b"\x00\xc0"
        try:
            with patch.object(server, "stream_speech", side_effect=provider):
                request = Request(f"http://{server.HOST}:{backend.server_port}/api/tts",
                                  data=b'{"text":"test"}', headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"})
                with urlopen(request, timeout=3) as response:
                    self.assertEqual(json.loads(response.readline())["format"], "pcm_s16le")
                    self.assertEqual(json.loads(response.readline())["event"], "audio")
                    self.assertFalse(release.is_set())
                    release.set()
                    self.assertEqual(json.loads(response.readline())["event"], "audio")
                    self.assertEqual(json.loads(response.readline())["event"], "done")
                    self.assertFalse(backend.tts_lock.locked())
        finally:
            release.set()
            backend.shutdown()
            backend.server_close()
            worker.join(3)

    def test_collects_mp3_chunks_and_keeps_credentials_backend_only(self):
        response = BytesIO(chunk(data=base64.b64encode(b"ID3first").decode())
                           + chunk(data=base64.b64encode(b"second").decode()) + chunk(20000000))
        with patch.object(tts, "urlopen", return_value=response) as call:
            self.assertEqual(tts.synthesize_speech("你好。", "test-secret"), b"ID3firstsecond")
        request = call.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["req_params"]["speaker"], tts.TTS_VOICE)
        self.assertNotIn("test-secret", request.data.decode())
        self.assertEqual(request.get_header("X-api-key"), "test-secret")
        self.assertEqual(request.get_header("X-api-resource-id"), "seed-tts-2.0")

    def test_rejects_incomplete_invalid_or_failed_audio(self):
        for body in [chunk(data="SUQz"), chunk(20000000), b"not json\n", chunk(data="!invalid!"),
                     chunk(45000000), b"[]\n", b"x" * (512 * 1024 + 1)]:
            with self.subTest(body=body[:30]), patch.object(tts, "urlopen", return_value=BytesIO(body)):
                with self.assertRaises(tts.TTSError):
                    tts.synthesize_speech("你好。", "test-only")

    def test_invalid_text_never_calls_provider(self):
        with patch.object(tts, "urlopen") as call:
            for text in [None, " ", "字" * 201]:
                with self.assertRaises(tts.TTSError):
                    tts.synthesize_speech(text, "test-only")
            call.assert_not_called()

    def test_http_settings_missing_key_busy_audio_and_private_files(self):
        class QuietHandler(server.AssistantHandler):
            def log_message(self, *_args):
                pass
        backend = server.LocalServer((server.HOST, 0), QuietHandler)
        backend.audio_cache = None
        backend.tts_api_key = ""
        worker = Thread(target=backend.serve_forever, daemon=True)
        worker.start()
        base = f"http://{server.HOST}:{backend.server_port}"

        def post(data):
            return urlopen(Request(base + "/api/tts", data=json.dumps(data).encode(),
                                   headers={"Content-Type": "application/json"}), timeout=3)
        try:
            with urlopen(base + "/api/tts", timeout=3) as response:
                settings = json.load(response)
                self.assertFalse(settings["configured"])
                self.assertEqual(settings["voice"], "魅力女友 2.0")
            with self.assertRaises(HTTPError) as missing:
                post({"text": "你好。"})
            self.assertEqual(missing.exception.code, 503)
            missing.exception.close()
            backend.tts_api_key = "test-only"
            with patch.object(server, "synthesize_speech", return_value=b"ID3fake") as synth:
                with post({"text": "你好。"}) as response:
                    self.assertEqual(response.headers.get_content_type(), "audio/mpeg")
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(response.read(), b"ID3fake")
                synth.assert_called_once_with("你好。", "test-only")
            with patch.object(server, "synthesize_speech") as synth:
                backend.tts_lock.acquire()
                try:
                    with self.assertRaises(HTTPError) as busy:
                        post({"text": "你好。"})
                    self.assertEqual(busy.exception.code, 429)
                    busy.exception.close()
                    synth.assert_not_called()
                finally:
                    backend.tts_lock.release()
            with patch.object(server, "synthesize_speech", side_effect=tts.TTSError("测试失败")):
                with self.assertRaises(HTTPError) as failure:
                    post({"text": "你好。"})
                self.assertEqual(failure.exception.code, 502)
                failure.exception.close()
                self.assertFalse(backend.tts_lock.locked())
            for path in ("/tts.py", "/local_config.py"):
                with self.assertRaises(HTTPError) as hidden:
                    urlopen(base + path, timeout=3)
                self.assertEqual(hidden.exception.code, 404)
                hidden.exception.close()
        finally:
            backend.shutdown()
            backend.server_close()
            worker.join(3)


if __name__ == "__main__":
    unittest.main()
