"""Einstieg des fertigen Pakets (PyInstaller, siehe Jahresshow.spec)."""

import multiprocessing
import sys

if __name__ == "__main__":
    # Zuerst: Die Hilfsprozesse für Vorschaubilder starten dieselbe exe. Ohne freeze_support
    # würde jeder von ihnen ein weiteres Hauptfenster öffnen.
    multiprocessing.freeze_support()

    from slideshow.cli import main

    sys.exit(main())
