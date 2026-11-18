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

import ipaddress
import logging
import os
import plistlib
import socket
import tempfile
from http.client import HTTPSConnection

from . import archive, util

logger = logging.getLogger(__name__)

SO_RECV_ANYIF = 0x1104  # macOS: allow traffic on the AWDL interface

AIRDROP_SERVICE = "_airdrop._tcp.local."


class AirDropBrowser:
    """Discover AirDrop receivers via mDNS/DNS-SD on the given interface."""

    def __init__(self, config):
        self.config = config
        self.iface = util.require_interface(config.interface)
        self.ip_addr = self.iface.address
        self.zeroconf = util.make_zeroconf(self.iface, config.interface)

        self.callback_add = None
        self.callback_remove = None
        self.browser = None

    def start(self, callback_add=None, callback_remove=None):
        """
        Start the AirDropBrowser to discover other AirDrop devices
        """
        if self.browser is not None:
            return  # already started
        self.callback_add = callback_add
        self.callback_remove = callback_remove
        # zeroconf is imported lazily so that `opendrop --help` stays fast
        from zeroconf import ServiceBrowser  # pylint: disable=import-outside-toplevel

        self.browser = ServiceBrowser(self.zeroconf, AIRDROP_SERVICE, self)

    def stop(self):
        if self.browser is not None:
            self.browser.cancel()
            self.browser = None
        if self.zeroconf is not None:
            self.zeroconf.close()
            self.zeroconf = None

    # zeroconf ServiceListener interface

    def add_service(self, zeroconf, service_type, name):
        logger.debug(f"Add service {name}")
        info = zeroconf.get_service_info(service_type, name)
        if info is None:
            logger.debug(f"Could not resolve service {name}")
            return
        if self.callback_add is not None:
            self.callback_add(info)

    def update_service(self, zeroconf, service_type, name):
        logger.debug(f"Update service {name}")

    def remove_service(self, zeroconf, service_type, name):
        logger.debug(f"Remove service {name}")
        if self.callback_remove is not None:
            self.callback_remove(name)


