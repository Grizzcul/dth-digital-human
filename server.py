"""本地数字人后端：只用 Python 标准库，不需要安装新依赖。
"""

import argparse
import base64
import json
from time import perf_counter
import webbrowser
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from cache import AudioCache, CacheError, digest, load_cases
from tts import MAX_SPEECH_LENGTH, SAMPLE_RATE, TTS_VOICE_NAME, TTSError, audio_profile, synthesize_speech, stream_speech


# __file__ 是当前 Python 文件；网页路径始终相对于它，与启动目录无关。
PROJECT_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"  # 只允许本机访问。此步骤还不是供其他人访问的线上服务。
PORT = 8000
MAX_MESSAGE_LENGTH = 500
# Python 调用已发布的 Dify Chatflow；模型名称和提示词在 Dify 画布中配置。
DIFY_URL = "https://api.dify.ai/v1/chat-messages"
# 此阶段只供你个人在本机使用，先使用固定的 Dify 用户标识。
# user 用于区分用户，conversation_id 用于区分这个用户的不同会话。
DIFY_USER = "local-developer"
# 只读取 Dify 应用密钥；腾讯模型密钥由 Dify 的模型供应商管理。
try:
    from local_config import DIFY_API_KEY
except ImportError:
    DIFY_API_KEY = ""

# 语音密钥与 Dify 密钥分别读取；没配置语音时，文字聊天仍可正常使用。
try:
    from local_config import DOUBAO_TTS_API_KEY
except ImportError:
    DOUBAO_TTS_API_KEY = ""

# 浏览器只能下载网页文件，以及下方登记的 png 文件夹内的图片和视频。
# 第一个值是文件路径，第二个值告诉浏览器“这是什么类型的内容”。
PUBLIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/script.js": ("script.js", "text/javascript; charset=utf-8"),
    "/speech.js": ("speech.js", "text/javascript; charset=utf-8"),
}

# 启动时自动登记角色素材，新增角色不用再逐个修改 Python 路径。
# png 是现有的素材文件夹名称，里面也可以存放 WebM / MP4 视频。
ASSET_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
}
ASSET_DIR = PROJECT_DIR / "png"
# 固定入口预先登记，运行期间新增、删除或替换后刷新网页即可生效。
AVATAR_ASSETS = {
    "png/avatar.png": "image/png",
    "png/avatar.webm": "video/webm",
    "png/avatar.mp4": "video/mp4",
}
PUBLIC_FILES.update({"/" + name: (name, kind) for name, kind in AVATAR_ASSETS.items()})
for asset in ASSET_DIR.rglob("*"):
    content_type = ASSET_TYPES.get(asset.suffix.lower())
    # 只开放素材文件；链接也不能指向素材目录以外的文件。
    if content_type and asset.is_file() and asset.resolve().is_relative_to(ASSET_DIR.resolve()):
        relative_path = asset.relative_to(PROJECT_DIR).as_posix()
        PUBLIC_FILES["/" + relative_path] = (relative_path, content_type)


def avatar_assets():
    """单角色固定素材；每次刷新重新检查，只允许素材目录内的普通文件。"""
    def available(name):
        path = PROJECT_DIR / name
        return path.is_file() and path.resolve().is_relative_to(ASSET_DIR.resolve())

    return {
        "image": "png/avatar.png" if available("png/avatar.png") else None,
        "video": next((name for name in ("png/avatar.webm", "png/avatar.mp4") if available(name)), None),
    }


class LocalServer(ThreadingHTTPServer):
    # Windows 下禁止第二个后端共用已经被监听的端口。
    allow_reuse_address = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 只允许停止本服务当前持有的任务；任务结束即移除，不接受任意云端编号。
        self.active_tasks = {}
        self.tasks_lock = Lock()
        self.tts_api_key = DOUBAO_TTS_API_KEY.strip()
        self.tts_lock = Lock()  # 一次只合成一句，重复点击不并发消耗额度。
        self.audio_cache = AudioCache(PROJECT_DIR / "data" / "cache.sqlite3")
        self.test_cases_path = PROJECT_DIR / "test_cases.json"


def speech_key(text, profile):
    return digest({"text": text.strip(), "profile": profile})


