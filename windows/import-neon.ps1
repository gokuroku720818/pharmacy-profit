$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path '.venv\Scripts\python.exe')) { throw 'Run windows\prepare-local.ps1 first.' }
if (Test-Path 'data\sales.db') { throw 'data\sales.db already exists. Import is intentionally blocked.' }
$secure = Read-Host 'Paste Neon direct connection URL (hidden input)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $env:SOURCE_DATABASE_URL = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    & .\.venv\Scripts\python.exe local_migrate.py
    if ($LASTEXITCODE -ne 0) { throw 'Import failed. Source is unchanged and the local target was not replaced.' }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    Remove-Item Env:SOURCE_DATABASE_URL -ErrorAction SilentlyContinue
}
