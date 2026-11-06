#!/usr/bin/env bash
set -euo pipefail
base_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$base_dir/scripts/control-center.py"
