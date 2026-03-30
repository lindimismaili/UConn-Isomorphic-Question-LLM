#!/usr/bin/env bash
set -euo pipefail

# =====================================================
# easy-deploy.sh — Convenience wrapper for deploy.sh
# =====================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/deploy.sh" "$@"
