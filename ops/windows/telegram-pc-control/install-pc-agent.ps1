[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(ParameterSetName = 'Install', Mandatory)]
    [Uri]$ServerBaseUrl,
    [Parameter(ParameterSetName = 'Install')]
    [Security.SecureString]$AgentSecret,
    [Parameter(ParameterSetName = 'Install')]
    [ValidateRange(3, 60)]
    [int]$PollSeconds = 5,
    [Parameter(ParameterSetName = 'Install')]
    [bool]$StartCodexOnLaunch = $true,
    [Parameter(ParameterSetName = 'Install')]
    [switch]$Replace,
    [Parameter(ParameterSetName = 'Uninstall', Mandatory)]
    [switch]$Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'Odysseus Telegram PC Agent'
$InstallRoot = Join-Path $env:LOCALAPPDATA 'Odysseus\PcAgent'
$InstalledAgent = Join-Path $InstallRoot 'pc-agent.ps1'
$ConfigPath = Join-Path $InstallRoot 'config.json'
$SecretPath = Join-Path $InstallRoot 'agent-secret.dpapi'
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if ($Uninstall) {
    if ($PSCmdlet.ShouldProcess($TaskName, 'Unregister scheduled task and archive agent files')) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $InstallRoot -PathType Container) {
            $archive = "$InstallRoot.disabled-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
            Move-Item -LiteralPath $InstallRoot -Destination $archive
        }
    }
    return
}

$httpLocal = $ServerBaseUrl.Scheme -eq 'http' -and $ServerBaseUrl.Host -in @('127.0.0.1', 'localhost', '::1')
if (($ServerBaseUrl.Scheme -ne 'https' -and -not $httpLocal) -or -not $ServerBaseUrl.IsAbsoluteUri -or $ServerBaseUrl.AbsolutePath -notin @('', '/') -or $ServerBaseUrl.Query -or $ServerBaseUrl.Fragment) {
    throw [InvalidOperationException]::new('server_url_invalid')
}
if ($null -eq $AgentSecret) {
    $AgentSecret = Read-Host 'Windows-PC-Agent-Secret' -AsSecureString
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing -and -not $Replace) {
    throw [InvalidOperationException]::new('scheduled_task_exists_use_replace')
}

if ($PSCmdlet.ShouldProcess($InstallRoot, 'Install DPAPI-protected PC agent and logon task')) {
    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'pc-agent.ps1') -Destination $InstalledAgent -Force
    $AgentSecret | ConvertFrom-SecureString | Set-Content -LiteralPath $SecretPath -Encoding ASCII
    $config = [ordered]@{
        schema_version = 1
        server_base_url = $ServerBaseUrl.AbsoluteUri.TrimEnd('/')
        secret_file = $SecretPath
        poll_seconds = $PollSeconds
        start_codex_on_launch = $StartCodexOnLaunch
        raw_content_visible = $false
        secret_values_visible = $false
    }
    $config | ConvertTo-Json | Set-Content -LiteralPath $ConfigPath -Encoding UTF8

    if ($null -ne $existing) {
        Export-ScheduledTask -TaskName $TaskName | Set-Content -LiteralPath (Join-Path $InstallRoot 'previous-task.xml') -Encoding UTF8
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$InstalledAgent`" -ConfigPath `"$ConfigPath`""
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
    $principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
}
