"""
OpenDrop: an open source AirDrop implementation
Copyright (C) 2018  Milan Stute
Copyright (C) 2018  Alexander Heinrich

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import io
import json
import logging
import os
import plistlib
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from . import archive, util

logger = logging.getLogger(__name__)

SO_RECV_ANYIF = 0x1104  # macOS: allow traffic on the AWDL interface
MAX_PLIST_SIZE = 16 * 1024 * 1024  # /Discover and /Ask bodies (incl. icons)
REQUEST_TIMEOUT = 60  # seconds of inactivity before a connection is dropped
LINKS_FILE = "received_links.txt"


@dataclass
class TransferRequest:
    """What a sender asks us to accept in ``/Ask``."""

    sender_name: str
    sender_model: str
    client_address: str
    files: list = field(default_factory=list)  # file names
    urls: list = field(default_factory=list)
    file_types: list = field(default_factory=list)  # Apple UTIs
    icon: bytes = None  # JPEG 2000 preview sent by the sender, if any

    @property
    def is_url(self):
        return bool(self.urls)

    def describe(self):
        items = self.urls if self.is_url else self.files
        return f"{self.sender_name}: {', '.join(map(str, items)) or 'file(s)'}"


class SessionStore:
    """
    Remembers which peers were allowed to upload after an accepted ``/Ask``.
    Without this, anyone could skip ``/Ask`` and push files directly.
    """

    def __init__(self, timeout):
        self.timeout = timeout
        self._sessions = {}
        self._lock = threading.Lock()

    def allow(self, peer, request):
        with self._lock:
            self._sessions[peer] = (time.monotonic() + self.timeout, request)

    def consume(self, peer):
        now = time.monotonic()
        with self._lock:
            entry = self._sessions.pop(peer, None)
            for key in [k for k, (exp, _) in self._sessions.items() if exp < now]:
                del self._sessions[key]
        if entry is None or entry[0] < now:
            return None
        return entry[1]


class AirDropServer:
    """
    HTTPS AirDrop server, announced in the local network via mDNS.

    :param config: :class:`~opendrop.config.AirDropConfig`
    :param bind_host: address to listen on (``"::"`` = all interfaces)
    :param announce: set to ``False`` to skip mDNS (useful for tests)
    """

    def __init__(self, config, bind_host="::", announce=True):
        self.config = config
        self.iface = None
        self.ip_addr = None
        self.zeroconf = None
        self.service_info = None
        self._serve_thread = None
        # Optional ``listener(event, **data)`` for UIs. Events: "links",
        # "upload_started", "upload_failed", "upload_done".
        self.listener = None
        self.on_transfer_complete = None  # callback(list_of_paths)

        if announce:
            self.iface = util.require_interface(self.config.interface)
            self.ip_addr = self.iface.address

        self.http_server = self._init_server(bind_host)

        if announce:
            self.zeroconf = util.make_zeroconf(self.iface, self.config.interface)
            self.service_info = self._init_service()

    @property
    def port(self):
        return self.http_server.server_address[1]

    def _init_service(self):
        from zeroconf import ServiceInfo  # pylint: disable=import-outside-toplevel

        properties = self.get_properties()
        server = self.config.host_name + ".local."
        service_name = self.config.service_id + "._airdrop._tcp.local."
        return ServiceInfo(
            "_airdrop._tcp.local.",
            service_name,
            port=self.port,
            properties=properties,
            server=server,
            addresses=[self.ip_addr.packed],
        )

    def start_service(self):
        if self.zeroconf is None:
            return
        logger.info(
            f"Announcing service: host {self.config.host_name}, "
            f"address {self.ip_addr}, port {self.port}"
        )
        self.zeroconf.register_service(self.service_info)

    def _init_server(self, bind_host):
        handler = type(
            "BoundAirDropServerHandler", (AirDropServerHandler,), {"config": self.config}
        )
        if ":" in bind_host and not socket.has_ipv6:
            bind_host = "0.0.0.0"
        server_class = HTTPServerV6 if ":" in bind_host else ThreadingHTTPServerV4

        httpd = None
        last_error = None
        # If the port is in use, try the next few ones
        for port in range(self.config.port, self.config.port + 10):
            try:
                httpd = server_class((bind_host, port), handler, bind_and_activate=False)
                if util.is_macos() and util.is_awdl(self.config.interface):
                    httpd.socket.setsockopt(socket.SOL_SOCKET, SO_RECV_ANYIF, 1)
                httpd.server_bind()
                httpd.server_activate()
                break
            except OSError as e:
                last_error = e
                if httpd is not None:
                    httpd.server_close()
                httpd = None
        if httpd is None:
            raise last_error
        self.config.port = httpd.server_address[1]

        # The TLS handshake is done in the per-connection thread (see
        # AirDropServerHandler.setup) so a slow client cannot block accept().
        httpd.socket = self.config.get_server_ssl_context().wrap_socket(
            httpd.socket, server_side=True, do_handshake_on_connect=False
        )
        httpd.sessions = SessionStore(self.config.session_timeout)
        httpd.connection_slots = threading.BoundedSemaphore(self.config.max_connections)
        httpd.airdrop_server = self
        return httpd

    def start_server(self, blocking=True):
        logger.info(f"Starting HTTPS server on port {self.port}")
        logger.info(f"Received files will be saved to {self.config.receive_dir}")
        if blocking:
            self.http_server.serve_forever()
        else:
            self._serve_thread = threading.Thread(
                target=self.http_server.serve_forever, name="opendrop-server", daemon=True
            )
            self._serve_thread.start()

    def stop(self):
        if self.zeroconf is not None:
            try:
                self.zeroconf.unregister_all_services()
            finally:
                self.zeroconf.close()
                self.zeroconf = None
        if self._serve_thread is not None:
            # shutdown() blocks forever unless serve_forever() is running in
            # another thread, so only call it in that case.
            self.http_server.shutdown()
            self._serve_thread.join()
            self._serve_thread = None
        self.http_server.server_close()

    def get_properties(self):
        return {b"flags": str(self.config.flags).encode("utf-8")}

    def notify(self, event, **data):
        if event == "upload_done" and self.on_transfer_complete is not None:
            self.on_transfer_complete(data.get("paths", []))
        if self.listener is not None:
            try:
                self.listener(event, **data)
            except Exception:  # pylint: disable=broad-except
                logger.exception(f"Listener failed for event {event}")


class _ThreadingServerMixin(ThreadingMixIn):
    daemon_threads = True
    allow_reuse_address = False
    connection_slots = None

    def process_request(self, request, client_address):
        # Bound the number of simultaneous connections
        slots = self.connection_slots
        if slots is not None and not slots.acquire(blocking=False):
            logger.warning(f"Too many connections, dropping {client_address[0]}")
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            if self.connection_slots is not None:
                self.connection_slots.release()

    def handle_error(self, request, client_address):
        logger.debug(f"Error while handling request from {client_address}", exc_info=True)


class HTTPServerV6(_ThreadingServerMixin, HTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        # Accept IPv4 connections too where the OS supports dual-stack sockets
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (OSError, AttributeError):
            pass
        super().server_bind()


class ThreadingHTTPServerV4(_ThreadingServerMixin, HTTPServer):
    address_family = socket.AF_INET


class HTTPChunkedReader(io.RawIOBase):
    """Decode an HTTP/1.1 ``Transfer-Encoding: chunked`` request body."""

    def __init__(self, rfile):
        super().__init__()
        self.rfile = rfile
        self.chunk = b""
        self.total = 0
        self.eof = False

    def readable(self):
        return True

    def _next_chunk(self):
        while not self.chunk and not self.eof:
            line = self.rfile.readline(1024)
            if not line:
                raise archive.ArchiveError("Connection closed during upload")
            size_str = line.split(b";", 1)[0].strip()  # ignore chunk extensions
            if not size_str:
                continue
            length = int(size_str, 16)
            if length == 0:
                # consume optional trailers up to the terminating empty line
                while True:
                    trailer = self.rfile.readline(1024)
                    if trailer in (b"\r\n", b"\n", b""):
                        break
                self.eof = True
                return
            self.chunk = self.rfile.read(length)
            if len(self.chunk) != length:
                raise archive.ArchiveError("Connection closed during upload")
            self.rfile.readline()  # strip trailing \r\n

    def readinto(self, buf):
        self._next_chunk()
        if self.eof and not self.chunk:
            return 0
        length = min(len(self.chunk), len(buf))
        buf[:length] = self.chunk[:length]
        self.chunk = self.chunk[length:]
        self.total += length
        return length


class AirDropServerHandler(BaseHTTPRequestHandler):
    """
    Server which responds to AirDrop HTTP POST requests
    """

    protocol_version = "HTTP/1.1"
    config = None
    timeout = REQUEST_TIMEOUT

    def setup(self):
        self.request.settimeout(self.timeout)
        if isinstance(self.request, ssl.SSLSocket):
            self.request.do_handshake()
        super().setup()

    @property
    def peer(self):
        return self.client_address[0]

    def _send_plist(self, payload):
        body = plistlib.dumps(payload, fmt=plistlib.FMT_BINARY)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return body

    def _send_empty(self, status, close=False):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_PLIST_SIZE:
            return None
        return self.rfile.read(length)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        logger.debug(f"GET request at {self.path}")
        body = b"\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_discover(self):
        post_data = self._read_body()
        if post_data is None:
            self._send_empty(413, close=True)
            return

        util.write_debug(self.config, post_data, "receive_discover_request.plist")

        # Don't advertise any codec/container support so that we receive legacy
        # file formats (JPEG instead of HEIF, etc.)
        media_capabilities = {"Version": 1}
        discover_answer = {
            "ReceiverMediaCapabilities": json.dumps(media_capabilities).encode("utf-8"),
            "ReceiverComputerName": self.config.computer_name,
            "ReceiverModelName": self.config.computer_model,
        }
        if self.config.record_data:
            discover_answer["ReceiverRecordData"] = self.config.record_data

        body = self._send_plist(discover_answer)
        util.write_debug(self.config, body, "receive_discover_response.plist")

    def handle_ask(self):
        post_data = self._read_body()
        if post_data is None:
            self._send_empty(413, close=True)
            return

        util.write_debug(self.config, post_data, "receive_ask_request.plist")

        try:
            request = plistlib.loads(post_data) if post_data else {}
            if not isinstance(request, dict):
                request = {}
        except (plistlib.InvalidFileException, ValueError):
            request = {}

        files = [f for f in request.get("Files", []) if isinstance(f, dict)]
        icon = request.get("FileIcon")
        transfer = TransferRequest(
            sender_name=str(request.get("SenderComputerName", "unknown sender")),
            sender_model=str(request.get("SenderModelName", "")),
            client_address=self.peer,
            files=[str(f.get("FileName", "?")) for f in files],
            urls=[str(u) for u in request.get("Items", [])],
            file_types=[str(f.get("FileType", "")) for f in files],
            icon=icon if isinstance(icon, (bytes, bytearray)) else None,
        )
        logger.info(f"Incoming request from {transfer.describe()}")

        if not self._accept(transfer):
            logger.info("Declined")
            self._send_empty(403, close=True)
            return

        if transfer.is_url:
            self._store_links(transfer)
            self._notify("links", transfer=transfer)
        else:
            self.server.sessions.allow(self.peer, transfer)

        ask_response = {
            "ReceiverModelName": self.config.computer_model,
            "ReceiverComputerName": self.config.computer_name,
        }
        body = self._send_plist(ask_response)
        util.write_debug(self.config, body, "receive_ask_response.plist")

    def _notify(self, event, **data):
        owner = getattr(self.server, "airdrop_server", None)
        if owner is not None:
            owner.notify(event, **data)

    def _accept(self, transfer):
        callback = self.config.accept_callback
        if callback is None:
            return True
        try:
            return bool(callback(transfer))
        except Exception:  # pylint: disable=broad-except
            logger.exception("Accept callback failed; declining")
            return False

    def _store_links(self, transfer):
        for url in transfer.urls:
            logger.info(f"  link: {url}")
        if not self.config.save_links:
            return
        os.makedirs(self.config.receive_dir, exist_ok=True)
        path = os.path.join(self.config.receive_dir, LINKS_FILE)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with _links_lock, open(path, "a", encoding="utf-8") as f:
            for url in transfer.urls:
                clean = url.replace("\n", " ").replace("\r", " ")
                f.write(f"{stamp}\t{transfer.sender_name}\t{clean}\n")
        logger.info(f"  saved to {path}")

    def handle_upload(self):
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != "application/x-cpio":
            logger.warning(f"Unsupported content-type: {self.headers.get('Content-Type')}")
            self._send_empty(415, close=True)
            return

        transfer = self.server.sessions.consume(self.peer)
        if transfer is None:
            logger.warning(f"Rejected upload from {self.peer} without an accepted /Ask")
            self._send_empty(403, close=True)
            return

        # Note: 'Expect: 100-continue' is answered automatically by
        # BaseHTTPRequestHandler before do_POST is called.

        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            reader = HTTPChunkedReader(self.rfile)
        else:
            try:
                length = int(self.headers.get("Content-Length", -1))
            except ValueError:
                length = -1
            if length < 0:
                logger.warning("Upload without chunked encoding or Content-Length")
                self._send_empty(411, close=True)
                return
            reader = _LimitedReader(self.rfile, length)

        logger.info("Receiving file(s) ...")
        self._notify("upload_started", transfer=transfer)
        start = time.perf_counter()
        try:
            created = archive.extract_archive(
                reader, self.config.receive_dir, max_bytes=self.config.max_upload_size
            )
        except (archive.ArchiveError, OSError, EOFError) as e:
            logger.error(f"Receiving failed: {e}")
            self._notify("upload_failed", transfer=transfer, error=str(e))
            self._send_empty(400, close=True)
            return

        transferred = reader.total / 1024.0 / 1024.0
        speed = transferred / max(time.perf_counter() - start, 1e-3)
        logger.info(f"File(s) received (size {transferred:.02f} MB, speed {speed:.02f} MB/s)")
        for path in created:
            logger.info(f"  saved {path}")

        self._send_empty(200, close=True)
        self._notify("upload_done", transfer=transfer, paths=created, size=reader.total)

    def do_POST(self):
        logger.debug(f"POST request at {self.path}")
        logger.debug(f"Headers\n{self.headers}")

        if self.path == "/Discover":
            self.handle_discover()
        elif self.path == "/Ask":
            self.handle_ask()
        elif self.path == "/Upload":
            self.handle_upload()
        else:
            self._send_empty(404, close=True)

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        logger.debug(
            f"{self.client_address[0]} - - [{self.log_date_time_string()}] {format % args}"
        )


_links_lock = threading.Lock()


class _LimitedReader(io.RawIOBase):
    def __init__(self, rfile, length):
        super().__init__()
        self.rfile = rfile
        self.remaining = length
        self.total = 0

    def readable(self):
        return True

    def readinto(self, buf):
        if self.remaining <= 0:
            return 0
        data = self.rfile.read(min(len(buf), self.remaining))
        n = len(data)
        buf[:n] = data
        self.remaining -= n
        self.total += n
        return n
