"""流式协议与本地 HTTP 集成测试，不调用真实 Dify。"""

import io
import json
from email.message import Message
from pathlib import Path
import sys
from threading import Event, Lock, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

CID = "00000000-0000-4000-8000-000000000001"
TID = "00000000-0000-4000-8000-000000000010"


def frame(kind, **fields):
    return ("data: " + json.dumps({"event": kind, **fields}, ensure_ascii=False) + "\r\n\r\n").encode()


def ending():
    return frame("message_end", conversation_id=CID) + frame("workflow_finished", data={"status": "succeeded"})


class Upstream(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = Message()
        self.headers["Content-Type"] = "text/event-stream"


class StreamingTests(unittest.TestCase):
    def collect(self, body):
        with patch.object(server, "urlopen", return_value=Upstream(body)) as open_mock:
            events = list(server.stream_reply("你好", "", "fake-key"))
            payload = json.loads(open_mock.call_args.args[0].data)
            self.assertEqual(payload["response_mode"], "streaming")
            self.assertFalse(payload["auto_generate_name"])
            return events

    def test_chinese_replacement_and_completion(self):
        body = b"event: ping\r\n\r\n" + frame("message", answer="你好🙂", conversation_id=CID)
        body += frame("message_replace", answer="替换后的回答", conversation_id=CID) + ending()
        events = self.collect(body)
        self.assertEqual([e["event"] for e in events], ["ping", "conversation", "delta", "replace", "done"])
        self.assertEqual(events[2]["text"], "你好🙂")
        self.assertEqual(events[-1]["conversation_id"], CID)

    def test_workflow_finishes_before_message_end(self):
        body = frame("message", answer="回答", conversation_id=CID)
        body += frame("workflow_finished", data={"status": "succeeded"}) + frame("message_end")
        self.assertEqual(self.collect(body)[-1]["event"], "done")

    def test_task_available_before_answer(self):
        body = frame("workflow_started", task_id=TID, conversation_id=CID)
        body += frame("message", task_id=TID, answer="回答", conversation_id=CID) + ending()
        events = self.collect(body)
        self.assertEqual([e["event"] for e in events], ["conversation", "task", "delta", "done"])
        self.assertEqual(events[1]["task_id"], TID)

    def test_stop_uses_matching_user_and_checks_acknowledgment(self):
        with patch.object(server, "urlopen", return_value=Upstream(b'{"result":"success"}')) as upstream:
            server.stop_dify_task(TID, "fake-key")
            request = upstream.call_args.args[0]
            self.assertEqual(request.full_url, server.DIFY_URL + "/" + TID + "/stop")
            self.assertEqual(json.loads(request.data), {"user": server.DIFY_USER})
        with patch.object(server, "urlopen", return_value=Upstream(b'{"result":"unknown"}')):
            with self.assertRaises(ValueError):
                server.stop_dify_task(TID, "fake-key")

    def stop_handler(self, active=True):
        handler = object.__new__(server.AssistantHandler)
        handler.server = SimpleNamespace(api_key="fake-key", tasks_lock=Lock(), active_tasks={})
        if active:
            handler.server.active_tasks[TID] = {"lock": Lock(), "stopped": Event()}
        handler.send_json = Mock()
        return handler

    def test_only_active_tasks_can_be_stopped(self):
        handler = self.stop_handler(active=False)
        with patch.object(server, "stop_dify_task") as stop:
            handler.stop_chat({"task_id": TID})
            self.assertEqual(handler.send_json.call_args.args[0], 404)
            handler.stop_chat({"task_id": "../invalid"})
            self.assertEqual(handler.send_json.call_args.args[0], 400)
            stop.assert_not_called()

    def test_stop_is_idempotent_and_failures_can_retry(self):
        handler = self.stop_handler()
        with patch.object(server, "stop_dify_task", side_effect=[URLError("private"), None]) as stop:
            handler.stop_chat({"task_id": TID})
            self.assertEqual(handler.send_json.call_args.args[0], 502)
            self.assertFalse(handler.server.active_tasks[TID]["stopped"].is_set())
            handler.stop_chat({"task_id": TID})
            handler.stop_chat({"task_id": TID})
            self.assertEqual(stop.call_count, 2)
            self.assertEqual(handler.send_json.call_args.args, (200, {"status": "stopped"}))

    def test_incomplete_stream_never_succeeds(self):
        with self.assertRaises(ValueError):
            self.collect(frame("message", answer="部分", conversation_id=CID))

    def test_workflow_error_never_succeeds(self):
        with self.assertRaises(ValueError):
            self.collect(frame("message", answer="部分", conversation_id=CID) + frame("workflow_finished", data={"status": "failed"}))

    def test_upstream_error(self):
        with self.assertRaises(ValueError):
            self.collect(frame("error", message="private upstream details"))

    def test_conversation_cannot_change(self):
        with self.assertRaises(ValueError):
            self.collect(frame("message", answer="a", conversation_id=CID) + frame("message", answer="b", conversation_id="00000000-0000-4000-8000-000000000002"))

    def test_partial_sse_frame_rejected(self):
        with self.assertRaises(ValueError):
            list(server.iter_dify_events(Upstream(b'data: {"event":"message"}\n')))

    def test_multiline_sse(self):
        result = list(server.iter_dify_events(Upstream(b'data: {"event":\r\ndata: "ping"}\r\n\r\n')))
        self.assertEqual(result, [{"event": "ping"}])

    def test_handler_reports_errors_without_leaking_upstream_details(self):
        for error in (HTTPError("https://example.invalid", 401, "private-details", {}, io.BytesIO(b"private-details")),
                      URLError("private-details"), ValueError("private-details")):
            with self.subTest(error=type(error).__name__):
                def failed_stream(*_args):
                    yield {"event": "delta", "text": "部分回答"}
                    raise error

                handler = object.__new__(server.AssistantHandler)
                handler.server = SimpleNamespace(api_key="fake-key")
                handler.wfile = io.BytesIO()
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock()
                with patch.object(server, "stream_reply", side_effect=failed_stream):
                    handler.send_chat_stream("hello", "")
                body = handler.wfile.getvalue()
                self.assertIn(b'"event": "delta"', body)
                self.assertIn(b'"event": "error"', body)
                self.assertNotIn(b'"event": "done"', body)
                self.assertNotIn(b"private-details", body)

    def test_stream_sends_text_before_upstream_finishes(self):
        release = Event()
        waiting = Event()

        class GatedUpstream(Upstream):
            def __init__(self):
                super().__init__(frame("workflow_started", task_id=TID, conversation_id=CID)
                                 + frame("message", answer="第一段🙂", conversation_id=CID))
                self.tail = io.BytesIO(ending())

            def readline(self, limit=-1):
                line = super().readline(limit)
                if line:
                    return line
                waiting.set()
                if not release.wait(5):
                    raise TimeoutError
                return self.tail.readline(limit)

        class QuietHandler(server.AssistantHandler):
            def log_message(self, *_args):
                pass

        backend = server.LocalServer((server.HOST, 0), QuietHandler)
        backend.api_key = "fake-key"
        worker = Thread(target=backend.serve_forever, daemon=True)
        worker.start()
        base = f"http://{server.HOST}:{backend.server_port}"
        upstream = GatedUpstream()
        try:
            with patch.object(server, "urlopen", return_value=upstream):
                req = Request(base + "/api/chat", data=b'{"message":"hello"}', headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
                with urlopen(req, timeout=3) as response:
                    self.assertEqual(response.headers.get_content_type(), "text/event-stream")
                    chunks = []
                    while True:
                        line = response.readline()
                        if line.startswith(b"data:"):
                            event = json.loads(line[5:])
                            chunks.append(event)
                            if event["event"] == "delta":
                                break
                    self.assertEqual(chunks[-1]["text"], "第一段🙂")
                    self.assertTrue(waiting.wait(2))
                    self.assertFalse(upstream.closed)
                    with urlopen(base + "/api/health", timeout=3) as health:
                        self.assertEqual(health.status, 200)
                    # 停止接口与原流并发处理，不能被正在等待的模型请求阻塞。
                    with patch.object(server, "stop_dify_task") as stop:
                        stop_request = Request(base + "/api/chat/stop", data=json.dumps({"task_id": TID}).encode(), headers={"Content-Type": "application/json"})
                        with urlopen(stop_request, timeout=3) as stopped:
                            self.assertEqual(json.load(stopped), {"status": "stopped"})
                        stop.assert_called_once_with(TID, "fake-key")
                    release.set()
                    self.assertIn(b'"event": "done"', response.read())
            self.assertTrue(upstream.closed)
            self.assertEqual(backend.active_tasks, {})
        finally:
            release.set()
            backend.shutdown()
            backend.server_close()
            worker.join(3)


if __name__ == "__main__":
    unittest.main()
