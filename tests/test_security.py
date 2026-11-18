"""Receiver hardening: session state, accept policy, limits, slow clients."""

import gzip
import io
import socket
import ssl
import time

import pytest

from opendrop import archive
from opendrop.client import AirDropClient
from opendrop.server import LINKS_FILE, AirDropServer, SessionStore

from .test_archive import _odc_entry


@pytest.fixture
def make_server(config_factory):
    servers = []

    def make(**kwargs):
        kwargs.setdefault("interface", "x")
        config = config_factory(**kwargs)
        server = AirDropServer(config, bind_host="127.0.0.1", announce=False)
        server.start_server(blocking=False)
        servers.append(server)
        return server, config

    yield make
    for server in servers:
        server.stop()


def _client(config_factory, tmp_path, server):
    config = config_factory(airdrop_dir=str(tmp_path / "sender"), interface="nope")
    return AirDropClient(config, ("127.0.0.1", server.port))


def _cpio_upload(client, data):
    ok, _ = client.send_POST(
        "/Upload", io.BytesIO(data), headers={"Content-Type": "application/x-cpio"}
    )
    return ok


def test_upload_without_ask_is_rejected(make_server, config_factory, tmp_path):
    server, config = make_server()
    client = _client(config_factory, tmp_path, server)
    payload = gzip.compress(_odc_entry("./x.txt", b"x") + _odc_entry("TRAILER!!!", mode=0))
    try:
        assert not _cpio_upload(client, payload)
        assert not (tmp_path / "received" / "x.txt").exists()
        # after an accepted /Ask, exactly one upload is allowed
        assert client.send_ask(str(tmp_path / "home"))  # any existing path
        assert _cpio_upload(client, payload)
        assert not _cpio_upload(client, payload)
    finally:
        client.close()


def test_declined_transfer(make_server, config_factory, tmp_path):
    seen = []

    def decline(transfer):
        seen.append(transfer)
        return False

    server, _ = make_server(accept_callback=decline, computer_name="R")
    f = tmp_path / "a.txt"
    f.write_text("a")
    client = _client(config_factory, tmp_path, server)
    try:
        assert client.send_ask(str(f)) is False
    finally:
        client.close()
    assert seen[0].files == ["a.txt"]
    assert seen[0].client_address == "127.0.0.1"


def test_failing_callback_declines(make_server, config_factory, tmp_path):
    def boom(_transfer):
        raise RuntimeError("bug")

    server, _ = make_server(accept_callback=boom)
    client = _client(config_factory, tmp_path, server)
    try:
        assert client.send_ask("https://example.org", is_url=True) is False
    finally:
        client.close()


def test_links_are_saved(make_server, config_factory, tmp_path):
    server, config = make_server()
    client = _client(config_factory, tmp_path, server)
    try:
        assert client.send_ask(["https://example.org/a", "https://example.org/b"], is_url=True)
    finally:
        client.close()
    lines = (tmp_path / "received" / LINKS_FILE).read_text(encoding="utf-8").splitlines()
    assert [line.split("\t")[-1] for line in lines] == [
        "https://example.org/a",
        "https://example.org/b",
    ]


def test_max_upload_size(make_server, config_factory, tmp_path):
    server, _ = make_server(max_upload_size=1000)
    big = tmp_path / "big.bin"
    big.write_bytes(b"\0" * 5000)
    client = _client(config_factory, tmp_path, server)
    try:
        assert client.send_ask(str(big))
        assert client.send_upload(str(big)) is False
    finally:
        client.close()
    assert not (tmp_path / "received" / "big.bin").exists()  # no partial file left


def test_extract_limits(tmp_path):
    data = gzip.compress(
        _odc_entry("./a", b"1") + _odc_entry("./b", b"2") + _odc_entry("TRAILER!!!", mode=0)
    )
    with pytest.raises(archive.LimitExceeded):
        archive.extract_archive(io.BytesIO(data), str(tmp_path / "o1"), max_entries=1)
    with pytest.raises(archive.LimitExceeded):
        archive.extract_archive(io.BytesIO(data), str(tmp_path / "o2"), max_bytes=1)


def test_slow_tls_client_does_not_block_others(make_server, config_factory, tmp_path):
    server, _ = make_server(computer_name="Busy")
    # A client that connects but never starts the TLS handshake
    idle = socket.create_connection(("127.0.0.1", server.port))
    try:
        time.sleep(0.2)
        client = _client(config_factory, tmp_path, server)
        try:
            assert client.send_discover() == "Busy"
        finally:
            client.close()
    finally:
        idle.close()


def test_connection_limit(make_server, config_factory, tmp_path):
    server, _ = make_server(max_connections=1, computer_name="One")
    idle = socket.create_connection(("127.0.0.1", server.port))
    try:
        time.sleep(0.2)
        client = _client(config_factory, tmp_path, server)
        with pytest.raises((OSError, ssl.SSLError)):
            client.send_discover()
        client.close()
    finally:
        idle.close()
    time.sleep(0.3)
    client = _client(config_factory, tmp_path, server)
    try:
        assert client.send_discover() == "One"
    finally:
        client.close()


def test_session_store_expiry():
    store = SessionStore(timeout=0.05)
    store.allow("peer", "req")
    assert store.consume("peer") == "req"
    assert store.consume("peer") is None
    store.allow("peer", "req")
    time.sleep(0.1)
    assert store.consume("peer") is None
