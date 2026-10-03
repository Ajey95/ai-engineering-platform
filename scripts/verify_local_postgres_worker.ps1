param(
    [string] $PostgresContainer = 'aiplatform-postgres-1',
    [string] $Image = 'aip-dev-sandbox:0.1.3',
    [switch] $ResumeProbe,
    [switch] $BudgetPauseProbe
)

$ErrorActionPreference = 'Stop'
if ($ResumeProbe -and $BudgetPauseProbe) {
    throw 'Choose one resume probe at a time'
}
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $workspace '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Workspace Python virtual environment is missing'
}

$database = 'aip_verify_' + [guid]::NewGuid().ToString('N').Substring(0, 12)
if ($database -notmatch '^aip_verify_[0-9a-f]{12}$') {
    throw 'Disposable database name is invalid'
}
$previousDatabaseUrl = $env:AIP_DATABASE_URL
$previousVerifyUrl = $env:AIP_VERIFY_DATABASE_URL
$created = $false
try {
    & wsl.exe -d Ubuntu-24.04 -u root -- docker exec $PostgresContainer `
        psql -U aip -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $database"
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the disposable database' }
    $created = $true

    $url = "postgresql+psycopg://aip:local_only@127.0.0.1:54329/$database"
    $env:AIP_DATABASE_URL = $url
    $env:AIP_VERIFY_DATABASE_URL = $url
    Push-Location -LiteralPath $workspace
    try {
        & $python -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw 'Disposable PostgreSQL migration failed' }
        $verifyArgs = @(
            '-m', 'scripts.verify_development_worker', '--controlled-provider',
            '--runtime', 'wsl', '--image', $Image, '--disposable-postgres-env'
        )
        if ($ResumeProbe) { $verifyArgs += '--resume-probe' }
        if ($BudgetPauseProbe) { $verifyArgs += '--budget-pause-probe' }
        & $python @verifyArgs
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL worker verification failed' }
    } finally {
        Pop-Location
    }
} finally {
    $env:AIP_DATABASE_URL = $previousDatabaseUrl
    $env:AIP_VERIFY_DATABASE_URL = $previousVerifyUrl
    if ($created) {
        & wsl.exe -d Ubuntu-24.04 -u root -- docker exec $PostgresContainer `
            psql -U aip -d postgres -v ON_ERROR_STOP=1 `
            -c "DROP DATABASE $database WITH (FORCE)"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Manual cleanup required for disposable database $database"
        }
    }
}
