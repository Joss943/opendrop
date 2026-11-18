"""
OpenDrop: an open source AirDrop implementation

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Desktop application built with Qt (PySide6). It wraps :class:`opendrop.engine.Engine`:

* "Send": nearby AirDrop devices; drop files on a device to send them.
* "Received": everything received, double-click to open.
* "Settings": name, network interface, download folder, auto-accept.
* A native dialog (with the sender's preview image) to accept or decline
  incoming transfers, a system tray icon and desktop notifications.
"""

import base64
import json
import logging
import os
import sys
import threading
import time

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, util
from ..config import AirDropConfig
from ..engine import PENDING_TIMEOUT, Engine, open_path
from .i18n import t

logger = logging.getLogger(__name__)

ACCENT = QColor("#2f6fec")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def make_app_icon(size=256):
    """Draw the app icon (a drop with an arrow) so no image file is needed."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(ACCENT))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)
    path = QPainterPath()
    s = size
    path.moveTo(s * 0.5, s * 0.16)
    path.cubicTo(s * 0.5, s * 0.16, s * 0.24, s * 0.48, s * 0.24, s * 0.62)
    path.cubicTo(s * 0.24, s * 0.78, s * 0.36, s * 0.86, s * 0.5, s * 0.86)
    path.cubicTo(s * 0.64, s * 0.86, s * 0.76, s * 0.78, s * 0.76, s * 0.62)
    path.cubicTo(s * 0.76, s * 0.48, s * 0.5, s * 0.16, s * 0.5, s * 0.16)
    p.setBrush(QBrush(QColor("white")))
    p.drawPath(path)
    pen = QPen(ACCENT, s * 0.06, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.drawLine(int(s * 0.5), int(s * 0.46), int(s * 0.5), int(s * 0.74))
    p.drawLine(int(s * 0.39), int(s * 0.63), int(s * 0.5), int(s * 0.74))
    p.drawLine(int(s * 0.61), int(s * 0.63), int(s * 0.5), int(s * 0.74))
    p.end()
    return QIcon(pm)


def make_device_icon(name, size=64):
    """Round avatar with the device's initial."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    hue = sum(map(ord, name or "?")) % 360
    p.setBrush(QColor.fromHsv(hue, 120, 220))
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.setPen(QColor("white"))
    font = QFont()
    font.setPixelSize(int(size * 0.45))
    font.setBold(True)
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignCenter, (name or "?").strip()[:1].upper())
    p.end()
    return QIcon(pm)


def pixmap_from_data_url(url):
    if not url or "," not in url:
        return None
    pm = QPixmap()
    pm.loadFromData(base64.b64decode(url.split(",", 1)[1]))
    return pm if not pm.isNull() else None


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


class Settings:
    """GUI preferences stored as JSON next to the keys (``~/.opendrop/gui.json``)."""

    DEFAULTS = {
        "name": None,
        "interface": None,
        "receive_dir": os.path.join(os.path.expanduser("~"), "Downloads", "OpenDrop"),
        "auto_accept": False,
        "minimize_to_tray": True,
    }

    def __init__(self, directory):
        self.path = os.path.join(directory, "gui.json")
        self.values = dict(self.DEFAULTS)
        try:
            with open(self.path, encoding="utf-8") as f:
                self.values.update(json.load(f))
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.values.get(key)

    def update(self, **values):
        self.values.update(values)
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.values, f, indent=1)
        except OSError:
            logger.warning("Could not save GUI settings")


class EventBridge(QObject):
    """Forwards engine events (from background threads) to the Qt main thread."""

    event = Signal(dict)

    def __init__(self, engine):
        super().__init__()
        self._queue = engine.events.subscribe()
        self._stop = threading.Event()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        import queue  # pylint: disable=import-outside-toplevel

        while not self._stop.is_set():
            try:
                message = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            self.event.emit(message)

    def stop(self):
        self._stop.set()


# --------------------------------------------------------------------------
# Widgets
# --------------------------------------------------------------------------


