[CmdletBinding()]
param(
    # Set HACKLOOP_API_KEY in your environment, or pass the key explicitly.
    [string]$ApiKey = $env:HACKLOOP_API_KEY,
    [ValidateRange(1, 200)]
    [int]$Limit = 100,
    # Use -Reset to fetch all messages again without changing saved state first.
    [switch]$Reset,
    [string]$BaseUrl = 'https://api.start-hack.5u.ai'
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    throw 'Missing API key. Set HACKLOOP_API_KEY or invoke with -ApiKey hk_...'
}

$scriptDirectory = Split-Path -Parent $PSCommandPath
$repositoryDirectory = Split-Path -Parent $scriptDirectory
$stateDirectory = Join-Path $repositoryDirectory '.mailbox-state'
$statePath = Join-Path $stateDirectory 'cursor.json'
$sinceId = 0

if (-not $Reset -and (Test-Path -LiteralPath $statePath)) {
    try {
        $savedState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($null -ne $savedState.next_since_id) {
            $sinceId = [int64]$savedState.next_since_id
        }
    }
    catch {
        throw "Unable to read mailbox state at $statePath. Delete it or run with -Reset. $($_.Exception.Message)"
    }
}

$uri = "$($BaseUrl.TrimEnd('/'))/v1/mailbox/messages?since_id=$sinceId&limit=$Limit"
$headers = @{ 'X-API-Key' = $ApiKey }

try {
    $response = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers
}
catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    throw "Mailbox poll failed (HTTP $statusCode): $($_.ErrorDetails.Message)"
}

# The API guarantees next_since_id. Keep the whole response available in case the
# service adds fields, but print each received mail as a JSON line for piping/logging.
$messages = if ($null -eq $response.messages) { @() } else { @($response.messages) }
foreach ($message in $messages) {
    $message | ConvertTo-Json -Depth 20 -Compress
}

if ($null -ne $response.next_since_id) {
    New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
    [ordered]@{
        next_since_id = [int64]$response.next_since_id
        updated_at    = (Get-Date).ToUniversalTime().ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
}
else {
    Write-Warning 'The API response did not include next_since_id; cursor was not updated.'
}
