[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

$pythonPath = Join-Path $projectRoot ".venv/Scripts/python.exe"
$python = if (Test-Path -LiteralPath $pythonPath) { Get-Command $pythonPath } else { Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) {
    Write-Error "Python was not found on PATH. Install Python 3.12 and rerun scripts/build_windows.ps1."
    exit 1
}

Push-Location $projectRoot
try {
    & $python.Source (Join-Path $PSScriptRoot "build_portable.py")
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