class DeviceList(QListWidget):
    """Grid of nearby devices that accepts dropped files."""

    files_dropped = Signal(str, list)  # peer id, paths

    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(64, 64))
        self.setGridSize(QSize(150, 120))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setWordWrap(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DropOnly)
        self.setSpacing(8)
        self._empty_text = t("searching")

    def set_empty_text(self, text):
        self._empty_text = text
        self.viewport().update()

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if self.count() == 0:
            p = QPainter(self.viewport())
            p.setPen(self.palette().placeholderText().color())
            p.drawText(self.viewport().rect(), Qt.AlignCenter | Qt.TextWordWrap, self._empty_text)

    def _paths(self, event):
        md = event.mimeData()
        if not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile()]

    def dragEnterEvent(self, event):  # noqa: N802
        if self._paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # noqa: N802
        item = self.itemAt(event.position().toPoint())
        if item is not None and self._paths(event):
            self.setCurrentItem(item)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):  # noqa: N802
        item = self.itemAt(event.position().toPoint())
        paths = self._paths(event)
        if item is not None and paths:
            event.acceptProposedAction()
            self.files_dropped.emit(item.data(Qt.UserRole), paths)
        else:
            event.ignore()


class JobWidget(QWidget):
    def __init__(self, job):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        self.title = QLabel()
        self.title.setTextFormat(Qt.PlainText)
        self.status = QLabel()
        self.status.setStyleSheet("color: palette(placeholder-text);")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        layout.addWidget(self.title)
        layout.addWidget(self.bar)
        layout.addWidget(self.status)
        self.update_job(job)

    def update_job(self, job):
        items = job["items"]
        label = items[0] if len(items) == 1 else f"{items[0]} (+{len(items) - 1})"
        self.title.setText(label)
        state = job["state"]
        self.status.setText(t(f"state_{state}", job["peer"], job.get("error") or ""))
        self.bar.setValue(int(job.get("progress", 0) * 1000))
        self.bar.setVisible(state in ("asking", "sending"))
        if state == "asking":
            self.bar.setRange(0, 0)  # busy indicator
        else:
            self.bar.setRange(0, 1000)
        color = {"done": "#1f9d55", "failed": "#d64545", "declined": "#d64545"}.get(state)
        self.status.setStyleSheet(
            f"color: {color};" if color else "color: palette(placeholder-text);"
        )


