from PIL import Image

from alice_censor.editor.layer_panel import LayerPropertyPanel
from alice_censor.editor.sticker_picker_dialog import StickerPickerDialog
from alice_censor.project import CensorLayer, LayerType


def _overlay_layer(sticker=""):
    return CensorLayer(
        id="l1",
        type=LayerType.OVERLAY,
        rect=(0.0, 0.0, 1.0, 1.0),
        params={"sticker": sticker, "fit": "contain", "rotation": 0, "opacity": 1.0},
    )


def test_choose_button_disabled_without_sticker_dir(qapp):
    panel = LayerPropertyPanel()
    panel.set_layer(_overlay_layer())

    assert not panel._overlay_choose_button.isEnabled()


def test_choose_button_enabled_with_sticker_dir(qapp, tmp_path):
    panel = LayerPropertyPanel(sticker_dir=tmp_path / "stickers")
    panel.set_layer(_overlay_layer())

    assert panel._overlay_choose_button.isEnabled()


def test_set_sticker_dir_updates_after_construction(qapp, tmp_path):
    panel = LayerPropertyPanel()
    panel.set_layer(_overlay_layer())
    assert not panel._overlay_choose_button.isEnabled()

    panel.set_sticker_dir(tmp_path / "stickers")
    panel.set_layer(_overlay_layer())  # rebuild widgets to pick up the new state

    assert panel._overlay_choose_button.isEnabled()


def test_choosing_a_sticker_updates_layer_params_and_emits_edited(qapp, tmp_path, monkeypatch):
    sticker_dir = tmp_path / "stickers"
    sticker_dir.mkdir()
    Image.new("RGBA", (10, 10), (0, 255, 0, 255)).save(sticker_dir / "meme.png", "PNG")

    panel = LayerPropertyPanel(sticker_dir=sticker_dir)
    layer = _overlay_layer()
    panel.set_layer(layer)

    monkeypatch.setattr(StickerPickerDialog, "exec", lambda self: 1)
    monkeypatch.setattr(StickerPickerDialog, "selected_filename", lambda self: "meme.png")
    edited = []
    panel.layer_edited.connect(lambda: edited.append(True))

    panel._overlay_choose_button.click()

    assert layer.params["sticker"] == "meme.png"
    assert edited == [True]


def test_cancelling_picker_leaves_sticker_unchanged(qapp, tmp_path, monkeypatch):
    sticker_dir = tmp_path / "stickers"
    panel = LayerPropertyPanel(sticker_dir=sticker_dir)
    layer = _overlay_layer(sticker="original.png")
    panel.set_layer(layer)

    monkeypatch.setattr(StickerPickerDialog, "exec", lambda self: 0)  # cancelled
    edited = []
    panel.layer_edited.connect(lambda: edited.append(True))

    panel._overlay_choose_button.click()

    assert layer.params["sticker"] == "original.png"
    assert edited == []
