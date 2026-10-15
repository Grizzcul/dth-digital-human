# 离线人物动作与抠图工具

本目录用于准备素材，运行网页 Demo 不需要启动这里的模型。产品运行与数据流见 [项目首页](../../README.md) 和 [架构](../../docs/ARCHITECTURE.md)。

## 1. 两个阶段

- **制作动作**：LivePortrait 将源人像和动作参考组合为视频。适合从一张写实人像制作短的眼睛动作。
- **去掉背景**：`prepare-avatar.py` 使用 RVM 分离人物/背景，输出透明 WebM 与配套 PNG。

已经有满意的 MP4 时，只执行抠图阶段。只有图片且需要新动作时，先生成动作视频。上述流程没有接入聊天时的实时嘴型驱动。

## 2. 目录与依赖

| 路径 | 用途 | Git |
| --- | --- | --- |
| `.venv/` | 工具专用 Python 与依赖 | 忽略，每台电脑单独创建 |
| `source/` | LivePortrait 上游源码及其人像权重 | 忽略 |
| `bin/ffmpeg.exe` / `bin/ffprobe.exe` | 编解码、读取视频元数据 | 忽略 |
| `matting/rvm_mobilenetv3_fp32.torchscript` | RVM 抠图模型 | 忽略模型数据 |
| [requirements-local.txt](requirements-local.txt) | 工具依赖版本及差异说明 | 跟踪 |
| [start.ps1](start.ps1) | 启动上游 Gradio 界面，端口8890 | 跟踪 |
| [prepare-avatar.py](prepare-avatar.py) | 带注释的离线抠图与导出脚本 | 跟踪 |
| `cache/` / `logs/` | 下载、临时、编译缓存与诊断记录 | 忽略 |

根目录轻量 `.venv` 不安装这些依赖。工具的 requirements 清单不包含 PyTorch 的单独安装步骤所需的全部外部文件，也不包含权重和 FFmpeg；只克隆本仓库不能还原完整制作环境。

当前上游源码提交已核对为 `9b294b3d0536135442ea73cb01e6cb3ca7029dd3`。本地依赖清单面向 Python 3.12 / Windows；安装记录使用 PyTorch 2.8.0 + CUDA 12.8 与 torchvision 0.23.0。具体固定版本以清单为准，不能把版本记录当作本轮重新验证 GPU 的结果。

来源：[LivePortrait](https://github.com/KlingAIResearch/LivePortrait)、[RVM](https://github.com/PeterL1n/RobustVideoMatting)。上游文档、代码与许可证保持原样。

## 3. 启动动作制作界面

在项目根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\liveportrait\start.ps1
```

模型加载完成后自行打开 `http://127.0.0.1:8890`。界面与数字人网页的8000端口不同。Ctrl+C 停止模型进程；仅关闭浏览器标签页不会停止工具。

脚本仅在当前进程设置 FFmpeg / 临时目录 / 编译缓存等环境变量，退出后恢复；不修改系统 Python。Windows PowerShell 脚本保留 UTF-8 BOM，Python 启用 UTF-8。

制作眨眼的基本步骤：

1. Image 上传源人像，Driving Video 上传短的眨眼参考视频。
2. 优先保持头、嘴和肩膀稳定，使用相同画幅的素材。
3. 开启相对动作、paste-back 和 stitching；Animation Region 选择 eyes。
4. 参考视频未裁脸时按界面情况启用自动裁剪。
5. Animate 后检查原图空间输出，保存满意的视频。

eyes 限制动作传递区域，不保证其余像素完全不变。检查眼部形变、头发/衣服抖动与首尾衔接；普通 MP4 不保留透明背景。

## 4. 对现成视频抠图

输入应为可读取帧数和帧率的短固定帧率视频。示例中的输入路径需要改为自己的文件；输出使用尚不存在的新版本文件名：

```powershell
$inputVideo = 'D:\你的素材\blink.mp4'
$outputVideo = '.\png\generated\avatar-v2.webm'
$outputPoster = '.\png\generated\avatar-v2.png'
& .\tools\liveportrait\.venv\Scripts\python.exe -X utf8 .\tools\liveportrait\prepare-avatar.py --input $inputVideo --output $outputVideo --poster $outputPoster
```

| 参数 | 含义 |
| --- | --- |
| `--input` | 原始动作视频，保留原件 |
| `--output` | 透明 WebM 输出，父目录可自动创建 |
| `--poster` | 同视频的透明首帧 PNG |
| `--seam-seconds` | 尾部向首帧融合的时长，默认0.3秒；0关闭 |

脚本拒绝覆盖已有输出，自动选择可用 CUDA，否则使用 CPU。它逐帧保留 RVM 时间状态，预热首帧，将 alpha 写入 VP9 / WebM，并去掉原视频音轨；失败会清理本次不完整输出。

尾部融合只适合末尾接近睁眼、位置稳定的片段。结尾闭眼或头部移动较大时可能出现重影，应调整输入或用 `--seam-seconds 0`。

## 5. 接入网页与验收

先预览新结果，再按 [素材入口](../../png/README.md) 将满意的 WebM / PNG 复制为 `png/avatar.webm`、`png/avatar.png`。刷新网页即可，不修改角色路径或重启后端。

检查：

- 透明背景和发丝边缘正常，衣服等前景没有被误抠除。
- PNG 与视频起始画面一致，末尾重新播放无明显跳动。
- WebM 不带声音，语音由网页独立播放。
- 当前浏览器能解码；失败时 PNG 回退正常。
- 舞台裁切、比例、移动端大小可接受。

动画制作、抠图耗时和显存占用随素材与设备变化，本仓库不保证实时生成速度。

## 6. 排错与重建边界

```powershell
.\tools\liveportrait\.venv\Scripts\python.exe -m pip check
```

该命令仅核对依赖声明，不能证明 GPU 推理、界面或抠图效果正常。进一步检查源视频元数据、工具日志与实际短片输出。

新电脑重建时，需要单独准备 Python 环境、对应上游源码、人像权重、CUDA 适配的 PyTorch、requirements 清单、FFmpeg / FFprobe 和 RVM 模型。本仓库尚无一键重装脚本；不要复制旧 .venv 或直接整体升级所有库。先按上游说明与当前依赖清单核对，再做短片验收。
