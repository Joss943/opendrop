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

---

Pure-Python implementation of the (gzip-compressed) CPIO archives that AirDrop
uses to transfer files. Replacing the native ``libarchive`` dependency makes
OpenDrop installable with ``pip`` alone on macOS, Linux and Windows.

Supported formats when reading: POSIX ``odc`` (070707), SVR4 ``newc``
(070701) and ``crc`` (070702), optionally compressed with gzip or bzip2.
Archives are always written in ``odc`` format with gzip compression, which is
what AirDrop senders produce.
"""

import bz2
import gzip
import io
import logging
import os
import re
import shutil
import stat
import time

logger = logging.getLogger(__name__)

ODC_MAGIC = b"070707"
NEWC_MAGIC = b"070701"
CRC_MAGIC = b"070702"
TRAILER = "TRAILER!!!"

ODC_HEADER_LEN = 76
NEWC_HEADER_LEN = 110
ODC_MAX_FILESIZE = 8**11 - 1  # 11 octal digits

_COPY_BUFSIZE = 1024 * 1024

# Characters that are not allowed in file names on Windows (and are a bad idea
# elsewhere, too). macOS/iOS happily produce names containing ':' or '?'.
_INVALID_CHARS = re.compile(r'[<>:"|?*\x00-\x1f\\]')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class ArchiveError(Exception):
    """Raised when an archive is malformed or uses an unsupported format."""


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


class CpioWriter:
    """Write a gzip-compressed POSIX ``odc`` CPIO archive to a binary stream."""

    def __init__(self, fileobj, compresslevel=6):
        self._gz = gzip.GzipFile(fileobj=fileobj, mode="wb", compresslevel=compresslevel)
        self._ino = 0
        self._closed = False

    def _write_header(self, name, mode, size, mtime, nlink=1):
        name_bytes = name.encode("utf-8") + b"\x00"
        if size > ODC_MAX_FILESIZE:
            raise ArchiveError(f"File too large for CPIO odc format: {name}")
        self._ino += 1
        header = (
            ODC_MAGIC
            + b"%06o" % 0  # dev
            + b"%06o" % (self._ino & 0o777777)  # ino
            + b"%06o" % (mode & 0o777777)
            + b"%06o" % 0  # uid
            + b"%06o" % 0  # gid
            + b"%06o" % nlink
            + b"%06o" % 0  # rdev
            + b"%011o" % max(0, int(mtime))
            + b"%06o" % len(name_bytes)
            + b"%011o" % size
        )
        assert len(header) == ODC_HEADER_LEN
        self._gz.write(header)
        self._gz.write(name_bytes)

    def add_file(self, path, arcname):
        """Add a regular file from disk."""
        st = os.stat(path)
        mode = stat.S_IFREG | (stat.S_IMODE(st.st_mode) or 0o644)
        self._write_header(arcname, mode, st.st_size, st.st_mtime)
        written = 0
        with open(path, "rb") as f:
            while True:
                chunk = f.read(_COPY_BUFSIZE)
                if not chunk:
                    break
                self._gz.write(chunk)
                written += len(chunk)
        if written != st.st_size:
            raise ArchiveError(f"File changed while being archived: {path}")

    def add_directory_entry(self, arcname, mtime=None):
        """Add an (empty) directory entry."""
        self._write_header(
            arcname, stat.S_IFDIR | 0o755, 0, time.time() if mtime is None else mtime, 2
        )

    def add_path(self, path, arcname=None):
        """Add a file or, recursively, a directory from disk."""
        if arcname is None:
            arcname = "./" + os.path.basename(os.path.normpath(path))
        if os.path.isdir(path):
            self.add_directory_entry(arcname, os.stat(path).st_mtime)
            for entry in sorted(os.listdir(path)):
                full = os.path.join(path, entry)
                if os.path.islink(full):
                    logger.debug(f"Skipping symbolic link {full}")
                    continue
                self.add_path(full, f"{arcname}/{entry}")
        elif os.path.isfile(path):
            self.add_file(path, arcname)
        else:
            raise ArchiveError(f"Not a regular file or directory: {path}")

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._write_header(TRAILER, 0, 0, 0, nlink=1)
        self._gz.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.close()
        else:
            self._gz.close()


def write_archive(fileobj, paths):
    """Write ``paths`` (files or directories) to ``fileobj`` as gzip'd CPIO."""
    with CpioWriter(fileobj) as writer:
        for path in paths:
            writer.add_path(path)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def _read_exact(stream, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            raise ArchiveError("Unexpected end of archive")
        buf += chunk
    return bytes(buf)


def _skip(stream, n):
    while n > 0:
        chunk = stream.read(min(n, _COPY_BUFSIZE))
        if not chunk:
            raise ArchiveError("Unexpected end of archive")
        n -= len(chunk)


class CpioEntry:
    def __init__(self, name, mode, size, mtime):
        self.name = name
        self.mode = mode
        self.size = size
        self.mtime = mtime

    @property
    def is_dir(self):
        return stat.S_ISDIR(self.mode)

    @property
    def is_file(self):
        return stat.S_ISREG(self.mode)


def _parse_header(stream):
    magic = _read_exact(stream, 6)
    try:
        if magic == ODC_MAGIC:
            h = _read_exact(stream, ODC_HEADER_LEN - 6)
            mode = int(h[12:18], 8)
            mtime = int(h[42:53], 8)
            namesize = int(h[53:59], 8)
            filesize = int(h[59:70], 8)
            name = _read_exact(stream, namesize)
            name_pad = data_pad = 0
        elif magic in (NEWC_MAGIC, CRC_MAGIC):
            h = _read_exact(stream, NEWC_HEADER_LEN - 6)
            fields = [int(h[i : i + 8], 16) for i in range(0, 104, 8)]
            mode, mtime, filesize, namesize = fields[1], fields[5], fields[6], fields[11]
            name = _read_exact(stream, namesize)
            name_pad = (-(NEWC_HEADER_LEN + namesize)) % 4
            data_pad = (-filesize) % 4
        else:
            raise ArchiveError(f"Unsupported CPIO format (magic {magic!r})")
    except ValueError as e:
        raise ArchiveError(f"Corrupt CPIO header: {e}") from e
    _skip(stream, name_pad)
    name = name.rstrip(b"\x00").decode("utf-8", errors="replace")
    return CpioEntry(name, mode, filesize, mtime), data_pad


def sanitize_member_path(name):
    """
    Turn an archive member name into a safe, relative list of path components.

    Returns ``None`` for entries that must not be extracted (absolute paths,
    ``..`` traversal, empty names).
    """
    name = name.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    parts = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        part = _INVALID_CHARS.sub("_", part).rstrip(" .")
        if not part:
            part = "_"
        if part.split(".")[0].upper() in _WINDOWS_RESERVED:
            part = "_" + part
        parts.append(part)
    return parts or None


def _unique_name(directory, name):
    candidate = name
    root, ext = os.path.splitext(name)
    i = 1
    while os.path.lexists(os.path.join(directory, candidate)):
        candidate = f"{root} ({i}){ext}"
        i += 1
    return candidate


def _decompressed(stream):
    """Wrap ``stream`` in a decompressor if needed, based on its magic bytes."""
    if not hasattr(stream, "peek"):
        stream = io.BufferedReader(stream)
    head = stream.peek(6)[:6]
    if head[:2] == b"\x1f\x8b":
        return io.BufferedReader(gzip.GzipFile(fileobj=stream, mode="rb"))
    if head[:3] == b"BZh":
        return io.BufferedReader(bz2.BZ2File(stream, mode="rb"))
    return stream


class LimitExceeded(ArchiveError):
    """Raised when an archive exceeds the configured size or entry limits."""


def extract_archive(stream, dest_dir, max_bytes=None, max_entries=100_000):
    """
    Safely extract a (possibly compressed) CPIO archive from ``stream`` into
    ``dest_dir``.

    * Absolute paths and ``..`` components are rejected.
    * Symbolic links, devices, FIFOs etc. are skipped.
    * Names that are invalid on the current OS are sanitized.
    * Existing files are never overwritten: a numbered name is used instead.
    * ``max_bytes`` / ``max_entries`` limit the total amount of data and the
      number of entries; free disk space is checked before writing each file.

    Returns the list of top-level paths that were created.
    """
    dest_dir = os.path.abspath(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    stream = _decompressed(stream)

    head = stream.peek(6)[:6]
    if head not in (ODC_MAGIC, NEWC_MAGIC, CRC_MAGIC):
        return _extract_with_libarchive(stream, dest_dir, head)

    renamed_roots = {}
    created = []
    total_bytes = 0
    entries = 0
    while True:
        entry, data_pad = _parse_header(stream)
        if entry.name == TRAILER:
            break
        entries += 1
        if max_entries is not None and entries > max_entries:
            raise LimitExceeded(f"Archive contains more than {max_entries} entries")
        total_bytes += entry.size
        if max_bytes is not None and total_bytes > max_bytes:
            raise LimitExceeded(f"Transfer exceeds the size limit of {max_bytes} bytes")
        parts = sanitize_member_path(entry.name)
        if parts is None or not (entry.is_file or entry.is_dir):
            logger.warning(f"Skipping unsafe or unsupported archive entry {entry.name!r}")
            _skip(stream, entry.size + data_pad)
            continue

        root = parts[0]
        if root not in renamed_roots:
            renamed_roots[root] = _unique_name(dest_dir, root)
            created.append(os.path.join(dest_dir, renamed_roots[root]))
        parts[0] = renamed_roots[root]
        target = os.path.join(dest_dir, *parts)

        if entry.is_dir:
            os.makedirs(target, exist_ok=True)
            _skip(stream, entry.size + data_pad)
            continue

        os.makedirs(os.path.dirname(target), exist_ok=True)
        if entry.size > 0 and shutil.disk_usage(dest_dir).free < entry.size + 16 * 1024 * 1024:
            raise LimitExceeded(f"Not enough disk space for {entry.name!r}")
        remaining = entry.size
        try:
            with open(target, "wb") as out:
                while remaining > 0:
                    chunk = stream.read(min(remaining, _COPY_BUFSIZE))
                    if not chunk:
                        raise ArchiveError("Unexpected end of archive")
                    out.write(chunk)
                    remaining -= len(chunk)
        except BaseException:
            # never leave a truncated file behind
            try:
                os.remove(target)
            except OSError:
                pass
            raise
        _skip(stream, data_pad)
        try:
            os.utime(target, (entry.mtime, entry.mtime))
        except (OSError, OverflowError):
            pass

    # Drain any trailing padding so that the HTTP body is fully consumed
    while stream.read(_COPY_BUFSIZE):
        pass
    return created


def _extract_with_libarchive(stream, dest_dir, head):
    """Fallback for exotic formats if the optional libarchive-c is installed."""
    try:
        import libarchive  # pylint: disable=import-outside-toplevel
        import libarchive.extract  # pylint: disable=import-outside-toplevel
    except (ImportError, OSError) as e:
        raise ArchiveError(
            f"Unsupported archive format (magic {head!r}). "
            "Install the optional dependency with `pip install opendrop[libarchive]`."
        ) from e

    flags = (
        libarchive.extract.EXTRACT_SECURE_NODOTDOT
        | libarchive.extract.EXTRACT_SECURE_NOABSOLUTEPATHS
        | libarchive.extract.EXTRACT_SECURE_SYMLINKS
    )
    before = set(os.listdir(dest_dir))
    cwd = os.getcwd()
    try:
        os.chdir(dest_dir)
        with libarchive.stream_reader(stream) as archive:
            libarchive.extract.extract_entries(archive, flags)
    finally:
        os.chdir(cwd)
    return [os.path.join(dest_dir, p) for p in sorted(set(os.listdir(dest_dir)) - before)]
