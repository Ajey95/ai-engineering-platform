param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ComposeArgs
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$composeFile = Join-Path $projectRoot 'compose.yaml'
if (-not (Test-Path -LiteralPath $composeFile -PathType Leaf)) {
    throw "Compose file is missing: $composeFile"
}

# Pass an argument array to WSL so Compose receives each caller argument intact.
$driveRoot = [System.IO.Path]::GetPathRoot($composeFile)
if ($driveRoot -notmatch '^([A-Za-z]):\\$') {
    throw "WSL path translation requires a drive-letter path: $composeFile"
}
$driveLetter = $Matches[1].ToLowerInvariant()
$relativePath = $composeFile.Substring($driveRoot.Length).Replace('\', '/')
$wslComposeFile = "/mnt/$driveLetter/$relativePath"

& (Join-Path $PSScriptRoot 'start_wsl_docker.ps1') | Write-Output
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
    throw 'WSL Docker Engine startup failed'
}

& wsl.exe -d Ubuntu-24.04 -u root -- docker compose -f $wslComposeFile @ComposeArgs
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed with exit code $LASTEXITCODE"
}
