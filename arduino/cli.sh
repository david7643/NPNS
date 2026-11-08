#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export ARDUINO_DIRECTORIES_DATA="$root/.local/arduino/data"
export ARDUINO_DIRECTORIES_DOWNLOADS="$root/.local/arduino/downloads"
export ARDUINO_DIRECTORIES_USER="$root/.local/arduino/user"
exec "$root/.local/arduino/bin/arduino-cli" "$@"
