# 本地数字人视频制作工具

本目录用于把人像和动作参考制作成视频。网页 index.html 只负责展示，制作视频时才运行这里的模型。

## 为什么现在选 LivePortrait

Live2D 需要先制作可驱动的角色，适合精细控制眼皮、嘴型、视线。LivePortrait 可以直接用现有写实人像，适合先做短视频素材。选择是针对当前阶段，不代表它在所有场景下更优。

## 每一部分为什么存在

| 名称 | 作用 | 你现在需要修改吗 |
| --- | --- | --- |
| `.venv` | 专用 Python 环境，安装这里使用的库，不混入系统或其他项目 | 不需要；由 Python 和 pip 管理 |
| `source` | 官方 LivePortrait 程序，里面包含推理、界面、人脸定位等代码 | 先使用官方程序，不需要逐个修改 |
| `source/pretrained_weights` | 模型学到的参数数据；缺少它们，代码无法生成人像动画 | 不需要编辑二进制数据 |
| `bin/ffmpeg.exe` | 读取、编码和导出视频 | 不需要 |
| `bin/ffprobe.exe` | 检查视频时长、帧率和音频信息 | 不需要 |
| `requirements-local.txt` | 记录我们选用的依赖版本和与官方清单的差异 | 维护环境时看它 |
| `start.ps1` | 用这里的 Python 和 FFmpeg 启动制作界面 | 想改启动端口时看它 |
| `cache` | 下载缓存、临时素材，可以重新生成 | 不需要手工编辑 |
| `logs` | 环境和运行验证记录 | 排错时查看 |
| `README.md` | 当前这份使用说明 | 可以补充你的理解 |

官方源码是现成的视频制作工具，不是新增加到网页里的产品代码。项目根目录的 `.gitignore` 忽略环境、源码和缓存，只让我们写的说明和配置进入网页项目的版本管理。

环境使用 Python 3.12.14，基础解释器来自本机 Codex 的已有运行时；`.venv` 不包含这个基础解释器的完整副本，因此不是可直接复制到其他电脑的便携包。基础 Python 未被修改。若这个基础运行时以后被移除，需要用新安装的 Python 重新创建环境。

## 启动和关闭

