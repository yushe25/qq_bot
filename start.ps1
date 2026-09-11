# Keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less scripts
# using the system code page, which corrupts non-ASCII string literals.
[CmdletBinding()]
param(
    [switch]$Mock,
    [string]$Config = "config.json",
    [string]$VenvDir = ".venv",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$env:PYTHONUTF8 = "1"
$projectRoot = $PSScriptRoot

function Resolve-ProjectPath {
    param(
        [string]$Path,
        [string]$BasePath
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "Path values cannot be empty."
    }

    try {
        if ([System.IO.Path]::IsPathRooted($Path)) {
            return [System.IO.Path]::GetFullPath($Path)
        }
        return [System.IO.Path]::GetFullPath((Join-Path $BasePath $Path))
    }
    catch {
        throw "Invalid path '$Path': $($_.Exception.Message)"
    }
}

$venvPath = Resolve-ProjectPath -Path $VenvDir -BasePath $projectRoot
$python = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python executable not found: $python`nCreate a virtual environment and install the dependencies from pyproject.toml."
}

$pythonVersionOutput = & $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$pythonVersionExitCode = $LASTEXITCODE
if ($pythonVersionExitCode -ne 0) {
    throw "Failed to run Python: $python"
}
if ($null -eq $pythonVersionOutput) {
    throw "Python did not report a version."
}

$pythonVersion = ($pythonVersionOutput | Select-Object -Last 1).Trim()
$versionParts = $pythonVersion.Split(".")
if ($versionParts.Count -lt 2) {
    throw "Could not determine the Python version. Output: $pythonVersion"
}
if ([int]$versionParts[0] -lt 3 -or ([int]$versionParts[0] -eq 3 -and [int]$versionParts[1] -lt 12)) {
    throw "QQF requires Python 3.12 or newer. Current version: $pythonVersion"
}

Push-Location $projectRoot
try {
    & $python -c "import fastapi, httpx, pydantic, uvicorn, websockets, yaml, app.main"
    $dependencyExitCode = $LASTEXITCODE
    if ($dependencyExitCode -ne 0) {
        throw "Required dependencies or project modules are unavailable. Install the project as described in README."
    }
}
finally {
    Pop-Location
}

$configFile = Resolve-ProjectPath -Path $Config -BasePath $projectRoot
if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
    if ($Mock) {
        $exampleConfig = Join-Path $projectRoot "config.example.json"
        if (-not (Test-Path -LiteralPath $exampleConfig -PathType Leaf)) {
            throw "Config file not found: $Config. config.example.json is also missing."
        }
        $configFile = $exampleConfig
        Write-Host "Config file $Config was not found. Mock mode will use config.example.json."
    }
    else {
        throw "Config file not found: $configFile`nCopy config.example.json to config.json and fill in the real settings."
    }
}

try {
    $configRaw = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($configRaw)) {
        throw "The file is empty."
    }
    $configObject = $configRaw | ConvertFrom-Json
    if ($null -eq $configObject -or $configObject -isnot [System.Management.Automation.PSCustomObject]) {
        throw "The top-level JSON value must be an object."
    }
}
catch {
    throw "Config file is not valid JSON: $configFile`n$($_.Exception.Message)"
}

$mode = if ($Mock) { "Mock" } else { "OneBot" }
Write-Host "QQF mode: $mode"
Write-Host "Project root: $projectRoot"
Write-Host "Config file: $configFile"

if ($CheckOnly) {
    Write-Host "Checks passed. The service was not started."
    return
}

function Start-QQF {
    param(
        [bool]$UseMock,
        [string]$ConfigPath
    )

    if ($UseMock) {
        $mockArgs = @("scripts/mock_qq_server.py")
        $mockProcess = Start-Process `
            -FilePath $python `
            -ArgumentList $mockArgs `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -PassThru

        try {
            if (-not $mockProcess) {
                throw "Failed to start the mock OneBot server."
            }

            Start-Sleep -Milliseconds 500
            $mockProcess.Refresh()
            if ($mockProcess.HasExited) {
                throw "Mock OneBot exited immediately with code $($mockProcess.ExitCode)."
            }

            Write-Host "Mock OneBot started (PID $($mockProcess.Id)); HTTP 3000 / WS 3001"
            & $python -m app.main --mock --config $ConfigPath
            $script:qqfExitCode = if ($null -eq $LASTEXITCODE) { 1 } else { [int]$LASTEXITCODE }
        }
        finally {
            try {
                if ($mockProcess -and -not $mockProcess.HasExited) {
                    Stop-Process -Id $mockProcess.Id -Force -ErrorAction SilentlyContinue
                    $null = $mockProcess.WaitForExit(5000)
                }
            }
            catch {
                Write-Warning "Failed to clean up mock OneBot (PID $($mockProcess.Id)): $($_.Exception.Message)"
            }
        }
    }
    else {
        & $python -m app.main --config $ConfigPath
        $script:qqfExitCode = if ($null -eq $LASTEXITCODE) { 1 } else { [int]$LASTEXITCODE }
    }
}

$script:qqfExitCode = 1
Push-Location $projectRoot
try {
    Start-QQF -UseMock ([bool]$Mock) -ConfigPath $configFile
}
finally {
    Pop-Location
}

if ($script:qqfExitCode -ne 0) {
    exit $script:qqfExitCode
}
