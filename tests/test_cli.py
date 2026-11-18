import json
import os

import pytest

from opendrop import cli

INFOS = [
    {"name": "John’s iPhone", "id": "eccb2f2dcfe7", "address": "fe80::1", "port": 8770},
    {"name": "Jane’s MacBook Pro", "id": "e63138ac6ba8", "address": "fe80::2", "port": 8770},
]


@pytest.mark.parametrize(
    "receiver,expected",
    [
        ("0", 0),
        ("1", 1),
        ("e63138ac6ba8", 1),
        ("John’s iPhone", 0),
        ("jane’s macbook pro", 1),
        ("2", None),
        ("-1", None),
        ("nobody", None),
    ],
)
def test_resolve_receiver(receiver, expected):
    result = cli.resolve_receiver(INFOS, receiver)
    assert result == (None if expected is None else INFOS[expected])


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.build_parser().parse_args(["--version"])
    assert e.value.code == 0
    assert "opendrop" in capsys.readouterr().out


def test_send_requires_receiver_and_file():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["send", "-f", "x"])
    with pytest.raises(SystemExit):
        parser.parse_args(["send", "-r", "0"])


def test_send_multiple_files_parsed():
    args = cli.build_parser().parse_args(["send", "-r", "0", "-f", "a", "b", "-d"])
    assert args.file == ["a", "b"]
    assert args.debug


def test_send_without_discovery_report(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    c = cli.AirDropCli(["send", "-r", "0", "-f", str(f), "--config-dir", str(tmp_path / "home")])
    assert c.exit_code == cli.EXIT_ERROR


def test_send_with_unknown_receiver(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "discover.last.json").write_text(json.dumps(INFOS), encoding="utf-8")
    f = tmp_path / "file.txt"
    f.write_text("x")
    c = cli.AirDropCli(["send", "-r", "nobody", "-f", str(f), "--config-dir", str(home)])
    assert c.exit_code == cli.EXIT_ERROR


def test_send_rejects_missing_file(tmp_path):
    with pytest.raises(SystemExit):
        cli.AirDropCli(["send", "-r", "0", "-f", os.path.join(str(tmp_path), "nope")])


def test_send_rejects_invalid_url():
    with pytest.raises(SystemExit):
        cli.AirDropCli(["send", "-r", "0", "-u", "-f", "not a url"])


def test_doctor_reports_missing_interface(capsys):
    c = cli.AirDropCli(["doctor", "-i", "does-not-exist"])
    assert c.exit_code == 1
    assert "does not exist" in capsys.readouterr().out
