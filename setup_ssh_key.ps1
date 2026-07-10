Write-Host "Setting up SSH key-based authentication..." -ForegroundColor Green

$pubkey = Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub"
$keyPath = 'C:\ProgramData\ssh\administrators_authorized_keys'

# Write the public key
Set-Content -Path $keyPath -Value $pubkey -NoNewline

# Copy ACL from an existing ssh key file
$acl = Get-Acl 'C:\ProgramData\ssh\ssh_host_ed25519_key.pub'
Set-Acl -Path $keyPath -AclObject $acl

Write-Host "SSH key installed! Restarting SSH service..." -ForegroundColor Green
Restart-Service sshd

Write-Host ""
Write-Host "=== Testing SSH with key ===" -ForegroundColor Cyan
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL spud9@localhost "echo SSH WITH KEY WORKS!"

Write-Host ""
Write-Host "Done!" -ForegroundColor Green
Pause