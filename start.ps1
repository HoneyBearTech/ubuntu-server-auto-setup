<#
.SYNOPSIS
    Starts the ubuntu-server-auto-setup web UI in Docker and opens it in the browser.

.DESCRIPTION
    For Windows with Docker Desktop (WSL 2 backend); also works with PowerShell 7 on Linux and macOS.
    The first run pulls the image, creates the container "usas" with its data in the Docker volume
    "usas-data", and shows the setup code for the first sign-in. Later runs start the existing container.
    The UI listens on 127.0.0.1 only. See docs/web-ui.md.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\start.ps1
.EXAMPLE
    .\start.ps1 -Update      # pull the latest image and recreate the container (the data volume stays)
.EXAMPLE
    .\start.ps1 -Verify      # check the image's signature with cosign before starting it
.EXAMPLE
    .\start.ps1 -Stop
#>
[CmdletBinding()]
param(
    [int]$Port = 8080,
    [string]$Image = "honeybeartech/ubuntu-server-auto-setup:latest",
    [string]$Name = "usas",
    [string]$Volume = "usas-data",
    [switch]$Update,
    [switch]$Verify,
    [switch]$Stop,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Docker {
    # Runs docker with an argument list and stops on failure.
    $output = & docker @args 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($args -join ' ') failed:`n$($output -join "`n")"
    }
    return $output
}

function Test-DockerRunning {
    & docker info --format "{{.ServerVersion}}" *> $null
    return ($LASTEXITCODE -eq 0)
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker isn't installed. Install Docker Desktop (https://docs.docker.com/desktop/) with the WSL 2 backend, then run this again." -ForegroundColor Red
    exit 1
}

if (-not (Test-DockerRunning)) {
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if ($env:ProgramFiles -and (Test-Path $desktop)) {
        Write-Host "Starting Docker Desktop..."
        Start-Process -FilePath $desktop | Out-Null
        $deadline = (Get-Date).AddMinutes(3)
        while (-not (Test-DockerRunning)) {
            if ((Get-Date) -gt $deadline) {
                Write-Host "Docker Desktop didn't start within 3 minutes. Start it by hand and run this again." -ForegroundColor Red
                exit 1
            }
            Start-Sleep -Seconds 3
        }
    } else {
        Write-Host "Docker isn't running. Start Docker Desktop (or the Docker service) and run this again." -ForegroundColor Red
        exit 1
    }
}

$existing = & docker ps -a --filter "name=^/$Name$" --format "{{.Names}}"
if ($Stop) {
    if ($existing) { Invoke-Docker stop $Name | Out-Null; Write-Host "Stopped $Name. Your settings stay in its data volume." }
    else { Write-Host "There is no container named $Name." }
    exit 0
}

& docker image inspect $Image *> $null
$haveImage = ($LASTEXITCODE -eq 0)
if ($Update -or -not $haveImage) {
    Write-Host "Pulling $Image..."
    Invoke-Docker pull $Image | Out-Null
}

if ($Verify) {
    if (-not (Get-Command cosign -ErrorAction SilentlyContinue)) {
        Write-Host "-Verify needs cosign (https://docs.sigstore.dev/cosign/system_config/installation/)." -ForegroundColor Red
        exit 1
    }
    & cosign verify $Image `
        --certificate-identity-regexp '^https://github\.com/HoneyBearTech/ubuntu-server-auto-setup/\.github/workflows/release\.yml@refs/tags/v' `
        --certificate-oidc-issuer https://token.actions.githubusercontent.com | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "The image's signature didn't verify. Don't run it; see docs/verifying-releases.md." -ForegroundColor Red
        exit 1
    }
    Write-Host "Signature verified: $Image was built by the project's release workflow." -ForegroundColor Green
}

if ($existing -and $Update) {
    Invoke-Docker rm -f $Name | Out-Null
    $existing = $null
}

if ($existing) {
    Invoke-Docker start $Name | Out-Null
} else {
    # Localhost only: anyone who can reach this port can try to sign in. See "Exposing it" in docs/web-ui.md.
    Invoke-Docker run -d --name $Name --restart unless-stopped `
        -p "127.0.0.1:${Port}:8080" -v "${Volume}:/data" `
        --cap-drop ALL --security-opt "no-new-privileges:true" $Image | Out-Null
}

Write-Host "Waiting for the web UI..."
$deadline = (Get-Date).AddSeconds(90)
do {
    Start-Sleep -Seconds 2
    $health = & docker inspect -f "{{.State.Health.Status}}" $Name 2>$null
    if ((Get-Date) -gt $deadline) {
        Write-Host "The web UI didn't become healthy. Its log:" -ForegroundColor Red
        & docker logs --tail 40 $Name
        exit 1
    }
} while ($health -ne "healthy")

$url = "http://localhost:$Port"
& docker exec $Name test -f /data/auth.json 2>$null
if ($LASTEXITCODE -ne 0) {
    $code = (& docker logs $Name 2>&1 | Select-String -Pattern "Setup code: (\S+)" | Select-Object -Last 1)
    if ($code) {
        $value = $code.Matches[0].Groups[1].Value
        Write-Host ""
        Write-Host "First run: set the password with this setup code: $value" -ForegroundColor Yellow
        try {
            Set-Clipboard -Value $value
            Write-Host "(It is on the clipboard.)"
        } catch {
            # No clipboard (a server without a desktop): the code above is enough.
        }
    }
}

Write-Host "The web UI is at $url"
if (-not $NoBrowser) { Start-Process $url }
