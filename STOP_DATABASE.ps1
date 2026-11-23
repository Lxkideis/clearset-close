$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.\.venv\Scripts\python.exe' -m scripts.local_db stop
if ($LASTEXITCODE -ne 0) { throw 'Could not stop the portfolio database.' }
