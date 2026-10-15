"""数字人延迟诊断；默认只测本地，--live 才发送两次真实 Dify 请求。

不输出密钥、回答内容或会话编号，不修改应用配置和浏览器的聊天记录。
真实测试各创建一个独立云端会话，会消耗少量模型额度。
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import statistics
import sys
from threading import Event, Thread
from time import perf_counter
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402

TEST_ID = "00000000-0000-4000-8000-000000000001"


def milliseconds(start):
    return round((perf_counter() - start) * 1000, 2)


def local_checks():
    class QuietHandler(server.AssistantHandler):
        def log_message(self, *_args):
            pass

    backend = server.LocalServer((server.HOST, 0), QuietHandler)
    backend.api_key = "audit-placeholder"
    worker = Thread(target=backend.serve_forever, daemon=True)
    worker.start()
    base = f"http://{server.HOST}:{backend.server_port}"

    def request(path, data=None, headers=None, method=None):
        req = Request(base + path, data=data, headers=headers or {}, method=method)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), dict(response.headers)

    def post(data, content_type="application/json"):
        return request("/api/chat", json.dumps(data).encode(), {"Content-Type": content_type})

    checks = {}
    times = []
    try:
        fake_reply = {"reply": "诊断回复", "conversation_id": TEST_ID}
        with patch.object(server, "get_reply", return_value=fake_reply):
            for _ in range(30):
                started = perf_counter()
                status, body, _ = post({"message": "你好"})
                times.append(milliseconds(started))
                if status != 200 or json.loads(body) != fake_reply:
                    raise RuntimeError("本地聊天接口校验失败")
            checks["empty_message_400"] = post({"message": " "})[0] == 400
            checks["long_message_400"] = post({"message": "a" * 501})[0] == 400
            checks["invalid_conversation_400"] = post({"message": "a", "conversation_id": "bad"})[0] == 400
            checks["non_object_400"] = post([])[0] == 400
            checks["content_type_415"] = post({"message": "a"}, "text/plain")[0] == 415
            checks["oversize_413"] = request("/api/chat", b"a" * 8193, {"Content-Type": "application/json"})[0] == 413
        for path in ("/server.py", "/local_config.py", "/.git/config", "/../local_config.py"):
            checks[f"private_{path}_404"] = request(path)[0] == 404
        asset_path = next(path for path in server.PUBLIC_FILES if path.endswith(".webm"))
        status, chunk, headers = request(asset_path, headers={"Range": "bytes=0-15"})
        checks["video_range_206"] = status == 206 and len(chunk) == 16 and headers.get("Content-Range", "").startswith("bytes 0-15/")
        checks["invalid_range_416"] = request(asset_path, headers={"Range": "bytes=999999999-"})[0] == 416
        status, body, _ = request(asset_path, method="HEAD")
        checks["head_without_body"] = status == 200 and not body
        started_upstream = Event()
        release_upstream = Event()

        def delayed_reply(*_args):
            started_upstream.set()
            if not release_upstream.wait(5):
                raise RuntimeError("并发诊断超时")
            return fake_reply

        with patch.object(server, "get_reply", side_effect=delayed_reply):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(post, {"message": "并发诊断"})
                try:
                    if not started_upstream.wait(3):
                        raise RuntimeError("聊天请求未启动")
                    started = perf_counter()
                    health = request("/api/health")
                    health_ms = milliseconds(started)
                    checks["health_responds_during_chat"] = health[0] == 200 and not pending.done()
                finally:
                    release_upstream.set()
                checks["delayed_chat_completes"] = pending.result()[0] == 200
        return {
            "samples": len(times),
            "mock_chat_median_ms": round(statistics.median(times), 2),
            "mock_chat_p95_ms": sorted(times)[math.ceil(len(times) * 0.95) - 1],
            "health_during_chat_ms": health_ms,
            "checks": checks,
            "note": "模拟模型回答，只衡量 HTTP/JSON 本地处理，不代表真实模型延迟。",
        }
    finally:
        backend.shutdown()
        backend.server_close()
        worker.join(timeout=3)


def cloud_probe(mode):
    payload = {
        "inputs": {}, "query": "你好，请用一句不超过20字的中文介绍你自己。",
        "response_mode": mode, "user": server.DIFY_USER,
        "conversation_id": "", "auto_generate_name": False,
    }
    req = Request(server.DIFY_URL, data=json.dumps(payload).encode(), headers={
        "Authorization": f"Bearer {server.DIFY_API_KEY.strip()}",
        "Content-Type": "application/json",
    })
    started = perf_counter()
    result = {"mode": mode}
    try:
        with urlopen(req, timeout=30) as response:
            result["headers_ms"] = milliseconds(started)
            if mode == "blocking":
                data = json.load(response)
                result["complete_ms"] = milliseconds(started)
                result["answer_characters"] = len(data.get("answer", ""))
                usage = data.get("metadata", {}).get("usage", {})
            else:
                usage = {}
                result.update(first_text_ms=None, answer_characters=0, events={}, nodes=[], message_end=False, workflow_finished=False)
                for line in response:
                    if perf_counter() - started > 90:
                        raise TimeoutError("诊断总时长超过90秒")
                    if not line.startswith(b"data:"):
                        continue
                    data = json.loads(line[5:])
                    event = data.get("event", "unknown")
                    result["events"][event] = result["events"].get(event, 0) + 1
                    if event in ("message", "agent_message") and data.get("answer"):
                        if result["first_text_ms"] is None:
                            result["first_text_ms"] = milliseconds(started)
                        result["answer_characters"] += len(data["answer"])
                    elif event == "node_finished":
                        node = data.get("data", {})
                        result["nodes"].append({k: node.get(k) for k in ("node_type", "elapsed_time", "status")})
                    elif event == "message_end":
                        result["message_end"] = True
                        result["answer_end_ms"] = milliseconds(started)
                        usage = data.get("metadata", {}).get("usage", {})
                    elif event == "workflow_finished":
                        result["workflow_finished"] = True
                        result["workflow_status"] = data.get("data", {}).get("status")
                        break
                    elif event == "error":
                        result["upstream_error_code"] = data.get("code")
                        break
                result["complete_ms"] = milliseconds(started)
            result["usage"] = {k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens", "latency") if k in usage}
    except HTTPError as error:
        result["http_error"] = error.code
        error.close()
    except (URLError, OSError, ValueError) as error:
        result["error_type"] = type(error).__name__
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="额外发送两次真实请求，消耗模型额度")
    parser.add_argument("--output", type=Path, help="将不含密钥和聊天正文的结果保存为JSON")
    args = parser.parse_args()
    result = {"local": local_checks()}
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if args.live:
        if not server.DIFY_API_KEY.strip():
            raise SystemExit("尚未配置 DIFY_API_KEY")
        result["cloud"] = []
        for mode in ("blocking", "streaming"):
            probe = cloud_probe(mode)
            result["cloud"].append(probe)
            print(json.dumps(probe, ensure_ascii=False, indent=2), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not all(result["local"]["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
