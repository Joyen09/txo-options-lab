#!/usr/bin/env bash
# 一鍵安裝：在 VM 上建立 venv 並安裝 txolab（含 CLI）。
# 用法：git clone <repo> ~/txo && cd ~/txo && bash deploy/setup_vm.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if command -v uv >/dev/null 2>&1; then
    uv sync
    echo "✅ uv sync 完成；指令：uv run txolab --help"
else
    python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip
    ./.venv/bin/pip install -e .
    echo "✅ venv 建立完成；指令：./.venv/bin/txolab --help"
fi

mkdir -p data/db data/samples
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo "⚠️  已產生 .env——把 DISCORD_WEBHOOK_URL 填進去，然後跑 notify-test"
fi
