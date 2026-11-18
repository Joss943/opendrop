import os
import socket
import ssl

from cryptography import x509

import opendrop.config as cfg
from opendrop.config import AirDropConfig


def test_creates_self_signed_certificate_without_openssl(config_factory, monkeypatch):
    monkeypatch.setenv("PATH", "")  # make sure no openssl binary is needed
    config = config_factory(computer_name="Test Machine")
    with open(config.cert_file, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    assert "Test Machine" in cert.subject.rfc4514_string()
    assert isinstance(config.get_server_ssl_context(), ssl.SSLContext)
    assert isinstance(config.get_client_ssl_context(), ssl.SSLContext)


def test_existing_certificate_is_reused(config_factory):
    first = config_factory()
    with open(first.cert_file, "rb") as f:
        pem = f.read()
    second = config_factory()
    with open(second.cert_file, "rb") as f:
        assert f.read() == pem


def test_expired_self_signed_certificate_is_renewed(config_factory, monkeypatch):
    monkeypatch.setattr(cfg, "CERT_VALIDITY_DAYS", -1)
    config = config_factory()
    assert config._self_signed_cert_expired()
    monkeypatch.setattr(cfg, "CERT_VALIDITY_DAYS", 30)
    config = config_factory()
    assert not config._self_signed_cert_expired()


def test_host_name_strips_local_suffix(config_factory, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "Johns-MacBook.local")
    config = config_factory()
    assert config.host_name == "Johns-MacBook"
    assert config.computer_name == "Johns-MacBook"


def test_service_id_format(config_factory):
    config = config_factory()
    assert len(config.service_id) == 12
    int(config.service_id, 16)


def test_default_receive_dir_is_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = AirDropConfig(airdrop_dir=str(tmp_path / "h"))
    assert config.receive_dir == os.path.abspath(str(tmp_path))
