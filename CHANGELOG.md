# Changelog

## 0.16.0 – desktop app

* **Desktop app** (Qt/PySide6): `opendrop gui` or `opendrop-gui`, installed with
  `pip install "opendrop[gui]"`. Nearby devices, drag-and-drop sending with progress,
  accept/decline window with the photo preview sent by iPhones, received-files history,
  settings, system tray and notifications, English and French.
* Standalone builds for Windows, macOS and Linux (PyInstaller) in the *Desktop app*
  workflow; attached to GitHub releases for `v*` tags.
* `opendrop.engine.Engine`: background service (receiver, discovery, send jobs, history)
  publishing events, reusable by any UI. Receivers can now be added by IP address when mDNS
  is blocked.
* Receiver events (`listener`) and richer `TransferRequest` (file types, preview).
* The interface is chosen automatically in the app (`awdl0` when available).
* Documentation: Windows is explicitly **not compatible with AirDrop** (no AWDL); notes on
  Android (Quick Share ↔ AirDrop) and Hackintosh.

## 0.15.0

### Security
* Uploads are only accepted right after an accepted `/Ask` from the same peer (previously
  anyone could push files straight to `/Upload`).
* Optional interactive confirmation: `receive --confirm`.
* Size limits: `receive --max-size 2G`, a cap on the number of archive entries, and a free
  disk space check. Partially written files are deleted when a transfer fails.
* Slow or idle clients can no longer freeze the receiver: TLS handshakes happen in the
  connection's own thread, idle connections time out after 60 s, and at most 16 connections
  are served at once.
* Key directory created with `0700` permissions.

### Features
* `send --scan SECONDS`: find the receiver and send in one command.
* Upload progress bar (`--no-progress` to hide it).
* `find --json`: machine-readable output.
* `receive --once`: exit after the first transfer.
* Received links are saved to `received_links.txt` (`--no-save-links` to disable).
* `opendrop doctor` also checks the HTTPS port, the configuration directory and firewall hints.
* Python API: `AirDropConfig(accept_callback=...)` receives a `TransferRequest`
  (sender, files, URLs) to accept or decline.

### Compatibility
* Accept `Content-Type: application/x-cpio; …` with parameters.
* Python 3.14 added to CI; Python 3.9 tested on Ubuntu 22.04 (no longer built for newer
  Ubuntu images).
* Accurate transfer speed on Windows (high-resolution timer).
* `pipx` installation documented.

## 0.14.0 – maintained fork

### Platform support
* **Windows support**: interfaces can be selected by friendly name (`-i Wi-Fi`), IPv6
  link-local connections use numeric scope ids, Ctrl+C works, device names with non-ASCII
  characters no longer crash the console, received file names are sanitized.
* **No native dependencies**: `libarchive` replaced by a pure-Python CPIO implementation
  (`opendrop/archive.py`); the `openssl` command replaced by the `cryptography` package.
  `libarchive-c` remains available as an optional fallback (`opendrop[libarchive]`).
* IPv4 fallback for OpenDrop-to-OpenDrop transfers on networks without IPv6.
* Python 3.9 – 3.13 supported; CI runs on Linux, macOS and Windows.

### Bug fixes
* Crash with Python ≥ 3.12: `HTTPSConnection(key_file=…, cert_file=…)` no longer exists.
* Crash with Pillow ≥ 10: `Image.ANTIALIAS` was removed.
* Crash with recent setuptools: `pkg_resources` is deprecated/removed.
* **Security**: received archives could write outside the current directory
  (`../` paths, absolute paths, symlinks). Extraction is now confined to the output folder
  and never overwrites existing files.
* The chunked upload reader could block forever after the last chunk and did not handle
  chunk extensions/trailers.
* The server answered `100 Continue` twice (Python already does it).
* `AirDropServer.stop()` dead-locked when the server was not running in a thread, and
  never closed zeroconf.
* `send --url` reported "Uploading has failed" although the link had been sent.
* Each file in `/Ask` got the type of the *first* file; `FileIsDirectory` was computed on
  the wrong path.
* `find`: crashes when a service could not be resolved or had an empty `flags` value; race
  condition on the result index; duplicates; results were only saved on Ctrl+C.
* `find`/`send`: network and TLS errors (other than timeouts) crashed the program; there was
  no timeout at all, so an unreachable peer could hang forever.
* Missing `Content-Length` headers crashed the request handlers.
* Self-signed certificates expired after one year and were never renewed.
* Host names ending in `.local` (macOS) produced `name.local.local.` in mDNS.
* On Windows, zeroconf could bind to the wrong adapter when several share a link-local address.
* The `DYLD_LIBRARY_PATH` hack had no effect (set after process start) and was removed.
* Unused dependencies (`requests`, `requests_toolbelt`, `fleep`) removed.

### New features
* `opendrop doctor` – checks dependencies, interfaces and AWDL support.
* `send` accepts several files and folders (`-f a.jpg b.pdf Folder/`).
* `receive -o DIR` – choose where files are saved; `--port`.
* `find -t SECONDS` – stop automatically.
* `--config-dir` / `$OPENDROP_HOME`, `--version`, meaningful exit codes.
* Receiver logs who is sending what, and where files were saved.
* Multi-threaded receiver; large files are streamed instead of being held in RAM.
* Receiver names can be matched case-insensitively.

### Development
* `pyproject.toml` replaces `setup.py`/`setup.cfg`/`Makefile`; `ruff` replaces
  black/isort/flake8/pylint.
* Test suite expanded from 2 to 65 tests, including a full TLS send/receive round trip.
