#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
    printf 'Нужен Python 3: sudo pacman -S python\n' >&2
    exit 1
fi
exec python3 "$root_dir/scripts/install.py" "$@"
