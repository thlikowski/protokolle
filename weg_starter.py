import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import threading
import sys
import os

# Pfade
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")

VENV_PYTHON = os.path.join(BASE_DIR, ".venv", "bin", "python3")
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = sys.executable  # Fallback auf System-Python

SCRIPTS = {
    "server": [os.path.join(BASE_DIR, "weg_server.py")],
    "ocr": [os.path.join(SRC_DIR, "weg_protokoll_processor.py"),
            os.path.join(BASE_DIR, "input"),
            os.path.join(BASE_DIR, "output")],
    "db": [os.path.join(SRC_DIR, "weg_to_db.py"),
           os.path.join(BASE_DIR, "output")],
}

# Laufende Prozesse
processes = {
    "server": None,
    "ocr": None,
    "db": None,
}


class WEGStarter(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WEG Protokolle – Startmenü")
        self.resizable(False, False)
        self.configure(bg="#f0f0f0")
        self._build_ui()
        self._update_status()

    def _build_ui(self):
        # Titel
        header = tk.Label(
            self,
            text="WEG Protokolle",
            font=("Helvetica", 18, "bold"),
            bg="#f0f0f0",
            fg="#2c3e50",
        )
        header.grid(row=0, column=0, columnspan=3, pady=(20, 4))

        subtitle = tk.Label(
            self,
            text="Startmenü",
            font=("Helvetica", 11),
            bg="#f0f0f0",
            fg="#7f8c8d",
        )
        subtitle.grid(row=1, column=0, columnspan=3, pady=(0, 16))

        ttk.Separator(self, orient="horizontal").grid(
            row=2, column=0, columnspan=3, sticky="ew", padx=20, pady=(0, 16)
        )

        # Einträge: (label, key, start_text, stop_text)
        entries = [
            ("🌐  HTML-Server", "server", "Server starten", "Server stoppen"),
            ("🔍  OCR / Textimport", "ocr", "OCR starten", "OCR stoppen"),
            ("🗄️  Datenbank-Import", "db", "DB-Import starten", "DB-Import stoppen"),
        ]

        self.status_labels = {}
        self.start_buttons = {}
        self.stop_buttons = {}

        for i, (label_text, key, start_lbl, stop_lbl) in enumerate(entries):
            row = i + 3

            # Label
            tk.Label(
                self,
                text=label_text,
                font=("Helvetica", 12),
                bg="#f0f0f0",
                fg="#2c3e50",
                anchor="w",
                width=22,
            ).grid(row=row, column=0, padx=(20, 8), pady=8, sticky="w")

            # Start-Button
            btn_start = tk.Button(
                self,
                text=start_lbl,
                width=16,
                font=("Helvetica", 10, "bold"),
                relief="raised",
                cursor="hand2",
                command=lambda k=key: self._start(k),
            )
            btn_start.grid(row=row, column=1, padx=4, pady=8)
            self.start_buttons[key] = btn_start

            # Stop-Button
            btn_stop = tk.Button(
                self,
                text=stop_lbl,
                width=16,
                font=("Helvetica", 10, "bold"),
                relief="raised",
                cursor="hand2",
                state="disabled",
                command=lambda k=key: self._stop(k),
            )
            btn_stop.grid(row=row, column=2, padx=(4, 20), pady=8)
            self.stop_buttons[key] = btn_stop

            # Status
            status = tk.Label(
                self,
                text="⚪ Gestoppt",
                font=("Helvetica", 10),
                bg="#f0f0f0",
                fg="#95a5a6",
                anchor="w",
            )
            status.grid(row=row + 10, column=0, columnspan=3, padx=20, sticky="w")
            self.status_labels[key] = status

        # Statuszeile unten
        ttk.Separator(self, orient="horizontal").grid(
            row=20, column=0, columnspan=3, sticky="ew", padx=20, pady=(16, 8)
        )

        self.footer_label = tk.Label(
            self,
            text="Bereit.",
            font=("Helvetica", 10),
            bg="#f0f0f0",
            fg="#7f8c8d",
        )
        self.footer_label.grid(row=21, column=0, columnspan=3, pady=(0, 16))

    def _start(self, key):
        if processes[key] and processes[key].poll() is None:
            return  # läuft bereits

        script = SCRIPTS[key]
        if not os.path.exists(script[0]):
            messagebox.showerror("Fehler", f"Script nicht gefunden:\n{script[0]}")
            return

        try:
            proc = subprocess.Popen(
                [VENV_PYTHON] + script,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            processes[key] = proc
            self._set_status(key, running=True)
            self.footer_label.config(text=f"✅ {key.upper()} gestartet (PID {proc.pid})")

            # Hintergrund-Thread überwacht den Prozess
            threading.Thread(target=self._monitor, args=(key,), daemon=True).start()

        except Exception as e:
            messagebox.showerror("Fehler beim Starten", str(e))

    def _stop(self, key):
        proc = processes[key]
        if proc and proc.poll() is None:
            proc.terminate()
            processes[key] = None
            self._set_status(key, running=False)
            self.footer_label.config(text=f"🛑 {key.upper()} gestoppt.")

    def _monitor(self, key):
        proc = processes[key]
        if proc:
            proc.wait()
            processes[key] = None
            self.after(0, lambda: self._set_status(key, running=False))
            self.after(0, lambda: self.footer_label.config(
                text=f"ℹ️ {key.upper()} wurde beendet."
            ))

    def _set_status(self, key, running: bool):
        if running:
            self.status_labels[key].config(text="🟢 Läuft", fg="#27ae60")
            self.start_buttons[key].config(state="disabled")
            self.stop_buttons[key].config(state="normal")
        else:
            self.status_labels[key].config(text="⚪ Gestoppt", fg="#95a5a6")
            self.start_buttons[key].config(state="normal")
            self.stop_buttons[key].config(state="disabled")

        # OCR und DB dürfen nicht gleichzeitig laufen
        ocr_running = processes["ocr"] is not None and processes["ocr"].poll() is None
        db_running = processes["db"] is not None and processes["db"].poll() is None

        if ocr_running:
            self.start_buttons["db"].config(state="disabled")
        elif not db_running:
            self.start_buttons["db"].config(state="normal")

        if db_running:
            self.start_buttons["ocr"].config(state="disabled")
        elif not ocr_running:
            self.start_buttons["ocr"].config(state="normal")

    def _update_status(self):
        for key, proc in processes.items():
            running = proc is not None and proc.poll() is None
            self._set_status(key, running)
        self.after(2000, self._update_status)


if __name__ == "__main__":
    app = WEGStarter()
    app.mainloop()
