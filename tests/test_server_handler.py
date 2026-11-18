import io

import pytest

from opendrop.server import HTTPChunkedReader


def _chunked(*chunks, trailer=b""):
    body = b"".join(b"%x\r\n%s\r\n" % (len(c), c) for c in chunks)
    return body + b"0\r\n" + trailer + b"\r\n"


def test_chunked_reader_reads_all_data():
    rfile = io.BytesIO(_chunked(b"hello ", b"world") + b"NEXT REQUEST")
    reader = io.BufferedReader(HTTPChunkedReader(rfile))
    assert reader.read() == b"hello world"
    # does not consume (or block on) data after the terminating chunk
    assert rfile.read() == b"NEXT REQUEST"


def test_chunked_reader_handles_extensions_and_trailers():
    rfile = io.BytesIO(b"5;foo=bar\r\nabcde\r\n0\r\nX-Trailer: 1\r\n\r\n")
    raw = HTTPChunkedReader(rfile)
    assert io.BufferedReader(raw).read() == b"abcde"
    assert raw.total == 5


def test_chunked_reader_eof_is_sticky():
    raw = HTTPChunkedReader(io.BytesIO(_chunked(b"x")))
    buf = bytearray(10)
    assert raw.readinto(buf) == 1
    assert raw.readinto(buf) == 0
    assert raw.readinto(buf) == 0  # must not try to read another chunk header


def test_chunked_reader_truncated_body():
    raw = HTTPChunkedReader(io.BytesIO(b"10\r\nshort"))
    with pytest.raises(Exception):  # noqa: B017
        io.BufferedReader(raw).read()
