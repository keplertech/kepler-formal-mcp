#!/usr/bin/env bash

set -euo pipefail

# Latest published Nix package (2026-09-11), not the newer uncached main.
revision=0e0abf2aa6979e337aead8996983952dc1742530
installable="git+https://github.com/keplertech/kepler-formal?rev=$revision&submodules=1#kepler-formal"
cache=https://keplertech.cachix.org
# Public signing key from https://app.cachix.org/api/v1/cache/keplertech.
cache_key='keplertech.cachix.org-1:f4jEdCmmFAS/Sd5OOKb1ddU61eJtRQytboKlBuSmZI8='
# Keep this array nonempty: Bash 3.2 treats empty arrays as unset under -u.
profile_args=(--no-update-lock-file)

usage() {
  cat <<'EOF'
Usage: install_kepler_formal.sh [--profile PATH]

Install the pinned Kepler Formal Nix package using cached binaries only.
Requires Nix 2.35+ on x86_64-linux or aarch64-darwin.

  --profile PATH  Install into this Nix profile instead of the user's default.
  -h, --help      Show this help.

Cache settings apply only to this command. The script does not install Nix,
change Nix configuration, initialize Git submodules, or compile source.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      if [[ $# -lt 2 || -z "$2" || "$2" == -* ]]; then
        echo "--profile requires a path." >&2
        exit 2
      fi
      profile_args=(--no-update-lock-file --profile "$2")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v nix >/dev/null 2>&1; then
  echo "Nix 2.35+ is required. Install it from https://nixos.org/download/ and rerun this script." >&2
  exit 1
fi

nix_options=(
  --extra-experimental-features 'nix-command flakes'
  --max-jobs 0
  --builders ''
  --option allow-import-from-derivation false
  --option accept-flake-config false
  --option substituters "$cache https://cache.nixos.org"
  --option extra-substituters ''
  --option extra-trusted-public-keys "$cache_key"
  --option require-sigs true
  --option fallback false
  --option narinfo-cache-negative-ttl 0
)

system=$(nix "${nix_options[@]}" eval --impure --raw --expr '
  if builtins.compareVersions builtins.nixVersion "2.35" < 0
  then throw "Nix 2.35+ is required."
  else builtins.currentSystem
')
case "$system" in
  x86_64-linux|aarch64-darwin) ;;
  *)
    echo "The pinned upstream package does not support $system (supported: x86_64-linux, aarch64-darwin)." >&2
    exit 1
    ;;
esac

echo "Kepler Formal revision: $revision ($system)"
package=$(nix "${nix_options[@]}" eval --raw --no-update-lock-file "$installable.outPath")
if [[ ! "$package" =~ ^/nix/store/[a-z0-9]{32}-[^/]+$ ]]; then
  echo "Invalid package output path: $package" >&2
  exit 1
fi

if nix "${nix_options[@]}" path-info --offline "$package" >/dev/null 2>&1; then
  echo "Reusing package already present in the Nix store: $package"
elif nix "${nix_options[@]}" path-info --store "$cache" "$package"; then
  echo "Found published package: $package"
else
  echo "The pinned package is unavailable in $cache. Ask the maintainer to publish this revision; source builds remain disabled." >&2
  exit 1
fi

if nix "${nix_options[@]}" profile install "${profile_args[@]}" "$installable"; then
  :
else
  install_status=$?
  echo "Cached installation failed. Check the cache/download error above and whether your Nix daemon trusts the keplertech cache. No source-build fallback was attempted." >&2
  exit "$install_status"
fi

echo "Installed: $package/bin/kepler-formal"
echo "Set KEPLER_FORMAL_BINARY to this path if your MCP client cannot find kepler-formal on PATH."
