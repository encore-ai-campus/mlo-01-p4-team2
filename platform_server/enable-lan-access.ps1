$ErrorActionPreference = 'Stop'
$ruleName = 'PLAYDATA-Campus-LAN-8004'
if (-not (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name $ruleName -DisplayName 'PLAYDATA Campus LAN 8004' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8004 -RemoteAddress LocalSubnet -Profile Any | Out-Null
}
