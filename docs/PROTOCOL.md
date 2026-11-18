# How OpenDrop works

## AirDrop in a nutshell

1. **Link layer – AWDL.** Apple devices form an ad-hoc Wi-Fi link (Apple Wireless Direct
   Link) exposed as the `awdl0` interface. Only IPv6 link-local addresses are used on it.
   (Apple devices first wake each other up via Bluetooth LE; OpenDrop does not implement
   that part.)
2. **Discovery – mDNS/DNS-SD.** Receivers announce a `_airdrop._tcp.local.` service whose
   instance name is a random 12-hex-digit ID. The TXT record carries a `flags` bitmask
   (`AirDropReceiverFlags` in `config.py`).
3. **Transport – HTTPS.** Each receiver runs an HTTPS server (port 8770/8771). Both sides
   present a TLS certificate; Apple devices accept self-signed ones in *Everyone* mode.
4. **Requests** (bodies are binary property lists):
   * `POST /Discover` – the sender asks for the receiver's name. A receiver that does not
     want to be seen by us answers without `ReceiverComputerName`.
   * `POST /Ask` – sender name, model, file list (`Files`) or URLs (`Items`) and an optional
     JPEG 2000 thumbnail (`FileIcon`). The receiver answers 200 once the user accepts.
   * `POST /Upload` – the files as a gzip-compressed CPIO archive (`application/x-cpio`),
     sent with `Transfer-Encoding: chunked`.

## Receiver state machine

```
/Discover ──► name returned (always)
/Ask ──► accept_callback(TransferRequest)
          ├─ declined ─► 403
          ├─ URLs ─────► 200, links saved to received_links.txt
          └─ files ────► 200, peer IP stored in SessionStore (10 min)
/Upload ──► SessionStore.consume(peer IP)
          ├─ none ─────► 403
          └─ ok ───────► archive.extract_archive(max_bytes=…) ─► 200 / 400
```

## Code layout

| Module | Role |
|--------|------|
| `opendrop/cli.py` | Command line (`find`, `send`, `receive`, `doctor`) |
| `opendrop/client.py` | `AirDropBrowser` (mDNS discovery) and `AirDropClient` (`/Discover`, `/Ask`, `/Upload`) |
| `opendrop/server.py` | `AirDropServer` (mDNS announcement + threaded HTTPS server) and request handlers |
| `opendrop/archive.py` | Pure-Python CPIO reader/writer with safe extraction |
| `opendrop/config.py` | Settings, TLS certificates and SSL contexts |
| `opendrop/util.py` | Network interface lookup (cross-platform), UTI types, thumbnails |
| `opendrop/doctor.py` | `opendrop doctor` diagnostics |

## Cross-platform details

* **Interfaces.** On Windows, interfaces have GUID names; `util.find_interface` also matches
  the friendly name (`Wi-Fi`) or the numeric index.
* **IPv6 scope.** Link-local addresses need a scope (interface). Instead of the
  `fe80::1%awdl0` string syntax (which does not work on Windows), `HTTPSConnectionAWDL`
  connects with a `(host, port, 0, scope_id)` socket address.
* **mDNS.** Zeroconf is bound to the interface *index*: on Windows several virtual adapters
  can share the same link-local address, so an address alone is ambiguous.
* **macOS.** Sockets on `awdl0` need the private `SO_RECV_ANYIF` (0x1104) option.
* **Certificates.** Generated with `cryptography` (no `openssl` binary needed).
* **Archives.** No `libarchive` needed; received names are sanitized for Windows
  (`:`, `?`, `CON`…), `..`/absolute paths and symlinks are refused.
