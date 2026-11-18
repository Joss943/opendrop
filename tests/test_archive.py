import gzip
import io
import os

import pytest

from opendrop import archive


def _odc_entry(name, data=b"", mode=0o100644):
    name_b = name.encode() + b"\0"
    header = (
        b"070707"
        + b"%06o" % 0  # dev
        + b"%06o" % 1  # ino
        + b"%06o" % mode
        + b"%06o" % 0  # uid
        + b"%06o" % 0  # gid
        + b"%06o" % 1  # nlink
        + b"%06o" % 0  # rdev
        + b"%011o" % 0  # mtime
        + b"%06o" % len(name_b)
        + b"%011o" % len(data)
    )
    return header + name_b + data


def _newc_entry(name, data=b"", mode=0o100644):
    name_b = name.encode() + b"\0"
    fields = [1, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name_b), 0]
    out = b"070701" + b"".join(b"%08X" % f for f in fields) + name_b
    out += b"\0" * ((-len(out)) % 4)
    out += data + b"\0" * ((-len(data)) % 4)
    return out


def _odc_archive(*entries):
    return gzip.compress(b"".join(entries) + _odc_entry("TRAILER!!!", mode=0))


def test_roundtrip_files_and_directories(tmp_path):
    src = tmp_path / "src"
    (src / "folder" / "sub").mkdir(parents=True)
    (src / "folder" / "a.txt").write_bytes(b"hello")
    big = os.urandom(300_000)
    (src / "folder" / "sub" / "b.bin").write_bytes(big)
    (src / "photo é.jpg").write_bytes(b"\xff\xd8\xff" + b"x" * 10)

    buf = io.BytesIO()
    archive.write_archive(buf, [str(src / "folder"), str(src / "photo é.jpg")])
    buf.seek(0)

    out = tmp_path / "out"
    created = archive.extract_archive(buf, str(out))
    assert sorted(os.path.basename(p) for p in created) == ["folder", "photo é.jpg"]
    assert (out / "folder" / "a.txt").read_bytes() == b"hello"
    assert (out / "folder" / "sub" / "b.bin").read_bytes() == big
    assert (out / "photo é.jpg").read_bytes().startswith(b"\xff\xd8\xff")


def test_written_archive_is_gzip_odc(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    buf = io.BytesIO()
    archive.write_archive(buf, [str(f)])
    raw = gzip.decompress(buf.getvalue())
    assert raw.startswith(b"070707")
    assert b"./x.txt\0" in raw
    assert b"TRAILER!!!\0" in raw


@pytest.mark.parametrize(
    "evil",
    ["../evil.txt", "/etc/evil.txt", "./a/../../evil.txt", "C:/evil.txt", "..\\evil.txt"],
)
def test_path_traversal_is_rejected(tmp_path, evil):
    data = _odc_archive(_odc_entry(evil, b"pwned"), _odc_entry("./ok.txt", b"ok"))
    out = tmp_path / "out"
    archive.extract_archive(io.BytesIO(data), str(out))
    assert (out / "ok.txt").read_bytes() == b"ok"
    assert not (tmp_path / "evil.txt").exists()
    assert sorted(os.listdir(out)) == ["ok.txt"]


def test_symlinks_are_skipped(tmp_path):
    data = _odc_archive(_odc_entry("./link", b"/etc/passwd", mode=0o120777))
    out = tmp_path / "out"
    assert archive.extract_archive(io.BytesIO(data), str(out)) == []
    assert os.listdir(out) == []


def test_existing_files_are_not_overwritten(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "doc.txt").write_bytes(b"original")
    data = _odc_archive(_odc_entry("./doc.txt", b"new"))
    archive.extract_archive(io.BytesIO(data), str(out))
    assert (out / "doc.txt").read_bytes() == b"original"
    assert (out / "doc (1).txt").read_bytes() == b"new"


def test_windows_unfriendly_names_are_sanitized(tmp_path):
    data = _odc_archive(_odc_entry("./what?: a*b.txt", b"1"), _odc_entry("./CON.txt", b"2"))
    out = tmp_path / "out"
    archive.extract_archive(io.BytesIO(data), str(out))
    assert sorted(os.listdir(out)) == ["_CON.txt", "what__ a_b.txt"]


def test_reads_newc_uncompressed(tmp_path):
    data = (
        _newc_entry("./dir", mode=0o40755)
        + _newc_entry("./dir/f.txt", b"abcde")
        + _newc_entry("TRAILER!!!", mode=0)
    )
    out = tmp_path / "out"
    archive.extract_archive(io.BytesIO(data), str(out))
    assert (out / "dir" / "f.txt").read_bytes() == b"abcde"


def test_truncated_archive_raises(tmp_path):
    data = gzip.compress(_odc_entry("./f.txt", b"abcdef")[:-3])
    with pytest.raises((archive.ArchiveError, EOFError)):
        archive.extract_archive(io.BytesIO(data), str(tmp_path / "out"))


@pytest.mark.parametrize(
    "name,expected",
    [
        ("./a/b.txt", ["a", "b.txt"]),
        ("a//b", ["a", "b"]),
        ("..", None),
        ("", None),
        ("./", None),
        ("nul", ["_nul"]),
        ("trailing. ", ["trailing"]),
    ],
)
def test_sanitize_member_path(name, expected):
    assert archive.sanitize_member_path(name) == expected
