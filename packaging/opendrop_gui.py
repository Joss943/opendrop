"""Entry point used by PyInstaller to build the standalone desktop app."""

import sys

from opendrop.gui import main

if __name__ == "__main__":
    sys.exit(main())
