# 我的数字人助手

这是一个用于逐步学习应用开发的本地 Demo：浏览器展示透明眨眼视频，右侧抽屉查看对话；模型开关默认关闭，关闭时 JS 即时按规则回复，开启时通过 Python 调用 Dify Cloud 中已发布的 Chatflow。模型和提示词在 Dify 画布中配置。

## 当前功能：流式回复与实时字幕

人物下方的“当前回答”直接显示最近一条回答。开启模型回答后，文字随 Dify 生成逐段出现，并同步更新右侧记录中的同一个气泡，不需要等整段生成完毕。等待首段文字时显示“正在思考…”，输出期间显示“正在回答”。本地规则回答也会显示在这个区域。

网页通过 `Accept: text/event-stream` 请求 `/api/chat`，Python 逐事件读取 Dify SSE 并立即转发。未声明这个 Accept 的请求保留原有 JSON 响应，兼容诊断工具。前端处理中文跨网络分包、替换回答、生成错误和连接意外结束；新会话编号一收到就保存。中断时保留用户消息和已经出现的回答，并标记本轮未完成，不自动重试。

这个功能改善回答生成期间的显示体验，不会缩短模型开始输出之前的思考与排队时间。网页整轮超过100秒会停止等待；超时和断网不代表云端已经停止。

## 当前功能：停止回答

模型请求期间，“发送”位置变成“停止回答”。点击后冻结当前字幕并显示“正在停止…”；网页通过本地 `/api/chat/stop` 接口请求 Dify 停止生成，收到云端确认后保留已显示文字、标记“已停止”，恢复输入。还没输出文字也能点击：网页会记住停止意图，等 Dify 返回任务编号后立即发起停止请求。

停止后可直接发送新消息，继续已收到编号的同一会话，不自动重发被停止的问题。已发送的问题可能仍保存在 Dify 会话上下文中，停止不会撤回或删除这条问题。停止失败或超时会显示“停止未确认”并提供“重试停止”，不会把断开本地连接当作云端停止成功。如果回答恰好自然完成，优先保留完整回答；迟到的停止响应不会影响下一轮。

后端只接受当前服务持有的活动任务编号，使用发起消息时相同的 Dify user 调用停止接口，防止用任意编号停止其他任务。同一活动任务重复点击在成功确认后不会再次调用云端；任务流结束时清理登记。该功能无需修改 Dify 画布或增加依赖。

边界：在 Dify 尚未返回任务编号、网络已断开或停止接口无法确认时，不能保证立即停止云端处理；页面会如实提示。Dify 的成功响应表示接收了停止请求，不代表此前已经产生的 token 不计费，也不是对供应商内部计算何时结束的测量。

本次验收：14 项 Python 测试及前端 Web Streams 测试通过，覆盖停止失败重试、重复点击、停止与自然完成竞争、保留部分文字和停止后续聊。真实浏览器验证了首字前停止和随后同会话短问题成功回复；期间一条长回答请求遇到云端连接中断或超时，页面正确标记失败，该条不计作成功验收。

修改后请关闭旧后端窗口，再双击启动快捷方式，并刷新网页。开启“模型回答”后发送一句话即可验证；本功能不要求修改 Dify 画布。

不调用真实模型的回归测试：

```powershell
& .venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -v
node tests/test_frontend_streaming.cjs
```

Python 测试使用临时本地端口和模拟 Dify 数据；Node.js 测试使用模拟页面与真实 Web Streams/UTF-8 解码器。运行第二条命令需要 Node.js，日常使用网页仍只需要 Python 标准库。

