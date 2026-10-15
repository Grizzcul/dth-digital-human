"""豆包语音：逐块读取音频。只用标准库，密钥始终留在 Python。"""

import base64
import binascii
import json
from urllib.request import Request, urlopen
from uuid import uuid4


TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
TTS_RESOURCE = "seed-tts-2.0"
# 换声音时主要修改这里；音色必须属于上面指定的模型版本。
TTS_VOICE = "zh_female_meilinvyou_uranus_bigtts"
TTS_VOICE_NAME = "魅力女友 2.0"
MAX_SPEECH_LENGTH = 200
SAMPLE_RATE = 24000


class TTSError(ValueError):
    """只返回可安全展示的错误，不把供应商原始响应或密钥传给网页。"""


def audio_profile(audio_format="pcm"):
    """请求与缓存共用参数；以后改音色、格式或采样率会自动使用新的缓存编号。"""
    return {"provider": "doubao", "resource": TTS_RESOURCE, "speaker": TTS_VOICE,
            "audio_params": {"format": audio_format, "sample_rate": SAMPLE_RATE}, "cache_version": 1}


def synthesize_speech(text, api_key):
    """保留完整 MP3 接口，供诊断或下载使用；网页走 PCM 流。"""
    return b"".join(stream_speech(text, api_key, audio_format="mp3"))


def stream_speech(text, api_key, audio_format="pcm"):
    """生成器：收到一块就 yield 一块，不等待整句音频生成完。"""
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_SPEECH_LENGTH:
        raise TTSError(f"朗读文字需要在 1～{MAX_SPEECH_LENGTH} 字以内。")
    profile = audio_profile(audio_format)
    payload = {
        "req_params": {
            "text": text.strip(),
            "speaker": profile["speaker"],
            "audio_params": profile["audio_params"],
        }
    }
    request = Request(
        TTS_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "X-Api-Key": api_key,
            "X-Api-Resource-Id": TTS_RESOURCE,
            "X-Api-Request-Id": str(uuid4()),
            "Content-Type": "application/json",
        },
    )
    size = 0
    # 服务按行返回 JSON；data 是 Base64 编码的音频片段，解码后按顺序拼接。
    # 不自动重试，避免同一句话在失败后重复计费。
    with urlopen(request, timeout=30) as response:
        for _ in range(512):
            line = response.readline(512 * 1024 + 1)
            if not line:
                break
            if len(line) > 512 * 1024:
                raise TTSError("语音响应过大，已停止读取。")
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError
                code = event.get("code")
                if code not in (0, 20000000):
                    raise TTSError("语音服务拒绝请求，请检查语音 2.0 权限、音色权限及账户额度。")
                if event.get("data"):
                    audio = base64.b64decode(event["data"], validate=True)
                    size += len(audio)
                else:
                    audio = b""
            except (json.JSONDecodeError, binascii.Error, TypeError, ValueError) as error:
                if isinstance(error, TTSError):
                    raise
                raise TTSError("语音服务返回的数据格式不正确。") from None
            if size > 4 * 1024 * 1024:
                raise TTSError("生成的音频过大，已停止读取。")
            if audio:
                yield audio
            # 0 表示一个片段成功；20000000 才表示整句合成结束。
            if code == 20000000:
                if not size:
                    raise TTSError("语音服务没有返回音频。")
                if audio_format == "pcm" and size % 2:
                    raise TTSError("语音 PCM 数据不完整。")
                return
    raise TTSError("语音传输未完整结束，请稍后手动重试。")
