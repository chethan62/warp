# warp — one-line install for Windows.
#
#   irm https://raw.githubusercontent.com/chethan62/warp/main/packaging/install.ps1 | iex
#
# Installs warp under %LOCALAPPDATA%\Programs\warp, puts a `warp` command on your
# PATH, adds a Start Menu entry, and then runs it to prove it works. User-level
# only: no admin, nothing system-wide.
#
#   -Repo owner/name   install from a fork
#   -Dest C:\somewhere install elsewhere
#   -Tag  v1.0.1       a specific tag instead of main
#
# Note: this is meant to be piped into `iex`, so it never calls `exit` — that
# would close the console you ran it from. Failures throw.

[CmdletBinding()]
param(
    [string]$Repo = 'chethan62/warp',
    [string]$Dest = (Join-Path $env:LOCALAPPDATA 'Programs\warp'),
    [string]$Tag  = 'main'
)

$ErrorActionPreference = 'Stop'

function Say($m) { Write-Host "  $m" }

# ── 1. a Python new enough to run it ────────────────────────────────────────
function Test-Python($exe, $pre) {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $false }
    try {
        $out = (& $exe @pre --version 2>&1 | Out-String)
        return ($out -match 'Python 3\.(\d+)') -and ([int]$Matches[1] -ge 9)
    } catch { return $false }
}

$python = $null
if (Test-Python 'py' @('-3'))          { $python = @('py', '-3') }
elseif (Test-Python 'python' @())      { $python = @('python') }
elseif (Test-Python 'python3' @())     { $python = @('python3') }

if (-not $python) {
    Write-Host 'warp needs Python 3.9 or newer, and none was found.' -ForegroundColor Yellow
    Write-Host '  winget install Python.Python.3.12'
    return
}
Say "python: $($python -join ' ')"

# ── 2. the source, at the requested ref ─────────────────────────────────────
$work = Join-Path $env:TEMP ("warp-install-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work -Force | Out-Null
$zip = Join-Path $work 'src.zip'
Say "downloading $Repo@$Tag"
# /archive/<ref>.zip resolves for a branch or a tag, so one form covers both
Invoke-WebRequest -Uri "https://github.com/$Repo/archive/$Tag.zip" -OutFile $zip -UseBasicParsing
Expand-Archive -Path $zip -DestinationPath $work -Force

# the archive has one top-level directory: <repo>-<ref>
$src = Get-ChildItem -Path $work -Directory | Select-Object -First 1
if (-not $src) { throw "nothing extracted from $zip" }

if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
New-Item -ItemType Directory -Path $Dest -Force | Out-Null
Copy-Item -Path (Join-Path $src.FullName '*') -Destination $Dest -Recurse -Force
Say "installed to $Dest"

# ── 3. a command you can run ────────────────────────────────────────────────
$cmd = Join-Path $Dest 'warp.cmd'
@"
@echo off
set "PYTHONPATH=%~dp0"
$($python -join ' ') -m warp %*
"@ | Set-Content -Path $cmd -Encoding ASCII

# Start Menu entry, using the .ico that ships in the source tree
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\warp.lnk'
$ico = Join-Path $Dest 'packaging\warp.ico'
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = $cmd
$sc.WorkingDirectory = $Dest
$sc.Description = 'Route this computer through Cloudflare WARP, without root'
if (Test-Path $ico) { $sc.IconLocation = $ico }
$sc.Save()

# PATH is per-user, so no elevation. It only affects NEW shells.
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$Dest*") {
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$Dest", 'User')
    Say 'added to your PATH (open a new terminal to use `warp`)'
}

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue

# ── 4. prove it runs ────────────────────────────────────────────────────────
Say 'checking it actually runs'
# run the installed launcher, not python directly: this exercises warp.cmd and
# the PYTHONPATH it sets, which is the part that can silently be wrong
& $cmd selftest

Say 'run it with: warp        (or the Start Menu entry)'
