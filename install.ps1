<#
.SYNOPSIS
    APIA one-line installer (Windows PowerShell 5.1+ and PowerShell 7).

.DESCRIPTION
    Installs the APIA GitHub Copilot skill package (.github/ and .apia/) into
    the current directory (your project root). The primary path shallow-clones
    the repo with git; if git is missing it falls back to downloading the repo
    zip. The temp clone is always cleaned up. Nothing in your project is ever
    deleted; files are only added or merged.

.PARAMETER WithSamples
    Also copy the samples/ directory.

.PARAMETER WithDocs
    Also copy the docs/ directory.

.EXAMPLE
    iwr https://raw.githubusercontent.com/Shyjil1024/APIA/main/install.ps1 | iex

.EXAMPLE
    # with flags (download then run so you can pass arguments):
    $s = iwr https://raw.githubusercontent.com/Shyjil1024/APIA/main/install.ps1
    & ([scriptblock]::Create($s.Content)) -WithSamples -WithDocs
#>

$ErrorActionPreference = 'Stop'

# When piped straight into `iex`, bound parameters are not available, so the
# entrypoint at the bottom reads optional switches from $args / env vars.
function Install-Apia {
    param(
        [switch]$WithSamples,
        [switch]$WithDocs
    )

    # --- constants ---
    $RepoUrl   = 'https://github.com/Shyjil1024/APIA.git'
    $ZipUrl    = 'https://github.com/Shyjil1024/APIA/archive/refs/heads/main.zip'
    $ZipTopDir = 'APIA-main'   # top folder inside the GitHub zip archive

    # --- logging helpers ---
    function Write-Info { param([string]$m) Write-Host "  $m" }
    function Write-Step { param([string]$m) Write-Host "`n==> $m" -ForegroundColor Cyan }
    function Write-WarnMsg { param([string]$m) Write-Warning $m }

    # Copy-Tree: recursively copy the *contents* of $Src into $Dest, merging.
    # Existing files are overwritten; nothing is ever removed. This is a manual
    # recursive merge because plain `Copy-Item -Recurse` into an existing
    # directory nests (dest\name\name) instead of merging, and that bug differs
    # between Windows PowerShell 5.1 and PowerShell 7. Recursing by hand is
    # deterministic on both.
    function Copy-Tree {
        param([string]$Src, [string]$Dest)
        if (-not (Test-Path -LiteralPath $Src)) {
            Write-WarnMsg "source '$Src' not found in the fetched package; skipping"
            return
        }
        if (-not (Test-Path -LiteralPath $Dest)) {
            New-Item -ItemType Directory -Path $Dest -Force | Out-Null
        }
        # -Force on Get-ChildItem includes dotfiles such as .gitkeep.
        foreach ($child in Get-ChildItem -LiteralPath $Src -Force) {
            $target = Join-Path $Dest $child.Name
            if ($child.PSIsContainer) {
                # Recurse so an existing same-named directory merges in place.
                Copy-Tree -Src $child.FullName -Dest $target
            }
            else {
                Copy-Item -LiteralPath $child.FullName -Destination $target -Force
            }
        }
    }

    # Merge the .github/ tree. Copilot only discovers copilot-instructions.md,
    # prompts/, skills/, agents/ at the repo root, so they must land there. An
    # existing copilot-instructions.md is protected: APIA's copy is written as
    # copilot-instructions.apia.md and a warning is printed.
    function Merge-Github {
        param([string]$Src)
        $dest = Join-Path '.' '.github'
        if (-not (Test-Path -LiteralPath $Src)) {
            Write-WarnMsg ".github not found in the fetched package; skipping"
            return
        }
        if (-not (Test-Path -LiteralPath $dest)) {
            New-Item -ItemType Directory -Path $dest -Force | Out-Null
        }

        foreach ($sub in @('prompts', 'skills', 'agents')) {
            $subSrc = Join-Path $Src $sub
            if (Test-Path -LiteralPath $subSrc) {
                Copy-Tree -Src $subSrc -Dest (Join-Path $dest $sub)
                Write-Info "copied .github/$sub/"
            }
        }

        $ciSrc  = Join-Path $Src 'copilot-instructions.md'
        $ciDest = Join-Path $dest 'copilot-instructions.md'
        if (Test-Path -LiteralPath $ciSrc) {
            if (Test-Path -LiteralPath $ciDest) {
                Copy-Item -LiteralPath $ciSrc -Destination (Join-Path $dest 'copilot-instructions.apia.md') -Force
                Write-WarnMsg "an existing .github/copilot-instructions.md was found and was NOT overwritten."
                Write-WarnMsg "APIA's protocol file was written to .github/copilot-instructions.apia.md instead."
                Write-WarnMsg "Review it and merge the APIA protocol into your copilot-instructions.md to activate the loop."
            }
            else {
                Copy-Item -LiteralPath $ciSrc -Destination $ciDest -Force
                Write-Info "copied .github/copilot-instructions.md"
            }
        }
    }

    Write-Host '=================================================='
    Write-Host ' APIA installer - GitHub Copilot skill package'
    Write-Host '=================================================='
    Write-Info "installing into: $((Get-Location).Path)"

    # Unique temp dir OUTSIDE the user's project.
    $tmpBase  = [System.IO.Path]::GetTempPath()
    $tmpDir   = Join-Path $tmpBase ("apia-install-" + [guid]::NewGuid().ToString('N'))
    $cloneDir = Join-Path $tmpDir 'clone'
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

    try {
        $fetchRoot = $null

        $hasGit = $null -ne (Get-Command git -ErrorAction SilentlyContinue)
        if ($hasGit) {
            Write-Step "Cloning APIA (shallow) with git"
            Write-Info "source: $RepoUrl"
            Write-Info "temp:   $cloneDir"
            try {
                git clone --depth 1 $RepoUrl $cloneDir 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "git clone exited with code $LASTEXITCODE" }
                $fetchRoot = $cloneDir
            }
            catch {
                Write-WarnMsg "git clone failed ($_); trying the zip fallback"
                $fetchRoot = $null
            }
        }

        if (-not $fetchRoot) {
            Write-Step "Downloading APIA zip archive"
            Write-Info "source: $ZipUrl"
            $zipPath = Join-Path $tmpDir 'apia.zip'
            Invoke-WebRequest -Uri $ZipUrl -OutFile $zipPath -UseBasicParsing
            Expand-Archive -Path $zipPath -DestinationPath $tmpDir -Force
            $candidate = Join-Path $tmpDir $ZipTopDir
            if (Test-Path -LiteralPath $candidate) {
                $fetchRoot = $candidate
            }
            else {
                # Resilient fallback: find the extracted dir that contains .apia
                $found = Get-ChildItem -Path $tmpDir -Directory |
                    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName '.apia') } |
                    Select-Object -First 1
                if ($found) { $fetchRoot = $found.FullName }
            }
        }

        if (-not $fetchRoot) {
            throw "both git clone and the zip download failed. Aborting."
        }
        if (-not (Test-Path -LiteralPath (Join-Path $fetchRoot '.apia')) -or
            -not (Test-Path -LiteralPath (Join-Path $fetchRoot '.github'))) {
            throw "fetched package is missing .github/ or .apia/. Aborting."
        }

        Write-Step "Installing .github/ (Copilot-facing artifacts)"
        Merge-Github -Src (Join-Path $fetchRoot '.github')

        Write-Step "Installing .apia/ (APIA engine, config, scripts, adapters)"
        Copy-Tree -Src (Join-Path $fetchRoot '.apia') -Dest (Join-Path '.' '.apia')
        Write-Info "wrote .apia/ (config.yml, scripts/, adapters/, knowledge/)"

        if ($WithSamples) {
            Write-Step "Installing samples/ (--with-samples)"
            Copy-Tree -Src (Join-Path $fetchRoot 'samples') -Dest (Join-Path '.' 'samples')
            Write-Info "wrote samples/"
        }
        if ($WithDocs) {
            Write-Step "Installing docs/ (--with-docs)"
            Copy-Tree -Src (Join-Path $fetchRoot 'docs') -Dest (Join-Path '.' 'docs')
            Write-Info "wrote docs/"
        }

        Write-Host "`n=================================================="
        Write-Host ' APIA installed.'
        Write-Host '=================================================='
        Write-Info "Next steps:"
        Write-Info "  1. Install the off-model script deps:  pip install pytest pyyaml"
        Write-Info "  2. Reload VS Code (Developer: Reload Window)."
        Write-Info "  3. Run /apia-ingest to learn this project."
        Write-Host ''
    }
    finally {
        if (Test-Path -LiteralPath $tmpDir) {
            Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# --- entrypoint --------------------------------------------------------------
# Parse flags from $args so the script works both when dot-sourced/piped (iex)
# and when invoked with -WithSamples / -WithDocs / --with-samples / --with-docs.
$withSamples = $false
$withDocs    = $false
foreach ($a in $args) {
    switch -regex ($a) {
        '^(--with-samples|-WithSamples)$' { $withSamples = $true }
        '^(--with-docs|-WithDocs)$'       { $withDocs = $true }
        default { Write-Warning "ignoring unknown argument: $a" }
    }
}
# Environment-variable overrides (useful when piped to iex).
if ($env:APIA_WITH_SAMPLES -eq '1') { $withSamples = $true }
if ($env:APIA_WITH_DOCS    -eq '1') { $withDocs = $true }

Install-Apia -WithSamples:$withSamples -WithDocs:$withDocs
