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
import ipaddress
import logging
import mimetypes
import os
import platform
import socket
from dataclasses import dataclass
from typing import Optional

import ifaddr

logger = logging.getLogger(__name__)

# Extension -> Apple Uniform Type Identifier. Anything unknown falls back to a
# generic UTI derived from the MIME type.
_UTI_BY_EXTENSION = {
    ".jpg": "public.jpeg",
    ".jpeg": "public.jpeg",
    ".jp2": "public.jpeg-2000",
    ".png": "public.png",
    ".gif": "com.compuserve.gif",
    ".heic": "public.heic",
    ".heif": "public.heif",
    ".tif": "public.tiff",
    ".tiff": "public.tiff",
    ".bmp": "com.microsoft.bmp",
    ".webp": "org.webmproject.webp",
    ".dng": "com.adobe.raw-image",
    ".cr2": "com.canon.cr2-raw-image",
    ".nef": "com.nikon.raw-image",
    ".mov": "com.apple.quicktime-movie",
    ".mp4": "public.mpeg-4",
    ".m4v": "com.apple.m4v-video",
    ".mp3": "public.mp3",
    ".m4a": "com.apple.m4a-audio",
    ".wav": "com.microsoft.waveform-audio",
    ".aac": "public.aac-audio",
    ".pdf": "com.adobe.pdf",
    ".txt": "public.plain-text",
    ".rtf": "public.rtf",
    ".html": "public.html",
    ".htm": "public.html",
    ".csv": "public.comma-separated-values-text",
    ".json": "public.json",
    ".zip": "public.zip-archive",
    ".gz": "org.gnu.gnu-zip-archive",
    ".tar": "public.tar-archive",
    ".vcf": "public.vcard",
    ".ics": "com.apple.ical.ics",
    ".doc": "com.microsoft.word.doc",
    ".docx": "org.openxmlformats.wordprocessingml.document",
    ".xls": "com.microsoft.excel.xls",
    ".xlsx": "org.openxmlformats.spreadsheetml.sheet",
    ".ppt": "com.microsoft.powerpoint.ppt",
    ".pptx": "org.openxmlformats.presentationml.presentation",
    ".key": "com.apple.keynote.key",
    ".pages": "com.apple.iwork.pages.pages",
    ".numbers": "com.apple.iwork.numbers.numbers",
}

# Magic bytes -> UTI, used when the file has no (known) extension
_UTI_BY_MAGIC = [
    (b"\xff\xd8\xff", "public.jpeg"),
    (b"\x89PNG\r\n\x1a\n", "public.png"),
    (b"GIF8", "com.compuserve.gif"),
    (b"%PDF", "com.adobe.pdf"),
    (b"PK\x03\x04", "public.zip-archive"),
    (b"\x1f\x8b", "org.gnu.gnu-zip-archive"),
]


@dataclass
class InterfaceInfo:
    """A network interface together with the addresses OpenDrop cares about."""

    name: str
    nice_name: str
    index: Optional[int]
    ipv6: Optional[ipaddress.IPv6Address]
    ipv4: Optional[ipaddress.IPv4Address]

    @property
    def address(self):
        """Address used for AirDrop: IPv6 (as Apple does), else IPv4 (LAN mode)."""
        return self.ipv6 if self.ipv6 is not None else self.ipv4

    @property
    def display_name(self):
        if self.nice_name and self.nice_name != self.name:
            return f"{self.nice_name} ({self.name})"
        return self.name


def list_interfaces() -> list[InterfaceInfo]:
    """
    List all network interfaces on this machine.

    On Windows, interface names are GUIDs; the human-readable name (e.g.
    ``Wi-Fi``) is available as ``nice_name``.
    """
    result = []
    for adapter in ifaddr.get_adapters():
        v6_link_local = v6_other = v4 = None
        scope = None
        for ip in adapter.ips:
            if ip.is_IPv6:
                addr_str, _flowinfo, scope_id = ip.ip
                addr = ipaddress.IPv6Address(addr_str.split("%")[0])
                if addr.is_link_local and v6_link_local is None:
                    v6_link_local, scope = addr, scope_id
                elif v6_other is None:
                    v6_other = addr
                    scope = scope if scope is not None else scope_id
            elif ip.is_IPv4 and v4 is None:
                v4 = ipaddress.IPv4Address(ip.ip)

        index = getattr(adapter, "index", None)
        if index is None:
            index = scope or _if_nametoindex(adapter.name)
        result.append(
            InterfaceInfo(
                name=str(adapter.name),
                nice_name=str(adapter.nice_name),
                index=index,
                ipv6=v6_link_local or v6_other,
                ipv4=v4,
            )
        )
    return result


def _if_nametoindex(name):
    try:
        return socket.if_nametoindex(name)
    except (OSError, AttributeError):
        return None


def find_interface(name) -> Optional[InterfaceInfo]:
    """Look up an interface by its system name, friendly name or index."""
    if name is None:
        return None
    interfaces = list_interfaces()
    for iface in interfaces:
        if iface.name == name:
            return iface
    for iface in interfaces:
        if iface.nice_name == name:
            return iface
    lowered = str(name).lower()
    for iface in interfaces:
        if iface.nice_name.lower() == lowered or iface.name.lower() == lowered:
            return iface
    if str(name).isdigit():
        for iface in interfaces:
            if iface.index == int(name):
                return iface
    return None


def default_interface_name():
    """AWDL interface name; ``awdl0`` on macOS and on Linux with OWL."""
    return "awdl0"


def is_awdl(interface_name):
    return str(interface_name).startswith("awdl")


def is_macos():
    return platform.system() == "Darwin"


