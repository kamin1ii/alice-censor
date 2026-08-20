from PIL import Image
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QMessageBox

from alice_censor.editor.batch_apply_dialog import BatchApplyDialog
from alice_censor.editor.editor_dialog import RegionEditorDialog
from alice_censor.project import CensorLayer, ImageRecord, LayerType, ProjectState


def _make_editable(qapp, tmp_path, path="a.png", size=(200, 100), project=None, group_members=None):
    image_path = tmp_path / path
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (255, 255, 255)).save(image_path, "PNG")
    project = project or ProjectState()
    record = project.images.setdefault(path, ImageRecord())
    dialog = RegionEditorDialog(
        image_path,
        record,
        parent=None,
        project=project,
        current_path=path,
        group_members=group_members or [],
    )
    dialog.show()
    return dialog, project, record


# -- BatchApplyDialog --------------------------------------------------------


def test_batch_apply_dialog_all_selected_by_default(qapp):
    dialog = BatchApplyDialog("scene/foo", ["a.png", "b.png", "c.png"], layer_count=1)
    assert sorted(dialog.selected_paths()) == ["a.png", "b.png", "c.png"]


def test_batch_apply_dialog_select_none(qapp):
    dialog = BatchApplyDialog("scene/foo", ["a.png", "b.png"], layer_count=1)
    dialog._set_all_checked(False)
    assert dialog.selected_paths() == []


def test_batch_apply_dialog_individual_uncheck(qapp):
    dialog = BatchApplyDialog("scene/foo", ["a.png", "b.png"], layer_count=1)
    from PySide6.QtCore import Qt

    dialog.list_widget.item(0).setCheckState(Qt.Unchecked)
    assert dialog.selected_paths() == ["b.png"]


# -- RegionEditorDialog wiring ------------------------------------------------


def test_batch_apply_button_disabled_without_group_members(qapp, tmp_path):
    dialog, project, record = _make_editable(qapp, tmp_path, group_members=[])
    assert not dialog.batch_apply_button.isEnabled()


def test_batch_apply_button_enabled_with_group_members(qapp, tmp_path):
    project = ProjectState()
    project.images["b.png"] = ImageRecord()
    dialog, project, record = _make_editable(
        qapp, tmp_path, project=project, group_members=["b.png"]
    )
    assert dialog.batch_apply_button.isEnabled()


