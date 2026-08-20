from pathlib import Path

from alice_censor.gui.new_project_dialog import NewProjectDialog


def test_project_file_auto_suggested_from_archive_and_output(qapp):
    dialog = NewProjectDialog()
    dialog.archive_row.set_text(str(Path("C:/games/Rance01CG.afa")))

    assert dialog.project_file_row.text() == str(
        Path("C:/games/Rance01CG") / "Rance01CG.acproj.json"
    )


def test_project_file_suggestion_does_not_clobber_manual_choice(qapp):
    dialog = NewProjectDialog()
    dialog.project_file_row.set_text(str(Path("D:/elsewhere/my_project.acproj.json")))

    dialog.archive_row.set_text(str(Path("C:/games/Rance01CG.afa")))

    assert dialog.project_file_row.text() == str(Path("D:/elsewhere/my_project.acproj.json"))


def test_accept_requires_project_file_path(qapp):
    dialog = NewProjectDialog()
    dialog.archive_row.set_text("archive.afa")
    dialog.alice_exe_row.set_text("alice.exe")
    dialog.output_row.set_text("out")
    dialog.project_file_row.set_text("")

    dialog._on_accept()

    assert dialog.result() == 0  # not accepted -- QDialog.Rejected/unset


def test_values_returns_all_four_paths(qapp):
    dialog = NewProjectDialog()
    dialog.archive_row.set_text("archive.afa")
    dialog.alice_exe_row.set_text("alice.exe")
    dialog.output_row.set_text("out")
    dialog.project_file_row.set_text("out/archive.acproj.json")

    assert dialog.values() == ("archive.afa", "alice.exe", "out", "out/archive.acproj.json")
