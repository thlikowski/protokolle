#!/bin/bash

# Projektverzeichnis (relativ zum Script-Speicherort)
cd "$(dirname "$0")"

# .venv aktivieren
source .venv/bin/activate

echo ""
echo "▶ WEG-Server wird gestartet..."
echo "  http://localhost:8765"
echo "  (Mit CTRL+C beenden)"
echo ""

python weg_server.py

echo ""
echo "Server beendet."
