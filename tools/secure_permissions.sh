#!/usr/bin/env bash
# Secure file permissions for Sniper AI runtime
# Run after install or when .env/.db files are created/modified

set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Sensitive files at repo root: 600 (owner rw only)
for file in .env sniper_brain.db sniper.log conflict_ab.log; do
    path="$ROOT/$file"
    if [ -f "$path" ]; then
        chmod 600 "$path"
        echo "🔒 Secured $file to 600"
    fi
done

# IPC directory: 700 (owner rwx only)
for dir in /dev/shm/sniper_cmd; do
    if [ -d "$dir" ]; then
        chmod 700 "$dir"
        echo "🔒 Secured $dir to 700"
    fi
done

echo "✅ Secure permissions applied"