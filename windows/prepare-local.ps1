$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required. Install Python 3.12 and retry.' }
}
& .\.venv\Scripts\python.exe -m pip install -r requirements-local.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }

New-Item -ItemType Directory -Force data | Out-Null
if (-not (Test-Path 'data\local_secret.key')) {
    $bytes = New-Object byte[] 48
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $key = [Convert]::ToBase64String($bytes)
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) 'data\local_secret.key'), $key)
}
Write-Host 'Local environment ready. The private session key is stored in data\local_secret.key.'
Write-Host 'Next: back up Neon, import its database, then run windows\start-local.ps1.'
