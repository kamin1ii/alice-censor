from PIL import Image

from alice_censor.stickers import add_sticker, list_stickers, make_sticker_resolver, remove_sticker


def _make_image(path, color=(255, 0, 0)):
    Image.new("RGBA", (20, 20), color + (255,)).save(path, "PNG")


def test_list_stickers_empty_when_folder_missing(tmp_path):
    assert list_stickers(tmp_path / "does_not_exist") == []


def test_add_sticker_copies_into_library_and_creates_folder(tmp_path):
    source = tmp_path / "external" / "meme.png"
    source.parent.mkdir()
    _make_image(source)
    sticker_dir = tmp_path / "stickers"

    name = add_sticker(sticker_dir, source)

    assert name == "meme.png"
    assert (sticker_dir / "meme.png").exists()
    assert list_stickers(sticker_dir) == ["meme.png"]


def test_add_sticker_does_not_move_or_delete_the_original(tmp_path):
    source = tmp_path / "meme.png"
    _make_image(source)
    add_sticker(tmp_path / "stickers", source)
    assert source.exists()  # copy, not move


def test_add_sticker_avoids_name_collision(tmp_path):
    sticker_dir = tmp_path / "stickers"
    first = tmp_path / "a" / "meme.png"
    second = tmp_path / "b" / "meme.png"
    first.parent.mkdir()
    second.parent.mkdir()
    _make_image(first, color=(255, 0, 0))
    _make_image(second, color=(0, 255, 0))

    name1 = add_sticker(sticker_dir, first)
    name2 = add_sticker(sticker_dir, second)

    assert name1 == "meme.png"
    assert name2 == "meme_1.png"
    with Image.open(sticker_dir / name1) as im:
        assert im.getpixel((10, 10))[:3] == (255, 0, 0)
    with Image.open(sticker_dir / name2) as im:
        assert im.getpixel((10, 10))[:3] == (0, 255, 0)


def test_remove_sticker_deletes_file(tmp_path):
    sticker_dir = tmp_path / "stickers"
    source = tmp_path / "meme.png"
    _make_image(source)
    add_sticker(sticker_dir, source)

    remove_sticker(sticker_dir, "meme.png")

    assert list_stickers(sticker_dir) == []


def test_remove_sticker_missing_file_is_noop(tmp_path):
    remove_sticker(tmp_path / "stickers", "nope.png")  # must not raise


def test_list_stickers_ignores_non_image_files(tmp_path):
    sticker_dir = tmp_path / "stickers"
    sticker_dir.mkdir()
    _make_image(sticker_dir / "a.png")
    (sticker_dir / "readme.txt").write_text("not an image")

    assert list_stickers(sticker_dir) == ["a.png"]


def test_sticker_resolver_treats_absolute_path_as_is(tmp_path):
    resolver = make_sticker_resolver(tmp_path / "stickers")
    absolute = tmp_path / "elsewhere" / "old_style.png"
    assert resolver(str(absolute)) == absolute


def test_sticker_resolver_resolves_relative_name_against_sticker_dir(tmp_path):
    sticker_dir = tmp_path / "stickers"
    resolver = make_sticker_resolver(sticker_dir)
    assert resolver("meme.png") == sticker_dir / "meme.png"


def test_sticker_resolver_with_no_sticker_dir_returns_ref_as_is(tmp_path):
    from pathlib import Path

    resolver = make_sticker_resolver(None)
    assert resolver("meme.png") == Path("meme.png")
