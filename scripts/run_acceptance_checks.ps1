param(
    [switch]$SkipAndroid,
    [string]$GradleCommand = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Args
    )
    $ext = [System.IO.Path]::GetExtension($FilePath).ToLowerInvariant()
    if ($ext -eq ".bat" -or $ext -eq ".cmd") {
        $quoted = @()
        foreach ($arg in $Args) {
            if ($arg -match '[\s"]') {
                $quoted += '"' + ($arg -replace '"', '\"') + '"'
            }
            else {
                $quoted += $arg
            }
        }
        $cmdLine = '"' + $FilePath + '" ' + ($quoted -join ' ')
        & cmd.exe /d /c $cmdLine
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "Command failed ($exitCode): $FilePath $($Args -join ' ')"
        }
        return
    }
    $global:LASTEXITCODE = 0
    & $FilePath @Args
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Command failed ($exitCode): $FilePath $($Args -join ' ')"
    }
}

function Resolve-AndroidSdkDir {
    $candidates = @()
    if ($env:ANDROID_HOME) { $candidates += $env:ANDROID_HOME }
    if ($env:ANDROID_SDK_ROOT) { $candidates += $env:ANDROID_SDK_ROOT }
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA "Android\Sdk") }
    if ($env:USERPROFILE) { $candidates += (Join-Path $env:USERPROFILE "AppData\Local\Android\Sdk") }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return (Resolve-Path $candidate).Path
        }
    }
    return ""
}

function Ensure-AndroidLocalProperties {
    param([Parameter(Mandatory = $true)][string]$AndroidDir)
    $sdkDir = Resolve-AndroidSdkDir
    if (-not $sdkDir) {
        throw "Android SDK location not found. Set ANDROID_HOME/ANDROID_SDK_ROOT or install SDK to %LOCALAPPDATA%\Android\Sdk."
    }
    $probe = Join-Path $sdkDir ".write_probe"
    try {
        Set-Content -Path $probe -Value "probe" -Encoding ASCII
        Remove-Item -Path $probe -Force
    }
    catch {
        throw "Android SDK directory is not writable: $sdkDir"
    }
    $escaped = $sdkDir.Replace("\", "\\")
    $content = "sdk.dir=$escaped`r`n"
    $localProps = Join-Path $AndroidDir "local.properties"
    Set-Content -Path $localProps -Value $content -Encoding ASCII
}

Write-Host "[1/3] Running host unit and flow tests..."
Push-Location (Join-Path $root "windows_host")
try {
    Invoke-Checked python -m unittest discover -s tests -p "test_*.py" -v
}
finally {
    Pop-Location
}

Write-Host "[2/3] Protocol docs present..."
if (-not (Test-Path (Join-Path $root "protocol\PROTOCOL.md"))) {
    throw "protocol/PROTOCOL.md is missing"
}

if ($SkipAndroid) {
    Write-Host "[3/3] Android build skipped (-SkipAndroid)."
    Write-Host "Acceptance checks passed in current environment."
    exit 0
}

Write-Host "[3/3] Building Android debug APK..."
$wrapperJar = Join-Path $root "android_client\gradle\wrapper\gradle-wrapper.jar"
$gradleUserHome = Join-Path $root ".gradle-user-home"
$androidUserHome = Join-Path $root ".android-user-home"
New-Item -ItemType Directory -Force -Path $gradleUserHome | Out-Null
New-Item -ItemType Directory -Force -Path $androidUserHome | Out-Null
$env:GRADLE_USER_HOME = $gradleUserHome
$env:ANDROID_USER_HOME = $androidUserHome
$userHomeProp = "-Duser.home=$androidUserHome"
if ($env:GRADLE_OPTS) {
    $env:GRADLE_OPTS = "$env:GRADLE_OPTS $userHomeProp"
}
else {
    $env:GRADLE_OPTS = $userHomeProp
}
if (-not (Test-Path $wrapperJar)) {
    $bootstrapScript = Join-Path $root "scripts\bootstrap_gradle_wrapper.ps1"
    if ($GradleCommand) {
        Invoke-Checked $bootstrapScript -GradleCommand $GradleCommand
    }
    elseif ($env:GRADLE_CMD) {
        Invoke-Checked $bootstrapScript -GradleCommand $env:GRADLE_CMD
    }
    else {
        Invoke-Checked $bootstrapScript
    }
}

Push-Location (Join-Path $root "android_client")
try {
    Ensure-AndroidLocalProperties -AndroidDir (Get-Location).Path
    Invoke-Checked .\gradlew.bat --no-daemon :app:assembleDebug
}
finally {
    Pop-Location
}

Write-Host "Acceptance checks passed in current environment."