def get_uti_type(path) -> str:
    """Return the Apple Uniform Type Identifier that best matches ``path``."""
    if os.path.isdir(path):
        return "public.folder"
    ext = os.path.splitext(path)[1].lower()
    if ext in _UTI_BY_EXTENSION:
        return _UTI_BY_EXTENSION[ext]
    try:
        with open(path, "rb") as f:
            head = f.read(16)
        for magic, uti in _UTI_BY_MAGIC:
            if head.startswith(magic):
                return uti
    except OSError:
        pass
    mime, _ = mimetypes.guess_type(path)
    if mime:
        major = mime.split("/")[0]
        if major in ("image", "audio", "video"):
            return f"public.{major}"
        if major == "text":
            return "public.text"
    return "public.content"


def is_image(path):
    return get_uti_type(path) in (
        "public.jpeg",
        "public.jpeg-2000",
        "public.png",
        "com.compuserve.gif",
        "public.tiff",
        "com.microsoft.bmp",
        "org.webmproject.webp",
        "public.image",
    )


def generate_file_icon(file_path):
    """
    Generate a JPEG 2000 thumbnail of an image that is shown as preview on the
    receiving device. Returns ``None`` if no thumbnail could be created.
    """
    try:
        from PIL import Image, ImageOps  # pylint: disable=import-outside-toplevel
    except ImportError:
        logger.debug("Pillow is not installed, not generating a preview")
        return None

    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:  # Pillow < 9.1
        resample = Image.LANCZOS  # pylint: disable=no-member

    try:
        with Image.open(file_path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((540, 540), resample)
            buf = io.BytesIO()
            try:
                im.save(buf, format="JPEG2000")
            except (OSError, KeyError):
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f"Could not create preview for {file_path}: {e}")
        return None


def write_debug(config, data, file_name):
    """Dump raw protocol data to ``~/.opendrop/debug`` when ``--debug`` is set."""
    if not config.debug:
        return
    os.makedirs(config.debug_dir, exist_ok=True)
    debug_file_path = os.path.join(config.debug_dir, file_name)
    with open(debug_file_path, "wb") as file:
        if hasattr(data, "read"):
            pos = data.tell()
            file.write(data.read())
            data.seek(pos)
        else:  # assume bytes-like
            file.write(data)


class AirDropUtil:
    """Backwards-compatible facade for code written against OpenDrop <= 0.13."""

    get_uti_type = staticmethod(get_uti_type)
    generate_file_icon = staticmethod(generate_file_icon)
    write_debug = staticmethod(write_debug)

    @staticmethod
    def get_ip_for_interface(interface_name, ipv6=False):
        iface = find_interface(interface_name)
        if iface is None:
            return None
        return iface.ipv6 if ipv6 else iface.ipv4


class InterfaceError(RuntimeError):
    """Raised when the requested network interface cannot be used."""


def _interface_hint(name):
    system = platform.system()
    if is_awdl(name):
        if system == "Darwin":
            return (
                "On macOS, awdl0 only gets an address while AWDL is enabled. "
                "Make sure Wi-Fi is turned on and AirDrop is not disabled."
            )
        if system == "Linux":
            return "On Linux, start OWL first (https://github.com/seemoo-lab/owl)."
        return (
            "Windows has no AWDL support. Use `--interface` with a normal network "
            "interface (e.g. -i Wi-Fi) to exchange files with other OpenDrop "
            "peers on the same network, or see docs/WINDOWS.md."
        )
    return "Run `opendrop doctor` to list usable interfaces."


def require_interface(name) -> InterfaceInfo:
    """Like :func:`find_interface`, but raises a helpful error if unusable."""
    iface = find_interface(name)
    if iface is None:
        raise InterfaceError(f"Interface {name!r} does not exist. {_interface_hint(name)}")
    if iface.ipv6 is None and (is_awdl(name) or iface.ipv4 is None):
        raise InterfaceError(
            f"Interface {name!r} does not have an IPv6 address. {_interface_hint(name)}"
        )
    return iface


def make_zeroconf(iface: InterfaceInfo, interface_name):
    """Create a Zeroconf instance restricted to IPv6 on ``iface``."""
    from zeroconf import IPVersion, Zeroconf  # pylint: disable=import-outside-toplevel

    if iface.ipv6 is None:
        # LAN mode on networks without IPv6 (only works between OpenDrop peers)
        return Zeroconf(interfaces=[str(iface.ipv4)], ip_version=IPVersion.V4Only)
    # Prefer the interface index: on Windows several adapters may share the
    # same link-local address, so an address alone is ambiguous.
    choice = iface.index if iface.index else str(iface.ipv6)
    return Zeroconf(
        interfaces=[choice],
        ip_version=IPVersion.V6Only,
        apple_p2p=is_macos() and is_awdl(interface_name),
    )


def suggest_interface():
    """
    Pick a sensible default interface for the desktop app: awdl0 when it is
    usable (macOS, Linux with OWL), otherwise the interface that carries the
    default route, otherwise the first interface with an address.
    """
    interfaces = [i for i in list_interfaces() if i.address is not None]
    for iface in interfaces:
        if is_awdl(iface.name) and iface.ipv6 is not None:
            return iface.name
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no packet is sent for UDP connect
            local = ipaddress.IPv4Address(s.getsockname()[0])
        for iface in interfaces:
            if iface.ipv4 == local:
                return iface.nice_name if iface.name.startswith("{") else iface.name
    except OSError:
        pass
    for iface in interfaces:
        if not (iface.address.is_loopback):
            return iface.nice_name if iface.name.startswith("{") else iface.name
    return default_interface_name()
