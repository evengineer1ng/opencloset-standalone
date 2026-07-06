# Oracle Radio — recurring local safety-net snapshot.
# Commits whatever is in the working tree (even WIP/broken) so work is never lost.
# These "auto: snapshot" commits are time-machine points; real, tested commits are
# made by hand. Only commits when something changed. Logs OUTSIDE the repo so the
# log itself never creates a diff. No push (add one here once a remote exists).

$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\evana\OneDrive\Documents\oracle-radio'
$log  = Join-Path $env:TEMP 'oracle-radio-autocommit.log'

function Write-Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | Out-File -FilePath $log -Append -Encoding utf8
}

try {
    $git = (Get-Command git -ErrorAction Stop).Source
    Set-Location $repo

    $changes = & $git status --porcelain
    if ([string]::IsNullOrWhiteSpace($changes)) {
        return   # nothing to snapshot
    }

    & $git add -A
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    & $git commit -q -m "auto: snapshot $ts"
    Write-Log "committed snapshot ($((($changes -split "`n").Count)) paths changed)"
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
}
