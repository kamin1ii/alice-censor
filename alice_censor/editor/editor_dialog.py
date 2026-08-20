"""Top-level region-based censor editor dialog.

Wires the canvas (draw/move/resize regions), the layer list, and the
property panel together. Editing happens on a *draft copy* of
image_record.layers (self._layers). The dialog has real Save and Cancel
buttons, so edits must not land in the caller's ImageRecord until Save is
actually clicked, and only accept() commits the draft back. Every edit
still re-renders the live preview via rendering.render_layers against the
draft, so what's shown is always exactly what Save would persist.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..gui.qt_image import pil_to_qpixmap
from ..project import CensorLayer, ImageRecord, LayerType, ProjectState
from ..rendering import RenderError, render_layers
from .batch_apply_dialog import BatchApplyDialog
from .canvas import RegionCanvas
from .geometry import fraction_to_image_rect, image_rect_to_fraction
from .layer_panel import DEFAULT_PARAMS, LAYER_TYPE_LABELS, LayerPropertyPanel


class RegionEditorDialog(QDialog):
    # Emitted once the batch-applied layers are in the target records, so
    # the owner can persist them. Batch apply commits as soon as it is
    # confirmed, unlike this image's own edits which wait for Save, but
    # the dialog does not write the project file to do it. Writing lives
    # with whoever owns the project, which is the only place that knows
    # how to report a failed write to the user.
    batch_applied = Signal()

    def __init__(
        self,
        image_path: Path,
        image_record: ImageRecord,
        sticker_resolver=None,
        parent: QWidget | None = None,
        *,
        project: ProjectState | None = None,
        current_path: str | None = None,
        group_members: list[str] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Edit: {image_path.name}")
        self.resize(1150, 780)

        self.image_record = image_record
        self._layers: list[CensorLayer] = copy.deepcopy(image_record.layers)
        self.sticker_resolver = sticker_resolver
        # For "Apply to Scene Group". project and current_path let us look
        # up and write other images' ImageRecords, and group_members is this
        # image's scene-group siblings, excluding itself. Any of these being
        # None just disables that button, so tests that only care about
        # single-image editing don't need to supply them.
        self.project = project
        self.current_path = current_path
        self.group_members = group_members or []
        self.batch_applied_paths: set[str] = set()
        # Type and params for the *next* region(s) you draw. Lets you pick
        # a sticker, color or blur radius once, with nothing selected, and
        # then stamp out several regions with it in a row, instead of
        # re-picking (re-browsing for a sticker file, say) every time.
        # Seeded from the last existing layer if this image already had
        # any when opened, so it also carries over across edit sessions.
        seed = self._layers[-1] if self._layers else None
        seed_type = seed.type if seed is not None else LayerType.SOLID
        self._pending_template = CensorLayer(
            id="",
            type=seed_type,
            rect=(0.0, 0.0, 0.0, 0.0),
            params=dict(seed.params) if seed is not None else dict(DEFAULT_PARAMS[seed_type]),
        )

        with Image.open(image_path) as opened:
            self._base_image = opened.convert("RGBA")
            self._base_image.load()

        self.canvas = RegionCanvas()
        self.canvas.set_image(pil_to_qpixmap(self._base_image))
        self.layer_list = QListWidget()
        self.property_panel = LayerPropertyPanel(
            sticker_dir=project.sticker_dir if project else None
        )
        self.move_up_button = QPushButton("Move Up")
        self.move_down_button = QPushButton("Move Down")
        self.render_error_label = QLabel()
        self.render_error_label.setStyleSheet("color: #e53935;")
        self.render_error_label.setWordWrap(True)
        self.render_error_label.hide()
        self.batch_apply_button = QPushButton("Apply to Scene Group…")
        self.batch_apply_button.setEnabled(bool(self.group_members) and self.project is not None)
        if not self.group_members:
            self.batch_apply_button.setToolTip("No other images share this image's scene group.")

        right = QVBoxLayout()
        right.addWidget(QLabel("Layers (drag on the image to add a region):"))
        right.addWidget(self.layer_list)
        move_row = QHBoxLayout()
        move_row.addWidget(self.move_up_button)
        move_row.addWidget(self.move_down_button)
        right.addLayout(move_row)
        right.addWidget(self.property_panel, 1)
        right.addWidget(self.render_error_label)
        right.addWidget(self.batch_apply_button)

        right_widget = QWidget()
        right_widget.setLayout(right)
        right_widget.setMaximumWidth(340)

        main_row = QHBoxLayout()
        main_row.addWidget(self.canvas, 3)
        main_row.addWidget(right_widget, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        outer = QVBoxLayout(self)
        outer.addLayout(main_row, 1)
        outer.addWidget(buttons)

        self.canvas.region_created.connect(self._on_region_created)
        self.canvas.region_changed.connect(self._on_region_changed)
        self.canvas.selection_changed.connect(self._on_canvas_selection_changed)
        self.layer_list.currentRowChanged.connect(self._on_list_row_changed)
        self.property_panel.layer_edited.connect(self._on_layer_edited)
        self.property_panel.delete_requested.connect(self._on_delete_requested)
        self.move_up_button.clicked.connect(lambda: self._move_selected(-1))
        self.move_down_button.clicked.connect(lambda: self._move_selected(1))
        self.batch_apply_button.clicked.connect(self._on_batch_apply)

        self._load_regions()
        self.property_panel.set_layer(self._pending_template, is_template=True)
        self._render_preview()
        QTimer.singleShot(0, self.canvas.fit_to_view)

    # ===== setup

    def _load_regions(self) -> None:
        image_size = self._base_image.size
        for layer in self._layers:
            rect = fraction_to_image_rect(layer.rect, image_size)
            self.canvas.add_region(layer.id, rect)
            self._add_list_entry(layer)

    def _add_list_entry(self, layer: CensorLayer) -> None:
        item = QListWidgetItem(self._layer_label(layer))
        item.setData(Qt.UserRole, layer.id)
        self.layer_list.addItem(item)

    @staticmethod
    def _layer_label(layer: CensorLayer) -> str:
        type_name = LAYER_TYPE_LABELS.get(layer.type, layer.type.value)
        suffix = "" if layer.enabled else " (disabled)"
        return f"{type_name}{suffix}"

    def _find_layer(self, layer_id: str | None) -> CensorLayer | None:
        if layer_id is None:
            return None
        for layer in self._layers:
            if layer.id == layer_id:
                return layer
        return None

    def _find_list_item(self, layer_id: str) -> QListWidgetItem | None:
        for i in range(self.layer_list.count()):
            item = self.layer_list.item(i)
            if item.data(Qt.UserRole) == layer_id:
                return item
        return None

    # ===== canvas events

    def _on_region_created(self, rect: QRectF) -> None:
        layer = CensorLayer(
            id=uuid.uuid4().hex,
            type=self._pending_template.type,
            rect=image_rect_to_fraction(rect, self._base_image.size),
            # Independent copy, since regions must not share one params dict,
            # or editing one would silently change every other region
            # that was stamped out from the same template.
            params=copy.deepcopy(self._pending_template.params),
        )
        self._layers.append(layer)
        self.canvas.add_region(layer.id, rect)
        self._add_list_entry(layer)
        self.layer_list.setCurrentRow(self.layer_list.count() - 1)
        self._render_preview()

    def _on_region_changed(self, layer_id: str) -> None:
        layer = self._find_layer(layer_id)
        rect = self.canvas.region_rect(layer_id)
        if layer is not None and rect is not None:
            layer.rect = image_rect_to_fraction(rect, self._base_image.size)
        self._render_preview()

    def _on_canvas_selection_changed(self, layer_id) -> None:
        item = self._find_list_item(layer_id) if layer_id else None
        self.layer_list.blockSignals(True)
        self.layer_list.setCurrentItem(item)
        self.layer_list.blockSignals(False)
        self._show_layer_or_template(layer_id)

    # ===== list / panel events

    def _on_list_row_changed(self, row: int) -> None:
        item = self.layer_list.item(row) if row >= 0 else None
        layer_id = item.data(Qt.UserRole) if item else None
        self.canvas.select_region(layer_id)
        self._show_layer_or_template(layer_id)

    def _show_layer_or_template(self, layer_id: str | None) -> None:
        layer = self._find_layer(layer_id)
        if layer is not None:
            self.property_panel.set_layer(layer, is_template=False)
        else:
            self.property_panel.set_layer(self._pending_template, is_template=True)

    def _on_layer_edited(self) -> None:
        row = self.layer_list.currentRow()
        if row >= 0:
            item = self.layer_list.item(row)
            layer = self._find_layer(item.data(Qt.UserRole))
            if layer is not None:
                item.setText(self._layer_label(layer))
                # Keep the template in sync with whatever you just edited,
                # so the next NEW region you draw matches it too, not just
                # edits made with nothing selected, which already mutate
                # self._pending_template directly, being the same object
                # the panel was editing.
                self._pending_template.type = layer.type
                self._pending_template.params = dict(layer.params)
        self._render_preview()

    def _on_delete_requested(self) -> None:
        row = self.layer_list.currentRow()
        if row < 0:
            return
        item = self.layer_list.item(row)
        layer_id = item.data(Qt.UserRole)
        self._layers = [layer for layer in self._layers if layer.id != layer_id]
        self.canvas.remove_region(layer_id)
        self.layer_list.takeItem(row)
        self.canvas.clear_selection()
        self.property_panel.set_layer(self._pending_template, is_template=True)
        self._render_preview()

    def _move_selected(self, delta: int) -> None:
        row = self.layer_list.currentRow()
        new_row = row + delta
        if row < 0 or not (0 <= new_row < self.layer_list.count()):
            return
        layers = self._layers
        layers[row], layers[new_row] = layers[new_row], layers[row]
        item = self.layer_list.takeItem(row)
        self.layer_list.insertItem(new_row, item)
        self.layer_list.setCurrentRow(new_row)
        self._render_preview()

    # ===== preview

    def _render_preview(self) -> None:
        # A layer can be individually unrenderable, most commonly an
        # overlay pointing at a sticker file that's missing, moved or
        # unreadable, without that being a bug in the tool. Show it as a
        # clear in-UI message rather than letting the exception propagate
        # out of this Qt slot, where it'd just print a console traceback
        # and leave the preview silently stuck on the last good render.
        try:
            rendered = render_layers(
                self._base_image, self._layers, sticker_resolver=self.sticker_resolver
            )
        except RenderError as e:
            self.render_error_label.setText(f"Couldn't render preview: {e}")
            self.render_error_label.show()
            return
        self.render_error_label.hide()
        self.canvas.update_image(pil_to_qpixmap(rendered))

    # ===== batch apply

    def _on_batch_apply(self) -> None:
        if not self._layers:
            QMessageBox.information(
                self, "Nothing to apply", "Draw at least one region on this image first."
            )
            return
        if not self.group_members or self.project is None or self.current_path is None:
            return

        current_record = self.project.images.get(self.current_path)
        group_key = (current_record.effective_group if current_record else None) or "?"
        dialog = BatchApplyDialog(group_key, self.group_members, len(self._layers), self)
        if not dialog.exec():
            return
        targets = dialog.selected_paths()
        if not targets:
            return

        for target_path in targets:
            record = self.project.images.setdefault(target_path, ImageRecord())
            for layer in self._layers:
                # New id and independent params and rect copies per target.
                # A batch-applied layer must be freely nudgeable on
                # each image afterward without affecting the others or the
                # source, exactly like any other layer.
                record.layers.append(
                    CensorLayer(
                        id=uuid.uuid4().hex,
                        type=layer.type,
                        rect=tuple(layer.rect),
                        params=copy.deepcopy(layer.params),
                        enabled=layer.enabled,
                    )
                )
            self.batch_applied_paths.add(target_path)

        self.batch_applied.emit()
        QMessageBox.information(
            self, "Applied", f"Added {len(self._layers)} layer(s) to {len(targets)} image(s)."
        )

    # ===== commit

    def _on_save(self) -> None:
        self.image_record.layers = self._layers
        self.accept()
