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

import datetime
import logging
import os
import secrets
import socket
import ssl

from . import util

logger = logging.getLogger(__name__)

CERT_VALIDITY_DAYS = 5 * 365


class AirDropReceiverFlags:
    """
    Recovered from sharingd`receiverSupportsX methods.
    A valid node needs to either have SUPPORTS_PIPELINING or SUPPORTS_MIXED_TYPES
    according to sharingd`[SDBonjourBrowser removeInvalidNodes:].
    Default flags on macOS: 0x3fb according to sharingd`[SDRapportBrowser defaultSFNodeFlags]
    """

    SUPPORTS_URL = 0x01
    SUPPORTS_DVZIP = 0x02
    SUPPORTS_PIPELINING = 0x04
    SUPPORTS_MIXED_TYPES = 0x08
    SUPPORTS_UNKNOWN1 = 0x10
    SUPPORTS_UNKNOWN2 = 0x20
    SUPPORTS_IRIS = 0x40
    SUPPORTS_DISCOVER_MAYBE = 0x80  # Probably indicates that server supports /Discover URL
    SUPPORTS_UNKNOWN3 = 0x100
    SUPPORTS_ASSET_BUNDLE = 0x200


def _default_host_name():
    host = socket.gethostname() or "opendrop"
    # macOS returns e.g. "MyMac.local"; mDNS appends ".local." itself
    if host.lower().endswith(".local"):
        host = host[: -len(".local")]
    return host.split(".")[0] or "opendrop"


class AirDropConfig:
    def __init__(
        self,
        host_name=None,
        computer_name=None,
        computer_model=None,
        server_port=8771,
        airdrop_dir="~/.opendrop",
        service_id=None,
        email=None,
        phone=None,
        debug=False,
        interface=None,
        receive_dir=None,
        timeout=10.0,
        ask_timeout=120.0,
        accept_callback=None,
        max_upload_size=None,
        max_connections=16,
        session_timeout=600.0,
        save_links=True,
    ):
        self.airdrop_dir = os.path.abspath(os.path.expanduser(airdrop_dir))
        os.makedirs(self.airdrop_dir, exist_ok=True)

        self.discovery_report = os.path.join(self.airdrop_dir, "discover.last.json")

        self.host_name = host_name or _default_host_name()
        self.computer_name = computer_name or self.host_name
        self.computer_model = computer_model or "OpenDrop"
        self.port = server_port

        # random 6-byte identifier in base16
        self.service_id = service_id or secrets.token_hex(6)

        self.debug = debug
        self.debug_dir = os.path.join(self.airdrop_dir, "debug")

        self.interface = interface or util.default_interface_name()
        self.receive_dir = os.path.abspath(receive_dir or os.getcwd())

        self.timeout = timeout
        self.ask_timeout = ask_timeout

        # Receiver policy. ``accept_callback(request)`` receives an
        # :class:`~opendrop.server.TransferRequest` and returns True to accept.
        # ``None`` accepts everything (historic behaviour).
        self.accept_callback = accept_callback
        self.max_upload_size = max_upload_size  # bytes, None = unlimited
        self.max_connections = max_connections
        self.session_timeout = session_timeout  # seconds between /Ask and /Upload
        self.save_links = save_links  # append received URLs to received_links.txt

        self.email = email or []
        self.phone = phone or []

        # Bare minimum, we currently do not support anything else
        self.flags = (
            AirDropReceiverFlags.SUPPORTS_MIXED_TYPES | AirDropReceiverFlags.SUPPORTS_DISCOVER_MAYBE
        )

        self.root_ca_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "certs", "apple_root_ca.pem"
        )
        if not os.path.exists(self.root_ca_file):
            raise FileNotFoundError(f"Need Apple root CA certificate: {self.root_ca_file}")

        self.key_dir = os.path.join(self.airdrop_dir, "keys")
        self.cert_file = os.path.join(self.key_dir, "certificate.pem")
        self.key_file = os.path.join(self.key_dir, "key.pem")

        if not os.path.exists(self.cert_file) or not os.path.exists(self.key_file):
            logger.info("Key file or certificate does not exist")
            self.create_default_key()
        elif self._self_signed_cert_expired():
            logger.info("Self-signed certificate has expired")
            self.create_default_key()

        self.record_file = os.path.join(self.key_dir, "validation_record.cms")
        self.record_data = None
        if os.path.exists(self.record_file):
            logger.debug("Using provided Apple ID Validation Record")
            with open(self.record_file, "rb") as f:
                self.record_data = f.read()
        else:
            logger.debug("No Apple ID Validation Record found")

    def _self_signed_cert_expired(self):
        """
        True if our own self-signed certificate has expired. Certificates
        extracted from macOS (signed by Apple) are never replaced.
        """
        try:
            from cryptography import x509  # pylint: disable=import-outside-toplevel

            with open(self.cert_file, "rb") as f:
                cert = x509.load_pem_x509_certificate(f.read())
        except Exception:  # pylint: disable=broad-except
            return False
        if cert.issuer != cert.subject:
            return False
        try:
            not_after = cert.not_valid_after_utc
        except AttributeError:  # cryptography < 42
            not_after = cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        return not_after < datetime.datetime.now(datetime.timezone.utc)

    def create_default_key(self):
        """
        Create a self-signed RSA certificate. This uses the ``cryptography``
        package, so no ``openssl`` binary is needed (important on Windows).
        """
        # pylint: disable=import-outside-toplevel
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        logger.info(f"Create new self-signed certificate in {self.key_dir}")
        os.makedirs(self.key_dir, mode=0o700, exist_ok=True)

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, self.computer_name[:64])])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=CERT_VALIDITY_DAYS))
            .sign(key, hashes.SHA256())
        )

        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        fd = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key_pem)
        with open(self.cert_file, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

    def _base_context(self, protocol):
        ctx = ssl.SSLContext(protocol)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(self.cert_file, keyfile=self.key_file)
        ctx.load_verify_locations(cafile=self.root_ca_file)
        return ctx

    def get_client_ssl_context(self):
        ctx = self._base_context(ssl.PROTOCOL_TLS_CLIENT)
        # AirDrop peers use self-signed certificates and IP addresses
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def get_server_ssl_context(self):
        ctx = self._base_context(ssl.PROTOCOL_TLS_SERVER)
        ctx.verify_mode = ssl.CERT_NONE  # we accept self-signed certificates as does Apple
        return ctx

    def get_ssl_context(self):
        """Kept for backwards compatibility; returns a client context."""
        return self.get_client_ssl_context()
