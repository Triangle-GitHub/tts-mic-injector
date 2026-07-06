#Requires -Version 7.0
<#
.SYNOPSIS
    TTS Mic Injector 一键发布脚本
.DESCRIPTION
    构建 PyInstaller 打包 → 创建 7z 归档 → 推送 tag → 创建 GitHub Release
.EXAMPLE
    .\release.ps1
#>

$ErrorActionPreference = "Stop"
$script:Root = $PSScriptRoot

function Write-Step($step, $total, $msg) {
    Write-Host "[" -NoNewline
    Write-Host $step -ForegroundColor Cyan -NoNewline
    Write-Host "/$total] $msg..." -NoNewline
}

function Write-OK {
    Write-Host " `u{2713}" -ForegroundColor Green
}

function Write-Fail($reason) {
    Write-Host " `u{2717}" -ForegroundColor Red
    Write-Host "   $reason" -ForegroundColor Red
    exit 1
}

# ============================================================
#  0. 收集发布信息
# ============================================================

Write-Host ""
Write-Host "  TTS Mic Injector Release" -ForegroundColor Cyan
Write-Host "  ========================" -ForegroundColor Cyan
Write-Host ""

do {
    $Version = Read-Host "  版本号 (如 1.0.4)"
    if (-not $Version) { Write-Host "  Version cannot be empty" -ForegroundColor Red }
} while (-not $Version)

$Version = $Version.Trim()
$Tag = "v$Version"
$ArchiveName = "TTSMicInjector_$Tag.7z"

Write-Host ""
Write-Host "  Release Notes (可选). " -NoNewline
$hasNotes = Read-Host "输入 Y 打开记事本编辑，直接回车跳过"

$Notes = ""
if ($hasNotes -eq 'Y' -or $hasNotes -eq 'y') {
    $tempNotes = New-TemporaryFile | Rename-Item -NewName { $_.Name + ".txt" } -PassThru
    $tempNotes = "$tempNotes"
    Set-Content -Path $tempNotes -Value "# v$Version Release Notes`r`n`r`n" -Encoding UTF8
    Start-Process notepad -Wait -ArgumentList $tempNotes
    $raw = Get-Content -Path $tempNotes -Raw -Encoding UTF8
    $lines = $raw -split "`r?`n" | Where-Object { $_ -notmatch '^\s*#' -and $_.Trim() -ne "" }
    $Notes = $lines -join "`n"
    Remove-Item $tempNotes -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "  ========================================" -ForegroundColor Yellow
Write-Host "    Version : $Version" -ForegroundColor Yellow
Write-Host "    Tag     : $Tag" -ForegroundColor Yellow
Write-Host "    Archive : $ArchiveName" -ForegroundColor Yellow
if ($Notes) {
    Write-Host "    Notes   : $(($Notes -split "`n")[0])..." -ForegroundColor Yellow
} else {
    Write-Host "    Notes   : (empty)" -ForegroundColor Yellow
}
Write-Host "  ========================================" -ForegroundColor Yellow
Write-Host ""

$confirm = Read-Host "  确认发布? (Y/n)"
if ($confirm -and $confirm -ne 'Y' -and $confirm -ne 'y') {
    Write-Host "  已取消" -ForegroundColor Red
    exit 0
}

$totalSteps = 5
$current = 0

# ============================================================
#  1. 前置检查
# ============================================================
$current++
Write-Step $current $totalSteps "检查依赖"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Fail "python 未找到，请确认已安装 Python 并加入 PATH"
}

$pyinstallerCheck = python -c "import PyInstaller; print(PyInstaller.__version__)" 2>$null
if (-not $pyinstallerCheck) {
    Write-Fail "PyInstaller 未安装，请运行: pip install PyInstaller>=5.0"
}

$sevenZipPaths = @(
    "$env:ProgramFiles\7-Zip\7z.exe",
    "${env:ProgramFiles(x86)}\7-Zip\7z.exe",
    (Get-Command 7z -ErrorAction SilentlyContinue).Source
) | Where-Object { $_ -and (Test-Path $_) }
if (-not $sevenZipPaths) {
    Write-Fail "7z 未找到，请安装 7-Zip 并加入 PATH"
}
$SevenZip = $sevenZipPaths[0]

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Fail "gh CLI 未找到，请安装: winget install --id GitHub.cli"
}

$ghAuth = gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Fail "gh CLI 未登录，请运行: gh auth login"
}

$branch = git branch --show-current
if ($branch -ne "master") {
    Write-Fail "当前不在 master 分支 (当前: $branch)"
}

$gitStatus = git status --porcelain
if ($gitStatus) {
    Write-Fail "工作区不干净，请先提交或暂存所有更改"
}

Write-OK

# ============================================================
#  2. 清理旧构建
# ============================================================
$current++
Write-Step $current $totalSteps "清理旧构建"

$distDir = Join-Path $script:Root "dist"
$buildDir = Join-Path $script:Root "build"

if (Test-Path $distDir) {
    Remove-Item -Recurse -Force $distDir
    Write-Host "  (已清理 dist/)" -ForegroundColor DarkGray
}
if (Test-Path $buildDir) {
    Remove-Item -Recurse -Force $buildDir
    Write-Host "  (已清理 build/)" -ForegroundColor DarkGray
}

Write-OK

# ============================================================
#  3. PyInstaller 构建
# ============================================================
$current++
Write-Step $current $totalSteps "PyInstaller 构建 (约 1-2 分钟)"

Push-Location $script:Root
try {
    $buildOutput = python build_qt.py 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host $buildOutput
        Write-Fail "构建失败，详见上方输出"
    }
} finally {
    Pop-Location
}

$distOutput = Join-Path $distDir "TTSMicInjector"
if (-not (Test-Path (Join-Path $distOutput "TTSMicInjector.exe"))) {
    Write-Fail "构建产物未找到: $distOutput\TTSMicInjector.exe"
}

Write-OK

# ============================================================
#  4. 打包 7z
# ============================================================
$current++
Write-Step $current $totalSteps "打包 $ArchiveName"

$archivePath = Join-Path $distDir $ArchiveName
if (Test-Path $archivePath) {
    Remove-Item -Force $archivePath
}

& $SevenZip a -t7z -mx=9 -mmt=on $archivePath "$distOutput\*" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Fail "7z 打包失败"
}

$archiveSize = [math]::Round((Get-Item $archivePath).Length / 1MB, 2)
Write-Host "  ($ArchiveName, ${archiveSize}MB)" -ForegroundColor DarkGray

Write-OK

# ============================================================
#  5. 推送 tag 并创建 Release
# ============================================================
$current++
Write-Step $current $totalSteps "推送 tag 并创建 GitHub Release"

git tag $Tag
git push origin $Tag
if ($LASTEXITCODE -ne 0) {
    Write-Fail "推送 tag 失败"
}

$releaseArgs = @(
    "release", "create", $Tag,
    "--title", $Tag,
    $archivePath
)
if ($Notes) {
    $releaseArgs += "--notes"
    $releaseArgs += $Notes
}

gh @releaseArgs
if ($LASTEXITCODE -ne 0) {
    Write-Fail "创建 GitHub Release 失败"
}

Write-OK

# ============================================================
#  Done
# ============================================================
Write-Host ""
Write-Host "  Release $Tag done!" -ForegroundColor Green
Write-Host "  $(gh release view $Tag --json url --jq '.url')" -ForegroundColor Cyan
Write-Host ""
