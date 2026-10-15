# 数字人助手

一个用于学习应用开发的个人本机 Demo：浏览器展示人物图片或预制眨眼视频，Python 提供接口，Dify 生成回答，豆包合成语音，SQLite 复用音频。

运行网页不需要 GPU 推理或第三方 Python 包。LivePortrait / RVM 属于可选的离线素材工具，使用独立环境。

## 文档导航

| 你要做什么 | 阅读入口 |
| --- | --- |
| 启动、配置密钥、体验功能 | 本文 |
| 让 Codex 接手、了解代码规范 | [AGENTS.md](AGENTS.md) |
| 理解模块、数据流、状态与缓存 | [架构与实现](docs/ARCHITECTURE.md) |
| 查看请求、响应、流式事件和错误码 | [接口约定](docs/API.md) |
| 修改代码、运行检查、排错、换电脑 | [开发维护](docs/DEVELOPMENT.md) |
| 区分已实现、待验证与未实现 | [功能状态与验收](docs/STATUS.md) |
| 更换人物图片和视频 | [素材入口](png/README.md) |
| 离线生成动作、抠背景 | [素材制作工具](tools/liveportrait/README.md) |

当前功能包括：单角色展示、右侧记录抽屉、本地规则回复、Dify 流式多轮对话与停止、固定女声流式播报、测试问答和持久音频缓存。人物眨眼来自预制视频，尚无随语音变化的口型。详细边界以功能状态文档为准。

## 已配置电脑：启动与关闭

在项目根目录双击 `启动数字人.lnk`；或者在 PowerShell 执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 .\server.py --open-browser
```

默认地址为 `http://127.0.0.1:8000/`。保留后端终端，Ctrl+C 停止服务。不要通过双击 HTML 使用接口功能。

- 修改 HTML / CSS / JS：刷新网页。
- 修改 Python 或 `local_config.py`：停止旧后端，重新启动，再刷新。
- 替换固定人物素材：刷新网页即可。
- 修改测试台词：点击“检查测试音频”重新读取，不需要重启。

重复启动时会打开同一项目已运行的页面；日志仍在原终端，新的启动命令会退出。仅关闭网页不会停止 Python 服务。

## 新电脑：首次准备

建议使用 Python 3.12；本项目核对环境为 Python 3.12.14。浏览器使用支持 Web Audio 和 Web Streams 的现代 Edge / Chrome。Node.js 只用于前端回归测试，运行网页不需要它。

1. 安装 Python 和 Git，把仓库克隆到本机目录，打开该目录的 PowerShell。
2. 创建轻量环境：

   ```powershell
   py -3 -m venv --without-pip .venv
   ```

   没有 `py` 时可用 `python -m venv --without-pip .venv`。网页后端没有额外 pip 依赖。

3. 生成本机配置和启动快捷方式：

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
   ```

   此处 Bypass 只作用于本次进程；已有 `local_config.py` 不会被覆盖。

4. 按 [配置模板](local_config.example.py) 填写本机 `local_config.py`：
   - `DIFY_API_KEY`：已发布的 Dify Chatflow 应用密钥，非模型供应商密钥。
   - `DOUBAO_TTS_API_KEY`：豆包语音服务密钥；所在项目需开通语音合成 2.0。
5. 双击生成的快捷方式启动。没有密钥也能使用本地规则和已准备的缓存。

Dify 云端的人设、模型供应商、Memory 与发布版本不在本仓库内。已有对话流需发布；LLM 节点需要使用会话记忆才能延续上下文。后端当前 `inputs={}`，不传自定义工作流变量。

## 三种使用方式

模型、语音和开发测试开关每次加载默认关闭。

| 使用方式 | 操作 | 外部服务用量 |
| --- | --- | --- |
| 本地规则 | 关闭开发测试和模型回答 | 文字不调用模型；开启语音后，音频未命中会调用豆包 |
| 模型聊天 | 关闭开发测试，开启模型回答 | 调用 Dify；语音开启时优先复用缓存 |
| 开发测试 | 开启开发测试，可再开启语音 | 固定问答与已缓存音频；未命中直接提示，不自动调用云端 |

普通模式的“试听”也优先查缓存，未命中时会调用语音服务；测试模式的“试听”仅播放第一条预设回答的缓存。

## 准备测试音频

1. 展开“测试句与音频缓存”，点击“检查测试音频”。检查不调用云端。
2. 如有缺失，核对页面显示的段数、字数，再点击“生成缺失音频”。这一步产生语音用量，不调用模型。
3. 开启开发测试和语音播报，输入 `你好`、`你好呀`、`你是谁` 或 `测试语音`。

在 [test_cases.json](test_cases.json) 修改 `inputs` 和 `reply` 后重新检查，只生成缺少的片段。JSON 不支持注释；字段约束见架构文档。

“本轮响应耗时”显示首字、首段、首包、开始播放及缓存命中情况。播放计时依据浏览器音频时钟，设备冷启动、声卡和蓝牙还可能引入延迟。

## 数据与同步

- 代码、文档、测试台词和确认后的角色素材可以进入 Git。
- 密钥、本机快捷方式、虚拟环境、模型、工具缓存和 `data/*.sqlite3*` 已忽略。
- 音频库 `data/cache.sqlite3` 不随 Git 自动同步。换电脑时按开发维护文档单独迁移。
- 浏览器仅保存 Dify 会话编号；旧消息不会在刷新后自动恢复，浏览器存储也不随 Git 同步。

当前只适合个人本机使用：监听 `127.0.0.1`，固定 Dify 用户，没有多人身份隔离。

## 最小回归检查

在项目根目录运行；Node.js 检查环境已验证为 v24.19.0：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_*.py'
node tests/test_speech.cjs
node tests/test_frontend_streaming.cjs
```

这些检查模拟供应商，不产生模型或语音费用。人工验收与真实云端检查见开发维护文档。
