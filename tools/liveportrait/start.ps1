# 启动本地视频制作界面。它与 index.html 的数字人展示页是两个独立程序。
$ErrorActionPreference = 'Stop'

# PSScriptRoot 是本脚本所在目录，不受终端当前所在位置影响。
$portraitToolRoot = $PSScriptRoot
$portraitPython = Join-Path $portraitToolRoot '.venv\Scripts\python.exe'
$portraitSource = Join-Path $portraitToolRoot 'source'
$portraitBin = Join-Path $portraitToolRoot 'bin'
$portraitTemp = Join-Path $portraitToolRoot 'cache\temp'

if (-not (Test-Path -LiteralPath $portraitPython)) {
    throw 'The local Python environment is missing.'
}
if (-not (Test-Path -LiteralPath (Join-Path $portraitBin 'ffmpeg.exe'))) {
    throw 'The local FFmpeg installation is missing.'
}

# 这些环境变量只影响此次启动的程序，不修改 Windows 的系统设置。
$portraitOldPath = $env:PATH
$portraitOldTemp = $env:TEMP
$portraitOldTmp = $env:TMP
$portraitOldGradioTemp = $env:GRADIO_TEMP_DIR
$portraitOldAnalytics = $env:GRADIO_ANALYTICS_ENABLED
$portraitOldImageio = $env:IMAGEIO_FFMPEG_EXE
$portraitOldCudaCache = $env:CUDA_CACHE_PATH
$portraitOldAlbuUpdate = $env:NO_ALBUMENTATIONS_UPDATE

New-Item -ItemType Directory -Path $portraitTemp -Force | Out-Null
$env:PATH = $portraitBin + ';' + $env:PATH
$env:TEMP = $portraitTemp
$env:TMP = $portraitTemp
$env:GRADIO_TEMP_DIR = Join-Path $portraitToolRoot 'cache\gradio'
$env:GRADIO_ANALYTICS_ENABLED = 'False'
$env:IMAGEIO_FFMPEG_EXE = Join-Path $portraitBin 'ffmpeg.exe'
# 显卡首次编译的缓存也放在这里，方便后续加载与管理。
$env:CUDA_CACHE_PATH = Join-Path $portraitToolRoot 'cache\cuda'
New-Item -ItemType Directory -Path $env:CUDA_CACHE_PATH -Force | Out-Null
$env:NO_ALBUMENTATIONS_UPDATE = '1'

Push-Location $portraitSource
try {
    Write-Host 'Open http://127.0.0.1:8890 after the models finish loading.'
    Write-Host 'Press Ctrl+C in this terminal to stop the model process.'
    # 只监听本机；share 默认关闭；Windows 不启用 torch.compile。
    # -X utf8 使用 UTF-8，避免中文 Windows 终端因进度图标而报编码错误。
    & $portraitPython -X utf8 app.py --server-name 127.0.0.1 --server-port 8890
    if ($LASTEXITCODE -ne 0) {
        throw "LivePortrait exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
    $env:PATH = $portraitOldPath
    $env:TEMP = $portraitOldTemp
    $env:TMP = $portraitOldTmp
    $env:GRADIO_TEMP_DIR = $portraitOldGradioTemp
    $env:GRADIO_ANALYTICS_ENABLED = $portraitOldAnalytics
    $env:IMAGEIO_FFMPEG_EXE = $portraitOldImageio
    $env:CUDA_CACHE_PATH = $portraitOldCudaCache
    $env:NO_ALBUMENTATIONS_UPDATE = $portraitOldAlbuUpdate
}