class RequestDialog(QDialog):
    """Accept/decline an incoming transfer, with the sender's preview image."""

    decided = Signal(str, bool)

    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.request_id = request["id"]
        self.setWindowTitle(t("request_title"))
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        avatar = QLabel()
        avatar.setPixmap(make_device_icon(request["sender"]).pixmap(48, 48))
        header.addWidget(avatar)
        text = QLabel(t("request_text", _escape(request["sender"])))
        text.setWordWrap(True)
        header.addWidget(text, 1)
        layout.addLayout(header)

        preview = pixmap_from_data_url(request.get("preview"))
        if preview is not None:
            img = QLabel()
            img.setAlignment(Qt.AlignCenter)
            img.setPixmap(preview.scaled(320, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            layout.addWidget(img)

        items = request.get("urls") or request.get("files") or []
        listing = QLabel(
            "\n".join(f"• {i}" for i in items[:12]) + ("\n…" if len(items) > 12 else "")
        )
        listing.setTextFormat(Qt.PlainText)
        listing.setWordWrap(True)
        layout.addWidget(listing)

        self.countdown = QLabel()
        self.countdown.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(self.countdown)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        decline = QPushButton(t("decline"))
        accept = QPushButton(t("accept"))
        accept.setDefault(True)
        decline.clicked.connect(lambda: self._finish(False))
        accept.clicked.connect(lambda: self._finish(True))
        buttons.addWidget(decline)
        buttons.addWidget(accept)
        layout.addLayout(buttons)

        self._deadline = time.monotonic() + PENDING_TIMEOUT - 2
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)
        self._tick()
        self._done = False

    def _tick(self):
        left = int(self._deadline - time.monotonic())
        if left <= 0:
            self._finish(False)
        else:
            self.countdown.setText(t("request_timeout", left))

    def _finish(self, accept):
        if getattr(self, "_done", False):
            return
        self._done = True
        self._timer.stop()
        self.decided.emit(self.request_id, accept)
        self.close()

    def closeEvent(self, event):  # noqa: N802
        if not getattr(self, "_done", True):
            self._finish(False)
        super().closeEvent(event)


def _escape(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self, engine, settings, start_hidden=False):
        super().__init__()
        self.engine = engine
        self.settings = settings
        self.dialogs = {}
        self.job_items = {}
        self._quitting = False

        self.setWindowTitle(f"OpenDrop {__version__}")
        self.setWindowIcon(make_app_icon())
        self.resize(820, 600)

        central = QWidget()
        root = QVBoxLayout(central)
        root.addLayout(self._build_header())
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_send_tab(), t("tab_send"))
        self.tabs.addTab(self._build_inbox_tab(), t("tab_inbox"))
        self.tabs.addTab(self._build_settings_tab(), t("tab_settings"))
        root.addWidget(self.tabs)
        self.setCentralWidget(central)

        self._build_tray()

        self.bridge = EventBridge(engine)
        self.bridge.event.connect(self.on_event)

        engine.start_receiving()
        engine.start_discovery()
        self.refresh_all()
        if not start_hidden:
            self.show()

    # ------------------------------------------------------------- layout

    def _build_header(self):
        bar = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(make_app_icon().pixmap(36, 36))
        bar.addWidget(icon)
        title = QLabel()
        title.setTextFormat(Qt.RichText)
        self.title_label = title
        bar.addWidget(title, 1)
        self.receive_switch = QCheckBox(t("receiving_on"))
        self.receive_switch.toggled.connect(self.toggle_receiving)
        bar.addWidget(self.receive_switch)
        return bar

    def _build_send_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel(f"<b>{t('devices')}</b>")
        layout.addWidget(heading)
        hint = QLabel(t("devices_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(hint)

        self.devices = DeviceList()
        self.devices.files_dropped.connect(self.send_paths)
        self.devices.itemDoubleClicked.connect(lambda _: self.pick_files())
        layout.addWidget(self.devices, 3)

        buttons = QHBoxLayout()
        for label, slot in (
            (t("send_files"), self.pick_files),
            (t("send_folder"), self.pick_folder),
            (t("send_link"), self.ask_link),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch(1)
        manual = QPushButton(t("add_manual"))
        manual.clicked.connect(self.add_manual)
        buttons.addWidget(manual)
        refresh = QPushButton(t("refresh"))
        refresh.clicked.connect(self.restart_discovery)
        buttons.addWidget(refresh)
        layout.addLayout(buttons)

        layout.addWidget(QLabel(f"<b>{t('transfers')}</b>"))
        self.jobs = QListWidget()
        layout.addWidget(self.jobs, 2)
        return page

    def _build_inbox_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.inbox = QListWidget()
        self.inbox.setIconSize(QSize(32, 32))
        self.inbox.itemDoubleClicked.connect(self.open_received)
        layout.addWidget(self.inbox)
        buttons = QHBoxLayout()
        open_btn = QPushButton(t("open_folder"))
        open_btn.clicked.connect(lambda: open_path(self.engine.config.receive_dir))
        clear_btn = QPushButton(t("clear_list"))
        clear_btn.clicked.connect(self.clear_inbox)
        buttons.addWidget(open_btn)
        buttons.addStretch(1)
        buttons.addWidget(clear_btn)
        layout.addLayout(buttons)
        return page

    def _build_settings_tab(self):
        page = QWidget()
        form = QFormLayout(page)
        self.name_edit = QLineEdit(self.engine.config.computer_name)
        form.addRow(t("settings_name"), self.name_edit)

        self.iface_combo = QComboBox()
        self.iface_combo.setEditable(True)
        for iface in util.list_interfaces():
            if iface.address is None:
                continue
            key = iface.nice_name if iface.name.startswith("{") else iface.name
            self.iface_combo.addItem(f"{key}  —  {iface.address}", key)
        idx = self.iface_combo.findData(self.engine.config.interface)
        if idx >= 0:
            self.iface_combo.setCurrentIndex(idx)
        else:
            self.iface_combo.setEditText(self.engine.config.interface)
        form.addRow(t("settings_interface"), self.iface_combo)
        note = QLabel(t("settings_awdl") + "\n\n" + t("settings_android"))
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        form.addRow("", note)

        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(self.engine.config.receive_dir)
        browse = QPushButton(t("settings_browse"))
        browse.clicked.connect(self.browse_folder)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        form.addRow(t("settings_folder"), folder_row)

        self.auto_check = QCheckBox(t("settings_auto"))
        self.auto_check.setChecked(bool(self.settings["auto_accept"]))
        form.addRow("", self.auto_check)
        self.tray_check = QCheckBox(t("settings_tray"))
        self.tray_check.setChecked(bool(self.settings["minimize_to_tray"]))
        form.addRow("", self.tray_check)

        save = QPushButton(t("settings_save"))
        save.clicked.connect(self.apply_settings)
        form.addRow("", save)
        self.settings_status = QLabel()
        form.addRow("", self.settings_status)
        return page

    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(make_app_icon(64), self)
        self.tray.setToolTip("OpenDrop")
        menu = QMenu()
        show = QAction(t("tray_show"), self)
        show.triggered.connect(self.show_window)
        self.tray_receive = QAction(t("tray_receive"), self)
        self.tray_receive.setCheckable(True)
        self.tray_receive.toggled.connect(self.toggle_receiving)
        quit_action = QAction(t("tray_quit"), self)
        quit_action.triggered.connect(self.quit)
        menu.addAction(show)
        menu.addAction(self.tray_receive)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_window() if reason == QSystemTrayIcon.Trigger else None
        )
        self.tray.show()
        self._tray_menu = menu

    # ------------------------------------------------------------ actions

    def selected_peer(self):
        item = self.devices.currentItem()
        if item is None:
            QMessageBox.information(self, "OpenDrop", t("pick_device"))
            return None
        return item.data(Qt.UserRole)

    def pick_files(self):
        peer = self.selected_peer()
        if peer:
            paths, _ = QFileDialog.getOpenFileNames(self, t("send_files"))
            if paths:
                self.send_paths(peer, paths)

    def pick_folder(self):
        peer = self.selected_peer()
        if peer:
            path = QFileDialog.getExistingDirectory(self, t("send_folder"))
            if path:
                self.send_paths(peer, [path])

    def ask_link(self):
        peer = self.selected_peer()
        if not peer:
            return
        url, ok = QInputDialog.getText(self, t("send_link"), t("link_prompt"), text="https://")
        url = url.strip()
        if ok and "://" in url and len(url) > 8:
            self._start_send(peer, urls=[url])

    def send_paths(self, peer_id, paths):
        self._start_send(peer_id, paths=paths)

    def _start_send(self, peer_id, paths=None, urls=None):
        try:
            self.engine.send(peer_id, paths=paths, urls=urls)
        except (KeyError, ValueError) as e:
            QMessageBox.warning(self, t("error"), str(e))

    def add_manual(self):
        text, ok = QInputDialog.getText(self, t("add_manual"), t("address_prompt"))
        text = text.strip()
        if not ok or not text:
            return
        host, port = text, 8771
        if text.count(":") == 1 or text.startswith("["):
            host, _, port_s = text.rpartition(":")
            host = host.strip("[]")
            port = int(port_s) if port_s.isdigit() else 8771
        try:
            self.engine.add_peer(host, port)
        except Exception as e:  # pylint: disable=broad-except
            QMessageBox.warning(self, t("error"), t("address_failed", text, e))

    def restart_discovery(self):
        self.engine.stop_discovery()
        self.devices.clear()
        self.engine.start_discovery()
        self.refresh_all()

    def toggle_receiving(self, on):
        if on and self.engine.server is None:
            self.engine.start_receiving()
        elif not on and self.engine.server is not None:
            self.engine.stop_receiving()
        self.refresh_header()

    def browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, t("settings_folder"), self.folder_edit.text())
        if path:
            self.folder_edit.setText(path)

    def apply_settings(self):
        iface = self.iface_combo.currentData()
        if iface is None or self.iface_combo.currentText() != self.iface_combo.itemText(
            self.iface_combo.currentIndex()
        ):
            iface = self.iface_combo.currentText().split("  —  ")[0].strip()
        name = self.name_edit.text().strip() or self.engine.config.computer_name
        folder = self.folder_edit.text().strip()
        self.engine.update_settings(
            name=name,
            interface=iface,
            receive_dir=folder,
            auto_accept=self.auto_check.isChecked(),
        )
        self.settings.update(
            name=name,
            interface=iface,
            receive_dir=self.engine.config.receive_dir,
            auto_accept=self.auto_check.isChecked(),
            minimize_to_tray=self.tray_check.isChecked(),
        )
        self.devices.clear()
        self.refresh_all()
        self.settings_status.setText(t("settings_saved"))
        QTimer.singleShot(3000, lambda: self.settings_status.setText(""))

    def open_received(self, item):
        paths = item.data(Qt.UserRole) or []
        for path in paths[:5]:
            if os.path.exists(path):
                open_path(path)

    def clear_inbox(self):
        self.engine.history.clear()
        self.inbox.clear()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit(self):
        self._quitting = True
        self.close()

    def notify(self, title, message):
        if self.tray is not None and QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(title, message, make_app_icon(64), 5000)

    # ------------------------------------------------------------- events

    def on_event(self, message):
        event, data = message["event"], message["data"]
        if event == "peer":
            self.add_device(data)
        elif event == "peer_removed":
            self.remove_device(data["id"])
        elif event == "job":
            self.update_job(data)
        elif event == "request":
            self.show_request(data)
        elif event == "request_closed":
            dialog = self.dialogs.pop(data["id"], None)
            if dialog is not None:
                dialog._done = True  # pylint: disable=protected-access
                dialog.close()
        elif event == "received":
            self.add_inbox(data, front=True)
            what = ", ".join(data["names"] or data["urls"])
            self.notify(t("received_notify", data["sender"]), what)
        elif event in ("error", "receiving", "discovering", "settings"):
            self.refresh_header()

    def add_device(self, peer):
        for i in range(self.devices.count()):
            if self.devices.item(i).data(Qt.UserRole) == peer["id"]:
                self.devices.item(i).setText(peer["name"])
                return
        item = QListWidgetItem(make_device_icon(peer["name"]), peer["name"])
        item.setData(Qt.UserRole, peer["id"])
        item.setToolTip(f"{peer['name']}\n{peer['address']}:{peer['port']}")
        item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.devices.addItem(item)

    def remove_device(self, peer_id):
        for i in range(self.devices.count()):
            if self.devices.item(i).data(Qt.UserRole) == peer_id:
                self.devices.takeItem(i)
                return

    def update_job(self, job):
        entry = self.job_items.get(job["id"])
        if entry is None:
            item = QListWidgetItem()
            widget = JobWidget(job)
            item.setSizeHint(widget.sizeHint())
            self.jobs.insertItem(0, item)
            self.jobs.setItemWidget(item, widget)
            self.job_items[job["id"]] = (item, widget)
        else:
            entry[1].update_job(job)

    def show_request(self, request):
        dialog = RequestDialog(request, self)
        dialog.decided.connect(self.engine.decide)
        dialog.decided.connect(lambda rid, _ok: self.dialogs.pop(rid, None))
        self.dialogs[request["id"]] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        what = ", ".join(request.get("files") or request.get("urls") or [])
        self.notify(t("request_title"), f"{request['sender']}: {what}")

    def add_inbox(self, entry, front=False):
        names = entry["names"] or entry["urls"]
        if not names:
            return
        label = names[0] if len(names) == 1 else f"{names[0]} (+{len(names) - 1})"
        when = time.strftime("%d/%m %H:%M", time.localtime(entry["time"]))
        size = f"  ·  {human_size(entry['size'])}" if entry.get("size") else ""
        text = f"{label}\n{t('from', entry['sender'])}  ·  {when}{size}"
        item = QListWidgetItem(make_device_icon(entry["sender"], 32), text)
        item.setData(Qt.UserRole, entry["paths"])
        item.setToolTip("\n".join(entry["paths"] or entry["urls"]))
        if front:
            self.inbox.insertItem(0, item)
        else:
            self.inbox.addItem(item)

    def refresh_header(self):
        state = self.engine.state()
        name = _escape(state["name"])
        errors = state["errors"]
        lines = [f"<span style='font-size:15pt; font-weight:600'>{name}</span>"]
        if "receive" in errors:
            lines.append(
                f"<span style='color:#d64545'>{_escape(t('error_receive', errors['receive']))}</span>"
            )
        if "discover" in errors:
            lines.append(
                f"<span style='color:#d64545'>{_escape(t('error_discover', errors['discover']))}</span>"
            )
        if not errors:
            lines.append(
                f"<span style='color:gray'>{_escape(state['interface'])}  ·  "
                f"{_escape(state['receive_dir'])}</span>"
            )
        self.title_label.setText("<br>".join(lines))
        for widget in (self.receive_switch, getattr(self, "tray_receive", None)):
            if widget is not None:
                widget.blockSignals(True)
                widget.setChecked(state["receiving"])
                widget.blockSignals(False)
        self.receive_switch.setText(t("receiving_on") if state["receiving"] else t("receiving_off"))
        if "discover" in errors:
            self.devices.set_empty_text(t("error_discover", errors["discover"]))
        else:
            self.devices.set_empty_text(t("searching"))

    def refresh_all(self):
        state = self.engine.state()
        self.devices.clear()
        for peer in state["peers"]:
            self.add_device(peer)
        self.inbox.clear()
        for entry in state["history"]:
            self.add_inbox(entry)
        self.refresh_header()

    # -------------------------------------------------------------- close

    def closeEvent(self, event):  # noqa: N802
        if not self._quitting and self.tray is not None and self.settings["minimize_to_tray"]:
            event.ignore()
            self.hide()
            if not getattr(self, "_told_tray", False):
                self._told_tray = True
                self.notify("OpenDrop", t("tray_still_running"))
            return
        self.bridge.stop()
        self.engine.shutdown()
        if self.tray is not None:
            self.tray.hide()
        event.accept()
        QApplication.instance().quit()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build_engine(config_dir="~/.opendrop", interface=None, debug=False):
    base = os.path.abspath(os.path.expanduser(config_dir))
    os.makedirs(base, exist_ok=True)
    settings = Settings(base)
    receive_dir = settings["receive_dir"]
    os.makedirs(receive_dir, exist_ok=True)
    config = AirDropConfig(
        airdrop_dir=base,
        computer_name=settings["name"],
        interface=interface or settings["interface"] or util.suggest_interface(),
        receive_dir=receive_dir,
        debug=debug,
    )
    engine = Engine(config)
    engine.auto_accept = bool(settings["auto_accept"])
    return engine, settings


def main(argv=None):
    import argparse  # pylint: disable=import-outside-toplevel

    parser = argparse.ArgumentParser(prog="opendrop-gui", description="OpenDrop desktop app")
    parser.add_argument("-i", "--interface", help="network interface (default: automatic)")
    parser.add_argument("--config-dir", default=os.environ.get("OPENDROP_HOME", "~/.opendrop"))
    parser.add_argument("--hidden", action="store_true", help="start minimized to the tray")
    parser.add_argument("-d", "--debug", action="store_true")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(message)s")
    logging.getLogger("zeroconf").setLevel(logging.WARNING)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("OpenDrop")
    app.setApplicationDisplayName("OpenDrop")
    app.setWindowIcon(make_app_icon())
    app.setQuitOnLastWindowClosed(False)

    engine, settings = build_engine(args.config_dir, args.interface, args.debug)
    window = MainWindow(engine, settings, start_hidden=args.hidden)
    app._opendrop_window = window  # keep a reference  # pylint: disable=protected-access
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
