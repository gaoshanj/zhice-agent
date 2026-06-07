# Push updated files to GitHub via API
param(
    [string]$Token
)

$headers = @{
    Authorization = "token $Token"
    Accept = "application/vnd.github.v3+json"
}

$repo = "gaoshanj/zhice-agent"
$files = @(
    "requirements.txt",
    "docs/azure-app-service-deploy.md"
)

# Also delete startup.txt if it exists on remote
Write-Output "=== Pushing files to GitHub ==="

foreach ($file in $files) {
    Write-Output "Processing: $file"
    
    # Read file content
    $content = Get-Content $file -Raw -Encoding UTF8
    $contentB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($content))
    
    # Check if file exists (to get SHA)
    $encodedPath = [System.Uri]::EscapeDataString($file)
    $url = "https://api.github.com/repos/$repo/contents/$encodedPath"
    
    $existing = $null
    try {
        $existing = Invoke-RestMethod -Uri $url -Headers $headers -Method Get -ErrorAction Stop
    } catch {
        # File doesn't exist
    }
    
    $body = @{
        message = "Add/Update $file for Azure App Service deployment"
        content = $contentB64
        branch = "main"
    }
    
    if ($existing -and $existing.sha) {
        $body.sha = $existing.sha
        Write-Output "  -> Updating existing file (SHA: $($existing.sha.Substring(0,7)))"
    } else {
        Write-Output "  -> Creating new file"
    }
    
    $jsonBody = $body | ConvertTo-Json
    
    try {
        $resp = Invoke-RestMethod -Uri $url -Method Put -Headers $headers -Body $jsonBody -ContentType "application/json"
        Write-Output "  ✅ Success: $file"
    } catch {
        $errBody = $_.ErrorDetails.Message | ConvertFrom-Json -ErrorAction SilentlyContinue
        Write-Output "  ❌ Failed: $($errBody.message)"
        Write-Output "     $($errBody | Out-String)"
    }
}

# Delete startup.txt from remote if it exists
Write-Output "`n=== Deleting startup.txt from remote (if exists) ==="
$startupUrl = "https://api.github.com/repos/$repo/contents/startup.txt"
$existing = $null
try {
    $existing = Invoke-RestMethod -Uri $startupUrl -Headers $headers -Method Get -ErrorAction Stop
} catch {
    Write-Output "  startup.txt not found on remote (already clean)"
}

if ($existing -and $existing.sha) {
    $delBody = @{
        message = "Remove startup.txt (not needed for App Service)"
        sha = $existing.sha
        branch = "main"
    } | ConvertTo-Json
    
    try {
        Invoke-RestMethod -Uri $startupUrl -Method Delete -Headers $headers -Body $delBody -ContentType "application/json"
        Write-Output "  ✅ Deleted startup.txt from remote"
    } catch {
        Write-Output "  ❌ Failed to delete: $_"
    }
}

Write-Output "`n=== Done ==="