私有代码仓库：[Grizzcul/dth-digital-human](https://github.com/Grizzcul/dth-digital-human)。访问和克隆时需要登录有权限的 GitHub 账号。

模型模式已支持同一会话的多轮上下文：第一次请求创建会话，JS 保存 Dify 返回的 conversation_id，后续请求带上相同编号。Dify 的 LLM 节点需要开启 Memory。会话编号现在同时保存到浏览器 localStorage，刷新或重新打开后可继续该角色的上次会话。当前步骤只恢复编号，旧消息暂时不会自动显示到右侧抽屉；从 Dify 读取历史消息是下一步。

在右侧记录抽屉点击“开始新对话”，会清空当前角色的本地编号和网页消息，下一条模型消息创建新会话；不会删除 Dify 云端旧对话。不同角色分别保存编号，不同浏览器、端口和电脑的浏览器存储不会自动同步。浏览器只保存会话编号，不保存 API 密钥。此功能延续已有会话，暂时没有跨会话长期记忆。

待机眨眼和本地回复不调用模型。本地模式期间的消息不进入 Dify，开关切回模型模式时仍继续此前的模型会话。当前只供个人本机使用，后端使用固定的 Dify 用户标识；开放给多人前需要加入用户身份与权限隔离。

## 文件为什么存在

| 文件 | 作用 |
| --- | --- |
| `index.html` | 页面结构、人物视频、输入框、模型开关、记录抽屉 |
| `style.css` | 页面外观与开关样式 |
| `script.js` | 角色配置、视频播放、抽屉开关、本地规则、向 Python 发送消息 |
| `server.py` | 提供网页、接收消息并调用 Dify；仅公开允许访问的网页素材 |
| `png/Grace/`、`png/Liz/` | 每个角色的原始图片、动作视频与处理好的透明素材 |
| `local_config.example.py` | 本机密钥配置的空白模板，可以提交到 Git |
| `local_config.py` | 真实密钥，只留在本机，Git 忽略；每台电脑填写一次 |
| `start.ps1` | 启动后端并打开网页，由本机快捷方式调用 |
| `setup.ps1` | 每台 Windows 电脑生成本机配置和快捷方式 |
| `tools/liveportrait/` | 之前准备的离线动画、抠图工具；运行网页 Demo 不需要这些模型 |

`.venv`、本机配置、快捷方式、模型和下载缓存不会上传。虚拟环境依赖本机 Python 安装位置，换电脑时重新创建，不复制旧环境。

## 更换角色和制作素材

角色以 `script.js` 顶部的 ACTIVE_CHARACTER 为准，例如选择 Grace 后保存、刷新网页：

```javascript
const ACTIVE_CHARACTER = "Grace";
```

角色的 `name`、`image`、`video` 集中在下方的 `CHARACTERS` 中。`image` 使用视频的透明第一帧，避免加载或回退时切到另一张照片；只有图片时将 `video` 设为 `null`。

新增角色时，将素材放入 `png/角色名/`，再在 `CHARACTERS` 内添加同样结构的配置。Python 启动时自动登记 `png/` 内的 PNG、JPG、JPEG、WebP 图片和 WebM、MP4 视频，因此添加新文件后需要重启后端。已有角色之间切换，只需刷新网页。

LivePortrait 制作动作视频，RVM 将动作视频抠成透明 WebM 和配套 PNG。操作见 [素材工具说明](tools/liveportrait/README.md)。原始素材保留，便于以后重新生成；网页日常只读取当前角色配置中的图片和视频。

早期独立接口测试脚本已经移除。需要测试模型时，在网页开启模型开关并发送一句话即可；这会产生一次模型请求。

## 另一台 Windows 电脑第一次使用

1. 安装 [Git for Windows](https://git-scm.com/install/windows) 和 [Python 3](https://www.python.org/downloads/windows/)。
2. 在 GitHub 登录同一账号，从项目私有仓库复制 HTTPS 克隆地址。
3. 打开 PowerShell，选择你希望保存项目的目录，克隆仓库并进入项目。例如：

   ```powershell
   git clone https://github.com/Grizzcul/dth-digital-human.git dth
   cd dth
   git config --local user.name "Grizz"
   git config --local user.email "185069331+Grizzcul@users.noreply.github.com"
   ```

   后两行只设置这个仓库的提交身份，使用 GitHub 的 noreply 地址；不用于账号登录。克隆私有仓库时若出现 GitHub 登录提示，请用 `Grizzcul` 账号登录授权。

4. 在项目目录创建只属于这台电脑的 Python 环境。Demo 仅使用标准库，不需要 pip 安装 PyTorch、LivePortrait 或其他依赖：

   ```powershell
   py -3 -m venv --without-pip .venv
   ```

   如果没有 `py` 命令，但已安装 Python，可改用 `python -m venv --without-pip .venv`。当前没有第三方依赖，`--without-pip` 省去安装包管理器。

5. 运行一次初始化。Bypass 仅作用于这次 PowerShell 进程，不改系统执行策略：

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
   ```

6. 打开生成的 `local_config.py`，在 `DIFY_API_KEY = ""` 的英文引号中填写 Dify 应用 API 密钥。它来自 Dify 应用的“访问点 → 后端服务 API → API 密钥”，不是腾讯模型密钥。保存后双击 `启动数字人.lnk`。

不填密钥也能使用本地模式。已填过的配置不会被初始化覆盖。密钥不要提交到 GitHub，也不要发到聊天里。

旧电脑配置中原来的 API_KEY 可以保留，但当前后端不再读取它。Dify Cloud 接口地址在 server.py 的 DIFY_URL 中；未来自部署 Dify 时再修改此处。工作流和腾讯模型凭据由 Dify 云端管理，不会自动随 Git 同步。

## 每天在两台电脑之间交接

同一时间尽量只在一台电脑上修改。新一天开始，先确认本机没有未提交的修改，再拉取：

```powershell
git status --short
git pull --ff-only
```

开发结束后，保存所有文件，检查待提交内容，再提交、推送：

```powershell
git status --short
git add .
git diff --cached --stat
git commit -m "描述今天完成的修改"
git push
```

第一次远端关联和推送由当前电脑完成；之后两台电脑连接同一个仓库、使用 `main` 分支。提交只保存在本机，推送才上传到 GitHub。电脑 A 结束后推送，电脑 B 开始前拉取。

只按上述流程推送 `main`。不要用 `git push --mirror` 上传本机内部快照；本机 Codex 辅助记录和真实密钥不属于共享项目内容。

如果 `pull --ff-only` 报错，或发现两台电脑分别修改过，请先保留两边的修改再处理合并；不要用 `reset --hard` 或强制推送覆盖。

## 确认交接成功

两台电脑都执行：

```powershell
git status --short
git rev-parse HEAD
```

状态应没有输出，最后提交编号应一致。这说明已跟踪代码和素材的版本一致；密钥、环境和快捷方式仍是各自电脑的本机配置。

修改 HTML、CSS、JS 后刷新网页。修改 Python 或本机密钥后，先停止旧后端，再双击快捷方式重启。关闭后端窗口或 Ctrl+C 停止本机服务。
