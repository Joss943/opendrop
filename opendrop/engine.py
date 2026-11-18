"""
OpenDrop: an open source AirDrop implementation

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

The engine runs everything the graphical interface needs in the background:
the AirDrop receiver, discovery of nearby receivers, outgoing transfers, and a
history of what was received. It reports what happens through an
:class:`EventBus` so that any UI can follow along.
"""

import base64
import http.client
import io
import logging
import os
import queue
import secrets
import shutil
import ssl
import threading
import time

from .config import AirDropReceiverFlags

logger = logging.getLogger(__name__)

PENDING_TIMEOUT = 60  # seconds the user has to accept an incoming transfer
HISTORY_LIMIT = 200


class EventBus:
    """Fan-out of events to any number of subscribers (one queue each)."""

    def __init__(self):
        self._subscribers = []
        self._lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event, data=None):
        message = {"event": event, "data": data if data is not None else {}}
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(message)
            except queue.Full:
                pass


def preview_data_url(icon_bytes, max_size=256):
    """Convert the JPEG 2000 preview of an AirDrop request to a JPEG data URL."""
    if not icon_bytes:
        return None
    try:
        from PIL import Image  # pylint: disable=import-outside-toplevel

        with Image.open(io.BytesIO(icon_bytes)) as im:
            im = im.convert("RGB")
            im.thumbnail((max_size, max_size))
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()
    except Exception:  # pylint: disable=broad-except
        return None


