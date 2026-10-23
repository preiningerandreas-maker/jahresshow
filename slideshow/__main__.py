import multiprocessing
import sys

from slideshow.cli import main

if __name__ == "__main__":
    # Vorschaubilder entstehen in eigenen Prozessen (auch im fertigen .exe-Paket)
    multiprocessing.freeze_support()
    sys.exit(main())
