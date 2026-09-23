<#
.SYNOPSIS
    Tag the current commit and publish a GitHub release for the version in VERSION.

.DESCRIPTION
    Reads the version from the VERSION file (or -Version), extracts that version's
    section from docs/CHANGELOG.md as the release notes, creates and pushes an
    annotated git tag (vX.Y), then creates the GitHub release with gh and attaches
    the packaged ZIP (pwllheli_race_officer_vX_Y.zip in the repo root).

    Build the ZIP first (the release build step) so it exists before running this.

    Re-running after a part-finished release is safe: an existing tag is reused,
    and an existing release has its notes and ZIP updated in place rather than
    failing with "release already exists".

    The GitHub "Latest" badge is worked out, not assumed: it is only claimed when
    this is the highest published version, so filling in an older release does not
    advertise it as current. -DryRun prints which way it will go.

.PARAMETER Version
    Override the version. Defaults to the contents of the VERSION file.

.PARAMETER Draft
    Create the release as a draft instead of publishing it.

.PARAMETER DryRun
    Print what would happen (version, tag, ZIP, "Latest" badge, extracted notes)
    and exit without tagging, pushing or creating anything.

.EXAMPLE
    .\release.ps1 -DryRun
    .\release.ps1
    .\release.ps1 -Version 0.165 -Draft
#>
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$Draft,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Fail($msg) { Write-Error $msg; exit 1 }

function Invoke-Native {
    <#
      Run a native executable, failing only on a non-zero exit code.

      Windows PowerShell turns anything a native command writes to stderr into an
      ErrorRecord, and with $ErrorActionPreference = "Stop" that terminates the
      script even when the command succeeded. `git push` writes its normal
      progress ("To https://github.com/...") to stderr, so v0.193 died at the push
      *after* the tag had already gone up, leaving a tag with no release. Exit
      code is the only trustworthy signal for a native command, so check that and
      let stderr through as ordinary text.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Arguments = @(),
        [string]$What = ""
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object { Write-Output "$_" }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) {
        if (-not $What) { $What = "$Exe $($Arguments -join ' ')" }
        Fail "$What failed (exit $code)."
    }
}

function Test-NativeOk {
    # True when the command exits 0. For asking a question, not for doing work.
    param([string]$Exe, [string[]]$Arguments = @())
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments *> $null
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    return ($code -eq 0)
}

function ConvertTo-VersionKey {
    # "1.010" -> @(1, 10), comparable. Minors are zero-padded to three digits
    # now (v1.008) and were not always (v0.165, v0.272), so they only sort
    # correctly as numbers: as text "1.9" would beat "1.010".
    param([string]$Text)
    if ($Text -notmatch '^(\d+)\.(\d+)$') { return $null }
    return @([int]$Matches[1], [int]$Matches[2])
}

function Compare-VersionKey {
    param($A, $B)
    if ($A[0] -ne $B[0]) { return $A[0].CompareTo($B[0]) }
    return $A[1].CompareTo($B[1])
}

function Test-IsNewestRelease {
    <#
      Is this the highest version that has been released? $null when we cannot
      tell (gh unreachable or unreadable output).

      This exists because the script used to pass --latest unconditionally, so
      publishing an *older* version moved GitHub's "Latest" badge backwards.
      That is not hypothetical: v1.008 had been tagged but never released, and
      filling it in after v1.009 would have advertised v1.008 as current to
      anyone landing on the repository. It had to be done by hand with
      --latest=false instead.
    #>
    param([string]$Version, [string]$Gh)
    $mine = ConvertTo-VersionKey $Version
    if ($null -eq $mine) { return $null }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $json = & $Gh @("release", "list", "--limit", "200", "--json", "tagName,isDraft") 2>$null
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0 -or -not $json) { return $null }
    try { $releases = (($json -join "") | ConvertFrom-Json) } catch { return $null }
    foreach ($release in $releases) {
        # A draft of a later version is not published and holds no badge.
        if ($release.isDraft) { continue }
        if ([string]$release.tagName -notmatch '^v(\d+\.\d+)$') { continue }
        $other = ConvertTo-VersionKey $Matches[1]
        if ($null -eq $other) { continue }
        if ((Compare-VersionKey $other $mine) -gt 0) { return $false }
    }
    return $true
}

# --- Resolve gh (may not be on PATH in every shell) -------------------------
$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) {
    foreach ($p in @("$env:ProgramFiles\GitHub CLI\gh.exe",
                     "${env:ProgramFiles(x86)}\GitHub CLI\gh.exe",
                     "$env:LOCALAPPDATA\Programs\GitHub CLI\gh.exe")) {
        if (Test-Path $p) { $gh = $p; break }
    }
}
if (-not $gh) { Fail "GitHub CLI (gh) not found. Install it and run 'gh auth login'." }

# --- Version, tag, ZIP ------------------------------------------------------
if (-not $Version) {
    if (-not (Test-Path "VERSION")) { Fail "VERSION file not found." }
    $Version = (Get-Content "VERSION" -Raw).Trim()
}
if ($Version -notmatch '^\d+\.\d+$') { Fail "Version '$Version' is not in the form N.N (e.g. 0.165)." }
$tag = "v$Version"
$zip = "pwllheli_race_officer_v$($Version.Replace('.', '_')).zip"
if (-not (Test-Path $zip)) {
    Fail "Release ZIP '$zip' not found in the repo root. Build it first, then re-run."
}

