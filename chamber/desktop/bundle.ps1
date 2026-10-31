# Build the downloadable Windows app (a one-click installer) with its own Node inside.
# Run on a Windows PC with Rust (rustup, with the Visual Studio C++ build tools) and Node 22+:
#   powershell -ExecutionPolicy Bypass -File desktop\bundle.ps1
# Output: desktop\src-tauri\target\release\bundle\nsis\chamber-windows-x64-setup.exe
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "src-tauri")
New-Item -ItemType Directory -Force binaries | Out-Null
Copy-Item (Get-Command node).Source "binaries\node-x86_64-pc-windows-msvc.exe" -Force
npm --prefix ..\..\relay ci --silent
npm --prefix ..\..\ui ci --silent
npm --prefix ..\..\ui run build
$env:CHAMBER_NODE = (Get-Command node).Source
# Without the updater's signing key (TAURI_SIGNING_PRIVATE_KEY), build no update archive: Tauri
# would otherwise fail asking for the key. The installer is the same either way.
if ($env:TAURI_SIGNING_PRIVATE_KEY) {
  npx --yes "@tauri-apps/cli@2" build --bundles nsis
} else {
  # A file, not inline JSON: PowerShell 5 and 7 quote JSON for a native command differently.
  Set-Content -Path "no-updater.json" -Value '{"bundle":{"createUpdaterArtifacts":false}}'
  npx --yes "@tauri-apps/cli@2" build --bundles nsis --config no-updater.json
  Remove-Item "no-updater.json"
}
if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
$out = "target\release\bundle\nsis"
Copy-Item "$out\Chamber_*_x64-setup.exe" "$out\chamber-windows-x64-setup.exe" -Force
Write-Host "Upload to R2: chamber/releases/stable/<version>/chamber-windows-x64-setup.exe"
