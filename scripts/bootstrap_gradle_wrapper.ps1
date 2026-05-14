param(
    [string]$GradleCommand = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$androidDir = Join-Path $root "android_client"
$wrapperJar = Join-Path $androidDir "gradle\wrapper\gradle-wrapper.jar"

if (Test-Path $wrapperJar) {
    Write-Host "Gradle wrapper jar already exists."
    exit 0
}

$gradleVersion = "8.7"
$distName = "gradle-$gradleVersion-bin"
$distZip = Join-Path $env:TEMP "$distName.zip"
$distUrl = "https://services.gradle.org/distributions/$distName.zip"
$extractDir = Join-Path $env:TEMP "gradle-wrapper-bootstrap"
$gradleBat = ""

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Args
    )
    & $FilePath @Args
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $FilePath $($Args -join ' ')"
    }
}

function Download-File {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$OutFile
    )

    try {
        [Net.ServicePointManager]::SecurityProtocol =
            [Net.SecurityProtocolType]::Tls12 -bor
            [Net.SecurityProtocolType]::Tls11 -bor
            [Net.SecurityProtocolType]::Tls
    }
    catch {
    }

    $attempts = 3
    for ($i = 1; $i -le $attempts; $i++) {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
            return
        }
        catch {
            if (Test-Path $OutFile) {
                Remove-Item -Force $OutFile
            }
            if ($i -eq $attempts) {
                break
            }
            Start-Sleep -Seconds (2 * $i)
        }
    }

    if (Get-Command Start-BitsTransfer -ErrorAction SilentlyContinue) {
        try {
            Start-BitsTransfer -Source $Url -Destination $OutFile -ErrorAction Stop
            return
        }
        catch {
            if (Test-Path $OutFile) {
                Remove-Item -Force $OutFile
            }
        }
    }

    if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
        & curl.exe -L --fail --retry 3 --retry-delay 2 --output $OutFile $Url
        if ($LASTEXITCODE -eq 0 -and (Test-Path $OutFile)) {
            return
        }
    }

    throw "Failed to download file from $Url"
}

if ($GradleCommand) {
    Write-Host "Using configured Gradle command: $GradleCommand"
    Invoke-Checked $GradleCommand -p $androidDir wrapper --gradle-version $gradleVersion --distribution-type bin
}
else {
    $gradleCmd = Get-Command gradle -ErrorAction SilentlyContinue
    if ($null -ne $gradleCmd) {
        Write-Host "Using system Gradle from PATH: $($gradleCmd.Path)"
        Invoke-Checked $gradleCmd.Path -p $androidDir wrapper --gradle-version $gradleVersion --distribution-type bin
    }
    else {
        Write-Host "Downloading Gradle $gradleVersion distribution..."
        Download-File -Url $distUrl -OutFile $distZip

        if (Test-Path $extractDir) {
            Remove-Item -Recurse -Force $extractDir
        }
        Expand-Archive -Path $distZip -DestinationPath $extractDir -Force

        $gradleBatCandidate = Get-ChildItem -Path $extractDir -Recurse -File -Filter "gradle.bat" | Select-Object -First 1
        if ($null -eq $gradleBatCandidate) {
            throw "Failed to locate gradle.bat in extracted distribution"
        }
        $gradleBat = $gradleBatCandidate.FullName

        Write-Host "Generating Gradle wrapper files..."
        Invoke-Checked $gradleBat -p $androidDir wrapper --gradle-version $gradleVersion --distribution-type bin
    }
}

if (-not (Test-Path $wrapperJar)) {
    throw "Failed to generate gradle-wrapper.jar"
}

Write-Host "Gradle wrapper bootstrap complete."
