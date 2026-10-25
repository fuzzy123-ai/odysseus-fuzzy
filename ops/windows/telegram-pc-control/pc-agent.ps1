[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $env:LOCALAPPDATA 'Odysseus\PcAgent\config.json'),
    [switch]$Once
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$AgentVersion = '1.0'
$AllowedActions = @('start_codex', 'lock', 'hibernate', 'shutdown')
$InstallRoot = Split-Path -Parent $ConfigPath
$StatusPath = Join-Path $InstallRoot 'agent-status.json'
$ReceiptPath = Join-Path $InstallRoot 'action-receipts.json'
$Utf8 = [System.Text.UTF8Encoding]::new($false)

function Write-AgentStatus {
    param([Parameter(Mandatory)][string]$Status)
    $safe = [ordered]@{
        schema_version = 1
        status = $Status
        recorded_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        raw_content_visible = $false
        secret_values_visible = $false
    }
    [System.IO.File]::WriteAllText($StatusPath, ($safe | ConvertTo-Json -Compress), $Utf8)
}

function Get-Sha256Hex {
    param([Parameter(Mandatory)][byte[]]$Bytes)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return -join ($sha.ComputeHash($Bytes) | ForEach-Object { $_.ToString('x2') })
    }
    finally {
        $sha.Dispose()
    }
}

function Get-HmacHex {
    param(
        [Parameter(Mandatory)][string]$Secret,
        [Parameter(Mandatory)][string]$Material
    )
    $hmac = [System.Security.Cryptography.HMACSHA256]::new($Utf8.GetBytes($Secret))
    try {
        return -join ($hmac.ComputeHash($Utf8.GetBytes($Material)) | ForEach-Object { $_.ToString('x2') })
    }
    finally {
        $hmac.Dispose()
    }
}

function Test-FixedTimeText {
    param(
        [Parameter(Mandatory)][string]$Left,
        [Parameter(Mandatory)][string]$Right
    )
    $leftBytes = $Utf8.GetBytes($Left)
    $rightBytes = $Utf8.GetBytes($Right)
    $difference = $leftBytes.Length -bxor $rightBytes.Length
    $count = [Math]::Max($leftBytes.Length, $rightBytes.Length)
    for ($index = 0; $index -lt $count; $index++) {
        $leftValue = if ($index -lt $leftBytes.Length) { $leftBytes[$index] } else { 0 }
        $rightValue = if ($index -lt $rightBytes.Length) { $rightBytes[$index] } else { 0 }
        $difference = $difference -bor ($leftValue -bxor $rightValue)
    }
    return $difference -eq 0
}

function New-AgentNonce {
    $bytes = [byte[]]::new(24)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Read-AgentSecret {
    param([Parameter(Mandatory)][string]$SecretPath)
    $protected = [System.IO.File]::ReadAllText($SecretPath, $Utf8).Trim()
    $secure = ConvertTo-SecureString $protected
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Invoke-SignedJsonRequest {
    param(
        [Parameter(Mandatory)][string]$ServerBaseUrl,
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][hashtable]$Payload,
        [Parameter(Mandatory)][string]$Secret
    )
    $bodyText = $Payload | ConvertTo-Json -Compress -Depth 4
    $bodyBytes = $Utf8.GetBytes($bodyText)
    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds().ToString()
    $nonce = New-AgentNonce
    $bodyHash = Get-Sha256Hex -Bytes $bodyBytes
    $material = "v1`n$timestamp`n$nonce`nPOST`n$Path`n$bodyHash"
    $signature = Get-HmacHex -Secret $Secret -Material $material
    $uri = $ServerBaseUrl.TrimEnd('/') + $Path
    try {
        $response = Invoke-WebRequest -Method Post -Uri $uri -ContentType 'application/json' -Body $bodyBytes -Headers @{
            'X-Odysseus-Pc-Timestamp' = $timestamp
            'X-Odysseus-Pc-Nonce' = $nonce
            'X-Odysseus-Pc-Signature' = $signature
        }
    }
    catch {
        throw [InvalidOperationException]::new('request_failed')
    }
    if ([int]$response.StatusCode -ne 200) {
        throw [InvalidOperationException]::new('request_rejected')
    }
    $responseBytes = $Utf8.GetBytes([string]$response.Content)
    $responseHash = Get-Sha256Hex -Bytes $responseBytes
    $expected = Get-HmacHex -Secret $Secret -Material "v1`n$nonce`n200`n$responseHash"
    $received = [string]$response.Headers['X-Odysseus-Pc-Response-Signature']
    if (-not (Test-FixedTimeText -Left $expected -Right $received)) {
        throw [InvalidOperationException]::new('response_auth_invalid')
    }
    try {
        return $response.Content | ConvertFrom-Json
    }
    catch {
        throw [InvalidOperationException]::new('response_invalid')
    }
}

function Read-Receipts {
    if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) {
        return @()
    }
    try {
        $value = Get-Content -Raw -LiteralPath $ReceiptPath | ConvertFrom-Json
        return @($value.action_ids | Where-Object { $_ -is [string] -and $_ -match '^[A-Za-z0-9][A-Za-z0-9_-]{5,63}$' })
    }
    catch {
        return @()
    }
}

