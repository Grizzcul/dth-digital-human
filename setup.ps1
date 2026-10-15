# 每台 Windows 电脑只运行一次：创建本机配置和快捷方式，不调用模型。
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw '请先安装 Python，并在项目目录执行：py -3 -m venv --without-pip .venv'
}

# 本机已经填过密钥时保留原文件，避免初始化覆盖它。
$taskConfig = Join-Path $taskRoot 'local_config.py'
if (-not (Test-Path -LiteralPath $taskConfig)) {
    Copy-Item -LiteralPath (Join-Path $taskRoot 'local_config.example.py') -Destination $taskConfig
}

# 快捷方式包含当前电脑的绝对路径，因此每台电脑单独生成，不上传 Git。
$taskShell = New-Object -ComObject WScript.Shell
$taskShortcut = $taskShell.CreateShortcut((Join-Path $taskRoot '启动数字人.lnk'))
$taskShortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskShortcut.Arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $taskRoot 'start.ps1') + '"'
$taskShortcut.WorkingDirectory = $taskRoot
$taskShortcut.WindowStyle = 1
$taskShortcut.Description = '启动数字人网页和后端；Ctrl+C 停止服务。'
$taskShortcut.Save()

Write-Host '本机初始化完成。已有密钥配置已保留。'
Write-Host '新电脑请在 local_config.py 填入 API_KEY，然后双击启动数字人.lnk。'
