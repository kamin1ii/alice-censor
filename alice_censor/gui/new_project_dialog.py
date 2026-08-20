from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)


class _PathRow(QWidget):
    def __init__(self, dialog_title: str, name_filter: str, *, pick_dir: bool = False, save: bool = False):
        super().__init__()
        self.line_edit = QLineEdit()
        button = QPushButton("Browse…")
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addRow(self.line_edit, button)
        self._dialog_title = dialog_title
        self._name_filter = name_filter
        self._pick_dir = pick_dir
        self._save = save
        button.clicked.connect(self._browse)

    def _browse(self) -> None:
        if self._pick_dir:
            path = QFileDialog.getExistingDirectory(self, self._dialog_title)
        elif self._save:
            path, _ = QFileDialog.getSaveFileName(self, self._dialog_title, filter=self._name_filter)
        else:
            path, _ = QFileDialog.getOpenFileName(self, self._dialog_title, filter=self._name_filter)
        if path:
            self.line_edit.setText(path)

    def text(self) -> str:
        return self.line_edit.text().strip()

    def set_text(self, value: str) -> None:
        self.line_edit.setText(value)


class NewProjectDialog(QDialog):
    """Collects the handful of paths needed to start a project. The source
    archive, alice.exe, where to extract to, and where to save the project
    sidecar file. Everything else (manifest path, cache dir) is derived
    from those."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Project")
        self.setMinimumWidth(480)

        self.archive_row = _PathRow("Select archive", "AliceSoft archives (*.afa *.ald)")
        self.alice_exe_row = _PathRow("Select alice.exe", "Executables (*.exe);;All files (*)")
        self.output_row = _PathRow("Select output/working folder", "", pick_dir=True)
        self.project_file_row = _PathRow(
            "Save project file as", "Alice Censor projects (*.acproj.json)", save=True
        )

        form = QFormLayout()
        form.addRow("Archive (.afa / .ald):", self.archive_row)
        form.addRow("alice.exe:", self.alice_exe_row)
        form.addRow("Working folder:", self.output_row)
        form.addRow("Project file:", self.project_file_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        outer = QFormLayout(self)
        outer.addRow(form)
        outer.addRow(buttons)

        self.archive_row.line_edit.textChanged.connect(self._suggest_output_dir)
        self.archive_row.line_edit.textChanged.connect(self._suggest_project_file)
        self.output_row.line_edit.textChanged.connect(self._suggest_project_file)

    def _suggest_output_dir(self, text: str) -> None:
        if self.output_row.text():
            return
        if not text:
            return
        archive = Path(text)
        self.output_row.set_text(str(archive.with_suffix("")))

    def _suggest_project_file(self, _text: str = "") -> None:
        # Only auto-fill while the user hasn't touched this field
        # themselves. Once they've picked (or typed) their own location,
        # don't clobber it just because the archive/output field changed.
        if self.project_file_row.text():
            return
        archive_text = self.archive_row.text()
        output_text = self.output_row.text()
        if not archive_text or not output_text:
            return
        stem = Path(archive_text).stem
        self.project_file_row.set_text(str(Path(output_text) / f"{stem}.acproj.json"))

    def _on_accept(self) -> None:
        if not self.archive_row.text():
            return
        if not self.alice_exe_row.text():
            return
        if not self.output_row.text():
            return
        if not self.project_file_row.text():
            return
        self.accept()

    def values(self) -> tuple[str, str, str, str]:
        """Returns (archive_path, alice_exe_path, output_dir, project_file_path)."""
        return (
            self.archive_row.text(),
            self.alice_exe_row.text(),
            self.output_row.text(),
            self.project_file_row.text(),
        )
