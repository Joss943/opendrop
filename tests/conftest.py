import pytest

from opendrop.config import AirDropConfig


@pytest.fixture
def config_factory(tmp_path):
    def make(**kwargs):
        kwargs.setdefault("airdrop_dir", str(tmp_path / "home"))
        kwargs.setdefault("receive_dir", str(tmp_path / "received"))
        kwargs.setdefault("server_port", 0)
        return AirDropConfig(**kwargs)

    return make


def loopback_name():
    import ifaddr

    for adapter in ifaddr.get_adapters():
        name = str(adapter.name)
        if name.startswith("lo") or "loopback" in str(adapter.nice_name).lower():
            return adapter.nice_name if name.startswith("{") else name
    return None
