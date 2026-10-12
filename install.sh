#!/usr/bin/env bash
#
# APIA one-line installer (POSIX / bash)
#
# Usage (from your project root):
#   curl -fsSL https://raw.githubusercontent.com/Shyjil1024/APIA/main/install.sh | bash
#
# Optional flags (append after `| bash -s --`):
#   curl -fsSL .../install.sh | bash -s -- --with-samples --with-docs
#
#   --with-samples   also copy the samples/ directory
#   --with-docs      also copy the docs/ directory
#
# Installs the APIA GitHub Copilot skill package (.github/ and .apia/) into the
# current directory. Primary path shallow-clones the repo with git; if git is
# missing it falls back to downloading the repo zip. The temp clone is always
# cleaned up. Nothing in your project is ever deleted; files are only added or
# merged.
#
set -euo pipefail

# --- constants ---------------------------------------------------------------
REPO_URL="https://github.com/Shyjil1024/APIA.git"
ZIP_URL="https://github.com/Shyjil1024/APIA/archive/refs/heads/main.zip"
ZIP_TOP_DIR="APIA-main"   # top folder inside the GitHub zip archive

# --- logging helpers ---------------------------------------------------------
info()  { printf '  %s\n' "$*"; }
step()  { printf '\n==> %s\n' "$*"; }
warn()  { printf 'WARNING: %s\n' "$*" >&2; }
err()   { printf 'ERROR: %s\n' "$*" >&2; }

# --- copy a source dir into the destination, merging ------------------------
# copy_tree <src_dir> <dest_dir> : recursively copy src into dest, creating
# dest if needed. Existing files in dest are overwritten by src, but no file is
# ever removed. Works whether or not dest already exists.
copy_tree() {
    local src="$1"
    local dest="$2"
    if [ ! -d "$src" ]; then
        warn "source '$src' not found in the fetched package; skipping"
        return 0
    fi
    mkdir -p "$dest"
    # Copy the *contents* of src into dest (merge rather than nest).
    cp -R "$src/." "$dest/"
}

# --- merge the .github/ tree with the user's existing one -------------------
# Copilot discovers .github/copilot-instructions.md, prompts/, skills/, agents/
# only at the repo root, so we must land them there. If the user already has a
# copilot-instructions.md we refuse to clobber it: we write APIA's copy
# alongside as copilot-instructions.apia.md and warn.
merge_github() {
    local src="$1"   # <temp>/.github
    local dest="./.github"
    local sub

    if [ ! -d "$src" ]; then
        warn ".github not found in the fetched package; skipping"
        return 0
    fi

    mkdir -p "$dest"

    # prompts / skills / agents: safe to merge directly.
    for sub in prompts skills agents; do
        if [ -d "$src/$sub" ]; then
            copy_tree "$src/$sub" "$dest/$sub"
            info "copied .github/$sub/"
        fi
    done

    # copilot-instructions.md: protect an existing one.
    if [ -f "$src/copilot-instructions.md" ]; then
        if [ -f "$dest/copilot-instructions.md" ]; then
            cp "$src/copilot-instructions.md" "$dest/copilot-instructions.apia.md"
            warn "an existing .github/copilot-instructions.md was found and was NOT overwritten."
            warn "APIA's protocol file was written to .github/copilot-instructions.apia.md instead."
            warn "Review it and merge the APIA protocol into your copilot-instructions.md to activate the loop."
        else
            cp "$src/copilot-instructions.md" "$dest/copilot-instructions.md"
            info "copied .github/copilot-instructions.md"
        fi
    fi
}

# --- obtain the package into a temp dir -------------------------------------
# Sets the global FETCH_ROOT to the directory that contains .github/ and .apia/.
fetch_via_git() {
    step "Cloning APIA (shallow) with git"
    info "source: $REPO_URL"
    info "temp:   $CLONE_DIR"
    git clone --depth 1 "$REPO_URL" "$CLONE_DIR" >/dev/null 2>&1
    FETCH_ROOT="$CLONE_DIR"
}

