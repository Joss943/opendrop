"""OpenDrop desktop application (Qt / PySide6). Start it with ``opendrop gui``."""


def main(argv=None):
    from .app import main as _main  # pylint: disable=import-outside-toplevel

    return _main(argv)
