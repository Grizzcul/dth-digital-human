"""第一步：单独测试 Kimi K3 接口。使用 Python 自带的库，无需安装依赖。"""

import json
from getpass import getpass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    # 对应 curl 的接口地址和 model 参数。
    url = "https://tokenhub.tencentmaas.com/v1/chat/completions"
    model = "kimi-k3"

    # 运行时输入密钥，输入不会显示，也不会保存到文件里。
    api_key = getpass("请输入 API Key（输入不显示，粘贴后按 Enter）：").strip()
    if not api_key:
        print("没有输入密钥，未发送请求。")
        return

    # 对应 curl 的 -d：Python 字典里的 messages 是发给模型的消息。
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "你好"},  # 以后可以改这句话。
        ],
        "stream": False,  # 等待完整回答，而不是逐段接收。
    }

    # JSON 文字需要转换成 UTF-8 字节，才能通过网络发送。
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",  # 对应 curl 的 -X POST。
        headers={
            "Authorization": f"Bearer {api_key}",  # 对应 curl 的 -H。
            "Content-Type": "application/json",
        },
    )

    print("正在发送一次请求，请等待……")
    try:
        # 最多等待 90 秒。这里只发送一次请求，不自动重试。
        with urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))

        # 标准 Chat Completions 返回格式：取第一个回答的正文。
        reply = result["choices"][0]["message"]["content"]
        if not isinstance(reply, str) or not reply.strip():
            print("接口已返回，但没有回答正文，需要检查返回格式。")
        else:
            print("\nKimi 的回答：")
            print(reply)
        # 展示接口提供的用量；token 数量不等于实际扣费金额。
        print("\nToken 用量：", result.get("usage", "接口未提供"))

    except HTTPError as error:
        # 常见错误：401 密钥问题，429 限流；具体原因看平台返回的说明。
        detail = error.read().decode("utf-8", errors="replace")
        print(f"接口请求失败，HTTP 状态码：{error.code}")
        print(detail.replace(api_key, "[密钥已隐藏]")[:1000])
    except (URLError, TimeoutError):
        print("网络连接失败或等待超时，请检查网络。程序没有自动重试。")
    except (ValueError, KeyError, IndexError, TypeError):
        print("返回格式与示例预期不一致，需要查看平台的接口返回说明。")


# 直接运行这个文件时才执行 main；以后导入文件时不会自动调用模型。
if __name__ == "__main__":
    main()
