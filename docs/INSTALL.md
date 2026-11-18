# Installation

OpenDrop is a pure-Python package. Its dependencies (`zeroconf`, `ifaddr`, `cryptography`,
`Pillow`) ship pre-built wheels for macOS, Linux and Windows, so `pip` is all you need.

After installing, run:

```bash
opendrop doctor
```

It lists your network interfaces and tells you what is missing.

## macOS

```bash
python3 -m pip install --user git+https://github.com/Joss943/opendrop.git
```

* The AWDL interface `awdl0` is used by default. It only has an address while Wi-Fi is on.
* Homebrew's `libarchive` is **no longer required**.
* The first time you run `opendrop receive`, macOS may ask whether Python may accept
  incoming connections: allow it.

## Linux

```bash
python3 -m pip install --user git+https://github.com/Joss943/opendrop.git
```

To talk to Apple devices you need [OWL](https://github.com/seemoo-lab/owl), an open
implementation of AWDL. OWL needs a Wi-Fi card that supports monitor mode and frame injection.

```bash
sudo apt install libpcap-dev libev-dev libnl-3-dev libnl-genl-3-dev libnl-route-3-dev cmake
git clone --recursive https://github.com/seemoo-lab/owl.git
cd owl && mkdir build && cd build && cmake .. && make && sudo make install
sudo owl -i wlan0      # creates the awdl0 interface
```

Then, in another terminal, use `opendrop` as usual (it defaults to `awdl0`).

Without OWL you can still use OpenDrop with other OpenDrop peers: `opendrop receive -i wlan0`.

## Windows

```powershell
py -m pip install git+https://github.com/Joss943/opendrop.git
opendrop doctor
```

If `opendrop` is not found, use `py -m opendrop ...` or add Python's `Scripts` folder to
your `PATH`. Interfaces can be referred to by their friendly name: `-i Wi-Fi`,
`-i Ethernet`. Windows Firewall will ask to allow Python on first `receive`: allow it on
private networks.

Read [WINDOWS.md](WINDOWS.md) for what is and isn't possible on Windows.

## From source (all platforms)

```bash
git clone https://github.com/Joss943/opendrop.git
cd opendrop
python -m venv .venv
# macOS/Linux: source .venv/bin/activate     Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

### Optional: libarchive

OpenDrop reads and writes AirDrop archives (gzip-compressed CPIO) itself. If a sender ever
uses a format it does not know, install the optional fallback:

```bash
pip install "opendrop[libarchive]"
```

(this requires the native libarchive library: `brew install libarchive`,
`apt install libarchive13`, …).
