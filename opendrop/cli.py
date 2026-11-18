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

import argparse
import http.client
import json
import logging
import os
import plistlib
import ssl
import sys
import threading
import time

from . import __version__, util

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DECLINED = 2


def main(argv=None):
    _make_console_unicode_safe()
    try:
        cli = AirDropCli(sys.argv[1:] if argv is None else argv)
    except util.InterfaceError as e:
        logger.error(str(e))
        sys.exit(EXIT_ERROR)
    sys.exit(cli.exit_code)


def _make_console_unicode_safe():
    """
    Device names such as "John’s iPhone" contain characters that the legacy
    Windows console code page cannot encode. Never crash because of that.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _wait_forever(timeout=None):
    """
    Sleep until Ctrl+C (or ``timeout`` seconds). A bare ``Event().wait()``
    cannot be interrupted by Ctrl+C on Windows, so wait in short slices.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    event = threading.Event()
    while deadline is None or time.monotonic() < deadline:
        event.wait(0.25)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-i",
        "--interface",
        default=None,
        help="network interface to use (default: awdl0). On Windows you can use the "
        "friendly name, e.g. 'Wi-Fi'. See `opendrop doctor`.",
    )
    common.add_argument("-n", "--name", help="computer name (displayed in sharing pane)")
    common.add_argument("-m", "--model", help="computer model (displayed in sharing pane)")
    common.add_argument("-d", "--debug", action="store_true", help="enable debug output")
    common.add_argument(
        "-e", "--email", nargs="*", help="user's email addresses (currently unused)"
    )
    common.add_argument("-p", "--phone", nargs="*", help="user's phone numbers (currently unused)")
    common.add_argument(
        "--config-dir",
        default=os.environ.get("OPENDROP_HOME", "~/.opendrop"),
        help="directory for keys and discovery results (default: ~/.opendrop, or $OPENDROP_HOME)",
    )

    parser = argparse.ArgumentParser(
        prog="opendrop",
        description="An open AirDrop implementation for macOS, Linux and Windows.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="action", metavar="ACTION")
    sub.required = True

    p_receive = sub.add_parser("receive", parents=[common], help="receive files")
    p_receive.add_argument(
        "-o",
        "--output",
        default=None,
        help="directory where received files are stored (default: current directory)",
    )
    p_receive.add_argument("--port", type=int, default=8771, help="HTTPS port (default 8771)")

    p_find = sub.add_parser("find", parents=[common], help="discover receivers nearby")
    p_find.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=None,
        help="stop after this many seconds (default: run until Ctrl+C)",
    )

    p_send = sub.add_parser("send", parents=[common], help="send file(s) or a link")
    p_send.add_argument(
        "-f", "--file", nargs="+", required=True, help="file(s)/folder(s) to send, or a URL"
    )
    p_send.add_argument("-u", "--url", action="store_true", help="-f,--file is a URL")
    p_send.add_argument(
        "-r",
        "--receiver",
        required=True,
        help="peer to send to (index, ID, or name from `opendrop find`)",
    )

    sub.add_parser("doctor", parents=[common], help="check whether this system can run OpenDrop")
    return parser


