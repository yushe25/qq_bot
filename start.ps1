[CmdletBinding()]
param(
    [switch]$Mock,
    [string]$Config = "config.json",
    [string]$VenvDir = ".venv"
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot (Join-Path $VenvDir "Scripts\python.exe")

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到 $python，请先在项目根目录创建 .venv 并安装依赖。"
}

function Start-QQF {
    param(
        [bool]$useMock,
        [string]$configPath
    )

    if ($useMock) {
        $mockArgs = @("scripts/mock_qq_server.py")
        $mockProcess = Start-Process `
            -FilePath $python `
            -ArgumentList $mockArgs `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -PassThru
        Write-Host "mock OneBot 已启动 (PID $($mockProcess.Id))，HTTP 3000 / WS 3001"
        try {
            & $python -m app.main --mock --config $configPath
        }
        finally {
            if ($mockProcess -and -not $mockProcess.HasExited) {
                Stop-Process -Id $mockProcess.Id
            }
        }
    }
    else {
        & $python -m app.main --config $configPath
    }
}

$configFile = Join-Path $projectRoot $Config
if (-not (Test-Path -LiteralPath $configFile)) {
    $configFile = Join-Path $projectRoot "config.example.json"
    Write-Host "未找到 $Config，使用 config.example.json 作为演示配置。"
}

Start-QQF -useMock ([bool]$Mock) -configPath $configFile
