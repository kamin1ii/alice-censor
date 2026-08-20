import pytest
from PIL import Image
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import QApplication, QFormLayout

from alice_censor.editor.editor_dialog import RegionEditorDialog
from alice_censor.project import ImageRecord, LayerType


def _make_dialog(qapp, tmp_path, size=(200, 100), initial_layers=None):
    image_path = tmp_path / "image.png"
    Image.new("RGB", size, (255, 255, 255)).save(image_path, "PNG")
    record = ImageRecord(layers=list(initial_layers) if initial_layers else [])
    dialog = RegionEditorDialog(image_path, record)
    dialog.resize(900, 600)
    dialog.show()
    QApplication.processEvents()
    return dialog, record


def test_drawing_a_region_creates_a_solid_layer_by_default(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)

    dialog._on_region_created(QRectF(10, 10, 50, 30))

    assert len(dialog._layers) == 1
    layer = dialog._layers[0]
    assert layer.type == LayerType.SOLID
    assert layer.rect == pytest.approx((10 / 200, 10 / 100, 50 / 200, 30 / 100))
    assert dialog.layer_list.count() == 1
    assert dialog.canvas.region_rect(layer.id) == QRectF(10, 10, 50, 30)
    assert record.layers == []  # nothing committed to the caller's record yet