def test_audio_plan(backend, data):
    """浏览器用同一套分句逻辑准备和播放；后端核对台词，拒绝过期计划。"""
    cases, revision = load_cases(backend.test_cases_path)
    manifest = data.get("cases")
    if data.get("revision") != revision:
        raise CacheError("测试台词已修改，请重新点击“检查测试音频”。")
    if not isinstance(manifest, list) or len(manifest) != len(cases):
        raise CacheError("测试音频计划不完整，请重新检查。")
    expected = {case["id"]: case for case in cases}
    seen, items = set(), []
    profile = audio_profile()
    for entry in manifest:
        if not isinstance(entry, dict):
            raise CacheError("测试音频计划格式错误。")
        name, segments = entry.get("id"), entry.get("segments")
        if not isinstance(name, str) or name not in expected or name in seen:
            raise CacheError("测试编号无效或重复。")
        seen.add(name)
        if not isinstance(segments, list) or not 1 <= len(segments) <= 24 or any(
            not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_SPEECH_LENGTH for text in segments
        ):
            raise CacheError("测试回答分段无效，单段不能超过 200 字。")
        if "".join("".join(segments).split()) != "".join(expected[name]["reply"].split()):
            raise CacheError("测试台词与分段不一致，请使用不含 Markdown 的普通文本并重新检查。")
        for text in segments:
            key = speech_key(text, profile)
            if not any(item["key"] == key for item in items):
                items.append({"text": text.strip(), "key": key})
    backend.audio_cache.pin(item["key"] for item in items)
    for item in items:
        item["cached"] = backend.audio_cache.get(item["key"], touch=False) is not None
    plan_id = digest({"revision": revision, "cases": manifest, "profile": profile})
    return {"plan_id": plan_id, "items": items, "can_generate": bool(backend.tts_api_key), "voice": TTS_VOICE_NAME}


def stop_dify_task(task_id, api_key):
    """使用与发起消息相同的 user，向 Dify 请求停止流式生成。"""
    request = Request(
        f"{DIFY_URL}/{task_id}/stop",
        data=json.dumps({"user": DIFY_USER}).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=10) as response:
        result = json.load(response)
    if not isinstance(result, dict) or result.get("result") != "success":
        raise ValueError("Dify 未确认停止请求。")


