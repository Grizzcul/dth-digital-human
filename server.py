"""本地数字人后端：只用 Python 标准库，不需要安装新依赖。
"""

import argparse
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


# __file__ 是当前 Python 文件；网页路径始终相对于它，与启动目录无关。
PROJECT_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"  # 只允许本机访问。此步骤还不是供其他人访问的线上服务。
PORT = 8000
MAX_MESSAGE_LENGTH = 500
# 与已经测试成功的 curl 相同；以后换模型时，先看这里。
MODEL_URL = "https://tokenhub.tencentmaas.com/v1/chat/completions"
MODEL_NAME = "kimi-k3"
# 从本机配置读取密钥；新电脑未配置时仍能使用本地回复。
try:
    from local_config import API_KEY
except ModuleNotFoundError:
    API_KEY = ""

# 浏览器只能下载这里列出的文件，不能下载 Python、.git 或 tools 内的文件。
# 第一个值是文件路径，第二个值告诉浏览器“这是什么类型的内容”。
PUBLIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/script.js": ("script.js", "text/javascript; charset=utf-8"),
    "/png/avatar-blink.webm": ("png/avatar-blink.webm", "video/webm"),
    "/png/avatar-idle.png": ("png/avatar-idle.png", "image/png"),
}


class LocalServer(ThreadingHTTPServer):
    # Windows 下禁止第二个后端共用已经被监听的端口。
    allow_reuse_address = False


def get_reply(message, assistant_name, api_key):
    """把用户的话发给 Kimi，取回回答。此步骤每次只发送当前这一句话。"""
    payload = {
        "model": MODEL_NAME,
        "messages": [
            # 【可以改】system 设置角色和语气；名字仍然来自 HTML。
            {"role": "system", "content": f"你叫 {assistant_name}，是一位私人数字人助手。请用中文回答，尽量用一到三句话说明。"},
            {"role": "user", "content": message},
        ],
        "stream": False,
    }
    request = Request(
        MODEL_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    # 和 test_model.py 一样只请求一次，不自动重试。
    with urlopen(request, timeout=90) as response:
        result = json.loads(response.read().decode("utf-8"))
    reply = result["choices"][0]["message"]["content"]
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("模型没有返回回答正文。")
    # 用量仅打印在本机终端；不会把密钥传给网页。
    print("Token 用量：", result.get("usage", "接口未提供"), flush=True)
    return reply


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
        if urlsplit(self.path).path != "/api/chat":
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
        message = data.get("message")
        name = data.get("assistant_name", "Grace")
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= MAX_MESSAGE_LENGTH:
            self.send_json(400, {"error": "请输入 1～500 字的消息。"})
            return
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            self.send_json(400, {"error": "数字人名字需要是 1～100 字。"})
            return

        # 没填密钥也能启动网页、使用本地模式；只在请求模型时提醒。
        if not self.server.api_key:
            self.send_json(503, {"error": '请在本机 local_config.py 中填写 API_KEY 并重启后端。也可以关闭模型开关使用本地回复。'})
            return

        # 模型故障也返回 JSON，网页才能显示错误并恢复发送按钮。
        try:
            reply = get_reply(message.strip(), name.strip(), self.server.api_key)
        except HTTPError as error:
            self.send_json(502, {"error": f"模型接口请求失败（HTTP {error.code}），请检查密钥、额度或限流。"})
            error.close()
            return
        except (URLError, TimeoutError):
            self.send_json(504, {"error": "模型连接失败或等待超时，请检查网络。没有自动重试。"})
            return
        except (ValueError, KeyError, IndexError, TypeError):
            self.send_json(502, {"error": "模型没有返回可用的回答正文，请检查接口返回格式。"})
            return
        self.send_json(200, {"reply": reply})

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
            server.api_key = API_KEY.strip()
            print(f"请在浏览器打开：http://{HOST}:{PORT}", flush=True)
            print("默认本地回复；打开网页的模型开关后才调用 Kimi K3。", flush=True)
            if not server.api_key:
                print("尚未填写 API_KEY，可先使用本地模式。", flush=True)
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
