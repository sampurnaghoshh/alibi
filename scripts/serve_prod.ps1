<#
.SYNOPSIS
  Run the ALIBI API for the public demo: uvicorn on port 8000, ONE worker, NO --reload.

.DESCRIPTION
  The Vercel frontend calls this laptop through a Cloudflare quick tunnel. One worker matters: the
  Live Desk feed and the background jobs live in this process (CLAUDE.md section 14). No --reload:
  a reload mid-demo drops every open Live Desk connection.

  With -Tunnel, it also opens a Cloudflare quick tunnel in a second window (needs cloudflared on PATH:
  winget install --id Cloudflare.cloudflared). That window prints the public https://....trycloudflare.com
  URL to put in Vercel's NEXT_PUBLIC_API_URL. A quick tunnel gets a NEW URL every time it starts.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\serve_prod.ps1 -Tunnel
#>
param(
    [int]$Port = 8000,
    [switch]$Tunnel
)
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repo "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python venv not found at $python. Set it up first (README: Backend)."
}

if ($Tunnel) {
    $cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
    if (-not $cloudflared) {
        throw "cloudflared is not installed. Run: winget install --id Cloudflare.cloudflared"
    }
    Write-Host "Opening the Cloudflare quick tunnel in a new window (copy its https://....trycloudflare.com URL)."
    Start-Process -FilePath $cloudflared.Source -ArgumentList @("tunnel", "--url", "http://localhost:$Port")
}

Write-Host "ALIBI API on http://localhost:$Port  (one worker, no reload; Ctrl+C to stop)"
Write-Host "Live Desk: http://localhost:$Port/live"
Set-Location $backend
# 127.0.0.1: reachable from this laptop and the tunnel only, not from the venue Wi-Fi.
& $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port --workers 1 `
    --proxy-headers --forwarded-allow-ips "127.0.0.1"
