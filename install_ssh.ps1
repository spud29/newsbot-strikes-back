Write-Host "Checking OpenSSH Server status..."
$cap = Get-WindowsCapability -Online | Where-Object { $_.Name -like 'OpenSSH.Server*' }
Write-Host "Current state: $($cap.State)"

if ($cap.State -ne 'Installed') {
    Write-Host "Installing OpenSSH Server..."
    Add-WindowsCapability -Online -Name $cap.Name
    Write-Host "Installation complete!"
}

Write-Host "Setting SSH service to start automatically..."
Set-Service sshd -StartupType 'Automatic'

Write-Host "Starting SSH service..."
Start-Service sshd

Write-Host "Opening firewall port for SSH..."
New-NetFirewallRule -DisplayName 'OpenSSH Server (sshd)' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== SSH Server Status ==="
Get-Service sshd | Format-List Name, Status, StartType

Write-Host ""
Write-Host "=== Testing SSH (should work locally) ==="
ssh spud29@localhost "echo SSH is working!"

Pause