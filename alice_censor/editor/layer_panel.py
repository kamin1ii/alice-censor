"""Property editor for the currently-selected censor layer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Signal

from .. import fonts
from ..project import CensorLayer, LayerType
from ..rendering import TEXT_PADDING, TEXT_SIZE
from .sticker_picker_dialog import StickerPickerDialog

DEFAULT_PARAMS: dict[LayerType, dict] = {
    LayerType.SOLID: {"color": "#000000", "opacity": 1.0},
    LayerType.BLUR: {"radius": 12},
    LayerType.PIXELATE: {"block_size": 12},
    LayerType.OVERLAY: {"sticker": "", "fit": "contain", "rotation": 0, "opacity": 1.0},
    LayerType.TEXT: {
        "text": "CENSORED",
        "color": "#FFFFFF",
        "size": TEXT_SIZE,
        "font": fonts.DEFAULT_FAMILY,
        "align": "center",
        "background": True,
        "background_color": "#000000",
        "padding": TEXT_PADDING,
        "opacity": 1.0,
    },
}

# Display names shown in the UI, kept separate from LayerType's own string
# values (which are the on-disk JSON schema for saved projects and must not
# change) so wording can be improved freely. "Overlay" read as unclear
# about what it actually does, where "Image / Sticker" says it directly.
LAYER_TYPE_LABELS: dict[LayerType, str] = {
    LayerType.SOLID: "Solid Color",
    LayerType.BLUR: "Blur",
    LayerType.PIXELATE: "Pixelate",
    LayerType.OVERLAY: "Image / Sticker",
    LayerType.TEXT: "Text",
}


class LayerPropertyPanel(QWidget):
    """Edits either a real, drawn layer or, when nothing is selected, a
    "pending template" (type and params only, no rect or id) that the next
    region(s) you draw will be created with. That's what lets you pick a
    sticker, color or blur radius once and then stamp out several regions
    with it in a row, instead of re-picking a sticker for every region."""

    layer_edited = Signal()
    delete_requested = Signal()

    def __init__(self, parent: QWidget | None = None, sticker_dir: str | Path | None = None):
        super().__init__(parent)
        self._layer: CensorLayer | None = None
        self._updating = False
        self._sticker_dir: Path | None = Path(sticker_dir) if sticker_dir else None

        self.mode_label = QLabel()
        self.mode_label.setStyleSheet("font-weight: bold;")

        self.type_combo = QComboBox()
        for layer_type in LayerType:
            self.type_combo.addItem(LAYER_TYPE_LABELS[layer_type], layer_type)
        self.enabled_checkbox = QCheckBox("Enabled")
        self.delete_button = QPushButton("Delete Layer")

        self.params_container = QWidget()
        self.params_layout = QFormLayout(self.params_container)

        top_form = QFormLayout()
        top_form.addRow("Type:", self.type_combo)
        top_form.addRow(self.enabled_checkbox)

        layout = QVBoxLayout(self)
        layout.addWidget(self.mode_label)
        layout.addLayout(top_form)
        layout.addWidget(self.params_container)
        layout.addStretch(1)
        layout.addWidget(self.delete_button)

        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        self.enabled_checkbox.toggled.connect(self._on_enabled_toggled)
        self.delete_button.clicked.connect(self.delete_requested)

        self.setEnabled(False)

    # ===== public API

    def set_sticker_dir(self, sticker_dir: str | Path | None) -> None:
        self._sticker_dir = Path(sticker_dir) if sticker_dir else None

    def set_layer(self, layer: CensorLayer | None, *, is_template: bool = False) -> None:
        """`is_template=True` means `layer` is the pending new-region
        template rather than a real drawn layer. The enabled checkbox and
        delete button, neither of which means anything for a template, are
        hidden, and the mode label makes clear what's being edited."""
        self._layer = layer
        self._updating = True
        try:
            if layer is None:
                self.setEnabled(False)
                self.mode_label.setText("")
                self._clear_param_widgets()
                return
            self.setEnabled(True)
            self.mode_label.setText(
                "New region defaults" if is_template else "Selected region"
            )
            self.enabled_checkbox.setVisible(not is_template)
            self.delete_button.setVisible(not is_template)
            self.type_combo.setCurrentIndex(self.type_combo.findData(layer.type))
            self.enabled_checkbox.setChecked(layer.enabled)
            self._build_param_widgets(layer.type, layer.params)
        finally:
            self._updating = False

    # ===== internal

    def _clear_param_widgets(self) -> None:
        while self.params_layout.rowCount():
            self.params_layout.removeRow(0)

    def _on_type_changed(self) -> None:
        if self._updating or self._layer is None:
            return
        # Re-wrap what the combo hands back. LayerType subclasses str, so a
        # round trip through Qt's variant storage comes out as a plain str
        # rather than the enum member that went in. This is the one place
        # in the app where that happens, so it is the one place that has to
        # convert. Assigning the raw value here is what used to leave
        # str-typed layers scattered for every later reader to cope with.
        new_type = LayerType(self.type_combo.currentData())
        self._layer.type = new_type
        self._layer.params = dict(DEFAULT_PARAMS[new_type])
        self._updating = True
        try:
            self._build_param_widgets(new_type, self._layer.params)
        finally:
            self._updating = False
        self.layer_edited.emit()

    def _on_enabled_toggled(self, checked: bool) -> None:
        if self._updating or self._layer is None:
            return
        self._layer.enabled = checked
        self.layer_edited.emit()

    def _param_changed(self) -> None:
        if self._updating:
            return
        self.layer_edited.emit()

    def _build_param_widgets(self, layer_type: LayerType, params: dict) -> None:
        self._clear_param_widgets()
        if layer_type == LayerType.SOLID:
            self._build_solid_widgets(params)
        elif layer_type == LayerType.BLUR:
            self._build_blur_widgets(params)
        elif layer_type == LayerType.PIXELATE:
            self._build_pixelate_widgets(params)
        elif layer_type == LayerType.OVERLAY:
            self._build_overlay_widgets(params)
        elif layer_type == LayerType.TEXT:
            self._build_text_widgets(params)

    def _color_button(self, params: dict, key: str, default: str, title: str) -> QPushButton:
        """A swatch that opens a colour picker and writes back to `key`."""
        button = QPushButton()

        def set_swatch(color_hex: str) -> None:
            button.setStyleSheet(f"background-color: {color_hex};")
            button.setText(color_hex)

        set_swatch(params.get(key, default))

        def pick_color() -> None:
            from PySide6.QtWidgets import QColorDialog

            color = QColorDialog.getColor(QColor(params.get(key, default)), self, title)
            if color.isValid():
                params[key] = color.name()
                set_swatch(params[key])
                self._param_changed()

        button.clicked.connect(pick_color)
        return button

    def _build_solid_widgets(self, params: dict) -> None:
        self.params_layout.addRow(
            "Color:", self._color_button(params, "color", "#000000", "Pick Color")
        )
        self.params_layout.addRow("Opacity:", self._opacity_spin(params))

    def _build_blur_widgets(self, params: dict) -> None:
        radius_spin = QSpinBox()
        radius_spin.setRange(1, 200)
        radius_spin.setValue(int(params.get("radius", 12)))

        def on_change(value: int) -> None:
            params["radius"] = value
            self._param_changed()

        radius_spin.valueChanged.connect(on_change)
        self.params_layout.addRow("Blur radius:", radius_spin)

    def _build_pixelate_widgets(self, params: dict) -> None:
        block_spin = QSpinBox()
        block_spin.setRange(2, 128)
        block_spin.setValue(int(params.get("block_size", 12)))

        def on_change(value: int) -> None:
            params["block_size"] = value
            self._param_changed()

        block_spin.valueChanged.connect(on_change)
        self.params_layout.addRow("Block size:", block_spin)

    def _build_overlay_widgets(self, params: dict) -> None:
        sticker_label = QLabel(params.get("sticker") or "(none)")
        sticker_label.setWordWrap(True)
        choose_button = QPushButton("Choose Sticker…")
        choose_button.setEnabled(self._sticker_dir is not None)
        if self._sticker_dir is None:
            choose_button.setToolTip("No project loaded. The sticker library isn't available.")

        def choose() -> None:
            if self._sticker_dir is None:
                return
            dialog = StickerPickerDialog(self._sticker_dir, current=params.get("sticker"), parent=self)
            if dialog.exec():
                chosen = dialog.selected_filename()
                if chosen:
                    params["sticker"] = chosen
                    sticker_label.setText(chosen)
                    self._param_changed()

        choose_button.clicked.connect(choose)
        sticker_row = QHBoxLayout()
        sticker_row.addWidget(sticker_label, 1)
        sticker_row.addWidget(choose_button)
        self.params_layout.addRow("Sticker:", sticker_row)
        self._overlay_choose_button = choose_button  # exposed for tests

        fit_combo = QComboBox()
        fit_combo.addItems(["contain", "stretch", "tile"])
        fit_combo.setCurrentText(params.get("fit", "contain"))

        def on_fit(text: str) -> None:
            params["fit"] = text
            self._param_changed()

        fit_combo.currentTextChanged.connect(on_fit)
        self.params_layout.addRow("Fit:", fit_combo)

        rotation_spin = QSpinBox()
        rotation_spin.setRange(-180, 180)
        rotation_spin.setValue(int(params.get("rotation", 0)))

        def on_rotation(value: int) -> None:
            params["rotation"] = value
            self._param_changed()

        rotation_spin.valueChanged.connect(on_rotation)
        self.params_layout.addRow("Rotation:", rotation_spin)

        self.params_layout.addRow("Opacity:", self._opacity_spin(params))

    def _build_text_widgets(self, params: dict) -> None:
        text_edit = QPlainTextEdit(str(params.get("text", "")))
        text_edit.setFixedHeight(60)
        text_edit.setPlaceholderText("CENSORED")

        def on_text() -> None:
            params["text"] = text_edit.toPlainText()
            self._param_changed()

        text_edit.textChanged.connect(on_text)
        self.params_layout.addRow("Text:", text_edit)

        font_combo = QComboBox()
        families = fonts.available() or (fonts.DEFAULT_FAMILY,)
        font_combo.addItems(families)
        current = params.get("font", fonts.DEFAULT_FAMILY)
        if current in families:
            font_combo.setCurrentText(current)

        def on_font(name: str) -> None:
            params["font"] = name
            self._param_changed()

        font_combo.currentTextChanged.connect(on_font)
        self.params_layout.addRow("Font:", font_combo)

        # Shown as a percentage because a fraction of image height is the
        # right thing to store, so the caption keeps its proportions on a
        # differently sized image, and the wrong thing to make somebody type.
        size_spin = QSpinBox()
        size_spin.setRange(1, 50)
        size_spin.setSuffix(" % of image height")
        size_spin.setValue(max(1, round(float(params.get("size", TEXT_SIZE)) * 100)))

        def on_size(value: int) -> None:
            params["size"] = value / 100
            self._param_changed()

        size_spin.valueChanged.connect(on_size)
        self.params_layout.addRow("Size:", size_spin)

        self.params_layout.addRow(
            "Text color:", self._color_button(params, "color", "#FFFFFF", "Pick Text Color")
        )

        align_combo = QComboBox()
        align_combo.addItems(["left", "center", "right"])
        align_combo.setCurrentText(str(params.get("align", "center")))

        def on_align(value: str) -> None:
            params["align"] = value
            self._param_changed()

        align_combo.currentTextChanged.connect(on_align)
        self.params_layout.addRow("Align:", align_combo)

        background_check = QCheckBox("Fill the box behind the text")
        background_check.setChecked(bool(params.get("background", True)))
        background_button = self._color_button(
            params, "background_color", "#000000", "Pick Background Color"
        )
        background_button.setEnabled(background_check.isChecked())

        def on_background(checked: bool) -> None:
            params["background"] = checked
            background_button.setEnabled(checked)
            self._param_changed()

        background_check.toggled.connect(on_background)
        self.params_layout.addRow("Background:", background_check)
        self.params_layout.addRow("Background color:", background_button)

        self.params_layout.addRow("Opacity:", self._opacity_spin(params))

    def _opacity_spin(self, params: dict) -> QDoubleSpinBox:
        opacity_spin = QDoubleSpinBox()
        opacity_spin.setRange(0.0, 1.0)
        opacity_spin.setSingleStep(0.05)
        opacity_spin.setValue(float(params.get("opacity", 1.0)))

        def on_change(value: float) -> None:
            params["opacity"] = value
            self._param_changed()

        opacity_spin.valueChanged.connect(on_change)
        return opacity_spin
