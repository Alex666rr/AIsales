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
        throw "Адрес AIsales должен быть корректным HTTPS-адресом."
    }

    if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne "https" -or
        -not [string]::IsNullOrEmpty($uri.UserInfo) -or
        -not [string]::IsNullOrEmpty($uri.Query) -or
        -not [string]::IsNullOrEmpty($uri.Fragment) -or
        ($uri.AbsolutePath -ne "/")) {
        throw "Адрес AIsales должен быть HTTPS-адресом без пути, параметров и фрагмента."
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
        throw "$Prompt не может быть пустым."
    }
    return $Value.Trim()
}

try {
    $resolvedBaseUrl = Resolve-HttpsBaseUrl -Candidate $BaseUrl
    $organizationName = Read-RequiredValue -Prompt "Название организации" -Value $OrganizationName
    $ownerEmail = Read-RequiredValue -Prompt "Рабочий email первого владельца" -Value $OwnerEmail

    if ($ownerEmail -notmatch "^[^@\s]+@[^@\s]+\.[^@\s]+$") {
        throw "Укажите корректный рабочий email."
    }

    $secureToken = Read-Host "Токен владельца платформы (не отображается)" -AsSecureString
    $tokenPointer = [IntPtr]::Zero

    try {
        $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
        $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
        if ([string]::IsNullOrWhiteSpace($token)) {
            throw "Токен не может быть пустым."
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
            throw "Сервер не вернул одноразовую ссылку настройки."
        }

        $setupUrl = "$resolvedBaseUrl/setup?token=$([uri]::EscapeDataString($setupToken))"
        Write-Output "Первый владелец создан для $([string]$response.owner_email)."
        Write-Output "Откройте эту одноразовую ссылку в этом браузере:"
        Write-Output $setupUrl
        Write-Output "Ссылка действует 48 часов. Не пересылайте её в чат и не сохраняйте в скриншотах."
    }
    finally {
        if ($tokenPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
        }
        Remove-Variable token -ErrorAction SilentlyContinue
    }
}
catch {
    Write-Error "Не удалось создать первого владельца. Проверьте HTTPS-адрес AIsales, данные и токен в Railway."
    exit 1
}
