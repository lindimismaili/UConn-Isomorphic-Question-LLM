#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET_BRANCH="${GIT_FORCE_BRANCH:-main}"

cd "$REPO_DIR"

echo "---- force sync git (${TARGET_BRANCH}) ----"
git fetch origin "$TARGET_BRANCH"
git reset --hard "origin/${TARGET_BRANCH}"
git clean -fd

echo "---- chmod hooks + deploy scripts ----"
chmod +x \
  .githooks/post-merge \
  .githooks/post-rewrite \
  deploy/raspi-deploy.sh \
  deploy/force-sync-and-raspi-config.sh

echo "---- run deploy/raspi-deploy.sh ----"
bash deploy/raspi-deploy.sh