def test_moving_a_region_updates_the_layer_rect(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    dialog._on_region_created(QRectF(10, 10, 50, 30))
    layer = dialog._layers[0]

    # simulate a completed move: the canvas item's rect changed, and the
    # canvas notified us via region_changed (as RegionItem.notify_changed does)
    item = dialog.canvas._region_items[layer.id]
    item.setPos(QPointF(20, 5))
    dialog._on_region_changed(layer.id)

    expected_scene_rect = item.mapRectToScene(item.rect())
    assert layer.rect[0] == pytest.approx(expected_scene_rect.left() / 200)
    assert layer.rect[0] == pytest.approx(30 / 200)  # 10 (original) + 20 (moved) == 30


def test_selecting_layer_populates_property_panel(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    dialog._on_region_created(QRectF(10, 10, 50, 30))
    layer = dialog._layers[0]

    dialog.layer_list.setCurrentRow(0)

    assert dialog.property_panel._layer is layer
    assert dialog.property_panel.isEnabled()


def test_changing_layer_type_resets_params_and_updates_list_label(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    dialog._on_region_created(QRectF(10, 10, 50, 30))
    layer = dialog._layers[0]
    dialog.layer_list.setCurrentRow(0)

    blur_index = dialog.property_panel.type_combo.findData(LayerType.BLUR)
    dialog.property_panel.type_combo.setCurrentIndex(blur_index)

    assert layer.type == LayerType.BLUR
    assert "radius" in layer.params
    assert dialog.layer_list.item(0).text() == "Blur"


def test_editing_param_via_panel_updates_layer_params(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    dialog._on_region_created(QRectF(10, 10, 50, 30))
    layer = dialog._layers[0]
    dialog.layer_list.setCurrentRow(0)

    # solid layer: find the opacity spinbox (last row widget) and change it
    opacity_row = dialog.property_panel.params_layout.rowCount() - 1
    field = dialog.property_panel.params_layout.itemAt(
        opacity_row, QFormLayout.ItemRole.FieldRole
    ).widget()
    field.setValue(0.3)

    assert layer.params["opacity"] == 0.3


def test_delete_layer_removes_from_draft_and_canvas(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    dialog._on_region_created(QRectF(10, 10, 50, 30))
    layer = dialog._layers[0]
    dialog.layer_list.setCurrentRow(0)

    dialog.property_panel.delete_requested.emit()

    assert dialog._layers == []
    assert dialog.layer_list.count() == 0
    assert dialog.canvas.region_rect(layer.id) is None


def test_disabling_layer_via_checkbox_marks_disabled_in_draft(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    dialog._on_region_created(QRectF(10, 10, 50, 30))
    layer = dialog._layers[0]
    dialog.layer_list.setCurrentRow(0)

    dialog.property_panel.enabled_checkbox.setChecked(False)

    assert layer.enabled is False
    assert dialog.layer_list.item(0).text() == "Solid Color (disabled)"


def test_move_up_reorders_layers(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    dialog._on_region_created(QRectF(0, 0, 10, 10))
    dialog._on_region_created(QRectF(20, 20, 10, 10))
    first_id, second_id = dialog._layers[0].id, dialog._layers[1].id

    dialog.layer_list.setCurrentRow(1)
    dialog._move_selected(-1)

    assert [layer.id for layer in dialog._layers] == [second_id, first_id]
    assert dialog.layer_list.currentRow() == 0


def test_preview_updates_after_edits(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    before = dialog.canvas._pixmap_item.pixmap().toImage()

    dialog._on_region_created(QRectF(10, 10, 50, 30))

    after = dialog.canvas._pixmap_item.pixmap().toImage()
    assert before != after


def test_save_commits_draft_to_callers_record(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    dialog._on_region_created(QRectF(10, 10, 50, 30))
    layer_id = dialog._layers[0].id

    dialog._on_save()

    assert dialog.result() == dialog.DialogCode.Accepted
    assert [layer.id for layer in record.layers] == [layer_id]


def test_cancel_discards_draft_changes(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    dialog._on_region_created(QRectF(10, 10, 50, 30))
    assert len(dialog._layers) == 1  # editing did happen in the draft

    dialog.reject()

    assert record.layers == []  # but never touched the caller's record


def test_opening_with_existing_layers_preserves_them_on_cancel(qapp, tmp_path):
    from alice_censor.project import CensorLayer

    existing = CensorLayer(id="pre-existing", type=LayerType.BLUR, rect=(0.1, 0.1, 0.2, 0.2))
    dialog, record = _make_dialog(qapp, tmp_path, initial_layers=[existing])
    assert dialog.layer_list.count() == 1

    # delete it in the draft, then cancel -- the original record must survive untouched
    dialog.layer_list.setCurrentRow(0)
    dialog.property_panel.delete_requested.emit()
    assert dialog._layers == []

    dialog.reject()

    assert len(record.layers) == 1
    assert record.layers[0].id == "pre-existing"


def test_new_regions_default_to_solid_for_a_fresh_image(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)

    dialog._on_region_created(QRectF(10, 10, 50, 30))

    assert dialog._layers[0].type == LayerType.SOLID


def test_new_regions_reuse_the_last_type_you_switched_to(qapp, tmp_path):
    # Otherwise every region needs an extra manual step to switch away from
    # solid, even when drawing a run of e.g. several blur regions in a row.
    dialog, record = _make_dialog(qapp, tmp_path)
    dialog._on_region_created(QRectF(10, 10, 50, 30))
    dialog.layer_list.setCurrentRow(0)
    blur_index = dialog.property_panel.type_combo.findData(LayerType.BLUR)
    dialog.property_panel.type_combo.setCurrentIndex(blur_index)

    dialog._on_region_created(QRectF(60, 10, 50, 30))

    assert dialog._layers[1].type == LayerType.BLUR
    assert dialog._layers[1].params == {"radius": 12}


def test_new_regions_default_to_last_type_of_image_opened_with_existing_layers(qapp, tmp_path):
    from alice_censor.project import CensorLayer

    existing = CensorLayer(id="pre-existing", type=LayerType.PIXELATE, rect=(0.1, 0.1, 0.2, 0.2))
    dialog, record = _make_dialog(qapp, tmp_path, initial_layers=[existing])

    dialog._on_region_created(QRectF(60, 10, 50, 30))

    assert dialog._layers[1].type == LayerType.PIXELATE


# -- pre-configuring the "next region" template before drawing anything ----


def test_template_shown_before_any_region_exists(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)

    panel = dialog.property_panel
    assert panel._layer is dialog._pending_template
    assert panel.isEnabled()
    assert panel.mode_label.text() == "New region defaults"
    assert not panel.delete_button.isVisible()
    assert not panel.enabled_checkbox.isVisible()


def test_configuring_template_before_drawing_applies_to_new_region(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    panel = dialog.property_panel

    overlay_index = panel.type_combo.findData(LayerType.OVERLAY)
    panel.type_combo.setCurrentIndex(overlay_index)
    # simulate having picked a sticker file via the (untestable-headlessly)
    # QFileDialog browse button -- same effect, setting the param directly
    dialog._pending_template.params["sticker"] = "C:/stickers/meme.png"

    dialog._on_region_created(QRectF(10, 10, 50, 30))

    layer = dialog._layers[0]
    assert layer.type == LayerType.OVERLAY
    assert layer.params["sticker"] == "C:/stickers/meme.png"


def test_multiple_regions_stamped_from_same_template_without_reselecting(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    panel = dialog.property_panel
    overlay_index = panel.type_combo.findData(LayerType.OVERLAY)
    panel.type_combo.setCurrentIndex(overlay_index)
    dialog._pending_template.params["sticker"] = "C:/stickers/meme.png"

    dialog._on_region_created(QRectF(10, 10, 30, 30))
    dialog._on_region_created(QRectF(60, 10, 30, 30))
    dialog._on_region_created(QRectF(110, 10, 30, 30))

    assert len(dialog._layers) == 3
    assert all(layer.type == LayerType.OVERLAY for layer in dialog._layers)
    assert all(layer.params["sticker"] == "C:/stickers/meme.png" for layer in dialog._layers)

    # independent params dicts: editing one region must not affect the others
    dialog._layers[0].params["sticker"] = "C:/stickers/different.png"
    assert dialog._layers[1].params["sticker"] == "C:/stickers/meme.png"
    assert dialog._layers[2].params["sticker"] == "C:/stickers/meme.png"


def test_selecting_a_real_layer_and_editing_it_updates_the_template_too(qapp, tmp_path):
    # Editing an existing layer (not just the template with nothing
    # selected) should also carry forward to the NEXT new region --
    # otherwise switching a region to overlay+sticker doesn't help the
    # next region you draw unless you remembered to also set up the
    # template separately.
    dialog, record = _make_dialog(qapp, tmp_path)
    dialog._on_region_created(QRectF(10, 10, 30, 30))
    dialog.layer_list.setCurrentRow(0)
    panel = dialog.property_panel
    overlay_index = panel.type_combo.findData(LayerType.OVERLAY)
    panel.type_combo.setCurrentIndex(overlay_index)
    dialog._layers[0].params["sticker"] = "C:/stickers/meme.png"
    dialog._on_layer_edited()  # normally fired by the panel's param widget

    dialog._on_region_created(QRectF(60, 10, 30, 30))

    assert dialog._layers[1].type == LayerType.OVERLAY
    assert dialog._layers[1].params["sticker"] == "C:/stickers/meme.png"


def test_deleting_only_layer_shows_template_again(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    dialog._on_region_created(QRectF(10, 10, 30, 30))
    dialog.layer_list.setCurrentRow(0)

    dialog.property_panel.delete_requested.emit()

    assert dialog.property_panel._layer is dialog._pending_template
    assert dialog.property_panel.mode_label.text() == "New region defaults"


def test_clicking_a_real_layer_switches_out_of_template_mode(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    dialog._on_region_created(QRectF(10, 10, 30, 30))

    dialog.layer_list.setCurrentRow(0)

    panel = dialog.property_panel
    assert panel._layer is dialog._layers[0]
    assert panel.mode_label.text() == "Selected region"
    assert panel.delete_button.isVisible()
    assert panel.enabled_checkbox.isVisible()


# -- graceful handling of an unrenderable layer (e.g. missing sticker file) --


def test_missing_sticker_shows_error_label_instead_of_crashing(qapp, tmp_path):
    dialog, record = _make_dialog(qapp, tmp_path)
    panel = dialog.property_panel
    overlay_index = panel.type_combo.findData(LayerType.OVERLAY)
    panel.type_combo.setCurrentIndex(overlay_index)
    dialog._pending_template.params["sticker"] = str(tmp_path / "does_not_exist.png")

    dialog._on_region_created(QRectF(10, 10, 30, 30))  # must not raise

    assert dialog.render_error_label.isVisible()
    assert "does_not_exist.png" in dialog.render_error_label.text()


def test_error_label_clears_once_the_layer_is_fixed(qapp, tmp_path):
    from PIL import Image as PILImage

    sticker_path = tmp_path / "sticker.png"
    PILImage.new("RGBA", (10, 10), (0, 255, 0, 255)).save(sticker_path, "PNG")

    dialog, record = _make_dialog(qapp, tmp_path)
    panel = dialog.property_panel
    overlay_index = panel.type_combo.findData(LayerType.OVERLAY)
    panel.type_combo.setCurrentIndex(overlay_index)
    dialog._pending_template.params["sticker"] = str(tmp_path / "does_not_exist.png")
    dialog._on_region_created(QRectF(10, 10, 30, 30))
    assert dialog.render_error_label.isVisible()

    dialog._layers[0].params["sticker"] = str(sticker_path)
    dialog._render_preview()

    assert not dialog.render_error_label.isVisible()


def test_overlay_with_real_sticker_renders_successfully(qapp, tmp_path):
    from PIL import Image as PILImage

    sticker_path = tmp_path / "sticker.png"
    PILImage.new("RGBA", (10, 10), (0, 255, 0, 255)).save(sticker_path, "PNG")

    dialog, record = _make_dialog(qapp, tmp_path)
    panel = dialog.property_panel
    overlay_index = panel.type_combo.findData(LayerType.OVERLAY)
    panel.type_combo.setCurrentIndex(overlay_index)
    dialog._pending_template.params["sticker"] = str(sticker_path)
    dialog._pending_template.params["fit"] = "stretch"

    dialog._on_region_created(QRectF(10, 10, 30, 30))

    assert not dialog.render_error_label.isVisible()
    assert dialog._layers[0].params["sticker"] == str(sticker_path)
