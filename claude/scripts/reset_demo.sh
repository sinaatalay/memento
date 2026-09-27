#!/bin/bash
# Fresh demo run: new namespace for Memento-written pages, the original Acme page,
# and 9 realistic emails sent through real Gmail (they arrive via GBrain's sync).
#   scripts/reset_demo.sh            # console on http://127.0.0.1:8765
set -e
cd "$(dirname "$0")/.."
RUN="${1:-demo$(date +%H%M)-}"
pkill -f "bin/memento" || true
sleep 1
MEMENTO_RUN="$RUN" nohup uv run memento > /tmp/memento-server.log 2>&1 &
sleep 7
uv run python scripts/write_page.py examples/acme-soc2.md projects/acme-pilot
uv run python scripts/seed.py --gmail
echo
echo "memento console: http://127.0.0.1:8765   (run: $RUN, log: /tmp/memento-server.log)"
echo "seed memories appear within ~30 s. Live beats: SOC 2 planned -> issued, flight -> change, +1 day."
