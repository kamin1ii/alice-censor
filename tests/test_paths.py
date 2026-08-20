from pathlib import Path

from alice_censor.paths import resolve_fs_path, split_dir_and_stem


def test_resolve_fs_path_treats_fullwidth_separator_as_literal_filename():
    # Confirmed against a real extraction: alice-tools writes files flat,
    # with the whole manifest path (including every '／') as one filename.
    src_dir = Path("C:/game/out")
    resolved = resolve_fs_path(src_dir, "イベント／シィル／公園Ｈ０１.png")
    assert resolved == src_dir / "イベント／シィル／公園Ｈ０１.png"
    assert resolved.name == "イベント／シィル／公園Ｈ０１.png"


def test_split_dir_and_stem_still_splits_on_fullwidth_separator_for_grouping():
    # Different purpose than resolve_fs_path: grouping wants a directory-ish
    # prefix split out, even though there's no real directory on disk.
    dir_path, stem, ext = split_dir_and_stem("イベント／シィル／公園Ｈ０１.png")
    assert dir_path == "イベント/シィル"
    assert stem == "公園Ｈ０１"
    assert ext == ".png"
