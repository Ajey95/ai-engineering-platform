$ErrorActionPreference = 'Stop'

# WSL systemd services alone do not prevent idle shutdown. Keep one explicit
# user process attached while this development Engine is needed.
$keepalive = Get-CimInstance Win32_Process -Filter "Name = 'wsl.exe'" |
    Where-Object { $_.CommandLine -like '*-d Ubuntu-24.04 -u root -- sleep infinity*' } |
    Select-Object -First 1
if (-not $keepalive) {
    $process = Start-Process -FilePath wsl.exe `
        -ArgumentList @('-d', 'Ubuntu-24.04', '-u', 'root', '--', 'sleep', 'infinity') `
        -WindowStyle Hidden -PassThru
    Write-Output "Started WSL keepalive process $($process.Id)"
} else {
    Write-Output "Using WSL keepalive process $($keepalive.ProcessId)"
}

wsl -d Ubuntu-24.04 -u root -- systemctl start docker
if ($LASTEXITCODE -ne 0) { throw 'Docker Engine did not start in Ubuntu-24.04' }
wsl -d Ubuntu-24.04 -u root -- docker info --format 'Server={{.ServerVersion}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker Engine is not responding' }
