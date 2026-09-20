# token-usage statusline example (Windows / PowerShell) — session cost + top activity.
#
# Setup: point Cursor or Claude Code statusline at this script (dependency-free):
#   "command": "pwsh -NoProfile -File C:/path/to/token-usage/examples/statusline.ps1"
#
# Reads the live ledger symlink maintained by Stop/SubagentStop hooks:
#   $env:TOKEN_USAGE_LEDGER_DIR/latest.json  (override directory)
#   or ~/.cache/token-usage/latest.json
#
# Missing or malformed ledgers exit 0 with no output (never blocks the editor).

$ErrorActionPreference = 'Stop'

function Get-UserHome {
    if ($env:USERPROFILE) { return $env:USERPROFILE }
    if ($env:HOME) { return $env:HOME }
    return [Environment]::GetFolderPath('UserProfile')
}

function Get-LedgerPath {
    if ($env:TOKEN_USAGE_LEDGER_DIR) {
        return Join-Path $env:TOKEN_USAGE_LEDGER_DIR 'latest.json'
    }
    return Join-Path (Join-Path (Get-UserHome) '.cache\token-usage') 'latest.json'
}

function Format-TokenCount([double]$n) {
    if ($n -ge 1000000) {
        $v = [math]::Round($n / 1000000 * 10) / 10
        return "{0}M" -f $v
    }
    if ($n -ge 1000) {
        $v = [math]::Round($n / 1000 * 10) / 10
        return "{0}k" -f $v
    }
    return [string][int]$n
}

function Format-Cost($cost) {
    if ($null -eq $cost) { return '?' }
    try {
        $d = [double]$cost
    } catch {
        return '?'
    }
    if (-not [double]::IsFinite($d)) { return '?' }
    $rounded = [math]::Round($d * 100) / 100
    return ('${0}' -f $rounded)
}

try {
    $ledgerPath = Get-LedgerPath
    if (-not (Test-Path -LiteralPath $ledgerPath)) { exit 0 }

    $raw = Get-Content -LiteralPath $ledgerPath -Raw -ErrorAction Stop
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

    $data = $raw | ConvertFrom-Json -ErrorAction Stop
    if ($null -eq $data -or $null -eq $data.total) { exit 0 }

    $outTokens = 0
    if ($null -ne $data.total.usage -and $null -ne $data.total.usage.output) {
        $outTokens = [double]$data.total.usage.output
    }
    $outFmt = Format-TokenCount $outTokens
    $costFmt = Format-Cost $data.total.cost_usd

    $topLabel = $null
    if ($null -ne $data.by_label) {
        $bestCost = [double]::NegativeInfinity
        foreach ($prop in $data.by_label.PSObject.Properties) {
            $entry = $prop.Value
            $labelCost = 0.0
            if ($null -ne $entry -and $null -ne $entry.cost_usd) {
                try { $labelCost = [double]$entry.cost_usd } catch { $labelCost = 0.0 }
            }
            if ($labelCost -gt $bestCost) {
                $bestCost = $labelCost
                $topLabel = $prop.Name
            }
        }
    }

    $line = "⏶ $outFmt out · $costFmt"
    if ($topLabel) { $line += " · top: $topLabel" }
    Write-Output $line
    exit 0
} catch {
    exit 0
}
