$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Please install uv from https://docs.astral.sh/uv/getting-started/installation/ first.' }
uv venv --python 3.12 .venv
if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed' }
uv pip install --python .venv/Scripts/python.exe -r requirements.lock -e './upstream/OmniRank/scripts/py'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
New-Item -ItemType Directory -Force data,logs | Out-Null
if (-not (Test-Path .env)) {
    $fusionToken = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    @("FUSION_API_TOKEN=$fusionToken", 'MODEL_BASE_URL=', 'MODEL_API_KEY=', 'MODEL_NAME=', 'FUSION_REPO_ROOT=') | Set-Content -Encoding utf8 .env
}
Write-Host 'Installed. Double-click start.cmd, then open http://127.0.0.1:8090/'
