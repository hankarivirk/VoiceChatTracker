#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f .env ]]; then
  echo "Missing .env. Copy .env.example to .env and fill in required credentials." >&2
  exit 1
fi
exec python -m VCBlogger.app
