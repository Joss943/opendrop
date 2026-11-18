# OpenDrop on Windows

> [!WARNING]
> **Windows is not compatible with AirDrop.** iPhones, Macs and Android phones (Quick Share ↔ AirDrop)
> cannot be reached from Windows. Only OpenDrop-to-OpenDrop transfers work.

## The short version

* ✅ OpenDrop installs and runs on Windows (`pip install`, no native libraries).
* ✅ Windows ↔ macOS/Linux/Windows **OpenDrop** transfers work over a normal network
  (`-i Wi-Fi` or `-i Ethernet`).
* ❌ Windows cannot talk to **iPhones/Macs** directly, because AirDrop only runs over
  Apple Wireless Direct Link (AWDL), and Windows has no AWDL driver. No Python code can
  change that: it requires driver-level control of the Wi-Fi card.
* 🧪 Experimental workaround: run OpenDrop + OWL inside WSL2 with a USB Wi-Fi adapter.

## Exchanging files with other OpenDrop peers

```powershell
opendrop doctor                       # lists interfaces, e.g. "Wi-Fi", "Ethernet"
opendrop receive -i Wi-Fi -o $HOME\Downloads\OpenDrop
```

On the other computer (any OS, same network):

```bash
opendrop find -i <its interface> -t 5
opendrop send -i <its interface> -r <name or index> -f file.pdf
```

Notes:

* Both computers must be on the same network segment (mDNS does not cross routers).
* IPv6 link-local addresses are used when available; on networks without IPv6, OpenDrop
  falls back to IPv4 automatically.
* Allow Python through Windows Firewall on *private* networks when asked. If you clicked
  "Cancel", re-enable it in *Windows Security → Firewall → Allow an app*.
* Some VPN clients (NordVPN, Tailscale…) create virtual adapters; always select the real
  interface with `-i`.

## Experimental: Apple devices through WSL2 + OWL

This needs a **USB** Wi-Fi adapter that supports monitor mode and frame injection
(e.g. based on Atheros AR9271 or MediaTek MT7612U chips), because the built-in card cannot be
passed through to WSL2.

1. Install [usbipd-win](https://github.com/dorssel/usbipd-win) and attach the adapter:
   ```powershell
   usbipd list
   usbipd bind --busid <BUSID>
   usbipd attach --wsl --busid <BUSID>
   ```
2. Inside WSL2 you need a kernel with the driver for your adapter (the default WSL kernel
   does not ship most Wi-Fi drivers, so you will likely have to build a custom kernel).
3. Follow the Linux instructions in [INSTALL.md](INSTALL.md) to build OWL and start it:
   `sudo owl -i wlan0`.
4. Run OpenDrop inside WSL2: `opendrop find`.

This path is untested and hardware-dependent; treat it as a research setup.
