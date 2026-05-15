#!/bin/bash
cd "$(dirname "$0")"

# Stop existing container if running
docker compose down 2>/dev/null

# Rebuild and start
docker compose up -d --build

echo "✅ http://localhost:7030"
