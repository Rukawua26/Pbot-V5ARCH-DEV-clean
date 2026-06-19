#!/bin/bash
cd "$(dirname "$0")/.."
exec ./.venv/bin/uvicorn \
  tools.dashboard_api_server:app --host 127.0.0.1 --port 8000