def make_dify_request(message, conversation_id, api_key, response_mode):
    """两种响应方式共用请求配置，密钥始终留在后端。"""
    payload = {
        "inputs": {},  # 当前画布没有自定义输入变量。
        "query": message,
        "response_mode": response_mode,
        "user": DIFY_USER,
        "conversation_id": conversation_id,  # 空字符串创建新会话；有编号就接着聊。
        "auto_generate_name": False,  # 暂不自动生成会话标题，减少额外模型调用。
    }
    return Request(
        DIFY_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

def get_reply(message, conversation_id, api_key):
    """保留 JSON 接口供诊断工具使用；网页使用下方的流式接口。"""
    request = make_dify_request(message, conversation_id, api_key, "blocking")
    # 只请求一次，不自动重试。
    with urlopen(request, timeout=90) as response:
        result = json.loads(response.read().decode("utf-8"))
    reply = result["answer"]
    next_conversation_id = result["conversation_id"]
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("Dify 没有返回回答正文。")
    if not isinstance(next_conversation_id, str) or not next_conversation_id:
        raise ValueError("Dify 没有返回会话编号。")
    UUID(next_conversation_id)  # 检查返回的编号格式，避免下一轮带着无效编号请求。
    # 用量仅打印在本机终端；不会把密钥传给网页。
    metadata = result.get("metadata")
    usage = metadata.get("usage") if isinstance(metadata, dict) else None
    print("Dify Token 用量：", usage or "接口未提供", flush=True)
    return {"reply": reply, "conversation_id": next_conversation_id}


def iter_dify_events(response):
    """按 SSE 空行分帧；网络分包和中文字符不会被当作完整事件。"""
    lines = []
    size = 0
    while True:
        raw = response.readline(1048577)
        if not raw:
            if lines:
                raise ValueError("Dify 事件未完整传输。")
            return
        size += len(raw)
        if size > 1048576:
            raise ValueError("Dify 单个事件过大。")
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if lines:
                event = json.loads("\n".join(lines))
                if not isinstance(event, dict):
                    raise ValueError("Dify 事件格式错误。")
                yield event
            else:
                yield {"event": "ping"}
            lines = []
            size = 0
        elif line.startswith("data:"):
            lines.append(line[5:].removeprefix(" "))


def stream_reply(message, conversation_id, api_key):
    """只转发回答文字和会话编号，不把工作流内部输入输出暴露给网页。"""
    request = make_dify_request(message, conversation_id, api_key, "streaming")
    next_id = conversation_id
    text = ""
    message_ended = False
    workflow_ended = False
    task_id = ""
    with urlopen(request, timeout=90) as response:
        if response.headers.get_content_type() != "text/event-stream":
            raise ValueError("Dify 未返回流式响应。")
        for event in iter_dify_events(response):
            kind = event.get("event")
            event_id = event.get("conversation_id")
            if event_id:
                if not isinstance(event_id, str):
                    raise ValueError("会话编号格式错误。")
                UUID(event_id)
                if next_id and next_id != event_id:
                    raise ValueError("会话编号发生变化。")
                if not next_id:
                    next_id = event_id
                    yield {"event": "conversation", "conversation_id": next_id}
            event_task = event.get("task_id")
            if event_task:
                if not isinstance(event_task, str):
                    raise ValueError("任务编号格式错误。")
                UUID(event_task)
                if task_id and task_id != event_task:
                    raise ValueError("任务编号发生变化。")
                if not task_id:
                    task_id = event_task
                    yield {"event": "task", "task_id": task_id}
            if kind in ("message", "message_replace"):
                answer = event.get("answer")
                if not isinstance(answer, str) or message_ended:
                    raise ValueError("Dify 回答事件格式错误。")
                text = answer if kind == "message_replace" else text + answer
                if answer or kind == "message_replace":
                    yield {"event": "replace" if kind == "message_replace" else "delta", "text": answer}
            elif kind == "message_end":
                message_ended = True
                metadata = event.get("metadata")
                usage = metadata.get("usage") if isinstance(metadata, dict) else None
                print("Dify Token 用量：", usage or "接口未提供", flush=True)
            elif kind in ("workflow_finished", "node_finished"):
                data = event.get("data")
                if not isinstance(data, dict):
                    raise ValueError("Dify 工作流事件格式错误。")
                if data.get("status") in ("failed", "stopped", "partial-succeeded"):
                    raise ValueError("Dify 工作流未成功完成。")
                if kind == "workflow_finished":
                    workflow_ended = data.get("status") == "succeeded"
            elif kind in ("error", "workflow_paused", "human_input_required"):
                raise ValueError("Dify 未能完成本次回答。")
            elif kind == "ping":
                yield {"event": "ping"}
            if message_ended and workflow_ended:
                break
    if not message_ended or not workflow_ended or not next_id or not text.strip():
        raise ValueError("Dify 回答未完整结束。")
    yield {"event": "done", "conversation_id": next_id}


class AssistantHandler(BaseHTTPRequestHandler):
    """每个浏览器请求都由这个类处理：GET 取文件，POST 发送消息。"""

    def send_json(self, status, data):
        # JSON 是前后端约定的数据格式。UTF-8 和 ensure_ascii=False 保留中文。
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ("/api/chat", "/api/chat/stop", "/api/tts", "/api/test/reply",
                        "/api/test/tts", "/api/test/plan", "/api/test/prepare"):
            self.send_json(404, {"error": "没有这个接口。"})
            return

        # 不信任浏览器的输入限制：后端也要检查格式、字数和请求大小。
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= (131072 if path in ("/api/test/plan", "/api/test/prepare") else 8192):
                self.send_json(413, {"error": "请求为空或太大。"})
                return
            if self.headers.get_content_type() != "application/json":
                self.send_json(415, {"error": "请使用 JSON 格式发送消息。"})
                return
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeError):
            self.send_json(400, {"error": "无法读取消息，请检查 JSON 格式。"})
            return

        if not isinstance(data, dict):
            self.send_json(400, {"error": "消息必须是一个 JSON 对象。"})
            return
        if path.startswith("/api/test/"):
            self.test_request(path, data)
            return
        if path == "/api/tts":
            self.send_speech(data)
            return
        if path == "/api/chat/stop":
            self.stop_chat(data)
            return
        message = data.get("message")
        conversation_id = data.get("conversation_id", "")
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= MAX_MESSAGE_LENGTH:
            self.send_json(400, {"error": "请输入 1～500 字的消息。"})
            return
        if not isinstance(conversation_id, str) or len(conversation_id) > 36:
            self.send_json(400, {"error": "会话编号格式错误，请刷新网页。"})
            return
        if conversation_id:
            try:
                UUID(conversation_id)
            except ValueError:
                self.send_json(400, {"error": "会话编号格式错误，请刷新网页。"})
                return

        # 没填密钥也能启动网页、使用本地模式；只在请求模型时提醒。
        if not self.server.api_key:
            self.send_json(503, {"error": '请在本机 local_config.py 中填写 DIFY_API_KEY 并重启后端。也可以关闭模型开关使用本地回复。'})
            return

        if "text/event-stream" in self.headers.get("Accept", ""):
            self.send_chat_stream(message.strip(), conversation_id)
            return

        # 模型故障也返回 JSON，网页才能显示错误并恢复发送按钮。
        try:
            result = get_reply(message.strip(), conversation_id, self.server.api_key)
        except HTTPError as error:
            hint = "请检查 DIFY_API_KEY 是否为此应用的有效密钥。" if error.code in (401, 403) else "请检查 Dify 已发布版本和模型配置；会话失效时可在右侧记录抽屉点击“开始新对话”。"
            self.send_json(502, {"error": f"Dify 请求失败（HTTP {error.code}）。{hint}"})
            error.close()
            return
        except (URLError, TimeoutError):
            self.send_json(504, {"error": "Dify 连接失败或等待超时，请检查网络。没有自动重试。"})
            return
        except (ValueError, KeyError, IndexError, TypeError):
            self.send_json(502, {"error": "Dify 没有返回有效的回答或会话编号，请确认应用类型为 Chatflow。"})
            return
        self.send_json(200, result)

    def test_request(self, path, data):
        # 独立测试入口绝不调用 Dify，也不创建/修改 conversation_id。
        try:
            if path == "/api/test/tts":
                self.send_speech({**data, "cache_only": True})
                return
            if path == "/api/test/reply":
                text = data.get("message")
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_MESSAGE_LENGTH:
                    self.send_json(400, {"error": "请输入 1～500 字的测试句。"})
                    return
                cases, _ = load_cases(self.server.test_cases_path)
                case = next((case for case in cases if text.strip() in [v.strip() for v in case["inputs"]]), None)
                if case is None:
                    self.send_json(404, {"error": "测试句未收录，请编辑 test_cases.json；本轮没有调用模型或语音服务。"})
                else:
                    self.send_json(200, {"reply": case["reply"], "source": "test_cache", "case_id": case["id"]})
                return
            plan = test_audio_plan(self.server, data)
            if path == "/api/test/plan":
                self.send_json(200, plan)
                return
            if "application/x-ndjson" not in self.headers.get("Accept", ""):
                self.send_json(415, {"error": "准备音频需要 application/x-ndjson 响应格式。"})
                return
            index = data.get("index")
            if data.get("plan_id") != plan["plan_id"] or type(index) is not int or not 0 <= index < len(plan["items"]):
                raise CacheError("音色、台词或准备计划已变化，请重新检查。")
            # 只有用户主动点“生成缺失音频”才访问此入口；一次只生成计划中的一段。
            self.send_speech({"text": plan["items"][index]["text"]}, pinned=True)
        except CacheError as error:
            self.send_json(409, {"error": str(error)})

    def send_speech(self, data, pinned=False):
        """先查本机音频，未命中时按模式决定是否合成；支持 PCM 流与 MP3。"""
        text = data.get("text")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_SPEECH_LENGTH:
            self.send_json(400, {"error": f"朗读文字需要在 1～{MAX_SPEECH_LENGTH} 字以内。"})
            return
        cache_only = data.get("cache_only", False)
        if type(cache_only) is not bool:
            self.send_json(400, {"error": "cache_only 必须是布尔值。"})
            return
        text = text.strip()
        streaming = "application/x-ndjson" in self.headers.get("Accept", "")
        profile = audio_profile("pcm" if streaming else "mp3")
        key = speech_key(text, profile)
        cache = getattr(self.server, "audio_cache", None)
        cached = None
        try:
            if cache:
                cached = cache.get(key)
        except CacheError as error:
            if cache_only or pinned:
                self.send_json(503, {"error": str(error)})
                return
            print("音频缓存不可用，本次按普通语音请求处理。", flush=True)
        if cached is not None:
            if streaming:
                self.send_speech_stream(text, cached=cached)
            else:
                self.send_mp3(cached, "hit")
            return
        if cache_only:
            self.send_json(412, {"error": "没有对应音频缓存，请先检查并生成测试音频；本轮未调用语音服务。"})
            return
        if not self.server.tts_api_key:
            self.send_json(503, {"error": "请在 local_config.py 填写 DOUBAO_TTS_API_KEY，然后重启后端。"})
            return
        if not self.server.tts_lock.acquire(blocking=False):
            self.send_json(429, {"error": "上一句语音仍在合成，请稍后再试。"})
            return
        if streaming:
            self.send_speech_stream(text, cache_key=key, profile=profile, pinned=pinned)
            return
        failure = None
        try:
            audio = synthesize_speech(text.strip(), self.server.tts_api_key)
            if cache:
                try:
                    cache.put(key, text, profile, audio)
                except CacheError:
                    print("MP3 已生成，但缓存保存失败。", flush=True)
        except HTTPError as error:
            hint = ("请在豆包语音“开通管理”检查语音合成2.0是否已开通，并确认 API Key 来自同一项目。"
                    if error.code == 403 else "请检查语音 API Key、服务权限及账户额度。")
            failure = (502, f"豆包语音请求失败（HTTP {error.code}）。{hint}")
            error.close()
        except (OSError, HTTPException):
            failure = (504, "语音服务连接失败或超时，请稍后手动重试。")
        except TTSError as error:
            failure = (502, str(error))
        finally:
            self.server.tts_lock.release()
        if failure:
            self.send_json(failure[0], {"error": failure[1]})
            return
        self.send_mp3(audio, "miss")

    def send_mp3(self, audio, cache_state):
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Audio-Cache", cache_state)
        self.end_headers()
        try:
            self.wfile.write(audio)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 页面停止播放或关闭时，不把断开连接当成模型错误。

    def send_speech_stream(self, text, cached=None, cache_key=None, profile=None, pinned=False):
        """同一个接口用 Accept 区分播放方式；NDJSON 每行一块，done 才算完整。"""
        def emit(event):
            self.wfile.write((json.dumps(event) + "\n").encode("utf-8"))
            self.wfile.flush()  # 少了这一行，音频可能又积攒到最后才送到网页。

        started = perf_counter()
        first = None
        events = (iter(cached[i:i + 24000] for i in range(0, len(cached), 24000)) if cached is not None
                  else stream_speech(text, self.server.tts_api_key))
        collected = bytearray()
        saved = cached is not None
        warning = None
        error_message = None
        disconnected = False
        try:
            # 响应头也放在 try 内：页面立即取消时，同样需要释放合成锁。
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            emit({"event": "start", "sample_rate": SAMPLE_RATE, "format": "pcm_s16le",
                  "cache": "hit" if cached is not None else "miss"})
            for audio in events:
                if first is None:
                    first = perf_counter() - started
                emit({"event": "audio", "data": base64.b64encode(audio).decode("ascii")})
                if cached is None and cache_key:
                    collected.extend(audio)
            cache = getattr(self.server, "audio_cache", None)
            if cached is None and cache and cache_key:
                try:
                    cache.put(cache_key, text, profile, bytes(collected), pinned=pinned)
                    saved = True
                except CacheError as error:
                    if pinned:
                        raise
                    warning = str(error)
        except (BrokenPipeError, ConnectionResetError):
            disconnected = True
        except HTTPError as error:
            error_message = f"语音服务请求失败（HTTP {error.code}），请检查语音权限及额度。"
            error.close()
        except (OSError, HTTPException):
            error_message = "语音连接中断或超时，请手动重试。"
        except TTSError as error:
            error_message = str(error)
        except CacheError as error:
            error_message = str(error)
        finally:
            if hasattr(events, "close"):
                events.close()  # 关闭上游连接；不能承诺云端立即停止计费。
            if cached is None:
                self.server.tts_lock.release()
        if disconnected:
            return
        try:
            # 先释放锁再通知完成，下一句即可开始合成，与当前句的播放重叠。
            if error_message:
                emit({"event": "error", "error": error_message})
            else:
                elapsed = perf_counter() - started
                emit({"event": "done", "first_chunk_ms": round(first * 1000),
                      "synthesis_ms": round(elapsed * 1000), "cache_saved": saved, "warning": warning})
                print(f"音频缓存{'命中' if cached is not None else '未命中'}：首包 {first:.2f}s，完成 {elapsed:.2f}s", flush=True)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def stop_chat(self, data):
        task_id = data.get("task_id")
        try:
            if not isinstance(task_id, str) or len(task_id) != 36:
                raise ValueError
            UUID(task_id)
        except ValueError:
            self.send_json(400, {"error": "任务编号格式错误。"})
            return
        with self.server.tasks_lock:
            task = self.server.active_tasks.get(task_id)
        if task is None:
            self.send_json(404, {"error": "任务已结束或不属于当前服务，请等待页面完成更新。"})
            return
        # 同一任务的重复点击只调用一次云端接口；失败时允许用户再次尝试。
        with task["lock"]:
            if not task["stopped"].is_set():
                try:
                    stop_dify_task(task_id, self.server.api_key)
                except HTTPError as error:
                    self.send_json(502, {"error": f"Dify 未确认停止（HTTP {error.code}），请稍后重试。"})
                    error.close()
                    return
                except (OSError, HTTPException, ValueError):
                    self.send_json(502, {"error": "未能确认云端停止，回答可能仍在生成，请重试停止。"})
                    return
                task["stopped"].set()
        self.send_json(200, {"status": "stopped"})

    def send_chat_stream(self, message, conversation_id):
        # HTTP/1.0 按连接关闭定界，不写 Content-Length，不缓存、不缓冲。
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(event):
            frame = "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            self.wfile.write(frame.encode("utf-8"))
            self.wfile.flush()

        events = stream_reply(message, conversation_id, self.server.api_key)
        registered_task = None
        try:
            emit({"event": "start"})
            try:
                for event in events:
                    if event.get("event") == "task":
                        registered_task = event["task_id"]
                        with self.server.tasks_lock:
                            self.server.active_tasks[registered_task] = {"lock": Lock(), "stopped": Event()}
                    emit(event)
            except HTTPError as error:
                hint = "请检查 DIFY_API_KEY。" if error.code in (401, 403) else "请检查 Dify 配置；会话失效时可开始新对话。"
                try:
                    emit({"event": "error", "error": f"Dify 请求失败（HTTP {error.code}）。{hint}"})
                finally:
                    error.close()
            except (URLError, OSError, HTTPException):
                emit({"event": "error", "error": "Dify 连接中断或等待超时，回答可能未完成。没有自动重试。"})
            except (ValueError, KeyError, TypeError):
                emit({"event": "error", "error": "Dify 未返回完整有效的回答，请检查已发布的 Chatflow 配置。"})
        except OSError:
            pass  # 浏览器关闭或断开；关闭上游连接，但不宣称已停止云端生成。
        finally:
            events.close()
            if registered_task:
                with self.server.tasks_lock:
                    self.server.active_tasks.pop(registered_task, None)

    def do_GET(self):
        if urlsplit(self.path).path == "/api/test/cases":
            try:
                cases, revision = load_cases(self.server.test_cases_path)
                self.send_json(200, {"cases": cases, "revision": revision})
            except CacheError as error:
                self.send_json(503, {"error": str(error)})
            return
        if urlsplit(self.path).path == "/api/tts":
            # 只返回是否配置及音色名称，不返回密钥；刷新页面不会合成语音。
            cache = getattr(self.server, "audio_cache", None)
            try:
                stats = cache.stats() if cache else {"entries": 0, "bytes": 0, "hits": 0}
                available = cache is not None
            except CacheError:
                stats, available = {"entries": 0, "bytes": 0, "hits": 0}, False
            self.send_json(200, {"configured": bool(self.server.tts_api_key), "voice": TTS_VOICE_NAME,
                                 "cache_available": available, "cache": stats})
            return
        if urlsplit(self.path).path == "/api/avatar":
            self.send_json(200, avatar_assets())
            return
        # 启动器只检查这个接口，不调用模型、不消耗 token。
        if urlsplit(self.path).path == "/api/health":
            self.send_json(200, {"app": "dth-avatar", "project": str(PROJECT_DIR)})
            return
        self.send_public_file()

    def do_HEAD(self):
        # HEAD 只返回文件信息，不发送文件内容。
        self.send_public_file(head_only=True)

    def send_public_file(self, head_only=False):
        entry = PUBLIC_FILES.get(urlsplit(self.path).path)
        if entry is None:
            self.send_error(404, "File not public")
            return
        relative_path, content_type = entry
        file_path = PROJECT_DIR / relative_path
        if relative_path.startswith("png/") and not file_path.resolve().is_relative_to(ASSET_DIR.resolve()):
            self.send_error(404, "File not public")
            return
        try:
            file = file_path.open("rb")
        except OSError:
            self.send_error(404, "File not found")
            return

        with file:
            size = file_path.stat().st_size
            start, end = 0, size - 1
            range_header = self.headers.get("Range")
            # 浏览器可能只索取视频的一段。支持 Range 才能可靠地跳回第一帧。
            # 例如 bytes=0-1023 表示读取文件的前 1024 个字节。
            if range_header:
                try:
                    if not range_header.startswith("bytes="):
                        raise ValueError
                    left, right = range_header[6:].split("-", 1)
                    if left:
                        start = int(left)
                        end = min(int(right), size - 1) if right else size - 1
                    else:
                        suffix_length = int(right)
                        if suffix_length <= 0:
                            raise ValueError
                        start = max(0, size - suffix_length)
                    if not 0 <= start <= end < size:
                        raise ValueError
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

            self.send_response(206 if range_header else 200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            if range_header:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head_only:
                return

            file.seek(start)
            remaining = end - start + 1
            try:
                while remaining > 0:
                    chunk = file.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # 用户关闭网页或取消视频加载时，不需要报错。


def open_existing_page():
    """端口已占用时，确认是不是本项目的后端，避免重复启动或打开别的服务。"""
    url = f"http://{HOST}:{PORT}"
    try:
        with urlopen(f"{url}/api/health", timeout=2) as response:
            health = json.loads(response.read().decode("utf-8"))
        if health.get("app") != "dth-avatar" or health.get("project") != str(PROJECT_DIR):
            return False
    except (OSError, ValueError, AttributeError):
        return False
    webbrowser.open(url)
    return True


def main(open_browser=False):
    # 先检查已启动的本项目；重复双击直接打开网页。
    if open_browser and open_existing_page():
        print("后端已经在运行，已打开网页。", flush=True)
        return
    try:
        with LocalServer((HOST, PORT), AssistantHandler) as server:
            # 启动时直接读取上面的配置，不再询问密钥。
            server.api_key = DIFY_API_KEY.strip()
            print(f"请在浏览器打开：http://{HOST}:{PORT}", flush=True)
            print("默认本地回复；打开网页的模型开关后才调用 Dify 对话流。", flush=True)
            if not server.api_key:
                print("尚未填写 DIFY_API_KEY，可先使用本地模式。", flush=True)
            print("关闭本窗口或按 Ctrl+C 停止后端。修改 Python 后请重启。", flush=True)
            # 先成功绑定端口，再打开网页；双击启动文件会启用这个选项。
            if open_browser:
                webbrowser.open(f"http://{HOST}:{PORT}")
            server.serve_forever()
    except KeyboardInterrupt:
        print("\n后端已停止。", flush=True)
    except OSError as error:
        # 重复双击时，打开已经运行的同一项目，不再启动第二个后端。
        if open_browser and open_existing_page():
            print("后端已经在运行，已打开网页。", flush=True)
            return
        raise SystemExit(f"后端启动失败：{error}\n请检查是否已经有一个后端在运行。")


if __name__ == "__main__":
    # --open-browser 是可选启动参数；普通导入本文件不会启动后端。
    parser = argparse.ArgumentParser(description="启动本地数字人网页")
    parser.add_argument("--open-browser", action="store_true", help="启动后自动打开浏览器")
    options = parser.parse_args()
    main(open_browser=options.open_browser)
