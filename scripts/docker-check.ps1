[CmdletBinding()]
param(
    [ValidatePattern('^(auto|[1-9][0-9]*)$')]
    [string] $Workers = '4',

    [switch] $NoBuild
)

$ErrorActionPreference = 'Stop'
$image = 'ha-household-tasks-dev'
$repositoryRoot = Split-Path -Parent $PSScriptRoot

Push-Location $repositoryRoot
try {
    if (-not $NoBuild) {
        docker build --file Dockerfile.dev --tag $image .
        if ($LASTEXITCODE -ne 0) {
            throw "Building the development image failed with exit code $LASTEXITCODE."
        }
    }

    docker run --rm --init `
        --volume "${repositoryRoot}:/workspace" `
        --workdir /workspace `
        --env "PYTEST_WORKERS=$Workers" `
        $image
    if ($LASTEXITCODE -ne 0) {
        throw "Quality checks failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
