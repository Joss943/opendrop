"""
Full protocol round trip between an OpenDrop sender and an OpenDrop receiver
over TLS on localhost (no mDNS, no AWDL needed). Runs on every OS.
"""

import socket

import pytest

from opendrop.client import AirDropClient
from opendrop.server import AirDropServer


def _hosts():
    hosts = ["127.0.0.1"]
    if socket.has_ipv6:
        try:
            s = socket.socket(socket.AF_INET6)
            s.bind(("::1", 0))
            s.close()
            hosts.append("::1")
        except OSError:
            pass
    return hosts


@pytest.fixture(params=_hosts())
def running_server(request, config_factory):
    host = request.param
    config = config_factory(computer_name="Receiver ✓", interface="does-not-matter")
    server = AirDropServer(config, bind_host=host, announce=False)
    server.start_server(blocking=False)
    yield host, server
    server.stop()


def _client(config_factory, tmp_path, host, port):
    config = config_factory(
        airdrop_dir=str(tmp_path / "sender"), computer_name="Sender", interface="nope"
    )
    return AirDropClient(config, (host, port))


def test_discover_ask_upload(running_server, config_factory, tmp_path):
    host, server = running_server

    src = tmp_path / "to_send"
    (src / "album").mkdir(parents=True)
    (src / "album" / "1.txt").write_text("one")
    (src / "notes.txt").write_text("hello from OpenDrop")

    client = _client(config_factory, tmp_path, host, server.port)
    try:
        assert client.send_discover() == "Receiver ✓"
        files = [str(src / "notes.txt"), str(src / "album")]
        assert client.send_ask(files) is True
        assert client.send_upload(files) is True
        # a second transfer re-opens the connection (server closed it)
        assert client.send_ask(str(src / "notes.txt")) is True
        assert client.send_upload(str(src / "notes.txt")) is True
    finally:
        client.close()

    out = tmp_path / "received"
    assert (out / "notes.txt").read_text() == "hello from OpenDrop"
    assert (out / "notes (1).txt").read_text() == "hello from OpenDrop"
    assert (out / "album" / "1.txt").read_text() == "one"


def test_url_send_does_not_upload(running_server, config_factory, tmp_path):
    host, server = running_server
    client = _client(config_factory, tmp_path, host, server.port)
    try:
        assert client.send_ask("https://example.org", is_url=True)
        assert client.send_upload("https://example.org", is_url=True) is True
    finally:
        client.close()


def test_unknown_path_and_bad_upload(running_server, config_factory, tmp_path):
    host, server = running_server
    client = _client(config_factory, tmp_path, host, server.port)
    try:
        ok, _ = client.send_POST("/Nope", b"")
        assert not ok
        ok, _ = client.send_POST(
            "/Upload", b"garbage", headers={"Content-Type": "application/x-cpio"}
        )
        assert not ok
    finally:
        client.close()


def test_server_stop_without_serving(config_factory):
    server = AirDropServer(config_factory(interface="x"), bind_host="127.0.0.1", announce=False)
    server.stop()  # used to dead-lock


def test_port_in_use_falls_back(config_factory):
    first = AirDropServer(config_factory(interface="x"), bind_host="127.0.0.1", announce=False)
    try:
        cfg = config_factory(interface="x", server_port=first.port)
        second = AirDropServer(cfg, bind_host="127.0.0.1", announce=False)
        assert second.port != first.port
        second.stop()
    finally:
        first.stop()
