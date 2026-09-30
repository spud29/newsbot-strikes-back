# Register the NewsBot eval scheduler tasks in Windows Task Scheduler.
#
# Usage (run as administrator from PowerShell):
#     .\evals\scheduler\register_tasks.ps1
#
# Idempotent: unregisters any existing tasks of the same name before re-creating.

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$NightlyBat  = Join-Path $PSScriptRoot "run_nightly.bat"
$WeeklyBat   = Join-Path $PSScriptRoot "run_weekly.bat"

$NightlyName = "NewsBotTune"
$WeeklyName  = "NewsBotReport"

function Register-SafeTask($name, $bat, $trigger, $description) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Removing existing task '$name'..."
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }

    $action = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $ProjectRoot
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RunOnlyIfNetworkAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 6)

    Register-ScheduledTask `
        -TaskName $name `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description $description `
        -RunLevel Limited `
        -User $env:USERNAME | Out-Null

    Write-Host "Registered '$name'."
}

$nightlyTrigger = New-ScheduledTaskTrigger -Daily -At 3am
Register-SafeTask `
    -name        $NightlyName `
    -bat         $NightlyBat `
    -trigger     $nightlyTrigger `
    -description "NewsBot nightly categorization tuning agent"

$weeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 12pm
Register-SafeTask `
    -name        $WeeklyName `
    -bat         $WeeklyBat `
    -trigger     $weeklyTrigger `
    -description "NewsBot weekly categorization eval report"

Write-Host ""
Write-Host "Done. Verify with: Get-ScheduledTask -TaskName $NightlyName,$WeeklyName"
Write-Host "Test run with:    Start-ScheduledTask -TaskName $NightlyName"
