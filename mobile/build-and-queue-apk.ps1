param(
    [Parameter(Mandatory = $true)]
    [string]$WorkspaceId,

    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$DeviceId = "fold5",
    [string]$BackendUrl = "http://127.0.0.1:5000",
    [string]$Variant = "debug",
    [string]$SessionId = "",
    [string]$ArtifactPath = "",
    [string]$ReleaseChannel = "debug",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

function Resolve-MobileRoot {
    if ($PSScriptRoot) {
        return $PSScriptRoot
    }
    return (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

function Normalize-BackendUrl {
    param([string]$Url)

    $trimmed = $Url.Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed)) {
        throw "BackendUrl is required."
    }
    return $trimmed.TrimEnd("/")
}

function Resolve-VariantInfo {
    param([string]$RequestedVariant)

    $normalized = $RequestedVariant.Trim().ToLowerInvariant()
    switch ($normalized) {
        "release" {
            return @{
                Variant = "release"
                Task = ":app:assembleRelease"
                ApkRelativePath = "app/build/outputs/apk/release/app-release.apk"
            }
        }
        default {
            return @{
                Variant = "debug"
                Task = ":app:assembleDebug"
                ApkRelativePath = "app/build/outputs/apk/debug/app-debug.apk"
            }
        }
    }
}

function Invoke-GradleBuild {
    param(
        [string]$MobileRoot,
        [hashtable]$VariantInfo
    )

    $gradleBat = Join-Path $MobileRoot "gradlew.bat"
    if (-not (Test-Path $gradleBat)) {
        throw "Gradle wrapper not found at $gradleBat"
    }

    Push-Location $MobileRoot
    try {
        & $gradleBat $VariantInfo.Task
        if ($LASTEXITCODE -ne 0) {
            throw "Gradle build failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

function Resolve-ApkPath {
    param(
        [string]$MobileRoot,
        [hashtable]$VariantInfo,
        [string]$RequestedArtifactPath
    )

    if (-not [string]::IsNullOrWhiteSpace($RequestedArtifactPath)) {
        $resolved = Resolve-Path -Path $RequestedArtifactPath
        return $resolved.Path
    }

    $candidate = Join-Path $MobileRoot $VariantInfo.ApkRelativePath
    if (-not (Test-Path $candidate)) {
        throw "APK not found at $candidate"
    }
    return $candidate
}

function Publish-ApkDelivery {
    param(
        [string]$UploadUrl,
        [string]$ApkPath,
        [string]$Device,
        [string]$Session,
        [hashtable]$Metadata
    )

    Add-Type -AssemblyName System.Net.Http

    $client = [System.Net.Http.HttpClient]::new()
    try {
        $form = [System.Net.Http.MultipartFormDataContent]::new()
        $fileName = [System.IO.Path]::GetFileName($ApkPath)
        $fileBytes = [System.IO.File]::ReadAllBytes($ApkPath)
        $fileContent = [System.Net.Http.ByteArrayContent]::new($fileBytes)
        $fileContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("application/vnd.android.package-archive")
        $form.Add($fileContent, "file", $fileName)
        $form.Add([System.Net.Http.StringContent]::new($Device), "device_id")
        $form.Add([System.Net.Http.StringContent]::new(($Metadata | ConvertTo-Json -Compress -Depth 10)), "metadata")
        if (-not [string]::IsNullOrWhiteSpace($Session)) {
            $form.Add([System.Net.Http.StringContent]::new($Session), "session_id")
        }

        $response = $client.PostAsync($UploadUrl, $form).GetAwaiter().GetResult()
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            throw "Upload failed: $($response.StatusCode) $body"
        }
        return $body | ConvertFrom-Json
    }
    finally {
        $client.Dispose()
    }
}

$mobileRoot = Resolve-MobileRoot
$backendBase = Normalize-BackendUrl -Url $BackendUrl
$variantInfo = Resolve-VariantInfo -RequestedVariant $Variant

if (-not $SkipBuild.IsPresent -and [string]::IsNullOrWhiteSpace($ArtifactPath)) {
    Write-Host "Building $($variantInfo.Variant) APK via Gradle wrapper..."
    Invoke-GradleBuild -MobileRoot $mobileRoot -VariantInfo $variantInfo
}

$apkPath = Resolve-ApkPath -MobileRoot $mobileRoot -VariantInfo $variantInfo -RequestedArtifactPath $ArtifactPath
$uploadUrl = "$backendBase/api/workspaces/$WorkspaceId/projects/$ProjectId/deliveries"

$metadata = @{
    release_channel = $ReleaseChannel
    build_variant = $variantInfo.Variant
    build_source = "desktop-script"
    produced_at = [DateTime]::UtcNow.ToString("o")
    artifact_name = [System.IO.Path]::GetFileName($apkPath)
}

Write-Host "Queueing $(Split-Path -Leaf $apkPath) for device '$DeviceId'..."
$delivery = Publish-ApkDelivery -UploadUrl $uploadUrl -ApkPath $apkPath -Device $DeviceId -Session $SessionId -Metadata $metadata

Write-Host "Delivery queued."
$delivery | ConvertTo-Json -Depth 10