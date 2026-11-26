$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') -X utf8 manage.py start
