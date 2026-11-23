$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.\.venv\Scripts\python.exe' -m scripts.local_db start
if ($LASTEXITCODE -ne 0) { throw 'Local test database did not start.' }
& '.\.venv\Scripts\python.exe' -m pytest -q --junitxml=artifacts/test-results.xml
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; see the output and artifacts/test-results.xml.' }