def test_batch_apply_copies_layers_to_selected_targets(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    project = ProjectState()
    project.save(tmp_path / "project.acproj.json")
    project.images["b.png"] = ImageRecord()
    project.images["c.png"] = ImageRecord()
    dialog, project, record = _make_editable(
        qapp, tmp_path, project=project, group_members=["b.png", "c.png"]
    )
    dialog._on_region_created(QRectF(10, 10, 40, 30))
    layer_before = dialog._layers[0]

    monkeypatch.setattr(BatchApplyDialog, "exec", lambda self: 1)
    monkeypatch.setattr(BatchApplyDialog, "selected_paths", lambda self: ["b.png", "c.png"])

    dialog._on_batch_apply()

    for target in ("b.png", "c.png"):
        applied = project.images[target].layers
        assert len(applied) == 1
        assert applied[0].id != layer_before.id  # independent id
        assert applied[0].type == layer_before.type
        assert applied[0].rect == layer_before.rect
        assert applied[0].params == layer_before.params
    assert dialog.batch_applied_paths == {"b.png", "c.png"}


def test_batch_apply_only_applies_to_checked_targets(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    project = ProjectState()
    project.save(tmp_path / "project.acproj.json")
    project.images["b.png"] = ImageRecord()
    project.images["c.png"] = ImageRecord()
    dialog, project, record = _make_editable(
        qapp, tmp_path, project=project, group_members=["b.png", "c.png"]
    )
    dialog._on_region_created(QRectF(10, 10, 40, 30))

    monkeypatch.setattr(BatchApplyDialog, "exec", lambda self: 1)
    monkeypatch.setattr(BatchApplyDialog, "selected_paths", lambda self: ["b.png"])

    dialog._on_batch_apply()

    assert len(project.images["b.png"].layers) == 1
    assert len(project.images["c.png"].layers) == 0
    assert dialog.batch_applied_paths == {"b.png"}


def test_batch_apply_keeps_existing_layers_on_target(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    project = ProjectState()
    project.save(tmp_path / "project.acproj.json")
    existing = CensorLayer(id="pre-existing", type=LayerType.BLUR, rect=(0.5, 0.5, 0.1, 0.1))
    project.images["b.png"] = ImageRecord(layers=[existing])
    dialog, project, record = _make_editable(
        qapp, tmp_path, project=project, group_members=["b.png"]
    )
    dialog._on_region_created(QRectF(10, 10, 40, 30))

    monkeypatch.setattr(BatchApplyDialog, "exec", lambda self: 1)
    monkeypatch.setattr(BatchApplyDialog, "selected_paths", lambda self: ["b.png"])
    dialog._on_batch_apply()

    ids = [layer.id for layer in project.images["b.png"].layers]
    assert "pre-existing" in ids
    assert len(ids) == 2


def test_batch_apply_produces_independent_params_dicts(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    project = ProjectState()
    project.save(tmp_path / "project.acproj.json")
    project.images["b.png"] = ImageRecord()
    project.images["c.png"] = ImageRecord()
    dialog, project, record = _make_editable(
        qapp, tmp_path, project=project, group_members=["b.png", "c.png"]
    )
    dialog._on_region_created(QRectF(10, 10, 40, 30))

    monkeypatch.setattr(BatchApplyDialog, "exec", lambda self: 1)
    monkeypatch.setattr(BatchApplyDialog, "selected_paths", lambda self: ["b.png", "c.png"])
    dialog._on_batch_apply()

    project.images["b.png"].layers[0].params["color"] = "#123456"
    assert project.images["c.png"].layers[0].params.get("color") != "#123456"


def test_batch_apply_saves_project_immediately(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    project = ProjectState()
    project_file = tmp_path / "project.acproj.json"
    project.save(project_file)
    project.images["b.png"] = ImageRecord()
    dialog, project, record = _make_editable(
        qapp, tmp_path, project=project, group_members=["b.png"]
    )
    # The dialog now reports the commit instead of writing the project file
    # itself. MainWindow connects this to its own save. Standing in for it
    # here keeps the "committed immediately" guarantee under test rather
    # than the dialog's internals.
    dialog.batch_applied.connect(lambda: project.save())
    dialog._on_region_created(QRectF(10, 10, 40, 30))

    monkeypatch.setattr(BatchApplyDialog, "exec", lambda self: 1)
    monkeypatch.setattr(BatchApplyDialog, "selected_paths", lambda self: ["b.png"])
    dialog._on_batch_apply()

    reloaded = ProjectState.load(project_file)
    assert len(reloaded.images["b.png"].layers) == 1


def test_batch_apply_noop_without_layers(qapp, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a))
    )
    project = ProjectState()
    project.images["b.png"] = ImageRecord()
    dialog, project, record = _make_editable(
        qapp, tmp_path, project=project, group_members=["b.png"]
    )

    dialog._on_batch_apply()  # nothing drawn yet

    assert project.images["b.png"].layers == []
    assert shown  # "nothing to apply" message shown


def test_batch_apply_cancelled_dialog_applies_nothing(qapp, tmp_path, monkeypatch):
    project = ProjectState()
    project.images["b.png"] = ImageRecord()
    dialog, project, record = _make_editable(
        qapp, tmp_path, project=project, group_members=["b.png"]
    )
    dialog._on_region_created(QRectF(10, 10, 40, 30))

    monkeypatch.setattr(BatchApplyDialog, "exec", lambda self: 0)  # rejected

    dialog._on_batch_apply()

    assert project.images["b.png"].layers == []
    assert dialog.batch_applied_paths == set()