在 PowerShell 中执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\AI\dth\tools\liveportrait\start.ps1"
```

`-ExecutionPolicy Bypass` 仅用于此次启动进程，不修改系统的执行策略。脚本加载模型后，在浏览器打开：

```text
http://127.0.0.1:8890
```

`127.0.0.1` 表示你自己的电脑。界面默认不共享到公网。结束制作时，在启动窗口按 Ctrl+C；仅关闭浏览器标签页不会自动结束 Python 模型进程。

脚本使用带 BOM 的 UTF-8 编码，兼容 Windows 自带 PowerShell 读取中文注释；启动 Python 时使用 `-X utf8`，避免项目的进度图标触发 GBK 编码错误。`cache/cuda` 保存显卡编译缓存，首次加载会比后续慢。

## 第一次制作眨眼视频

1. 在源素材的 Image 标签上传 `D:\AI\dth\png\avatar.png`。
2. 在 Driving Video 标签上传你录制的 3～5 秒眨眼视频；头、嘴巴和肩膀尽量保持不动。
3. 保持 relative motion、paste-back 和 stitching 开启。
4. 在 Animation Options 内，将 animation region 选为 eyes。
5. 若驱动视频未按脸部裁剪，开启 driving video 的自动裁剪。
6. 点击 Animate，查看 The animated video in the original image space。
7. 下载结果。以后再把满意的视频接入网页。

eyes 限制传递的动作参数，不保证眼睛以外所有输出像素完全相同。仍需检查眼睛是否变形、脸部是否稳定、首尾是否适合衔接。普通 MP4 不保留原 PNG 的透明背景。

## 怎样理解依赖

- PyTorch / torchvision：在显卡上运行人像模型。本机选用 2.8.0 / 0.23.0 的 CUDA 12.8 构建，以支持 RTX 5060。
- ONNX Runtime：运行人脸检测和关键点模型，选用与 CUDA 12 / cuDNN 9 相匹配的版本。
- OpenCV / NumPy / SciPy：读取素材并处理像素、位置和数值。
- Gradio：制作本地操作网页，不是产品展示页的框架。
- FFmpeg：把生成的画面写成视频。
- 其余库：用于配置读取、进度显示、绘图和界面内部的数据处理。

本地清单基于官方依赖，针对 Python 3.12 和 Windows 使用 headless 版 OpenCV，未安装源码人像流程不使用的旧 lmdb，也不安装动物模式的 X-Pose 编译组件。Albucore、FastAPI、Starlette、Pydantic 等接口相关依赖已固定为与官方 Gradio 5.1 和图像处理流程匹配的版本，避免自动更新引入接口差异。

## 版本与验证

- 官方源码：https://github.com/KlingAIResearch/LivePortrait
- 当前源码提交：9b294b3d0536135442ea73cb01e6cb3ca7029dd3
- 人像权重仓库：KlingTeam/LivePortrait
- 当前权重提交：82a4fa6735ca58432b6ce39301b4b9ee066dea47
- 人像权重：8 个文件，约 628.4MiB。
- GPU：RTX 5060 Laptop GPU，8151MiB 显存；系统内存约 16GB。
- PyTorch：2.8.0+cu128；torchvision：0.23.0+cu128；ONNX Runtime：1.23.2。
- Gradio：5.1.0；FastAPI：0.115.6；Starlette：0.41.3；FFmpeg：9.0.2。
- 验证日期：2026-10-14。
- 依赖检查 `pip check` 通过；GPU 半精度卷积测试通过。
- 原始 avatar.png + 官方驱动视频的前两秒，眼睛区域推理通过。
- 测试输出：`source/animations/verification/avatar--verification-driving.mp4`，512×768，25帧/秒，共50帧，时长2秒。
- 该次测试总耗时约38.71秒，包含加载、推理和导出；不是所有素材的固定耗时。首次 CUDA / 人脸检测预热可能更慢。
- 本地页面和配置接口均返回 HTTP 200，界面中存在 eyes 区域选项。
- 测试界面已关闭；需要制作时使用上面的启动命令。
- 原有 index.html、style.css、script.js 和 png 素材没有因为安装而改动。

## 维护提示

先看 `requirements-local.txt` 的中文注释，再决定是否升级依赖。不要一开始就执行所有库的整体升级。需要复查环境时，在项目根目录执行：

```powershell
& "D:\AI\dth\tools\liveportrait\.venv\Scripts\python.exe" -m pip check
```

`pip check` 检查库之间声明的版本要求；它不能代替实际 GPU 推理或网页启动测试。详细推理和界面启动日志在 `logs` 中。

## 把现成的 MP4 抠掉背景并接入网页

这一步在视频生成之后进行。LivePortrait 制作动作，RVM（Robust Video Matting）负责分离人物和背景；网页只播放处理好的素材。

新增文件的作用：

| 文件 | 作用 |
| --- | --- |
| `prepare-avatar.py` | 带中文注释的离线处理脚本：读取视频、逐帧抠背景、融合尾部、导出透明素材 |
| `matting/rvm_mobilenetv3_fp32.torchscript` | 官方已经导出的抠图模型，复用本目录的 PyTorch |
| 项目 `png/avatar-blink.webm` | 透明眨眼视频，供 HTML 的 video 元素播放 |
| 项目 `png/avatar-idle.png` | 同一视频的透明第一帧，用作加载期间的 poster 和加载失败的备用画面 |

抠图原理：模型给每个像素预测透明度 alpha。0 是背景、1 是人物，发丝边缘保留中间值。模型保留上一帧的状态，减少逐帧单独抠图产生的闪烁。普通 MP4 / H.264 不保存这种透明度，所以导出带 alpha 的 WebM / VP9。

本次处理使用原始 `D:\AI\dth\png\avatar.mp4`，视频流83帧、30帧/秒，输出时长约2.77秒。原始 MP4 的音频未带入眨眼素材。结尾0.3秒在睁眼状态下融合回第一帧，降低重新播放时的跳动；眨眼本身保留原视频的连续画面。

第一次运行的默认命令（默认输出存在时会拒绝覆盖）：

```powershell
& "D:\AI\dth\tools\liveportrait\.venv\Scripts\python.exe" -X utf8 "D:\AI\dth\tools\liveportrait\prepare-avatar.py"
```

以后换视频，可以使用新文件名生成第二版，先比较效果：

```powershell
& "D:\AI\dth\tools\liveportrait\.venv\Scripts\python.exe" -X utf8 "D:\AI\dth\tools\liveportrait\prepare-avatar.py" --input "D:\AI\dth\png\avatar.mp4" --output "D:\AI\dth\png\avatar-blink-v2.webm" --poster "D:\AI\dth\png\avatar-idle-v2.png"
```

脚本面向短的固定帧率 MP4，处理时使用已有 GPU；运行结束后模型进程退出。默认尾部融合适合本次结尾已经睁眼的素材；换成结尾闭眼或动作较大的视频时，先调整素材或用 `--seam-seconds 0` 关闭融合，以免产生重影。

确认新素材满意后，在项目 `index.html` 中更换 video 的 src、poster，以及备用 img 的 src。显示高度看 `style.css` 的 `.avatar`；播放后的随机等待看 `script.js` 的 `VIDEO_WAIT_MIN` 和 `VIDEO_WAIT_MAX`。页面不会运行 Python 或抠图模型。

已检查输出83帧均保留透明度；背景角落透明、衣服区域保留，首尾在展示背景上的平均差异约0.009/255。JavaScript 的播放、等待、后台暂停、减少动画设置、备用静态图及聊天抽屉逻辑检查通过。尚未自动验证 Edge 中该本地文件页面的实际渲染，需刷新页面查看观感。发丝边缘是模型估计，可能略柔和。

官方模型及原理：https://github.com/PeterL1n/RobustVideoMatting