function Add-Receipt {
    param([Parameter(Mandatory)][string]$ActionId)
    $ids = @((Read-Receipts) + $ActionId | Select-Object -Unique | Select-Object -Last 100)
    $value = [ordered]@{
        schema_version = 1
        action_ids = $ids
        raw_content_visible = $false
    }
    [System.IO.File]::WriteAllText($ReceiptPath, ($value | ConvertTo-Json -Compress), $Utf8)
}

function Test-CodexRunning {
    return $null -ne (Get-Process -Name 'ChatGPT' -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Start-CodexIfNeeded {
    if (-not [Environment]::UserInteractive) {
        return @{ outcome = 'failed'; code = 'interactive_session_required' }
    }
    if (Test-CodexRunning) {
        return @{ outcome = 'completed'; code = 'codex_already_running' }
    }
    Start-Process -FilePath (Join-Path $env:SystemRoot 'explorer.exe') -ArgumentList 'shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App'
    return @{ outcome = 'completed'; code = 'codex_started' }
}

function Invoke-AgentAction {
    param([Parameter(Mandatory)][string]$Action)
    switch ($Action) {
        'start_codex' { return Start-CodexIfNeeded }
        'lock' {
            Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\rundll32.exe') -ArgumentList 'user32.dll,LockWorkStation'
            return @{ outcome = 'completed'; code = 'locked' }
        }
        'hibernate' {
            $command = 'Start-Sleep -Seconds 5; & "$env:SystemRoot\System32\shutdown.exe" /h'
            Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -WindowStyle Hidden -ArgumentList @('-NoProfile', '-NonInteractive', '-Command', $command)
            return @{ outcome = 'completed'; code = 'hibernate_started' }
        }
        'shutdown' {
            $process = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\shutdown.exe') -ArgumentList @('/s', '/t', '10', '/d', 'p:0:0', '/c', 'Odysseus Telegram PC control') -PassThru -Wait
            if ($process.ExitCode -ne 0) {
                return @{ outcome = 'failed'; code = 'execution_failed' }
            }
            return @{ outcome = 'completed'; code = 'shutdown_started' }
        }
        default { return @{ outcome = 'failed'; code = 'unsupported_action' } }
    }
}

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw [InvalidOperationException]::new('config_missing')
}

try {
    $config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
    $serverUri = [Uri][string]$config.server_base_url
    $secretPath = [string]$config.secret_file
    $pollSeconds = [Math]::Max(3, [Math]::Min(60, [int]$config.poll_seconds))
    $startCodexOnLaunch = $config.start_codex_on_launch -eq $true
    $httpLocal = $serverUri.Scheme -eq 'http' -and $serverUri.Host -in @('127.0.0.1', 'localhost', '::1')
    if (($serverUri.Scheme -ne 'https' -and -not $httpLocal) -or -not $serverUri.IsAbsoluteUri -or $serverUri.AbsolutePath -notin @('', '/') -or -not (Test-Path -LiteralPath $secretPath -PathType Leaf)) {
        throw [InvalidOperationException]::new('config_invalid')
    }
}
catch {
    Write-AgentStatus -Status 'config_invalid'
    throw [InvalidOperationException]::new('config_invalid')
}

$secret = Read-AgentSecret -SecretPath $secretPath
if ($secret.Length -lt 24) {
    Write-AgentStatus -Status 'secret_invalid'
    throw [InvalidOperationException]::new('secret_invalid')
}

if ($startCodexOnLaunch) {
    try { $null = Start-CodexIfNeeded } catch { Write-AgentStatus -Status 'codex_start_failed' }
}

do {
    try {
        $poll = Invoke-SignedJsonRequest -ServerBaseUrl $serverUri.AbsoluteUri -Path '/api/plugins/telegram/pc-agent/poll' -Secret $secret -Payload ([ordered]@{
            agent_version = $AgentVersion
            interactive_session = [Environment]::UserInteractive
            codex_running = (Test-CodexRunning)
        })
        Write-AgentStatus -Status 'online'
        if ($null -ne $poll.command) {
            $actionId = [string]$poll.command.action_id
            $action = [string]$poll.command.action
            $expiresAt = [long]$poll.command.expires_at
            $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
            if ($actionId -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{5,63}$' -or $action -notin $AllowedActions -or $expiresAt -le $now) {
                Write-AgentStatus -Status 'command_rejected'
            }
            elseif ($actionId -in (Read-Receipts)) {
                $null = Invoke-SignedJsonRequest -ServerBaseUrl $serverUri.AbsoluteUri -Path '/api/plugins/telegram/pc-agent/result' -Secret $secret -Payload ([ordered]@{
                    action_id = $actionId
                    outcome = 'completed'
                    code = 'duplicate_action'
                })
            }
            else {
                try {
                    $result = Invoke-AgentAction -Action $action
                }
                catch {
                    $result = @{ outcome = 'failed'; code = 'execution_failed' }
                }
                if ($result.outcome -eq 'completed') { Add-Receipt -ActionId $actionId }
                $null = Invoke-SignedJsonRequest -ServerBaseUrl $serverUri.AbsoluteUri -Path '/api/plugins/telegram/pc-agent/result' -Secret $secret -Payload ([ordered]@{
                    action_id = $actionId
                    outcome = [string]$result.outcome
                    code = [string]$result.code
                })
                Write-AgentStatus -Status ([string]$result.code)
            }
        }
    }
    catch {
        Write-AgentStatus -Status 'poll_failed'
    }
    if (-not $Once) { Start-Sleep -Seconds $pollSeconds }
} while (-not $Once)

$secret = $null