class AirDropCli:
    def __init__(self, args):
        parser = build_parser()
        args = parser.parse_args(args)

        if args.debug:
            logging.basicConfig(
                level=logging.DEBUG,
                format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            )
        else:
            logging.basicConfig(level=logging.INFO, format="%(message)s")
            logging.getLogger("zeroconf").setLevel(logging.WARNING)

        self.args = args
        self.exit_code = EXIT_OK
        self.server = None
        self.client = None
        self.browser = None
        self.discover = []
        self.lock = threading.Lock()

        if args.action == "doctor":
            from .doctor import run_doctor  # pylint: disable=import-outside-toplevel

            self.exit_code = run_doctor(args.interface)
            return

        if args.action == "send":
            if args.url:
                for url in args.file:
                    if "://" not in url:
                        parser.error(f"Not a valid URL: {url}")
            else:
                for path in args.file:
                    if not os.path.exists(path):
                        parser.error(f"File not found: {path}")
            self.file = args.file
            self.is_url = args.url
            self.receiver = args.receiver

        from .config import AirDropConfig  # pylint: disable=import-outside-toplevel

        self.config = AirDropConfig(
            email=args.email,
            phone=args.phone,
            computer_name=args.name,
            computer_model=args.model,
            debug=args.debug,
            interface=args.interface,
            airdrop_dir=args.config_dir,
            receive_dir=getattr(args, "output", None),
            server_port=getattr(args, "port", 8771),
        )

        try:
            if args.action == "receive":
                self.receive()
            elif args.action == "find":
                self.find(args.timeout)
            else:
                self.exit_code = self.send()
        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            if self.browser is not None:
                self.browser.stop()
            if self.server is not None:
                self.server.stop()
            if self.client is not None:
                self.client.close()

    # ---------------------------------------------------------------- find

    def find(self, timeout=None):
        from .client import AirDropBrowser  # pylint: disable=import-outside-toplevel

        logger.info("Looking for receivers. Press Ctrl+C to stop ...")
        self.browser = AirDropBrowser(self.config)
        self._write_report()  # start with an empty report
        self.browser.start(callback_add=self._found_receiver)
        _wait_forever(timeout)

    def _found_receiver(self, info):
        thread = threading.Thread(target=self._send_discover, args=(info,), daemon=True)
        thread.start()

    def _send_discover(self, info):
        from .client import AirDropClient  # pylint: disable=import-outside-toplevel
        from .config import AirDropReceiverFlags  # pylint: disable=import-outside-toplevel

        addresses = info.parsed_addresses()
        # Prefer IPv6 (AirDrop uses IPv6 link-local addresses)
        addresses.sort(key=lambda a: ":" not in a)
        if not addresses:
            logger.warning(f"Ignoring receiver with missing address {info.name}")
            return
        address = addresses[0]
        identifier = info.name.split(".")[0]
        port = int(info.port)
        logger.debug(f"AirDrop service found: {info.server}, {address}:{port}, ID {identifier}")

        try:
            flags = int((info.properties or {}).get(b"flags") or 0)
        except ValueError:
            flags = 0
        if not flags:
            # in some cases, `flags` are not set in service info; try anyway
            flags = AirDropReceiverFlags.SUPPORTS_DISCOVER_MAYBE

        receiver_name = None
        if flags & AirDropReceiverFlags.SUPPORTS_DISCOVER_MAYBE:
            client = AirDropClient(self.config, (address, port))
            try:
                receiver_name = client.send_discover()
            except (OSError, ssl.SSLError, http.client.HTTPException) as e:
                logger.debug(f"Discover request to {identifier} failed: {e}")
            finally:
                client.close()
        discoverable = receiver_name is not None

        with self.lock:
            if any(node["id"] == identifier for node in self.discover):
                return
            index = len(self.discover)
            self.discover.append(
                {
                    "name": receiver_name,
                    "address": address,
                    "port": port,
                    "id": identifier,
                    "flags": flags,
                    "discoverable": discoverable,
                }
            )
            self._write_report()
            if discoverable:
                logger.info(f"Found  index {index}  ID {identifier}  name {receiver_name}")
            else:
                logger.debug(f"Receiver ID {identifier} is not discoverable")

    def _write_report(self):
        """Persist results after every find so nothing is lost on a hard kill."""
        tmp = self.config.discovery_report + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.discover, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.config.discovery_report)

    # ------------------------------------------------------------- receive

    def receive(self):
        from .server import AirDropServer  # pylint: disable=import-outside-toplevel

        self.server = AirDropServer(self.config)
        self.server.start_service()
        logger.info("Press Ctrl+C to stop.")
        self.server.start_server()

    # ---------------------------------------------------------------- send

    def send(self):
        from .client import AirDropClient  # pylint: disable=import-outside-toplevel

        info = self._get_receiver_info()
        if info is None:
            return EXIT_ERROR
        self.client = AirDropClient(self.config, (info["address"], info["port"]))
        try:
            logger.info("Asking receiver to accept ...")
            if not self.client.send_ask(self.file, is_url=self.is_url):
                logger.warning("Receiver declined")
                return EXIT_DECLINED
            logger.info("Receiver accepted")
            if self.is_url:
                return EXIT_OK
            logger.info("Uploading file(s) ...")
            if not self.client.send_upload(self.file):
                logger.error("Uploading has failed")
                return EXIT_ERROR
        except (
            OSError,
            ssl.SSLError,
            http.client.HTTPException,
            plistlib.InvalidFileException,
        ) as e:
            logger.error(f"Could not reach receiver: {e}")
            return EXIT_ERROR
        logger.info("Uploading has been successful")
        return EXIT_OK

    def _get_receiver_info(self):
        report = self.config.discovery_report
        if not os.path.exists(report):
            logger.error("No discovery report exists, please run 'opendrop find' first")
            return None
        age = time.time() - os.path.getmtime(report)
        if age > 60:  # warn if report is older than a minute
            logger.warning(
                f"Old discovery report ({age:.1f} seconds), consider running 'opendrop find' again"
            )
        try:
            with open(report, encoding="utf-8") as f:
                infos = json.load(f)
        except (OSError, ValueError) as e:
            logger.error(f"Could not read discovery report: {e}")
            return None
        return resolve_receiver(infos, self.receiver)


def resolve_receiver(infos, receiver):
    """
    Find a receiver in the discovery report by (1) index, (2) ID, (3) name.
    """
    receiver = str(receiver).strip()
    if receiver.isdigit() and int(receiver) < len(infos):
        return infos[int(receiver)]
    for info in infos:
        if info.get("id") == receiver:
            return info
    for info in infos:
        if info.get("name") == receiver:
            return info
    lowered = receiver.casefold()
    for info in infos:
        if (info.get("name") or "").casefold() == lowered:
            return info
    logger.error(
        "Receiver does not exist (check -r,--receiver format or try 'opendrop find' again)"
    )
    return None
