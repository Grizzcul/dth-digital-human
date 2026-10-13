# 我的数字人助手

这是一个用于逐步学习应用开发的本地 Demo：浏览器展示透明眨眼视频，右侧抽屉查看对话；模型开关默认关闭，关闭时 JS 即时按规则回复，开启时通过 Python 调用腾讯云 TokenHub 的 Kimi K3。

私有代码仓库：[Grizzcul/dth-digital-human](https://github.com/Grizzcul/dth-digital-human)。访问和克隆时需要登录有权限的 GitHub 账号。

当前消息仅保存在页面中，刷新后清空；每次模型请求只发送当前一句话，还没有多轮上下文或长期记忆。待机眨眼和本地回复不调用模型。下一阶段可以增加本次会话上下文。

## 文件为什么存在

| 文件 | 作用 |
| --- | --- |
| `index.html` | 页面结构、人物视频、输入框、模型开关、记录抽屉 |
| `style.css` | 页面外观与开关样式 |
| `script.js` | 视频播放、抽屉开关、本地规则、向 Python 发送消息 |
| `server.py` | 提供网页、接收消息并调用 Kimi；仅公开允许访问的网页素材 |
| `png/` | 当前人物图片、原始视频、已处理的透明眨眼视频 |
| `local_config.example.py` | 本机密钥配置的空白模板，可以提交到 Git |
| `local_config.py` | 真实密钥，只留在本机，Git 忽略；每台电脑填写一次 |
| `start.ps1` | 启动后端并打开网页，由本机快捷方式调用 |
| `setup.ps1` | 每台 Windows 电脑生成本机配置和快捷方式 |
| `test_model.py` | 单独测试模型接口，运行时输入密钥，会调用一次模型 |
| `tools/liveportrait/` | 之前准备的离线动画、抠图工具；运行网页 Demo 不需要这些模型 |

`.venv`、本机配置、快捷方式、模型和下载缓存不会上传。虚拟环境依赖本机 Python 安装位置，换电脑时重新创建，不复制旧环境。

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

6. 打开生成的 `local_config.py`，在 `API_KEY = ""` 的英文引号中填写自己的 TokenHub 密钥。保存后双击 `启动数字人.lnk`。

不填密钥也能使用本地模式。已填过的配置不会被初始化覆盖。密钥不要提交到 GitHub，也不要发到聊天里。

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
