#!/bin/bash

# Projektverzeichnis (relativ zum Script-Speicherort)
cd "$(dirname "$0")"

# .venv aktivieren
source .venv/bin/activate

while true; do
    clear
    echo "==============================="
    echo "   WEG Protokolle – Startmenü"
    echo "==============================="
    echo ""
    echo "  1)  OCR / Textimport starten"
    echo "      (weg_protokoll_processor.py)"
    echo ""
    echo "  2)  Datenbank-Import starten"
    echo "      (weg_to_db.py)"
    echo ""
    echo "  3)  HTML-Server starten"
    echo "      (weg_server.py)"
    echo ""
    echo "  0)  Beenden"
    echo ""
    echo "==============================="
    read -p "  Auswahl: " choice

    case $choice in
        1)
            echo ""
            echo "▶ OCR / Textimport wird gestartet..."
            python src/weg_protokoll_processor.py input/ output/
            echo ""
            read -p "↩ Enter drücken um zurückzukehren..."
            ;;
        2)
            echo ""
            echo "▶ Datenbank-Import wird gestartet..."
            python src/weg_to_db.py output/
            echo ""
            read -p "↩ Enter drücken um zurückzukehren..."
            ;;
        3)
            echo ""
            echo "▶ HTML-Server wird gestartet..."
            echo "  (Mit CTRL+C beenden)"
            echo ""
            python weg_server.py
            echo ""
            read -p "↩ Enter drücken um zurückzukehren..."
            ;;
        0)
            echo ""
            echo "Auf Wiedersehen! 👋"
            echo ""
            exit 0
            ;;
        *)
            echo ""
            echo "❌ Ungültige Eingabe – bitte 0-3 wählen."
            sleep 1
            ;;
    esac
done