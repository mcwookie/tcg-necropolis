#!/bin/sh
# Bring this server up to date with main: the latest compose file and Caddy
# config (git), the latest published image (docker), and restart only what
# changed. Run by tcgnecro-update.timer every few minutes; safe to run by hand.
set -eu
cd "$(dirname "$0")/.."

git pull --ff-only --quiet
docker compose pull --quiet
docker compose up -d            # recreates a container only if its image or config changed
docker image prune -f > /dev/null
