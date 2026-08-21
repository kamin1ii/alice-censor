from pathlib import Path

from alice_censor.paths import natural_sort_key, resolve_fs_path, split_dir_and_stem


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


# ===== natural_sort_key
#
# Archives list files in packing order, which scatters a scene's frames.
# The gallery sorts by name instead, and plain text ordering is not enough.


def test_numbers_order_by_value_not_by_digit():
    assert sorted(["a10.png", "a2.png", "a1.png"], key=natural_sort_key) == [
        "a1.png", "a2.png", "a10.png"
    ]


def test_fullwidth_digits_order_the_same_way():
    """AliceSoft names use fullwidth digits, which int() reads as normal."""
    names = ["幅１７０.png", "幅９０.png", "幅１２８.png"]
    assert sorted(names, key=natural_sort_key) == ["幅９０.png", "幅１２８.png", "幅１７０.png"]
    assert sorted(names) != sorted(names, key=natural_sort_key), (
        "this is exactly the case plain sorting gets wrong"
    )


def test_zero_padded_sequences_stay_in_order():
    frames = ["コスプレＨ１０.png", "コスプレＨ０２.png", "コスプレＨ０１.png"]
    assert sorted(frames, key=natural_sort_key) == [
        "コスプレＨ０１.png", "コスプレＨ０２.png", "コスプレＨ１０.png"
    ]


def test_files_group_by_folder():
    """Which folder comes first is just codepoint order. What matters is
    that a folder's files stay together and in sequence."""
    paths = ["イベント／ありす／０２.png", "trim_立ち絵／ありす／基本.png", "イベント／ありす／０１.png"]
    ordered = sorted(paths, key=natural_sort_key)
    event = [i for i, p in enumerate(ordered) if p.startswith("イベント")]
    assert event == [1, 2], "the folder's files must be adjacent"
    assert ordered[1].endswith("０１.png") and ordered[2].endswith("０２.png")


def test_separators_do_not_change_the_order():
    """The same path written with either separator sorts identically."""
    assert natural_sort_key("a／b／c.png") == natural_sort_key("a/b/c.png")


def test_a_digit_like_character_that_is_not_a_number_does_not_crash():
    """'²'.isdigit() is True but int('²') raises, so the key identifies
    digit runs by position rather than by asking."""
    assert natural_sort_key("x²y.png")  # must not raise
    assert sorted(["x²y.png", "a1.png"], key=natural_sort_key)


def test_paths_that_tie_numerically_still_order_deterministically():
    """a1 and a01 are the same number, so without a tiebreaker their order
    would depend on the order they arrived in."""
    assert natural_sort_key("a01.png") == natural_sort_key("a01.png")
    assert natural_sort_key("a1.png") != natural_sort_key("a01.png")
    assert sorted(["a1.png", "a01.png"], key=natural_sort_key) == ["a01.png", "a1.png"]
    assert sorted(["a01.png", "a1.png"], key=natural_sort_key) == ["a01.png", "a1.png"]
