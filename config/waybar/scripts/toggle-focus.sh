#!/usr/bin/env bash
set -u
dunstctl set-paused toggle >/dev/null 2>&1 || exit 0
