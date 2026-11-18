# Changelog

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
