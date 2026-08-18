#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
destination=${HOME}/.config/omarchy/themes

# The dark variant is the repository root, so `omarchy theme install <repo-url>`
# installs it directly. Copy only the theme files -- the root also holds the
# README, tests, and the light variant, none of which belong in a theme.
dark_name=macos-classic
dark_files=(
  backgrounds
  btop.theme
  chromium.theme
  colors.toml
  hyprland.lua
  icons.theme
  preview.png
  preview-unlock.png
  shell.hyprland.toml
  slack.theme
  unlock.png
  vscode.json
  zed.json
)
light_name=macos-classic-light
activate=1

usage() {
  echo "Usage: ./install.sh [--destination DIR] [--no-activate]"
}

while (($#)); do
  case "$1" in
    --destination)
      if (($# < 2)); then
        echo "Error: --destination requires a directory." >&2
        usage >&2
        exit 2
      fi
      destination=$2
      shift 2
      ;;
    --no-activate)
      activate=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

mkdir -p -- "$destination"

rm -rf -- "${destination:?}/$dark_name"
mkdir -p -- "$destination/$dark_name"
for file in "${dark_files[@]}"; do
  cp -R -- "$repo_dir/$file" "$destination/$dark_name/$file"
done
echo "Installed $dark_name"

rm -rf -- "${destination:?}/$light_name"
cp -R -- "$repo_dir/$light_name" "$destination/$light_name"
echo "Installed $light_name"

if fc-list : family 2>/dev/null | tr ',' '\n' | grep -Fxiq 'Monaco'; then
  echo "Monaco is available. Apply it with: omarchy font set Monaco"
else
  echo "Monaco is not installed. Install a licensed copy, then run: omarchy font set Monaco"
fi

if ((activate)) && command -v omarchy >/dev/null 2>&1; then
  omarchy theme set "$dark_name"
  echo "Applied $dark_name"
  echo "Switch to the light variant with: omarchy theme set $light_name"
else
  echo "Choose a variant with: omarchy theme set $dark_name"
  echo "Or:                    omarchy theme set $light_name"
fi