class AirDropClient:
    """Talk to a single AirDrop receiver (``/Discover``, ``/Ask``, ``/Upload``)."""

    def __init__(self, config, receiver):
        self.config = config
        self.receiver_host = str(receiver[0]).split("%")[0]
        self.receiver_port = int(receiver[1])
        self.http_conn = None
        self._scope_id = None
        iface = util.find_interface(config.interface)
        if iface is not None:
            self._scope_id = iface.index

    def close(self):
        if self.http_conn is not None:
            self.http_conn.close()
            self.http_conn = None

    def send_POST(self, url, body, headers=None, timeout=None):
        logger.debug(f"Send {url} request")

        util.write_debug(self.config, body, f"send_{url.lower().strip('/')}_request.plist")

        _headers = self._get_headers()
        if headers is not None:
            _headers.update(headers)

        if timeout is None:
            timeout = self.config.timeout
        if self.http_conn is None:
            # Use single connection
            self.http_conn = HTTPSConnectionAWDL(
                self.receiver_host,
                self.receiver_port,
                interface_name=self.config.interface,
                scope_id=self._scope_id,
                timeout=timeout,
                context=self.config.get_client_ssl_context(),
            )
        self.http_conn.timeout = timeout
        if self.http_conn.sock is not None:
            self.http_conn.sock.settimeout(timeout)

        try:
            self.http_conn.request("POST", url, body=body, headers=_headers)
            http_resp = self.http_conn.getresponse()
            response_bytes = http_resp.read()
        except Exception:
            self.close()
            raise

        util.write_debug(
            self.config, response_bytes, f"send_{url.lower().strip('/')}_response.plist"
        )

        if http_resp.status != 200:
            logger.debug(f"{url} request failed: {http_resp.status}")
            return False, response_bytes
        logger.debug(f"{url} request successful")
        return True, response_bytes

    def send_discover(self):
        """
        Returns the receiver's computer name, or ``None`` if it is not
        discoverable by us.
        """
        discover_body = {}
        if self.config.record_data:
            discover_body["SenderRecordData"] = self.config.record_data

        discover_plist_binary = plistlib.dumps(discover_body, fmt=plistlib.FMT_BINARY)
        success, response_bytes = self.send_POST("/Discover", discover_plist_binary)
        if not success or not response_bytes:
            return None
        try:
            response = plistlib.loads(response_bytes)
        except (plistlib.InvalidFileException, ValueError):
            logger.debug("Invalid /Discover response")
            return None

        # if name is returned, then receiver is discoverable
        return response.get("ReceiverComputerName")

    def send_ask(self, file_path, is_url=False, icon=None):
        """
        Ask the receiver to accept the given file(s) or URL(s). Blocks until
        the user on the other side accepts/declines (or ``ask_timeout``).
        """
        paths = [file_path] if isinstance(file_path, (str, os.PathLike)) else list(file_path)
        paths = [os.fspath(p) for p in paths]

        ask_body = {
            "SenderComputerName": self.config.computer_name,
            "BundleID": "com.apple.finder",
            "SenderModelName": self.config.computer_model,
            "SenderID": self.config.service_id,
            "ConvertMediaFormats": False,
        }
        if self.config.record_data:
            ask_body["SenderRecordData"] = self.config.record_data

        if is_url:
            ask_body["Items"] = paths
        else:
            ask_body["Files"] = [
                {
                    "FileName": os.path.basename(os.path.normpath(p)),
                    "FileType": util.get_uti_type(p),
                    "FileBomPath": "./" + os.path.basename(os.path.normpath(p)),
                    "FileIsDirectory": os.path.isdir(p),
                    "ConvertMediaFormats": 0,
                }
                for p in paths
            ]
            if icon is None and util.is_image(paths[0]):
                icon = util.generate_file_icon(paths[0])
        if icon:
            ask_body["FileIcon"] = icon

        ask_binary = plistlib.dumps(ask_body, fmt=plistlib.FMT_BINARY)
        success, _ = self.send_POST("/Ask", ask_binary, timeout=self.config.ask_timeout)
        return success

    def send_upload(self, file_path, is_url=False):
        """
        Send file(s) to a receiver as a gzip-compressed CPIO archive.
        """
        # Don't send an upload request if we just sent a link
        if is_url:
            return True

        paths = [file_path] if isinstance(file_path, (str, os.PathLike)) else list(file_path)

        headers = {"Content-Type": "application/x-cpio"}

        # Build the archive in a temporary file (kept in memory while small) so
        # that large files do not have to fit into RAM.
        with tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024) as stream:
            archive.write_archive(stream, [os.fspath(p) for p in paths])
            stream.seek(0)
            # No Content-Length -> http.client uses chunked transfer encoding,
            # which is what AirDrop receivers expect.
            success, _ = self.send_POST(
                "/Upload", stream, headers=headers, timeout=max(self.config.timeout, 60)
            )
        return success

    @staticmethod
    def _get_headers():
        """
        Get the headers for requests sent
        """
        return {
            "Content-Type": "application/octet-stream",
            "Connection": "keep-alive",
            "Accept": "*/*",
            "User-Agent": "AirDrop/1.0",
            "Accept-Language": "en-us",
            "Accept-Encoding": "br, gzip, deflate",
        }


class HTTPSConnectionAWDL(HTTPSConnection):
    """
    HTTPS connection that works with IPv6 link-local addresses on every OS
    (by passing the numeric scope id) and with macOS' AWDL interface.
    """

    def __init__(
        self,
        host,
        port=None,
        timeout=None,
        *,
        context=None,
        interface_name=None,
        scope_id=None,
    ):
        host = str(host).split("%")[0]
        super().__init__(host=host, port=port, timeout=timeout, context=context)
        self.interface_name = interface_name
        self.scope_id = scope_id

    def _candidate_addresses(self):
        try:
            addr = ipaddress.ip_address(self.host)
        except ValueError:
            addr = None
        if isinstance(addr, ipaddress.IPv6Address) and addr.is_link_local and self.scope_id:
            return [(socket.AF_INET6, (self.host, self.port, 0, int(self.scope_id)))]
        return [
            (af, sa)
            for af, _, _, _, sa in socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)
        ]

    def _connect_socket(self):
        err = None
        for af, sa in self._candidate_addresses():
            sock = None
            try:
                sock = socket.socket(af, socket.SOCK_STREAM)
                sock.settimeout(self.timeout)
                if util.is_macos() and util.is_awdl(self.interface_name or ""):
                    sock.setsockopt(socket.SOL_SOCKET, SO_RECV_ANYIF, 1)
                sock.connect(sa)
                return sock
            except OSError as e:
                err = e
                if sock is not None:
                    sock.close()
        if err is not None:
            raise err
        raise OSError("getaddrinfo returned an empty list")

    def connect(self):
        sock = self._connect_socket()
        # Peers are addressed by IP, so no SNI / hostname verification
        self.sock = self._context.wrap_socket(sock, server_hostname=None)