def open_path(path):
    """Open a file or folder with the default application of the OS."""
    import subprocess  # pylint: disable=import-outside-toplevel
    import sys  # pylint: disable=import-outside-toplevel

    if sys.platform.startswith("win"):
        os.startfile(path)  # pylint: disable=no-member
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class Engine:
    """
    Background service used by the GUI.

    :param config: :class:`~opendrop.config.AirDropConfig`
    :param bind_host: where the AirDrop receiver listens
    :param announce: announce/browse with mDNS (disable in tests)
    """

    def __init__(self, config, bind_host="::", announce=True):
        self.config = config
        self.bind_host = bind_host
        self.announce = announce
        self.events = EventBus()
        self.auto_accept = False

        self.server = None
        self.browser = None
        self.peers = {}  # id -> peer dict
        self.pending = {}  # id -> pending request dict
        self.history = []  # received transfers, newest first
        self.jobs = {}  # id -> outgoing job dict
        self.errors = {}  # component -> message
        self._lock = threading.RLock()
        self.config.accept_callback = self._on_ask

    # ------------------------------------------------------------ helpers

    def _new_id(self):
        return secrets.token_hex(6)

    def _set_error(self, component, message):
        with self._lock:
            if message:
                self.errors[component] = message
            else:
                self.errors.pop(component, None)
        self.events.publish("error", {"component": component, "message": message})

    def state(self):
        with self._lock:
            return {
                "name": self.config.computer_name,
                "interface": self.config.interface,
                "receive_dir": self.config.receive_dir,
                "receiving": self.server is not None,
                "discovering": self.browser is not None,
                "auto_accept": self.auto_accept,
                "peers": sorted(self.peers.values(), key=lambda p: p["name"] or ""),
                "pending": [self._public_pending(p) for p in self.pending.values()],
                "history": list(self.history),
                "jobs": sorted(self.jobs.values(), key=lambda j: -j["started"]),
                "errors": dict(self.errors),
            }

    @staticmethod
    def _public_pending(p):
        return {k: v for k, v in p.items() if not k.startswith("_")}

    # ------------------------------------------------------------ receive

    def start_receiving(self):
        from .server import AirDropServer  # pylint: disable=import-outside-toplevel

        with self._lock:
            if self.server is not None:
                return
            try:
                server = AirDropServer(
                    self.config, bind_host=self.bind_host, announce=self.announce
                )
            except Exception as e:  # pylint: disable=broad-except
                self._set_error("receive", str(e))
                return
            server.listener = self._on_server_event
            server.start_service()
            server.start_server(blocking=False)
            self.server = server
        self._set_error("receive", None)
        self.events.publish("receiving", {"receiving": True, "port": server.port})

    def stop_receiving(self):
        with self._lock:
            server, self.server = self.server, None
            for p in self.pending.values():
                p["_decision"] = False
                p["_event"].set()
        if server is not None:
            server.stop()
        self.events.publish("receiving", {"receiving": False})

    def _on_ask(self, transfer):
        """Called by the receiver thread: wait for the user to decide."""
        request = {
            "id": self._new_id(),
            "sender": transfer.sender_name,
            "model": transfer.sender_model,
            "address": transfer.client_address,
            "files": transfer.files,
            "urls": transfer.urls,
            "preview": preview_data_url(transfer.icon),
            "time": time.time(),
            "_event": threading.Event(),
            "_decision": False,
        }
        if self.auto_accept:
            self.events.publish("auto_accepted", self._public_pending(request))
            return True
        with self._lock:
            self.pending[request["id"]] = request
        self.events.publish("request", self._public_pending(request))
        decided = request["_event"].wait(PENDING_TIMEOUT)
        with self._lock:
            self.pending.pop(request["id"], None)
        accepted = decided and request["_decision"]
        self.events.publish("request_closed", {"id": request["id"], "accepted": accepted})
        return accepted

    def decide(self, request_id, accept):
        with self._lock:
            request = self.pending.get(request_id)
        if request is None:
            return False
        request["_decision"] = bool(accept)
        request["_event"].set()
        return True

    def _on_server_event(self, event, transfer=None, paths=None, error=None, size=0):
        if event == "upload_started":
            self.events.publish(
                "incoming", {"sender": transfer.sender_name, "files": transfer.files}
            )
        elif event == "upload_failed":
            self.events.publish("incoming_failed", {"sender": transfer.sender_name, "error": error})
        elif event in ("upload_done", "links"):
            entry = {
                "id": self._new_id(),
                "sender": transfer.sender_name,
                "via": "AirDrop",
                "time": time.time(),
                "paths": paths or [],
                "names": [os.path.basename(p) for p in (paths or [])],
                "urls": transfer.urls,
                "size": size,
            }
            self._add_history(entry)

    def _add_history(self, entry):
        with self._lock:
            self.history.insert(0, entry)
            del self.history[HISTORY_LIMIT:]
        self.events.publish("received", entry)

    # ---------------------------------------------------------- discovery

    def start_discovery(self):
        from .client import AirDropBrowser  # pylint: disable=import-outside-toplevel

        with self._lock:
            if self.browser is not None or not self.announce:
                return
            try:
                browser = AirDropBrowser(self.config)
                browser.start(
                    callback_add=self._on_service, callback_remove=self._on_service_removed
                )
            except Exception as e:  # pylint: disable=broad-except
                self._set_error("discover", str(e))
                return
            self.browser = browser
        self._set_error("discover", None)
        self.events.publish("discovering", {"discovering": True})

    def stop_discovery(self):
        with self._lock:
            browser, self.browser = self.browser, None
            self.peers.clear()
        if browser is not None:
            browser.stop()
        self.events.publish("discovering", {"discovering": False})

    def _on_service(self, info):
        threading.Thread(target=self._probe_service, args=(info,), daemon=True).start()

    def _probe_service(self, info):
        from .client import AirDropClient  # pylint: disable=import-outside-toplevel

        addresses = sorted(info.parsed_addresses(), key=lambda a: ":" not in a)
        if not addresses:
            return
        identifier = info.name.split(".")[0]
        if self.server is not None and identifier == self.config.service_id:
            return  # that's us
        try:
            flags = int((info.properties or {}).get(b"flags") or 0)
        except ValueError:
            flags = 0
        name = None
        if not flags or flags & AirDropReceiverFlags.SUPPORTS_DISCOVER_MAYBE:
            client = AirDropClient(self.config, (addresses[0], int(info.port)))
            try:
                name = client.send_discover()
            except (OSError, ssl.SSLError, http.client.HTTPException):
                name = None
            finally:
                client.close()
        if name is None:
            return
        peer = {
            "id": identifier,
            "name": name,
            "address": addresses[0],
            "port": int(info.port),
            "seen": time.time(),
        }
        with self._lock:
            self.peers[identifier] = peer
        self.events.publish("peer", peer)

    def _on_service_removed(self, name):
        identifier = name.split(".")[0]
        with self._lock:
            removed = self.peers.pop(identifier, None)
        if removed:
            self.events.publish("peer_removed", {"id": identifier})

    def add_peer(self, address, port, name=None):
        """Add a receiver by address (e.g. when mDNS is blocked on the network)."""
        from .client import AirDropClient  # pylint: disable=import-outside-toplevel

        client = AirDropClient(self.config, (address, int(port)))
        try:
            found = client.send_discover()
        finally:
            client.close()
        peer = {
            "id": f"manual-{address}-{port}",
            "name": name or found or address,
            "address": address,
            "port": int(port),
            "seen": time.time(),
        }
        with self._lock:
            self.peers[peer["id"]] = peer
        self.events.publish("peer", peer)
        return peer

    # --------------------------------------------------------------- send

    def send(self, peer_id, paths=None, urls=None, cleanup_dir=None):
        """Start sending files (or URLs) to a peer in the background."""
        with self._lock:
            peer = self.peers.get(peer_id)
        if peer is None:
            raise KeyError(f"Unknown receiver {peer_id}")
        items = list(urls or paths or [])
        if not items:
            raise ValueError("Nothing to send")
        job = {
            "id": self._new_id(),
            "peer": peer["name"],
            "items": [os.path.basename(os.path.normpath(i)) if not urls else i for i in items],
            "state": "asking",
            "progress": 0.0,
            "error": None,
            "started": time.time(),
        }
        with self._lock:
            self.jobs[job["id"]] = job
        self.events.publish("job", dict(job))
        threading.Thread(
            target=self._run_job,
            args=(job, peer, items, bool(urls), cleanup_dir),
            daemon=True,
        ).start()
        return job["id"]

    def _update_job(self, job, **changes):
        with self._lock:
            job.update(changes)
            snapshot = dict(job)
        self.events.publish("job", snapshot)

    def _run_job(self, job, peer, items, is_url, cleanup_dir):
        from .client import AirDropClient  # pylint: disable=import-outside-toplevel

        client = AirDropClient(self.config, (peer["address"], peer["port"]))
        last = [0.0]

        def progress(done, total):
            now = time.monotonic()
            if total and (now - last[0] > 0.2 or done >= total):
                last[0] = now
                self._update_job(job, progress=done / total)

        try:
            if not client.send_ask(items, is_url=is_url):
                self._update_job(job, state="declined")
                return
            if is_url:
                self._update_job(job, state="done", progress=1.0)
                return
            self._update_job(job, state="sending")
            if client.send_upload(items, progress=progress):
                self._update_job(job, state="done", progress=1.0)
            else:
                self._update_job(job, state="failed", error="The receiver rejected the upload")
        except Exception as e:  # pylint: disable=broad-except
            self._update_job(job, state="failed", error=str(e) or e.__class__.__name__)
        finally:
            client.close()
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

    # ----------------------------------------------------------- settings

    def update_settings(self, name=None, interface=None, receive_dir=None, auto_accept=None):
        restart = False
        if auto_accept is not None:
            self.auto_accept = bool(auto_accept)
        if name and name != self.config.computer_name:
            self.config.computer_name = name
            restart = True
        if interface and interface != self.config.interface:
            self.config.interface = interface
            restart = True
        if receive_dir:
            receive_dir = os.path.abspath(os.path.expanduser(receive_dir))
            os.makedirs(receive_dir, exist_ok=True)
            self.config.receive_dir = receive_dir
        if restart:
            was_receiving = self.server is not None
            was_discovering = self.browser is not None
            self.stop_receiving()
            self.stop_discovery()
            if was_receiving:
                self.start_receiving()
            if was_discovering:
                self.start_discovery()
        self.events.publish("settings", self.state())

    def shutdown(self):
        self.stop_discovery()
        self.stop_receiving()
