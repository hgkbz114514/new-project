# vpnkit one-click installer for AI agents / PowerShell users.
# Standalone: paste this single line in PowerShell -
#   iwr https://raw.githubusercontent.com/hgkbz114514/vpnkit/main/install.ps1 -OutFile install.ps1; .\install.ps1
# Already cloned? just run .\install.ps1
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host ""
Write-Host " ============================================" -ForegroundColor Cyan
Write-Host "   vpnkit one-click installer"
Write-Host "   https://github.com/hgkbz114514/vpnkit"
Write-Host " ============================================" -ForegroundColor Cyan
Write-Host ""

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# --- standalone mode: fetch the rest of the repo when running from a bare file ---
$standalone = -not (Test-Path (Join-Path $root "bin\vpn.py"))
if ($standalone) {
    Write-Host " [0/4] Standalone mode - downloading full repo ..." 
    $zip = Join-Path $env:TEMP "vpnkit-main.zip"
    $dst = Join-Path $env:TEMP "vpnkit-main"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    # plain Invoke-WebRequest first; retry through local proxy if GitHub is blocked
    try {
        Invoke-WebRequest "https://codeload.github.com/hgkbz114514/vpnkit/zip/refs/heads/main" -OutFile $zip -UseBasicParsing
    } catch {
        Write-Host "       direct download failed, retrying via 127.0.0.1:7897 ..."
        Invoke-WebRequest "https://codeload.github.com/hgkbz114514/vpnkit/zip/refs/heads/main" -OutFile $zip -UseBasicParsing -Proxy "http://127.0.0.1:7897"
    }
    if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
    Expand-Archive $zip $dst -Force
    Copy-Item (Join-Path $dst "vpnkit-main\*") $root -Recurse -Force
    Remove-Item $zip, $dst -Recurse -Force
    Write-Host "       repo ready"
}

# --- 1. python ---
$py = $null
foreach ($c in @("py", "python")) {
    if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
}
if (-not $py) {
    $p313 = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
    if (Test-Path $p313) { $py = $p313 }
}
if (-not $py) {
    Write-Host " [X] Python 3.9+ not found." -ForegroundColor Red
    Write-Host "     AI agent: winget install -e --id Python.Python.3.13"
    Write-Host "     Human:    https://www.python.org/downloads/"
    exit 1
}
Write-Host " [1/4] Python OK"

# --- 2. deps ---
Write-Host " [2/4] Installing dependencies ..."
& $py -m pip install --quiet --disable-pip-version-check requests pyyaml
if ($LASTEXITCODE -ne 0) {
    Write-Host "     pip failed. If PyPI is blocked:  " -NoNewline
    Write-Host '$env:HTTPS_PROXY="http://127.0.0.1:7897"' -ForegroundColor Yellow
    exit 1
}

# --- 3. doctor ---
Write-Host " [3/4] Environment check ..."
& $py bin\vkcore.py
& $py bin\vpn.py doctor
if ($LASTEXITCODE -ne 0) {
    Write-Host " [X] doctor failed - fix the problems above, then re-run." -ForegroundColor Red
    exit 1
}

# --- 4. first build ---
if (Test-Path "profiles\vpnkit.yaml") {
    Write-Host " [4/4] Config exists - skipping collection."
} else {
    Write-Host " [4/4] Collecting free nodes (about 6 minutes) ..."
    & $py bin\vk.py all
    if ($LASTEXITCODE -ne 0) {
        Write-Host " [X] node collection failed." -ForegroundColor Red
        exit 1
    }
}

& $py bin\vpn.py start
Write-Host ""
Write-Host " DONE. Proxy on 127.0.0.1:7897, system proxy enabled." -ForegroundColor Green
Write-Host " Menu: vpnkit.bat   Status: python bin\vpn.py status"