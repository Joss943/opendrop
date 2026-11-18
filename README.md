# OpenDrop: an open source AirDrop implementation

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

*OpenDrop* shares files between devices directly over Wi-Fi. It is protocol-compatible with
Apple AirDrop, so it can exchange files with iPhones, iPads, Macs and Android phones that
support AirDrop through Quick Share. It comes as a **desktop app** and a command-line tool.

> This is a maintained fork of [seemoo-lab/opendrop](https://github.com/seemoo-lab/opendrop)
> (no longer maintained upstream). See [CHANGELOG.md](CHANGELOG.md).
>
> 🇫🇷 [Lire en français](README.fr.md)

> [!WARNING]
> **Windows is not compatible with AirDrop.** AirDrop needs Apple Wireless Direct Link
> (AWDL), and Windows has no AWDL driver, so a Windows PC **cannot** exchange files with
> iPhones, Macs or Android phones over AirDrop. On Windows, OpenDrop only works with other
> computers running OpenDrop on the same network.

## What works where

| Platform | iPhone / iPad / Mac | Android (Quick Share ↔ AirDrop) | Other OpenDrop computers |
|----------|---------------------|---------------------------------|--------------------------|
| **macOS** | ✅ Native, via `awdl0` | ✅ in theory, untested | ✅ |
| **Linux** | ✅ With [OWL](https://github.com/seemoo-lab/owl) (Wi-Fi card with monitor mode) | ✅ in theory with OWL, untested | ✅ |
| **Windows** | ❌ **Not compatible** (no AWDL) | ❌ **Not compatible** (no AWDL) | ✅ Over the normal network |

**Android:** Google added AirDrop interoperability to Quick Share in late 2025, starting with
the Pixel 10, then the Pixel 9 and some Samsung Galaxy phones in 2026. Set Quick Share to
*Everyone*; like iPhones, these phones need AWDL on the computer side. This has not been
tested with OpenDrop yet.

**Hackintosh:** OpenDrop uses the `awdl0` interface that macOS creates. If AirDrop does not
work on a Hackintosh because its Wi-Fi card/driver has no AWDL support (e.g. most Intel cards),
`awdl0` does not work either and OpenDrop cannot fix that.

"OpenDrop computers" mode uses a normal network (Wi-Fi/Ethernet) instead of AWDL, so
OpenDrop on macOS, Linux or Windows on the same network can exchange files with each other.

## Desktop app

```bash
pip install "opendrop[gui] @ git+https://github.com/Joss943/opendrop.git"
opendrop gui        # or: opendrop-gui
```

Ready-to-run builds (no Python needed) are produced by the *Desktop app* workflow in the
Actions tab, and attached to GitHub releases.

* **Send:** nearby devices appear as icons; drop files on a device, or use the buttons to
  send files, folders or a link. Progress is shown for every transfer.
* **Incoming transfers:** a window shows who is sending what, with the photo preview sent by
  iPhones, and lets you **Accept** or **Decline** (auto-declines after 60 s). Auto-accept can
  be enabled in the settings.
* **Received:** history of received files and links; double-click to open.
* **Settings:** name shown to others, network interface, download folder.
* Runs in the system tray with notifications; available in English and French.

Run `opendrop doctor` to check your setup.

## Installation

Requires **Python 3.9+**. No native libraries are needed any more.

```bash
pip install git+https://github.com/Joss943/opendrop.git
```

or from a clone:

```bash
git clone https://github.com/Joss943/opendrop.git
cd opendrop
pip install .
```

Recommended: install it as an isolated command-line app with [pipx](https://pipx.pypa.io):
`pipx install git+https://github.com/Joss943/opendrop.git`

On Windows, use `py -m pip install .` if `pip` is not on your `PATH`; `py -m opendrop` always works.
Platform-specific notes: [docs/INSTALL.md](docs/INSTALL.md).

## Usage

```text
opendrop {receive,find,send,doctor} [options]
```

Options shared by all commands:

| Option | Meaning |
|--------|---------|
| `-i, --interface` | Interface to use. Default `awdl0`. On Windows the friendly name works (`-i Wi-Fi`). |
| `-n, --name` | Computer name shown on the other device |
| `-m, --model` | Computer model shown on the other device |
| `-d, --debug` | Verbose logs; raw protocol messages are saved to `~/.opendrop/debug` |
| `--config-dir` | Where keys and discovery results live (default `~/.opendrop`, or `$OPENDROP_HOME`) |

### Find receivers

```console
$ opendrop find
Looking for receivers. Press Ctrl+C to stop ...
Found  index 0  ID eccb2f2dcfe7  name John’s iPhone
Found  index 1  ID e63138ac6ba8  name Jane’s MacBook Pro
```

Use `-t 10` to stop automatically after 10 seconds, and `--json` to get one JSON object per
receiver on stdout (handy in scripts).
Results are saved to `~/.opendrop/discover.last.json` as soon as they are found.

### Send files, folders or a link

```console
$ opendrop send -r 0 -f photo.jpg
Asking receiver to accept ...
Receiver accepted
Uploading file(s) ...
Uploading has been successful
```

* `-r` accepts the **index**, the **ID** or the **name** (case-insensitive) from `find`.
* `-f` accepts **several files and folders**: `opendrop send -r 0 -f a.pdf b.jpg Photos/`
* Send a web link with `--url`: `opendrop send -r 0 -f https://example.org --url`
* **One step, no separate `find`:** `opendrop send --scan 10 -r "Jane’s MacBook Pro" -f a.pdf`
  looks for that receiver for up to 10 seconds and sends as soon as it shows up.
* A progress bar is shown while uploading (`--no-progress` to hide it).

Exit codes: `0` success, `1` error, `2` declined by the receiver.

### Receive files

```console
$ opendrop receive -o ~/Downloads/AirDrop --confirm
Incoming request from Jane’s MacBook Pro: holiday.jpg
Accept? [y/N] y
```

| Option | Meaning |
|--------|---------|
| `-o, --output DIR` | Where to store received files (default: current directory) |
| `-c, --confirm` | Ask before accepting each transfer (otherwise everything is accepted) |
| `--max-size SIZE` | Refuse transfers bigger than `SIZE`, e.g. `500M`, `2G` |
| `--once` | Exit after the first successful transfer |
| `--no-save-links` | Received links are normally appended to `received_links.txt` |
| `--port` | HTTPS port (default 8771, the next free one is used if busy) |

## Security

The receiver is hardened against malicious senders:

* files can only be uploaded right after an **accepted** `/Ask` from the same peer;
* archives cannot write outside the output directory (`..`, absolute paths, symlinks,
  devices are refused), never overwrite existing files and never leave partial files behind;
* optional confirmation (`--confirm`) and size limit (`--max-size`); free disk space is checked;
* idle connections time out after 60 s, at most 16 connections are served at once, and the
  TLS handshake no longer blocks other clients;
* received links are only saved to a text file, never opened automatically.

There is still **no peer authentication** (see Limitations): with `--confirm` off, anyone in
radio range can send you files. Use `--confirm` on untrusted networks.

### Between two computers without AWDL

```bash
# computer A (e.g. Windows)
opendrop receive -i Wi-Fi -n "PC A"
# computer B (e.g. Linux)
opendrop find -i wlan0 -t 5
opendrop send -i wlan0 -r "PC A" -f report.pdf
```

## Contacts-only receivers

Apple devices set to *Contacts Only* require Apple-signed certificates. You can use
AirDrop credentials extracted from macOS with the
[keychain extractor](https://github.com/seemoo-lab/airdrop-keychain-extractor): put
`certificate.pem`, `key.pem` and `validation_record.cms` into `~/.opendrop/keys/`.
OpenDrop never overwrites certificates that are not its own self-signed ones.

## Limitations

* **Waking up Apple receivers via Bluetooth LE is not implemented.** Apple devices only
  turn on AWDL after receiving a BLE advertisement, so an iPhone may not show up even when
  it is discoverable by *Everyone*. Opening the AirDrop share sheet on the iPhone/Mac
  usually helps.
* **No peer authentication.** OpenDrop does not verify that the TLS certificate is signed by
  Apple or that the validation record is valid.
* Receivers accept everything automatically unless `--confirm` is used.
* HEIC/Live Photos are not requested: Apple senders convert them to JPEG for OpenDrop.

## Documentation

* [docs/INSTALL.md](docs/INSTALL.md) – installation per OS, OWL setup
* [docs/WINDOWS.md](docs/WINDOWS.md) – what works on Windows and how
* [docs/PROTOCOL.md](docs/PROTOCOL.md) – how AirDrop/OpenDrop work, code layout
* [CONTRIBUTING.md](CONTRIBUTING.md) – development setup and tests

## Papers

* Alexander Heinrich, Matthias Hollick, Thomas Schneider, Milan Stute, and Christian Weinert.
  **PrivateDrop: Practical Privacy-Preserving Authentication for Apple AirDrop.**
  *USENIX Security ’21.* [Paper](https://www.usenix.org/conference/usenixsecurity21/presentation/heinrich)
* Milan Stute, Sashank Narain, Alex Mariotto, Alexander Heinrich, David Kreitschmann,
  Guevara Noubir, and Matthias Hollick. **A Billion Open Interfaces for Eve and Mallory:
  MitM, DoS, and Tracking Attacks on iOS and macOS Through Apple Wireless Direct Link.**
  *USENIX Security ’19.* [Paper](https://www.usenix.org/conference/usenixsecurity19/presentation/stute)

## Authors and license

Original authors: **Milan Stute** and **Alexander Heinrich** (Secure Mobile Networking Lab,
TU Darmstadt). This fork keeps their work under the same license.

OpenDrop is experimental software, not affiliated with or endorsed by Apple Inc.
It is licensed under the [**GNU General Public License v3.0**](LICENSE).