# --- Extract this version's notes from docs/CHANGELOG.md ---------------------
if (-not (Test-Path "docs/CHANGELOG.md")) { Fail "docs/CHANGELOG.md not found." }
$lines = Get-Content "docs/CHANGELOG.md" -Encoding UTF8
$notes = New-Object System.Collections.Generic.List[string]
$inSection = $false
foreach ($line in $lines) {
    if ($line -match "^##\s+v$([regex]::Escape($Version))\b") { $inSection = $true; continue }
    if ($inSection -and $line -match "^##\s+v") { break }   # next version -> stop
    if ($inSection) { $notes.Add($line) }
}
$notesText = ($notes -join "`n").Trim()
if (-not $notesText) { Fail "No '## v$Version' section found in docs/CHANGELOG.md." }
$notesText = "## $tag`n`n$notesText`n`n**Download:** ``$zip`` below."

# --- Preflight report -------------------------------------------------------
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$dirty  = (git status --porcelain)
Write-Output "Version : $Version"
Write-Output "Tag     : $tag"
Write-Output "ZIP     : $zip"
Write-Output "Branch  : $branch"
if ($dirty) { Write-Warning "Working tree has uncommitted changes; commit & push before releasing." }

# How far ahead of the remote this branch is. Pushing a tag carries the commits
# it reaches, so the release and its ZIP were always right - but the *branch*
# ref stayed where it was, and anyone browsing the repository saw whatever
# master last pointed at. That went unnoticed for sixteen releases: GitHub
# showed a v0.205 README while v0.221 was on the releases page.
$ahead = 0
try {
    $counts = (git rev-list --left-right --count "origin/$branch...HEAD" 2>$null)
    if ($counts) { $ahead = [int](($counts -split "\s+")[1]) }
} catch { $ahead = 0 }
if ($ahead -gt 0) { Write-Output "Unpushed : $ahead commit(s) on $branch - these will be pushed" }

# Which way the "Latest" badge should go. Worked out before -DryRun returns so
# that a dry run shows it.
$isNewest = $null
if (-not $Draft) { $isNewest = Test-IsNewestRelease $Version $gh }
if ($Draft) {
    Write-Output "Latest  : no (draft)"
} elseif ($isNewest -eq $true) {
    Write-Output "Latest  : yes"
} elseif ($isNewest -eq $false) {
    Write-Output "Latest  : no - a higher version is already released"
} else {
    Write-Output "Latest  : gh decides (could not read the release list)"
}
Write-Output "--- release notes ---"
Write-Output $notesText
Write-Output "---------------------"

if ($DryRun) { Write-Output "DryRun: nothing was tagged, pushed or created."; exit 0 }

# --- Tag, push, release -----------------------------------------------------
$existing = (git tag --list $tag)
if (-not $existing) {
    Invoke-Native "git" @("tag", "-a", $tag, "-m", "Pwllheli Race Officer $tag") "git tag"
    Write-Output "Created tag $tag"
} else {
    Write-Output "Tag $tag already exists; reusing it."
}
# The branch first, then the tag. A tag alone leaves master behind: the release
# downloads fine and the commits are on the server, but the repository's front
# page - the README included - keeps showing the last branch push.
if ($ahead -gt 0) {
    Invoke-Native "git" @("push", "origin", $branch) "git push origin $branch"
    Write-Output "Pushed $ahead commit(s) to origin/$branch"
}
Invoke-Native "git" @("push", "origin", $tag) "git push origin $tag"

$notesFile = (New-TemporaryFile).FullName
# Write UTF-8 without BOM so gh renders non-ASCII (→, ©, em-dash) cleanly.
[System.IO.File]::WriteAllText($notesFile, $notesText, (New-Object System.Text.UTF8Encoding($false)))
try {
    # A part-finished run (tag pushed, release not created) must converge on a
    # re-run rather than stopping at "release already exists", which is what
    # v0.193 left behind and had to be finished by hand.
    if (Test-NativeOk $gh @("release", "view", $tag)) {
        Write-Output "Release $tag already exists; updating its notes and asset."
        $editArgs = @("release", "edit", $tag, "--notes-file", $notesFile)
        # Only ever promote on an edit. Leaving the badge alone is the safe
        # answer for an existing release we are merely re-uploading to.
        if ($isNewest -eq $true) { $editArgs += "--latest" }
        Invoke-Native $gh $editArgs "gh release edit"
        Invoke-Native $gh @("release", "upload", $tag, $zip, "--clobber") "gh release upload"
    } else {
        $ghArgs = @("release", "create", $tag, $zip, "--title", $tag, "--notes-file", $notesFile)
        if ($Draft) {
            $ghArgs += "--draft"
        } elseif ($isNewest -eq $true) {
            $ghArgs += "--latest"
        } elseif ($isNewest -eq $false) {
            # Filling in a release older than one already published.
            $ghArgs += "--latest=false"
        }
        # Otherwise say nothing and let gh decide by date and version.
        Invoke-Native $gh $ghArgs "gh release create"
    }
} finally {
    Remove-Item $notesFile -ErrorAction SilentlyContinue
}
Write-Output "Done: https://github.com/mojito9047/pwllheli-race-officer/releases/tag/$tag"
