#!/bin/sh
# Container start-up: bring the index up to date, then run the server.
#
# index.db is a cache of the card catalogs plus the image folders, and it
# stores absolute paths, so it's rebuilt on every start: `catalog` reloads card
# data (ArkhamDB comes from data/cache when present) and `reindex` rescans the
# collections. Both are quick. Set TCGNECRO_REBUILD_INDEX=0 to skip.
set -e

if [ "${TCGNECRO_REBUILD_INDEX:-1}" = "1" ]; then
    echo "Loading card catalogs..."
    tcgnecro catalog
    echo "Indexing collections..."
    tcgnecro reindex
fi

exec "$@"
