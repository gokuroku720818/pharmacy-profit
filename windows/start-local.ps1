$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path '.venv\Scripts\python.exe') -or -not (Test-Path 'data\local_secret.key')) {
    throw 'Run windows\prepare-local.ps1 first.'
}
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
$env:SECRET_KEY = [System.IO.File]::ReadAllText((Join-Path (Get-Location) 'data\local_secret.key')).Trim()
if (-not (Test-Path 'data\sales.db')) {
    $secure = Read-Host 'Choose a NEW local admin password (16+ characters; hidden input)' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $env:ADMIN_BOOTSTRAP_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        if ($env:ADMIN_BOOTSTRAP_PASSWORD.Length -lt 16) { throw 'Password must be at least 16 characters.' }
        & .\.venv\Scripts\python.exe manage_db.py init
        if ($LASTEXITCODE -ne 0) { throw 'Database initialization failed.' }
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        Remove-Item Env:ADMIN_BOOTSTRAP_PASSWORD -ErrorAction SilentlyContinue
    }
}
Write-Host 'Open http://127.0.0.1:8765/login in your browser. Press Ctrl+C here to stop.'
& .\.venv\Scripts\waitress-serve.exe --listen=127.0.0.1:8765 app:app
