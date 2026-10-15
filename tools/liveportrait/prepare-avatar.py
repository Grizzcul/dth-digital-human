"""一次性抠掉 MP4 背景，制作网页用的透明眨眼视频；网页运行时不调用本脚本。"""

import argparse
import json
import os
from pathlib import Path
import subprocess

import cv2
import numpy as np
import torch


# 根据脚本的位置找到项目，不要求你先 cd 到某个目录。
TOOL_ROOT = Path(__file__).resolve().parent
FFMPEG = TOOL_ROOT / "bin" / "ffmpeg.exe"
FFPROBE = TOOL_ROOT / "bin" / "ffprobe.exe"
MODEL = TOOL_ROOT / "matting" / "rvm_mobilenetv3_fp32.torchscript"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # 明确指定输入和输出，不再默认使用某个角色或旧文件夹路径。
    parser.add_argument("--input", type=Path, required=True, help="原始动作视频路径")
    parser.add_argument("--output", type=Path, required=True, help="透明 WebM 输出路径")
    parser.add_argument("--poster", type=Path, required=True, help="透明第一帧 PNG 输出路径")
    # 只融合结尾的睁眼画面，眨眼过程直接保留视频原来的连续画面。
    parser.add_argument("--seam-seconds", type=float, default=0.3)
    args = parser.parse_args()
    if not args.input.is_file() or not MODEL.is_file():
        raise FileNotFoundError("输入视频或 RVM 抠图模型不存在。")
    if args.output.resolve() == args.input.resolve():
        raise ValueError("输出路径不能覆盖原始视频。")
    # 已有输出先保留；重做时使用 --output 和 --poster 指定新的文件名。
    if args.output.exists() or args.poster.exists():
        raise FileExistsError("输出素材已经存在，请为新版本指定其他文件名。")
    if args.seam_seconds < 0:
        raise ValueError("结尾融合时间不能小于 0。")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    cuda_cache = TOOL_ROOT / "cache/cuda"
    cuda_cache.mkdir(parents=True, exist_ok=True)
    os.environ["CUDA_CACHE_PATH"] = str(cuda_cache)
    # TorchScript 是官方已经导出的模型，复用现有 PyTorch，不再安装另一套环境。
    model = torch.jit.load(str(MODEL), map_location=device).eval()
    metadata = json.loads(subprocess.check_output([
        str(FFPROBE), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,nb_frames", "-of", "json", str(args.input)
    ]))["streams"][0]
    fps_text = metadata["avg_frame_rate"]
    numerator, denominator = map(int, fps_text.split("/"))
    fps = numerator / denominator
    total_frames = int(metadata["nb_frames"])
    video = cv2.VideoCapture(str(args.input))
    success, first_bgr = video.read()
    if not success:
        raise ValueError("无法读取视频第一帧。")
    height, width = first_bgr.shape[:2]
    # 比例只控制抠图模型内部的低分辨率计算；输出仍保持原视频尺寸。
    downsample_ratio = min(1.0, 512 / max(width, height))
    states = [None] * 4

    def matte(bgr):
        nonlocal states
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        tensor = torch.from_numpy(rgb).to(device=device, dtype=torch.float32)
        tensor = tensor.permute(2, 0, 1).unsqueeze(0) / 255
        foreground, alpha, *states = model(tensor, *states, downsample_ratio)
        # alpha=0 表示透明，alpha=1 表示人物；中间值保留柔和的发丝边缘。
        rgba = torch.cat((foreground, alpha), dim=1)[0].permute(1, 2, 0)
        return rgba.clamp(0, 1).cpu().numpy()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.poster.parent.mkdir(parents=True, exist_ok=True)
    seam_frames = min(round(args.seam_seconds * fps), max(0, total_frames - 1))
    encoder = None
    completed = False
    try:
        with torch.inference_mode():
            # 先用第一帧预热“时间记忆”，减少开头与后面抠图边缘的差异。
            for _ in range(8):
                first_rgba = matte(first_bgr)
            poster_bytes = np.rint(first_rgba * 255).astype(np.uint8).tobytes()
            subprocess.run([
                str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
                "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-i", "pipe:0",
                "-frames:v", "1", str(args.poster)
            ], input=poster_bytes, check=True)
            # 普通 H.264 MP4 不保存这种透明度；WebM / VP9 保留 alpha 通道。
            encoder = subprocess.Popen([
                str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
                "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-r", fps_text,
                "-i", "pipe:0", "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                "-b:v", "0", "-crf", "24", "-deadline", "good", "-cpu-used", "4",
                "-row-mt", "1", "-auto-alt-ref", "0", str(args.output)
            ], stdin=subprocess.PIPE)
            for index in range(total_frames):
                if index == 0:
                    rgba = first_rgba.copy()
                else:
                    success, bgr = video.read()
                    if not success:
                        raise ValueError(f"读取到第 {index} 帧时视频提前结束。")
                    rgba = matte(bgr)
                if seam_frames and index >= total_frames - seam_frames:
                    # 结尾逐渐回到第一帧，减少回放时的人物位置和边缘跳动。
                    # 使用“预乘透明度”融合，避免透明边缘出现黑色光晕。
                    blend = (index - (total_frames - seam_frames) + 1) / seam_frames
                    alpha = rgba[:, :, 3:4] * (1 - blend) + first_rgba[:, :, 3:4] * blend
                    color = (rgba[:, :, :3] * rgba[:, :, 3:4] * (1 - blend)
                             + first_rgba[:, :, :3] * first_rgba[:, :, 3:4] * blend)
                    rgba = np.concatenate((color / np.maximum(alpha, 1e-6), alpha), axis=2)
                encoder.stdin.write(np.rint(rgba.clip(0, 1) * 255).astype(np.uint8).tobytes())
                if (index + 1) % 15 == 0 or index == total_frames - 1:
                    print(f"已处理 {index + 1}/{total_frames} 帧", flush=True)
            encoder.stdin.close()
            if encoder.wait() != 0:
                raise RuntimeError("透明视频编码失败。")
            completed = True
    finally:
        video.release()
        if encoder is not None and encoder.poll() is None:
            encoder.terminate()
            encoder.wait()
        if not completed:
            # 只删除本次创建的不完整输出，不触碰输入或已有素材。
            args.output.unlink(missing_ok=True)
            args.poster.unlink(missing_ok=True)
    print(f"完成：{args.output}\n待机画面：{args.poster}", flush=True)


if __name__ == "__main__":
    main()
