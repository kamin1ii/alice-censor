from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from alice_censor.editor.sticker_picker_dialog import StickerPickerDialog
from alice_censor.stickers import add_sticker, list_stickers


def _make_image(path, color=(255, 0, 0)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (20, 20), color + (255,)).save(path, "PNG")


def test_lists_existing_stickers_on_open(qapp, tmp_path):
    sticker_dir = tmp_path / "stickers"
    _make_image(sticker_dir / "a.png")
    _make_image(sticker_dir / "b.png")

    dialog = StickerPickerDialog(sticker_dir)

    names = {
        dialog.list_widget.item(i).data(Qt.UserRole) for i in range(dialog.list_widget.count())
    }
    assert names == {"a.png", "b.png"}


def test_preselects_current_sticker(qapp, tmp_path):
    sticker_dir = tmp_path / "stickers"
    _make_image(sticker_dir / "a.png")
    _make_image(sticker_dir / "b.png")

    dialog = StickerPickerDialog(sticker_dir, current="b.png")

    assert dialog.selected_filename() == "b.png"


def test_empty_library_shows_no_items(qapp, tmp_path):
    dialog = StickerPickerDialog(tmp_path / "stickers")
    assert dialog.list_widget.count() == 0
    assert dialog.selected_filename() is None


def test_add_sticker_via_dialog_adds_and_selects_it(qapp, tmp_path, monkeypatch):
    sticker_dir = tmp_path / "stickers"
    external = tmp_path / "external" / "meme.png"
    _make_image(external)
    dialog = StickerPickerDialog(sticker_dir)

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(external), ""))
    )
    dialog._on_add()

    assert list_stickers(sticker_dir) == ["meme.png"]
    assert dialog.selected_filename() == "meme.png"


def test_add_sticker_cancelled_file_dialog_does_nothing(qapp, tmp_path, monkeypatch):
    sticker_dir = tmp_path / "stickers"
    dialog = StickerPickerDialog(sticker_dir)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))

    dialog._on_add()

    assert list_stickers(sticker_dir) == []


def test_remove_sticker_via_dialog(qapp, tmp_path, monkeypatch):
    sticker_dir = tmp_path / "stickers"
    _make_image(sticker_dir / "a.png")
    dialog = StickerPickerDialog(sticker_dir, current="a.png")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    dialog._on_remove()

    assert list_stickers(sticker_dir) == []
    assert dialog.selected_filename() is None


def test_remove_sticker_declined_confirmation_keeps_it(qapp, tmp_path, monkeypatch):
    sticker_dir = tmp_path / "stickers"
    _make_image(sticker_dir / "a.png")
    dialog = StickerPickerDialog(sticker_dir, current="a.png")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))

    dialog._on_remove()

    assert list_stickers(sticker_dir) == ["a.png"]


def test_double_click_accepts_dialog(qapp, tmp_path):
    sticker_dir = tmp_path / "stickers"
    _make_image(sticker_dir / "a.png")
    dialog = StickerPickerDialog(sticker_dir)

    item = dialog.list_widget.item(0)
    dialog.list_widget.itemDoubleClicked.emit(item)

    assert dialog.result() == dialog.DialogCode.Accepted
