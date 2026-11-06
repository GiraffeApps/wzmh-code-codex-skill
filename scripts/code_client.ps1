# HTTP client for Windows devices without Python. Matches code_client.py's command shape.
$ErrorActionPreference = 'Stop'
$clientArgs = @($args)
if ($clientArgs.Count -lt 1) { throw 'Usage: code_client.ps1 health|upload|search|highlight [options]' }

$operation = $clientArgs[0]
$serverUrl = $env:WZMH_CODE_SERVER_URL
if (-not $serverUrl) { $serverUrl = 'http://127.0.0.1:8765' }
$serverUrl = $serverUrl.TrimEnd('/')
$serverUri = [Uri]$serverUrl
if ($serverUri.Scheme -notin @('http', 'https')) { throw 'WZMH_CODE_SERVER_URL must be an HTTP or HTTPS URL' }
$headers = @{}
if ($env:WZMH_CODE_SERVER_TOKEN) { $headers['Authorization'] = 'Bearer ' + $env:WZMH_CODE_SERVER_TOKEN }

function Invoke-CodeRequest([string]$endpoint, [object]$payload, [string]$contentType = 'application/json') {
    $request = @{
        Uri = $serverUrl + $endpoint
        Headers = $headers
        TimeoutSec = 240
        ErrorAction = 'Stop'
    }
    if ($null -eq $payload) {
        $request.Method = 'Get'
    } else {
        $request.Method = 'Post'
        $request.ContentType = $contentType
        if ($payload -is [byte[]]) {
            $request.Body = $payload
        } else {
            $json = ConvertTo-Json -InputObject $payload -Depth 12 -Compress
            $request.Body = [Text.Encoding]::UTF8.GetBytes($json)
        }
    }
    Invoke-RestMethod @request
}

switch ($operation) {
    'health' {
        if ($clientArgs.Count -eq 1) {
            $result = Invoke-CodeRequest '/health' $null
        } elseif ($clientArgs.Count -eq 2 -and $clientArgs[1] -eq '--full') {
            $result = Invoke-CodeRequest '/health/full' $null
        } else {
            throw 'Unexpected health arguments'
        }
        if ($result -isnot [pscustomobject]) { throw 'Health endpoint did not return JSON' }
        if ($clientArgs.Count -eq 1 -and ($result.status -ne 'ok' -or $null -eq $result.sources)) {
            throw 'Health endpoint did not return the WZMH Code server status'
        }
    }
    'upload' {
        if ($clientArgs.Count -ne 2) { throw 'Usage: upload <local-pdf-path>' }
        $pdfPath = [IO.Path]::GetFullPath($clientArgs[1])
        $result = Invoke-CodeRequest '/upload' ([IO.File]::ReadAllBytes($pdfPath)) 'application/pdf'
    }
    { $_ -in @('search', 'highlight') } {
        $sourceId = $null
        $excerptOutput = $null
        $forwardArgs = [System.Collections.Generic.List[string]]::new()
        for ($i = 1; $i -lt $clientArgs.Count; $i++) {
            if ($clientArgs[$i] -in @('--source-id', '--excerpt-output')) {
                if ($i + 1 -ge $clientArgs.Count) { throw "Missing value for $($clientArgs[$i])" }
                if ($clientArgs[$i] -eq '--source-id') { $sourceId = $clientArgs[++$i] }
                else { $excerptOutput = $clientArgs[++$i] }
            } else {
                $forwardArgs.Add($clientArgs[$i])
            }
        }
        if (-not $sourceId) { throw 'Missing --source-id' }
        if ($operation -eq 'search' -and $excerptOutput) { throw '--excerpt-output is only for highlight' }
        if ($operation -eq 'highlight' -and -not $excerptOutput) { throw 'Missing --excerpt-output' }
        $payload = @{ source_id = $sourceId; args = @($forwardArgs.ToArray()) }
        $result = Invoke-CodeRequest ('/' + $operation) $payload
        if ($operation -eq 'highlight') {
            $pdfBytes = [Convert]::FromBase64String([string]$result.pdf_base64)
            $sha = [Security.Cryptography.SHA256]::Create()
            try { $actualHash = [BitConverter]::ToString($sha.ComputeHash($pdfBytes)).Replace('-', '').ToLowerInvariant() }
            finally { $sha.Dispose() }
            if ($actualHash -ne [string]$result.sha256) { throw 'Downloaded excerpt checksum did not match' }
            if (-not $result.metadata.verified) { throw 'Server did not verify the excerpt' }
            $outputPath = [IO.Path]::GetFullPath($excerptOutput)
            if (Test-Path -LiteralPath $outputPath) { throw 'Excerpt output already exists' }
            $outputDir = [IO.Path]::GetDirectoryName($outputPath)
            [IO.Directory]::CreateDirectory($outputDir) | Out-Null
            [IO.File]::WriteAllBytes($outputPath, $pdfBytes)
            $result = $result.metadata
            $result | Add-Member -NotePropertyName preview -NotePropertyValue $outputPath -Force
            $fragment = ([string]$result.browser_url).Split('#', 2)[1]
            $result.browser_url = ([Uri]$outputPath).AbsoluteUri + $(if ($fragment) { '#' + $fragment } else { '' })
        }
    }
    default { throw "Unknown operation: $operation" }
}

ConvertTo-Json -InputObject $result -Depth 20 -Compress
