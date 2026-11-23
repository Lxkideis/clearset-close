$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { & (Join-Path $PSScriptRoot 'SETUP.ps1') }
& $projectPython -m scripts.local_db start
if ($LASTEXITCODE -ne 0) { throw 'Local database did not start.' }
& $projectPython -m scripts.seed_demo
if ($LASTEXITCODE -ne 0) { throw 'Demo evidence validation failed.' }
Write-Output 'Clearset Close: http://127.0.0.1:8766 (Ctrl+C stops the web server)'
& $projectPython -m uvicorn src.api:app --host 127.0.0.1 --port 8766
