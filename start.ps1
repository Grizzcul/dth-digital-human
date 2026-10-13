# 快捷方式直接运行本文件：启动后端、打开网页，Ctrl+C 停止。
$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = '我的数字人 - 后端（Ctrl+C 停止）'

# $PSScriptRoot 是本文件所在目录，双击启动时不会依赖当前工作目录。
# 新电脑使用项目自己的轻量环境；这台电脑原有的环境也继续兼容。
$taskPythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPythonPath)) {
    $taskPythonPath = Join-Path $PSScriptRoot 'tools\liveportrait\.venv\Scripts\python.exe'
}
$taskServerPath = Join-Path $PSScriptRoot 'server.py'

try {
    if (-not (Test-Path -LiteralPath $taskPythonPath)) {
        throw '找不到 Python 环境。请先创建 .venv，再运行 setup.ps1。步骤见 README.md。'
    }
    if (-not (Test-Path -LiteralPath $taskServerPath)) {
        throw '找不到 server.py，请把启动文件放在项目目录中。'
    }

    # 同步运行：Python 服务运行多久，这个窗口就保留多久。
    # --open-browser 会让后端启动后自动打开网页。
    & $taskPythonPath -X utf8 $taskServerPath --open-browser
    if ($LASTEXITCODE -ne 0) {
        throw "Python 启动失败，退出码：$LASTEXITCODE"
    }
} catch {
    # 失败时保留真正的报错，避免闪退后无法判断原因。
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host '启动失败。请查看上面的错误，按 Enter 关闭'
    exit 1
}
