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
import plistlib
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from . import archive, util

logger = logging.getLogger(__name__)

SO_RECV_ANYIF = 0x1104  # macOS: allow traffic on the AWDL interface
MAX_PLIST_SIZE = 16 * 1024 * 1024  # /Discover and /Ask bodies (incl. icons)


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

        httpd.socket = self.config.get_server_ssl_context().wrap_socket(
            httpd.socket, server_side=True
        )
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


class _ThreadingServerMixin(ThreadingMixIn):
    daemon_threads = True
    allow_reuse_address = False

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
        except (plistlib.InvalidFileException, ValueError):
            request = {}
        sender = request.get("SenderComputerName", "unknown sender")
        if "Items" in request:
            logger.info(f"{sender} wants to send: {', '.join(map(str, request['Items']))}")
            logger.info("(Receiving links is not supported; only files are saved.)")
        else:
            names = [f.get("FileName", "?") for f in request.get("Files", [])]
            logger.info(f"{sender} wants to send: {', '.join(names) or 'file(s)'}")

        ask_response = {
            "ReceiverModelName": self.config.computer_model,
            "ReceiverComputerName": self.config.computer_name,
        }
        body = self._send_plist(ask_response)
        util.write_debug(self.config, body, "receive_ask_response.plist")

    def handle_upload(self):
        if self.headers.get("Content-Type", "").lower() != "application/x-cpio":
            logger.warning(f"Unsupported content-type: {self.headers.get('Content-Type')}")
            self._send_empty(415, close=True)
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
        start = time.time()
        try:
            created = archive.extract_archive(reader, self.config.receive_dir)
        except (archive.ArchiveError, OSError, EOFError) as e:
            logger.error(f"Receiving failed: {e}")
            self._send_empty(400, close=True)
            return

        transferred = reader.total / 1024.0 / 1024.0
        speed = transferred / max(time.time() - start, 1e-6)
        logger.info(f"File(s) received (size {transferred:.02f} MB, speed {speed:.02f} MB/s)")
        for path in created:
            logger.info(f"  saved {path}")

        self._send_empty(200, close=True)

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
