"""The engine behind the desktop app: two engines exchanging files on localhost."""

import queue
import threading
import time

import pytest

from opendrop.engine import Engine, EventBus, preview_data_url


def wait_for(q, event, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msg = q.get(timeout=0.2)
        except queue.Empty:
            continue
        if msg["event"] == event:
            return msg["data"]
    raise AssertionError(f"event {event!r} not received")


@pytest.fixture
def engines(config_factory, tmp_path):
    receiver = Engine(
        config_factory(
            airdrop_dir=str(tmp_path / "r"),
            receive_dir=str(tmp_path / "inbox"),
            computer_name="Receiver",
            interface="x",
        ),
        bind_host="127.0.0.1",
        announce=False,
    )
    sender = Engine(
        config_factory(airdrop_dir=str(tmp_path / "s"), computer_name="Sender", interface="x"),
        bind_host="127.0.0.1",
        announce=False,
    )
    receiver.start_receiving()
    yield sender, receiver
    sender.shutdown()
    receiver.shutdown()


def test_send_with_user_confirmation(engines, tmp_path):
    sender, receiver = engines
    events = receiver.events.subscribe()
    sender_events = sender.events.subscribe()

    f = tmp_path / "photo.txt"
    f.write_text("hello")
    peer = sender.add_peer("127.0.0.1", receiver.server.port)
    assert peer["name"] == "Receiver"
    sender.send(peer["id"], paths=[str(f)])

    request = wait_for(events, "request")
    assert request["sender"] == "Sender"
    assert request["files"] == ["photo.txt"]
    assert receiver.state()["pending"][0]["id"] == request["id"]
    assert receiver.decide(request["id"], True)

    received = wait_for(events, "received")
    assert received["names"] == ["photo.txt"]
    assert (tmp_path / "inbox" / "photo.txt").read_text() == "hello"

    while True:
        job = wait_for(sender_events, "job")
        if job["state"] in ("done", "failed", "declined"):
            break
    assert job["state"] == "done"
    assert job["progress"] == 1.0


def test_declined_transfer(engines, tmp_path):
    sender, receiver = engines
    events = receiver.events.subscribe()
    sender_events = sender.events.subscribe()
    f = tmp_path / "a.txt"
    f.write_text("a")
    peer = sender.add_peer("127.0.0.1", receiver.server.port)
    sender.send(peer["id"], paths=[str(f)])
    request = wait_for(events, "request")
    receiver.decide(request["id"], False)
    while True:
        job = wait_for(sender_events, "job")
        if job["state"] != "asking":
            break
    assert job["state"] == "declined"
    assert not (tmp_path / "inbox" / "a.txt").exists()


def test_auto_accept_and_links(engines):
    sender, receiver = engines
    receiver.auto_accept = True
    events = receiver.events.subscribe()
    peer = sender.add_peer("127.0.0.1", receiver.server.port)
    sender.send(peer["id"], urls=["https://example.org"])
    received = wait_for(events, "received")
    assert received["urls"] == ["https://example.org"]
    assert receiver.state()["history"][0]["urls"] == ["https://example.org"]


def test_stop_receiving_declines_pending(engines, tmp_path):
    sender, receiver = engines
    events = receiver.events.subscribe()
    f = tmp_path / "b.txt"
    f.write_text("b")
    peer = sender.add_peer("127.0.0.1", receiver.server.port)
    sender.send(peer["id"], paths=[str(f)])
    wait_for(events, "request")
    threading.Thread(target=receiver.stop_receiving).start()
    closed = wait_for(events, "request_closed")
    assert closed["accepted"] is False


def test_send_to_unknown_peer(engines):
    sender, _ = engines
    with pytest.raises(KeyError):
        sender.send("nope", paths=["x"])


def test_event_bus_fan_out():
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish("x", {"n": 1})
    assert a.get_nowait()["data"] == {"n": 1}
    assert b.get_nowait()["event"] == "x"
    bus.unsubscribe(a)
    bus.publish("y")
    assert a.empty()


def test_preview_conversion():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (600, 400), (10, 20, 30)).save(buf, format="JPEG2000")
    url = preview_data_url(buf.getvalue())
    assert url.startswith("data:image/jpeg;base64,")
    assert preview_data_url(b"garbage") is None
    assert preview_data_url(None) is None