fetch_via_zip() {
    step "git not found; downloading APIA zip archive"
    info "source: $ZIP_URL"
    local zip_path="$TMP_DIR/apia.zip"

    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$ZIP_URL" -o "$zip_path"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$zip_path" "$ZIP_URL"
    else
        err "neither git, curl, nor wget is available; cannot download APIA."
        return 1
    fi

    if ! command -v unzip >/dev/null 2>&1; then
        err "unzip is not available; cannot extract the APIA archive."
        return 1
    fi

    unzip -q "$zip_path" -d "$TMP_DIR"
    # The archive extracts into a top-level folder (e.g. APIA-main/).
    if [ -d "$TMP_DIR/$ZIP_TOP_DIR" ]; then
        FETCH_ROOT="$TMP_DIR/$ZIP_TOP_DIR"
    else
        # Be resilient if GitHub ever renames the top dir: pick the single
        # extracted directory that contains .apia.
        FETCH_ROOT="$(find "$TMP_DIR" -maxdepth 2 -type d -name '.apia' -exec dirname {} \; | head -n 1)"
    fi
    if [ -z "${FETCH_ROOT:-}" ] || [ ! -d "$FETCH_ROOT" ]; then
        err "could not locate the extracted APIA package."
        return 1
    fi
}

main() {
    with_samples=0
    with_docs=0
    for arg in "$@"; do
        case "$arg" in
            --with-samples) with_samples=1 ;;
            --with-docs)    with_docs=1 ;;
            -h|--help)
                printf 'Usage: install.sh [--with-samples] [--with-docs]\n'
                return 0
                ;;
            *)
                warn "ignoring unknown argument: $arg"
                ;;
        esac
    done

    printf '==================================================\n'
    printf ' APIA installer - GitHub Copilot skill package\n'
    printf '==================================================\n'
    info "installing into: $(pwd)"

    # Unique temp dir OUTSIDE the user's project (OS temp dir).
    TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/apia-install.XXXXXX")"
    CLONE_DIR="$TMP_DIR/clone"
    FETCH_ROOT=""

    # Always clean up the temp dir, success or failure.
    cleanup() {
        if [ -n "${TMP_DIR:-}" ] && [ -d "$TMP_DIR" ]; then
            rm -rf "$TMP_DIR"
        fi
    }
    trap cleanup EXIT

    # Fetch: prefer git, fall back to zip.
    if command -v git >/dev/null 2>&1; then
        if ! fetch_via_git; then
            warn "git clone failed; trying the zip fallback"
            if ! fetch_via_zip; then
                err "both git clone and the zip download failed. Aborting."
                exit 1
            fi
        fi
    else
        if ! fetch_via_zip; then
            err "git is unavailable and the zip download failed. Aborting."
            exit 1
        fi
    fi

    if [ ! -d "$FETCH_ROOT/.apia" ] || [ ! -d "$FETCH_ROOT/.github" ]; then
        err "fetched package is missing .github/ or .apia/. Aborting."
        exit 1
    fi

    # --- copy the two required folders ---
    step "Installing .github/ (Copilot-facing artifacts)"
    merge_github "$FETCH_ROOT/.github"

    step "Installing .apia/ (APIA engine, config, scripts, adapters)"
    copy_tree "$FETCH_ROOT/.apia" "./.apia"
    info "wrote .apia/ (config.yml, scripts/, adapters/, knowledge/)"

    # --- optional extras ---
    if [ "$with_samples" -eq 1 ]; then
        step "Installing samples/ (--with-samples)"
        copy_tree "$FETCH_ROOT/samples" "./samples"
        info "wrote samples/"
    fi
    if [ "$with_docs" -eq 1 ]; then
        step "Installing docs/ (--with-docs)"
        copy_tree "$FETCH_ROOT/docs" "./docs"
        info "wrote docs/"
    fi

    # --- next steps ---
    printf '\n==================================================\n'
    printf ' APIA installed.\n'
    printf '==================================================\n'
    info "Next steps:"
    info "  1. Install the off-model script deps:  pip install pytest pyyaml"
    info "  2. Reload VS Code (Developer: Reload Window)."
    info "  3. Run /apia-ingest to learn this project."
    printf '\n'
}

main "$@"
