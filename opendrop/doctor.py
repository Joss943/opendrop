"""
OpenDrop: an open source AirDrop implementation

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

``opendrop doctor``: explain what works on this machine and what is missing.
"""

import os
import platform

from . import __version__, util


def _ok(msg):
    print(f"  [ok]   {msg}")


def _warn(msg):
    print(f"  [warn] {msg}")


def _fail(msg):
    print(f"  [FAIL] {msg}")


def _owl_running():
    """Best-effort check for a running OWL daemon on Linux."""
    proc = "/proc"
    if not os.path.isdir(proc):
        return False
    for pid in os.listdir(proc):
        if not pid.isdigit():
            continue
        try:
            with open(os.path.join(proc, pid, "comm"), encoding="utf-8") as f:
                if f.read().strip() == "owl":
                    return True
        except OSError:
            continue
    return False


def _check_port(port=8771):
    import socket  # pylint: disable=import-outside-toplevel

    family = socket.AF_INET6 if socket.has_ipv6 else socket.AF_INET
    host = "::" if family == socket.AF_INET6 else "0.0.0.0"
    try:
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.bind((host, port))
        _ok(f"TCP port {port} is free for `opendrop receive`")
    except OSError as e:
        _warn(f"TCP port {port} is busy ({e}); `receive` will pick the next free port")
    if platform.system() == "Windows":
        _warn(
            "Windows Firewall: allow Python on private networks when asked, "
            "otherwise other devices cannot reach `opendrop receive`."
        )
    elif platform.system() == "Darwin":
        _warn(
            "macOS firewall: allow incoming connections for Python when asked "
            "(System Settings > Network > Firewall)."
        )
    return 0


def _check_config_dir(config_dir):
    path = os.path.abspath(os.path.expanduser(config_dir))
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write-test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        _ok(f"configuration directory {path} is writable")
        return 0
    except OSError as e:
        _fail(f"configuration directory {path} is not writable: {e}")
        return 1


def run_doctor(interface=None, config_dir="~/.opendrop"):
    problems = 0
    system = platform.system()
    interface = interface or util.default_interface_name()

    print(f"OpenDrop {__version__}")
    print(f"Python {platform.python_version()} on {system} {platform.release()}\n")

    print("Python dependencies:")
    for module in ("zeroconf", "ifaddr", "cryptography", "PIL"):
        try:
            mod = __import__(module)
            _ok(f"{module} {getattr(mod, '__version__', '')}".strip())
        except ImportError:
            problems += 1
            _fail(f"{module} is not installed (pip install opendrop)")

    print("\nNetwork interfaces with IPv6:")
    interfaces = util.list_interfaces()
    for iface in interfaces:
        if iface.ipv6 is not None:
            print(f"  - {iface.display_name}: {iface.ipv6} (index {iface.index})")

    print(f"\nSelected interface: {interface}")
    try:
        iface = util.require_interface(interface)
        if iface.ipv6 is not None:
            _ok(f"{iface.display_name} has IPv6 address {iface.ipv6}")
        else:
            _warn(
                f"{iface.display_name} has no IPv6 address; using IPv4 {iface.ipv4} "
                "(works between OpenDrop peers only, not with Apple devices)"
            )
    except util.InterfaceError as e:
        problems += 1
        _fail(str(e))

    print("\nLocal setup:")
    problems += _check_port()
    problems += _check_config_dir(config_dir)

    print("\nApple AirDrop compatibility:")
    if system == "Darwin":
        _ok("macOS has native AWDL support (interface awdl0)")
    elif system == "Linux":
        if _owl_running():
            _ok("OWL is running")
        else:
            _warn(
                "OWL does not seem to be running. It is required to talk to Apple "
                "devices: https://github.com/seemoo-lab/owl"
            )
    elif system == "Windows":
        _warn(
            "Windows has no AWDL support, so Apple devices cannot be reached directly. "
            "OpenDrop works between OpenDrop peers on the same network with "
            "`-i <interface>`. See docs/WINDOWS.md for the WSL2 + OWL option."
        )
    else:
        _warn(f"{system} is untested")

    print()
    print("All checks passed." if problems == 0 else f"{problems} problem(s) found.")
    return 0 if problems == 0 else 1
