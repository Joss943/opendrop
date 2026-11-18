"""Smoke tests of the desktop app (skipped when PySide6 is not installed)."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from opendrop.engine import Engine  # noqa: E402
from opendrop.gui import app as gapp  # noqa: E402


@pytest.fixture
def window(qtbot, config_factory, tmp_path):
    config = config_factory(computer_name="Test PC", interface="x")
    engine = Engine(config, bind_host="127.0.0.1", announce=False)
    settings = gapp.Settings(str(tmp_path))
    settings.values["minimize_to_tray"] = False
    win = gapp.MainWindow(engine, settings, start_hidden=True)
    qtbot.addWidget(win)
    yield win
    engine.shutdown()


def test_window_lists_devices_and_jobs(window):
    window.add_device({"id": "p1", "name": "iPhone", "address": "::1", "port": 1})
    window.add_device({"id": "p1", "name": "iPhone 2", "address": "::1", "port": 1})
    assert window.devices.count() == 1
    assert window.devices.item(0).text() == "iPhone 2"
    window.remove_device("p1")
    assert window.devices.count() == 0

    job = {"id": "j", "peer": "Mac", "items": ["a.pdf"], "state": "asking", "progress": 0}
    window.update_job(job)
    window.update_job({**job, "state": "failed", "error": "boom"})
    assert window.jobs.count() == 1


def test_request_dialog_decides(window, qtbot):
    dialog = gapp.RequestDialog(
        {"id": "r1", "sender": "Julie", "files": ["a.jpg"], "urls": [], "preview": None}, window
    )
    qtbot.addWidget(dialog)
    with qtbot.waitSignal(dialog.decided) as blocker:
        dialog._finish(True)
    assert blocker.args == ["r1", True]


def test_settings_roundtrip(tmp_path):
    s = gapp.Settings(str(tmp_path))
    s.update(name="X", auto_accept=True)
    again = gapp.Settings(str(tmp_path))
    assert again["name"] == "X" and again["auto_accept"] is True


def test_inbox_entry(window):
    window.add_inbox(
        {
            "id": "h",
            "sender": "Pixel",
            "time": 0,
            "paths": [],
            "names": ["a.txt"],
            "urls": [],
            "size": 10,
        }
    )
    assert window.inbox.count() == 1
    assert "Pixel" in window.inbox.item(0).text()
