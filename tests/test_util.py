import io

import pytest

from opendrop import util


@pytest.mark.parametrize(
    "name,content,expected",
    [
        ("a.JPG", b"", "public.jpeg"),
        ("a.png", b"", "public.png"),
        ("a.pdf", b"", "com.adobe.pdf"),
        ("noext", b"\x89PNG\r\n\x1a\n....", "public.png"),
        ("noext2", b"random", "public.content"),
        ("song.mid", b"", "public.audio"),
    ],
)
def test_uti_type(tmp_path, name, content, expected):
    path = tmp_path / name
    path.write_bytes(content)
    assert util.get_uti_type(str(path)) == expected


def test_uti_type_folder(tmp_path):
    assert util.get_uti_type(str(tmp_path)) == "public.folder"


def test_generate_file_icon(tmp_path):
    from PIL import Image

    path = tmp_path / "img.png"
    Image.new("RGBA", (1200, 800), (255, 0, 0, 128)).save(path)
    icon = util.generate_file_icon(str(path))
    assert icon
    with Image.open(io.BytesIO(icon)) as im:
        assert max(im.size) <= 540


def test_generate_file_icon_on_non_image_returns_none(tmp_path):
    path = tmp_path / "x.png"
    path.write_bytes(b"not an image")
    assert util.generate_file_icon(str(path)) is None


def test_list_and_find_interfaces():
    interfaces = util.list_interfaces()
    assert interfaces
    first = interfaces[0]
    assert util.find_interface(first.name) == first
    assert util.find_interface(first.nice_name).name == first.name
    assert util.find_interface("definitely-not-an-interface") is None


def test_require_interface_error_message():
    with pytest.raises(util.InterfaceError) as e:
        util.require_interface("awdl-does-not-exist")
    assert "does not exist" in str(e.value)
