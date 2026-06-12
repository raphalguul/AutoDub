$ErrorActionPreference = "Stop"

# Clean previous build
if (Test-Path "dist\AutoDub") { Remove-Item -Recurse -Force dist\AutoDub }

# Read current version
$verFile = "VERSION"
$ver = (Get-Content $verFile -Raw).Trim()

# Increment last decimal
$parts = $ver.Split(".")
$parts[-1] = [int]$parts[-1] + 1
$newVer = $parts -join "."

# Write new version back
Set-Content -Path $verFile -Value $newVer -NoNewline -Encoding ASCII

Write-Host "Building v$newVer ..."

# Clean stale config from previous frozen runs
Remove-Item -LiteralPath "dist\config.json" -Force -ErrorAction SilentlyContinue

# Reload PATH from registry (in case UPX was installed after terminal started)
$env:Path = [Environment]::GetEnvironmentVariable('Path','User') + ';' + [Environment]::GetEnvironmentVariable('Path','Machine')

pyinstaller AutoDub.spec
if (-not $?) {
    Write-Host "FAILED: pyinstaller returned exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

# Verify
$exePath = "dist\AutoDub\AutoDub.exe"
if (Test-Path $exePath) {
    $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path $exePath))
    $sig = [System.Text.Encoding]::ASCII.GetString($bytes[2..4])
    $exeSizeMB = [math]::Round($bytes.Length / 1MB, 1)
    $totalSize = (Get-ChildItem "dist\AutoDub" -Recurse | Measure-Object -Sum Length).Sum
    $totalSizeMB = [math]::Round($totalSize / 1MB, 1)
    Write-Host "Done! dist\AutoDub\ — EXE ${exeSizeMB} MB, total ${totalSizeMB} MB — UPX: $(if ($sig -eq 'UPX') { 'YES' } else { 'NO' }) — v$newVer"
} else {
    Write-Host "FAILED: $exePath not found"
    exit 1
}
