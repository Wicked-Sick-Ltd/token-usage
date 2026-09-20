# token-usage statusline example (Windows / PowerShell 7+) — Claude Code session
# cost + top activity.
#
# Requires PowerShell 7 or newer (`pwsh`). Windows PowerShell 5.1 is not supported.
#
# Setup (Claude Code on Windows): point /statusline at this script (dependency-free):
#   "command": "pwsh -NoProfile -File C:/path/to/token-usage/examples/statusline.ps1"
#
# Claude Code pipes its statusline JSON on stdin. This script reads `session_id`
# from it and resolves the ledger the Stop/SubagentStop hooks write, in order:
#   1. <ledger dir>/<session_id>.json — the current session's own aggregate.
#   2. <ledger dir>/latest.json — a pointer to the most recent session aggregate.
#      Used only as a fall back, when stdin carried no usable session id or that
#      session has no ledger yet. The hook creates it as a symlink on a
#      best-effort basis and Windows commonly refuses, so it is often absent;
#      the per-session path above is the reliable one.
#
# <ledger dir> is $env:TOKEN_USAGE_LEDGER_DIR when set, else ~/.cache/token-usage.
#
# Cursor hook storage is append-only JSONL and writes no per-session JSON or
# latest.json, so this is not a Cursor statusline. For a refreshing Cursor view
# in the terminal use:
#   python3 scripts/token_usage.py live --runtime cursor
#
# Missing or malformed input and ledgers exit 0 with no stdout/stderr, so a
# broken statusline never blocks the editor.

$ErrorActionPreference = 'Stop'

function Get-UserHome {
    if ($env:USERPROFILE) { return $env:USERPROFILE }
    if ($env:HOME) { return $env:HOME }
    return [Environment]::GetFolderPath('UserProfile')
}

function Get-LedgerDir {
    if ($env:TOKEN_USAGE_LEDGER_DIR) { return $env:TOKEN_USAGE_LEDGER_DIR }
    return (Join-Path (Join-Path (Get-UserHome) '.cache') 'token-usage')
}

function Get-StdinSessionId($raw) {
    # The hook names the ledger after the session id stripped to
    # [A-Za-z0-9_-]; applying the same filter here is what makes the two agree,
    # and it also keeps a hostile id from addressing a file outside the ledger
    # directory. An unusable id is $null, which falls through to latest.json.
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    try {
        $payload = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        return $null
    }
    if ($null -eq $payload) { return $null }
    $id = $payload.session_id
    if ($null -eq $id) { return $null }
    $clean = ([string]$id) -replace '[^A-Za-z0-9_-]', ''
    if ([string]::IsNullOrEmpty($clean)) { return $null }
    return $clean
}

function Resolve-LedgerPath($sessionId) {
    $dir = Get-LedgerDir
    if ($sessionId) {
        $session = Join-Path $dir "$sessionId.json"
        if (Test-Path -LiteralPath $session -PathType Leaf) { return $session }
    }
    $latest = Join-Path $dir 'latest.json'
    if (Test-Path -LiteralPath $latest -PathType Leaf) { return $latest }
    return $null
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
    $raw = ''
    try { $raw = [Console]::In.ReadToEnd() } catch { $raw = '' }

    $ledgerPath = Resolve-LedgerPath (Get-StdinSessionId $raw)
    if (-not $ledgerPath) { exit 0 }

    $body = Get-Content -LiteralPath $ledgerPath -Raw -ErrorAction Stop
    if ([string]::IsNullOrWhiteSpace($body)) { exit 0 }

    $data = $body | ConvertFrom-Json -ErrorAction Stop
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
