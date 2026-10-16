# Turns the background Gmail sync on or off (Windows Task Scheduler, current user only).
# On: syncs every morning at 9:00 and each time you sign in to Windows, with no window.
# Each run appends one line to sync.log in the data folder.
param([switch]$Off)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$name = 'Job Search Analytics - Gmail sync'

if ($Off) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host '  Automatic sync is now OFF.'
    exit 0
}

$python = Join-Path $root 'venv\Scripts\pythonw.exe'
if (-not (Test-Path $python)) {
    Write-Host '  Run update.bat once first (it sets up Python), then run this again.'
    exit 1
}

$action = New-ScheduledTaskAction -Execute $python -Argument '-m jobtracker sync --background' `
    -WorkingDirectory $root
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$daily = New-ScheduledTaskTrigger -Daily -At 9am
$description = 'Reads new job-application emails so the tracker is up to date when you open it.'

try {
    $logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    Register-ScheduledTask -TaskName $name -Action $action -Trigger @($daily, $logon) `
        -Settings $settings -Description $description -Force | Out-Null
    Write-Host '  Automatic sync is ON: every morning at 9:00 and whenever you sign in.'
} catch {
    # Some Windows setups only allow sign-in triggers for administrators; daily still works.
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $daily `
        -Settings $settings -Description $description -Force | Out-Null
    Write-Host '  Automatic sync is ON: every morning at 9:00 (or as soon as the PC is on).'
}
Write-Host '  Turn it off any time with: auto_sync.bat off'
