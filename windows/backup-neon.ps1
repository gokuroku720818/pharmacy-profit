$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Get-Command pg_dump -ErrorAction SilentlyContinue)) {
    throw 'Install PostgreSQL 18 client tools and add pg_dump to PATH first.'
}
$secure = Read-Host 'Paste Neon direct connection URL (hidden input)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $url = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    $uri = [Uri]$url
    if ($uri.Scheme -notin @('postgres', 'postgresql')) { throw 'Expected a PostgreSQL URL.' }
    $identity = $uri.UserInfo.Split(':', 2)
    if ($identity.Count -ne 2) { throw 'The URL must include username and password.' }
    $env:PGHOST = $uri.Host
    $env:PGPORT = if ($uri.Port -gt 0) { [string]$uri.Port } else { '5432' }
    $env:PGDATABASE = [Uri]::UnescapeDataString($uri.AbsolutePath.TrimStart('/'))
    $env:PGUSER = [Uri]::UnescapeDataString($identity[0])
    $env:PGPASSWORD = [Uri]::UnescapeDataString($identity[1])
    $env:PGSSLMODE = 'require'
    New-Item -ItemType Directory -Force data | Out-Null
    $path = Join-Path (Get-Location) ('data\neon-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.dump')
    & pg_dump --format=custom --no-owner --no-acl --file $path
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed. No backup has been verified.' }
    & pg_restore --list $path | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'pg_restore could not read the archive.' }
    Write-Host "Backup archive readable: $path"
    Write-Host 'Next: restore this archive into a separate empty PostgreSQL 18 database for a full recovery check.'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    $url = $null
    Remove-Item Env:PGHOST,Env:PGPORT,Env:PGDATABASE,Env:PGUSER,Env:PGPASSWORD,Env:PGSSLMODE -ErrorAction SilentlyContinue
}
