#!/bin/sh
# Download a native GitHub Release. No Python is needed on the destination host.
set -eu
repository=${CODEXX_REPOSITORY:-}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --repository) [ "$#" -ge 2 ] || exit 2; repository=$2; shift 2 ;;
        -h|--help) echo 'Usage: sh install.sh --repository OWNER/REPO'; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
if [ -z "$repository" ]; then
    echo 'No GitHub repository configured; release installation skipped.'
    exit 0
fi
case "$repository" in
    *[!a-zA-Z0-9_./-]*|/*|*/|*/*/*|*..*) echo 'Expected GitHub OWNER/REPO' >&2; exit 2 ;;
    */*) ;;
    *) echo 'Expected GitHub OWNER/REPO' >&2; exit 2 ;;
esac
case "$(uname -s)" in
    Darwin) system=macos ;;
    Linux) system=linux ;;
    *) echo 'Use install.ps1 on Windows' >&2; exit 2 ;;
esac
case "$(uname -m)" in
    x86_64|amd64) arch=x86_64 ;;
    arm64|aarch64) arch=arm64 ;;
    *) echo 'Unsupported architecture' >&2; exit 2 ;;
esac
asset="codexx-$system-$arch.tar.gz"
base="https://github.com/$repository/releases/latest/download"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
curl --fail --location --proto '=https' --proto-redir '=https' "$base/SHA256SUMS" -o "$work/SHA256SUMS"
curl --fail --location --proto '=https' --proto-redir '=https' "$base/$asset" -o "$work/$asset"
expected=$(awk -v name="$asset" '$2 == name {print $1}' "$work/SHA256SUMS")
[ "${#expected}" -eq 64 ] || { echo 'Missing/duplicate checksum' >&2; exit 1; }
case "$expected" in *[!a-fA-F0-9]*) echo 'Invalid checksum' >&2; exit 1 ;; esac
if command -v sha256sum >/dev/null 2>&1; then
    actual=$(sha256sum "$work/$asset" | awk '{print $1}')
else
    actual=$(shasum -a 256 "$work/$asset" | awk '{print $1}')
fi
[ "$actual" = "$expected" ] || { echo 'Archive checksum mismatch' >&2; exit 1; }
# Extract a single exact member to stdout, never archive paths or symlinks.
tar -xOzf "$work/$asset" codexx > "$work/codexx"
chmod 755 "$work/codexx"
"$work/codexx" --codexx-no-update --codexx-install --codexx-repository "$repository"
