# Sync atr_grid code + latest HTML to private GitHub repo qhgy/atr-grid
# Usage: pwsh -File bin\sync-atr-grid.ps1 [-Message "optional commit message"]
param([string]$Message = "")

$SRC = "D:\000trae\A股数据\aaa"
$REPO = "D:\000trae\atr-grid-repo"

# Files/dirs to sync
$items = @(
    @{from = "atr_grid"; to = "atr_grid"; type = "dir"},
    @{from = "core";     to = "core";     type = "dir"},
    @{from = "tests\atr_grid"; to = "tests\atr_grid"; type = "dir"},
    @{from = "pyproject.toml"; to = "pyproject.toml"; type = "file"},
    @{from = "uv.lock";        to = "uv.lock";        type = "file"}
)

foreach ($item in $items) {
    $from = Join-Path $SRC $item.from
    $to   = Join-Path $REPO $item.to
    if ($item.type -eq "dir") {
        $parent = Split-Path $to
        New-Item $parent -ItemType Directory -Force | Out-Null
        # Robocopy: /MIR syncs additions + deletions; exclude secrets and caches
        robocopy $from $to /MIR /XD __pycache__ .vscode .specstory /XF "*.cookie" "*_cookies.txt" "xueqiu*.txt" /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
    } else {
        Copy-Item $from $to -Force
    }
}

# Sync latest HTML dashboard
$html = Join-Path $SRC "output\atr_grid.html"
if (Test-Path $html) {
    New-Item "$REPO\docs" -ItemType Directory -Force | Out-Null
    Copy-Item $html "$REPO\docs\index.html" -Force
    Write-Host "HTML dashboard synced -> docs/index.html"
}

# Git commit and push
Push-Location $REPO
git add . 2>&1 | Out-Null
$status = git status --porcelain
if (-not $status) {
    Write-Host "Nothing to commit, up to date."
    Pop-Location; exit 0
}

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm"
$msg = if ($Message) { $Message } else { "sync: $stamp" }
git commit -m "$msg`n`nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" 2>&1
# Use gh token for push (avoids credential manager hang)
$token = gh auth token 2>&1
git remote set-url origin "https://nb95276:$token@github.com/qhgy/atr-grid.git"
git push origin main 2>&1
git remote set-url origin "https://github.com/qhgy/atr-grid.git"
Pop-Location

Write-Host "`nDone. https://github.com/qhgy/atr-grid"
