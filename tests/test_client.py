import pytest

from opendrop.client import AirDropBrowser
from opendrop.server import AirDropServer

from .conftest import loopback_name


def test_browser_and_service_setup(config_factory):
    """Starts mDNS browsing and announcing on the loopback interface."""
    loopback = loopback_name()
    if loopback is None:
        pytest.skip("no loopback interface found")
    config = config_factory(interface=loopback)
    try:
        browser = AirDropBrowser(config)
    except OSError as e:  # multicast on loopback is not available everywhere
        pytest.skip(f"mDNS not available on loopback: {e}")
    browser.start()
    browser.stop()
    browser.stop()  # idempotent

    try:
        server = AirDropServer(config_factory(interface=loopback))
    except OSError as e:
        pytest.skip(f"mDNS not available on loopback: {e}")
    try:
        server.start_service()
    finally:
        server.stop()
