"""本地数字人后端：只用 Python 标准库，不需要安装新依赖。
"""

import argparse
import json
import webbrowser
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID


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

# 浏览器只能下载网页文件，以及下方登记的 png 文件夹内的图片和视频。
# 第一个值是文件路径，第二个值告诉浏览器“这是什么类型的内容”。
PUBLIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/script.js": ("script.js", "text/javascript; charset=utf-8"),
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
for asset in ASSET_DIR.rglob("*"):
    content_type = ASSET_TYPES.get(asset.suffix.lower())
    # 只开放素材文件；链接也不能指向素材目录以外的文件。
    if content_type and asset.is_file() and asset.resolve().is_relative_to(ASSET_DIR.resolve()):
        relative_path = asset.relative_to(PROJECT_DIR).as_posix()
        PUBLIC_FILES["/" + relative_path] = (relative_path, content_type)


class LocalServer(ThreadingHTTPServer):
    # Windows 下禁止第二个后端共用已经被监听的端口。
    allow_reuse_address = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 只允许停止本服务当前持有的任务；任务结束即移除，不接受任意云端编号。
        self.active_tasks = {}
        self.tasks_lock = Lock()


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
        if path not in ("/api/chat", "/api/chat/stop"):
            self.send_json(404, {"error": "没有这个接口。"})
            return

        # 不信任浏览器的输入限制：后端也要检查格式、字数和请求大小。
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
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
