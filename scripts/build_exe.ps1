# Build EC-Buyma Windows desktop app (.exe)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "==> Install PyInstaller"
py -3 -m pip install -q "pyinstaller>=6.0" Pillow

Write-Host "==> Generate Buyma B icon"
py -3 scripts\make_app_icon.py

# PyInstaller --clean deletes dist\EC-Buyma. Preserve runtime data first.
$dist = Join-Path (Get-Location) "dist\EC-Buyma"
$preserveDir = Join-Path (Get-Location) "build\_dist_preserve"
if (Test-Path $preserveDir) {
    Remove-Item -Recurse -Force $preserveDir
}
New-Item -ItemType Directory -Force -Path $preserveDir | Out-Null
if (Test-Path $dist) {
    Write-Host "==> Preserve dist workspace / secrets / logs / .env"
    foreach ($name in @("workspace", "secrets", "logs", ".env")) {
        $src = Join-Path $dist $name
        if (Test-Path $src) {
            Copy-Item -Path $src -Destination (Join-Path $preserveDir $name) -Recurse -Force
        }
    }
    Get-ChildItem -Path $dist -Filter "*.md" -File -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination (Join-Path $preserveDir $_.Name) -Force
    }
}

Write-Host "==> PyInstaller build (onedir)"
py -3 -m PyInstaller build\ec_buyma.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE. Close EC-Buyma.exe and retry."
}

$dist = Join-Path (Get-Location) "dist\EC-Buyma"
if (-not (Test-Path (Join-Path $dist "EC-Buyma.exe"))) {
    throw "Build failed: EC-Buyma.exe not found in $dist"
}

if (Test-Path $preserveDir) {
    Write-Host "==> Restore preserved dist runtime data"
    foreach ($name in @("workspace", "secrets", "logs", ".env")) {
        $src = Join-Path $preserveDir $name
        if (Test-Path $src) {
            $dest = Join-Path $dist $name
            if (Test-Path $dest) {
                Remove-Item -Recurse -Force $dest
            }
            Copy-Item -Path $src -Destination $dest -Recurse -Force
        }
    }
    Get-ChildItem -Path $preserveDir -Filter "*.md" -File -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination (Join-Path $dist $_.Name) -Force
    }
    Remove-Item -Recurse -Force $preserveDir -ErrorAction SilentlyContinue
}

# Seed runtime folders next to the exe (do not overwrite existing secrets).
foreach ($name in @("workspace\scrape", "workspace\generate", "workspace\buyma", "secrets", "logs", "assets")) {
    $p = Join-Path $dist $name
    New-Item -ItemType Directory -Force -Path $p | Out-Null
}
$srcAssets = Join-Path (Get-Location) "assets"
$destAssets = Join-Path $dist "assets"
if (Test-Path $srcAssets) {
    Write-Host "==> Copy notice images into dist assets (98/99 sources)"
    foreach ($name in @("provided_image_1.png", "provided_image_2.png", "brand_intro_image.png")) {
        $src = Join-Path $srcAssets $name
        if (Test-Path $src) {
            Copy-Item -Path $src -Destination (Join-Path $destAssets $name) -Force
        }
    }
    $destApp = Join-Path $destAssets "app"
    New-Item -ItemType Directory -Force -Path $destApp | Out-Null
    foreach ($name in @("ec_buyma.ico", "ec_buyma_256.png")) {
        $src = Join-Path $srcAssets "app\$name"
        if (Test-Path $src) {
            Copy-Item -Path $src -Destination (Join-Path $destApp $name) -Force
        }
    }
}

# Replace CustomTkinter's default Python/CTk window icon with the black B mark.
$appIco = Join-Path (Get-Location) "assets\app\ec_buyma.ico"
$ctkIconDir = Join-Path $dist "_internal\customtkinter\assets\icons"
if ((Test-Path $appIco) -and (Test-Path $ctkIconDir)) {
    Write-Host "==> Replace CustomTkinter window icon with Buyma B"
    Copy-Item -Path $appIco -Destination (Join-Path $ctkIconDir "CustomTkinter_icon_Windows.ico") -Force
    Get-ChildItem -Path $ctkIconDir -Filter "CustomTkinter_icon_Windows.*" -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item -Path $appIco -Destination $_.FullName -Force
    }
}

# Copy local secrets if present (cookies/sessions) without deleting dist secrets.
$srcSecrets = Join-Path (Get-Location) "secrets"
if (Test-Path $srcSecrets) {
    Write-Host "==> Copy secrets into dist (cookies/sessions)"
    Copy-Item -Path (Join-Path $srcSecrets "*") -Destination (Join-Path $dist "secrets") -Recurse -Force -ErrorAction SilentlyContinue
}

# Copy .env if present
if (Test-Path ".env") {
    Copy-Item ".env" (Join-Path $dist ".env") -Force
}
$docsDir = Join-Path (Get-Location) "docs"
if (Test-Path $docsDir) {
    Get-ChildItem -Path $docsDir -Filter "*.md" -File -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination (Join-Path $dist $_.Name) -Force
    }
}

Write-Host ""
Write-Host "Build OK:"
Write-Host "  $dist\EC-Buyma.exe"
Write-Host "Chrome must be installed on the PC (channel=chrome)."
Write-Host "workspace/ and secrets/ live next to the .exe."
