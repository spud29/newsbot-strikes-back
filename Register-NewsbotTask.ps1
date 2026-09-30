# Register-NewsbotTask.ps1
# Registers a Windows Task Scheduler job that launches Newsbot Strikes Back
# on every system startup. Compatible with Windows PowerShell 5.1.
# Run once with: powershell -ExecutionPolicy Bypass -File ".\Register-NewsbotTask.ps1"

$TaskName   = "Newsbot Strikes Back"
$Description = "Launches main.py for Newsbot Strikes Back on system startup."
$ScriptDir  = "C:\Users\spud9\OneDrive\Documents\newsbot strikes back"
$ScriptFile = "main.py"

# ---------------------------------------------------------------------------
# Detect python executable
# ---------------------------------------------------------------------------
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($PythonCmd) {
    $PythonExe = $PythonCmd.Source
} else {
    $PyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($PyCmd) {
        $PythonExe = $PyCmd.Source
    } else {
        Write-Error "Could not locate python or py on PATH. Install Python or set PythonExe manually."
        exit 1
    }
}

Write-Host "Using Python: $PythonExe"

# ---------------------------------------------------------------------------
# Build task components
# ---------------------------------------------------------------------------
$Action = New-ScheduledTaskAction `
    -Execute          $PythonExe `
    -Argument         "`"$ScriptFile`"" `
    -WorkingDirectory $ScriptDir

$Trigger = New-ScheduledTaskTrigger -AtStartup

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit  (New-TimeSpan -Hours 0) `
    -RestartCount        3 `
    -RestartInterval     (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

$Principal = New-ScheduledTaskPrincipal `
    -UserId    $env:USERNAME `
    -LogonType Interactive

# ---------------------------------------------------------------------------
# Register (or replace) the task
# ---------------------------------------------------------------------------
$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($ExistingTask) {
    Write-Host "Task '$TaskName' already exists - replacing it."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName    $TaskName `
    -Description $Description `
    -Action      $Action `
    -Trigger     $Trigger `
    -Settings    $Settings `
    -Principal   $Principal

Write-Host ""
Write-Host "Task '$TaskName' registered successfully." -ForegroundColor Green
Write-Host "It will run '$PythonExe ""$ScriptFile""' from '$ScriptDir' on every startup."
