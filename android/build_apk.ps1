# 书萤 · 一键构建 Android APK
#
#   powershell -ExecutionPolicy Bypass -File android\build_apk.ps1
#   powershell -ExecutionPolicy Bypass -File android\build_apk.ps1 -Release
#
# 为什么要拷到 C:\bookfly-android 再构建：
#   AGP 拒绝含非 ASCII 字符的工程路径（这台机器用户目录是「韩凌云」），
#   虽然 gradle.properties 里能 overridePathCheck 关掉检查，但 aapt2 和
#   Chaquopy 的 native 处理在中文路径下仍偶发失败。拷到纯 ASCII 目录
#   构建是最省事的稳妥做法，产物再拷回来。

param(
    [switch]$Release,
    [string]$WorkDir = "C:\bookfly-android",
    [string]$JdkHome = "C:\Users\$env:USERNAME\.bookfly-jdk\jdk-17.0.20.1+1",
    [string]$GradleHome = "C:\Users\$env:USERNAME\.gradle\wrapper\dists\gradle-8.9-all\34ncldp5ayui479swhyf2hcth\gradle-8.9"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path "$root\novelhub")) { $root = (Get-Location).Path }

Write-Host "==> 1/5 同步 Python 源码与前端资源" -ForegroundColor Cyan
& python "$root\android\sync_sources.py"
if ($LASTEXITCODE -ne 0) { throw "sync_sources 失败" }

Write-Host "==> 2/5 拷到 ASCII 构建目录 $WorkDir" -ForegroundColor Cyan
if (-not (Test-Path $WorkDir)) { New-Item -ItemType Directory -Force $WorkDir | Out-Null }
# 只同步构建需要的，不覆盖 build/ 缓存（省时间）
robocopy "$root\android" $WorkDir /MIR /XD build .gradle /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy 失败 ($LASTEXITCODE)" }

Write-Host "==> 3/5 检查工具链" -ForegroundColor Cyan
if (-not (Test-Path "$JdkHome\bin\java.exe")) {
    throw "找不到 JDK 17：$JdkHome`n 下载：https://mirrors.tuna.tsinghua.edu.cn/Adoptium/17/jdk/x64/windows/"
}
if (-not (Test-Path "$GradleHome\bin\gradle.bat")) {
    throw "找不到 Gradle 8.9：$GradleHome"
}
$env:JAVA_HOME = $JdkHome
$env:ANDROID_HOME = "C:\Android"
$env:ANDROID_SDK_ROOT = "C:\Android"
$env:BOOKFLY_BUILD_PYTHON = (Get-Command python).Source

Write-Host "==> 4/5 构建（$(if ($Release) {'release'} else {'debug'})）" -ForegroundColor Cyan
Push-Location $WorkDir
try {
    $task = if ($Release) { "assembleRelease" } else { "assembleDebug" }
    & "$GradleHome\bin\gradle.bat" $task --no-daemon --console=plain
    if ($LASTEXITCODE -ne 0) { throw "gradle 构建失败" }
} finally {
    Pop-Location
}

Write-Host "==> 5/5 取回产物" -ForegroundColor Cyan
$variant = if ($Release) { "release" } else { "debug" }
$apk = Get-ChildItem "$WorkDir\app\build\outputs\apk\$variant" -Filter "*.apk" |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $apk) { throw "没找到 APK 产物" }

$outDir = "$root\dist\android"
New-Item -ItemType Directory -Force $outDir | Out-Null
$outName = "书萤-$variant.apk"
Copy-Item $apk.FullName "$outDir\$outName" -Force

$sizeMb = [math]::Round((Get-Item "$outDir\$outName").Length / 1MB, 1)
Write-Host ""
Write-Host "构建完成：$outDir\$outName  ($sizeMb MB)" -ForegroundColor Green
Write-Host ""
Write-Host "安装到手机（USB 调试打开）：" -ForegroundColor Yellow
Write-Host "  adb install -r `"$outDir\$outName`""
Write-Host ""
Write-Host "看日志："
Write-Host "  adb logcat -s BookFly python.stderr"
