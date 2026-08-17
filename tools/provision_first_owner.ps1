#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$BaseUrl = "https://aisales-production-d3b0.up.railway.app",
    [string]$OrganizationName,
    [string]$OwnerEmail
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-HttpsBaseUrl {
    param([Parameter(Mandatory = $true)][string]$Candidate)

    try {
        $uri = [uri]$Candidate
    }
    catch {
        throw "AIsales URL must be a valid HTTPS URL."
    }

    if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne "https" -or
        -not [string]::IsNullOrEmpty($uri.UserInfo) -or
        -not [string]::IsNullOrEmpty($uri.Query) -or
        -not [string]::IsNullOrEmpty($uri.Fragment) -or
        ($uri.AbsolutePath -ne "/")) {
        throw "AIsales URL must use HTTPS and contain no path, query, or fragment."
    }

    return $uri.GetLeftPart([System.UriPartial]::Authority)
}

function Read-RequiredValue {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        $Value = Read-Host $Prompt
    }
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "$Prompt cannot be empty."
    }
    return $Value.Trim()
}

try {
    $resolvedBaseUrl = Resolve-HttpsBaseUrl -Candidate $BaseUrl
    $organizationName = Read-RequiredValue -Prompt "Organization name" -Value $OrganizationName
    $ownerEmail = Read-RequiredValue -Prompt "First owner work email" -Value $OwnerEmail

    if ($ownerEmail -notmatch "^[^@\s]+@[^@\s]+\.[^@\s]+$") {
        throw "Enter a valid work email address."
    }

    $secureToken = Read-Host "Platform owner token (hidden)" -AsSecureString
    $tokenPointer = [IntPtr]::Zero

    try {
        $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
        $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
        if ([string]::IsNullOrWhiteSpace($token)) {
            throw "Platform owner token cannot be empty."
        }

        $payload = @{
            organization_name = $organizationName
            owner_email       = $ownerEmail
        } | ConvertTo-Json -Compress

        $response = Invoke-RestMethod `
            -Uri "$resolvedBaseUrl/platform/organizations" `
            -Method Post `
            -ContentType "application/json" `
            -Headers @{ Authorization = "Bearer $token" } `
            -Body $payload

        $setupToken = [string]$response.setup_token
        if ([string]::IsNullOrWhiteSpace($setupToken)) {
            throw "The server did not return a one-time setup link."
        }

        $setupUrl = "$resolvedBaseUrl/setup?token=$([uri]::EscapeDataString($setupToken))"
        Write-Output "First owner created for $([string]$response.owner_email)."
        Write-Output "Open this one-time link in the current browser:"
        Write-Output $setupUrl
        Write-Output "The link expires in 48 hours. Do not share it in chat or screenshots."
    }
    finally {
        if ($tokenPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
        }
        Remove-Variable token -ErrorAction SilentlyContinue
    }
}
catch {
    Write-Error "Could not create the first owner. Check the AIsales HTTPS URL, details, and Railway token."
    exit 1
}
