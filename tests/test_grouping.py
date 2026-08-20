from pathlib import Path

from alice_censor.grouping import (
    afa_scene_group_key,
    compute_ald_clusters,
    compute_groups,
    find_explicit_by_naming,
    looks_explicit_by_naming,
    parse_ald_id,
    strip_variant_suffix,
)
from alice_censor.manifest import parse_manifest

FIXTURES = Path(__file__).parent / "fixtures"


def test_strip_h_suffix():
    base, removed = strip_variant_suffix("シィルH01")
    assert base == "シィル"
    assert removed == ["H01"]


def test_strip_fullwidth_h_suffix():
    # Real AliceSoft manifests use the FULLWIDTH H + fullwidth digits
    # (Ｈ０１), never ASCII "H01" -- confirmed by running alice-tools
    # against a real archive. This is the primary case in practice.
    base, removed = strip_variant_suffix("公園Ｈ０１")
    assert base == "公園"
    assert removed == ["Ｈ０１"]


def test_strip_combined_fullwidth_h_and_qualifier():
    # "拷問Ｈ０１挿入前" seen in a real manifest: qualifier suffix stacked
    # on top of a fullwidth H-sequence suffix.
    base, removed = strip_variant_suffix("拷問Ｈ０１挿入前")
    assert base == "拷問"
    assert removed == ["Ｈ０１", "挿入前"]


def test_strip_bare_numeric_suffix():
    # Non-explicit sequential variants with no "H" at all, e.g.
    # "脱ぐ０１".."脱ぐ０８", also seen in a real manifest.
    base, removed = strip_variant_suffix("脱ぐ０１")
    assert base == "脱ぐ"
    assert removed == ["０１"]


def test_strip_qualifier_suffix():
    base, removed = strip_variant_suffix("シィル挿入前")
    assert base == "シィル"
    assert removed == ["挿入前"]


def test_strip_prefers_longest_qualifier_match():
    # "挿入前" must win over "挿入" (which is also a valid, shorter suffix).
    base, _ = strip_variant_suffix("シィル挿入前")
    assert base == "シィル"
    base2, _ = strip_variant_suffix("シィル挿入")
    assert base2 == "シィル"


def test_strip_does_not_collapse_to_empty():
    base, removed = strip_variant_suffix("H01")
    assert base == "H01"
    assert removed == []


def test_strip_no_suffix_present():
    base, removed = strip_variant_suffix("タイトル")
    assert base == "タイトル"
    assert removed == []


def test_afa_scene_group_key_groups_variants_together():
    assert afa_scene_group_key("イベント／シィルＨ０１.png") == afa_scene_group_key(
        "イベント／シィル挿入前.png"
    )
    assert afa_scene_group_key("イベント／シィルＨ０１.png") == afa_scene_group_key(
        "イベント／シィル笑う.png"
    )


def test_afa_scene_group_key_keeps_different_characters_separate():
    assert afa_scene_group_key("イベント／シィルＨ０１.png") != afa_scene_group_key(
        "イベント／リリスＨ０１.png"
    )


def test_compute_groups_for_afa_manifest():
    m = parse_manifest(FIXTURES / "afa_manifest.txt")
    groups = compute_groups(m)
    all_members = sorted(p for g in groups.values() for p in g.members)
    assert all_members == sorted(m.paths())
    assert all(g.authoritative for g in groups.values())

    siiru_group = next(g for g in groups.values() if "シィル" in g.key)
    assert len(siiru_group.members) == 8  # H01,H02,H03,挿入前,挿入,射精,射精後,笑う

    ririsu_group = next(g for g in groups.values() if "リリス" in g.key)
    assert len(ririsu_group.members) == 2


def test_parse_ald_id():
    info = parse_ald_id("cg21051")
    assert info is not None
    assert info.prefix == "cg"
    assert info.num == 21051
    assert info.suffix == ""
    assert info.width == 5


def test_parse_ald_id_no_digits_returns_none():
    assert parse_ald_id("cover") is None


def test_ald_clusters_split_on_gaps():
    paths = [
        "cg00001.png",
        "cg00002.png",
        "cg00003.png",
        "cg00010.png",  # far from 3 -> new cluster
        "cg21050.png",
        "cg21051.png",
        "cg21052.png",  # consecutive -> same cluster as above
    ]
    clusters = compute_ald_clusters(paths, gap_threshold=1)
    sizes = sorted(len(g.members) for g in clusters.values())
    assert sizes == [1, 3, 3]
    assert all(not g.authoritative for g in clusters.values())


def test_ald_clusters_gap_threshold_merges_nearby_ids():
    paths = ["cg00001.png", "cg00003.png", "cg00005.png"]
    tight = compute_ald_clusters(paths, gap_threshold=1)
    assert len(tight) == 3  # each isolated at gap=2

    loose = compute_ald_clusters(paths, gap_threshold=2)
    assert len(loose) == 1


def test_compute_groups_dispatches_by_format():
    m = parse_manifest(FIXTURES / "ald_manifest.txt")
    groups = compute_groups(m)
    assert all(not g.authoritative for g in groups.values())
    all_members = sorted(p for g in groups.values() for p in g.members)
    assert all_members == sorted(m.paths())


# -- auto-flagging explicit scenes by naming convention ----------------------


def test_looks_explicit_detects_fullwidth_h_suffix():
    assert looks_explicit_by_naming("イベント／シィル／公園Ｈ０１.png") is True


def test_looks_explicit_detects_bare_trailing_h_with_no_digit():
    # The in-game CG-viewer's thumbnail counterpart for a whole H-scene is
    # typically named with a bare trailing H and no variant number at all
    # (real example reported against actual game assets), alongside the
    # full-size frames that DO have digits -- both must be caught, or the
    # thumbnail silently spoils the censored content in the game's gallery.
    assert looks_explicit_by_naming("システム／サムネイル／リア／大制裁Ｈ.png") is True
    assert looks_explicit_by_naming("イベント／リア／大制裁Ｈ０１挿入前.png") is True


def test_strip_bare_trailing_h_with_no_digit():
    base, removed = strip_variant_suffix("大制裁Ｈ")
    assert base == "大制裁"
    assert removed == ["Ｈ"]


def test_thumbnail_and_event_scene_share_the_same_stripped_base_name():
    # Different folders (システム／サムネイル／ vs イベント／) so they still
    # land in different scene-group buckets -- batch-apply's "same relative
    # region" assumption isn't safe across a differently-composed/resized
    # thumbnail anyway -- but the underlying scene name they resolve to
    # should match, since that's what ties them together conceptually.
    thumb_key = afa_scene_group_key("システム／サムネイル／リア／大制裁Ｈ.png")
    event_key = afa_scene_group_key("イベント／リア／大制裁Ｈ０１挿入前.png")
    assert thumb_key.rsplit("/", 1)[-1] == event_key.rsplit("/", 1)[-1] == "大制裁"
    assert thumb_key != event_key  # different directories -> different groups


def test_bare_numbered_files_group_by_parent_folder():
    # Real example: the scene name lives in the *folder*, not the filename
    # -- "和姦" (the scene) contains plainly-numbered files with no
    # descriptive text of their own at all. strip_variant_suffix correctly
    # refuses to strip "０１" down to nothing (it would otherwise collapse
    # every bare-numbered file anywhere into one bucket), which without this
    # fallback left each number as its own singleton group.
    keys = {afa_scene_group_key(f"イベント／ユラン／和姦／{n}.png") for n in ("０１", "０２", "０８")}
    assert len(keys) == 1
    assert list(keys)[0] == "イベント/ユラン/和姦"


def test_bare_numbered_files_in_different_folders_stay_separate():
    key_a = afa_scene_group_key("イベント／ユラン／和姦／０１.png")
    key_b = afa_scene_group_key("イベント／リア／和姦／０１.png")
    assert key_a != key_b  # different characters' scenes, not merged together


def test_non_numeric_stems_are_unaffected_by_the_bare_numeric_fallback():
    # "夜" is not purely numeric, so different locations that happen to
    # share a "夜" (night) variant name must NOT get merged into one group.
    assert afa_scene_group_key("背景／城下町／夜.png") != afa_scene_group_key("背景／城内／夜.png")


def test_strip_lone_h_does_not_collapse_to_empty():
    base, removed = strip_variant_suffix("Ｈ")
    assert base == "Ｈ"
    assert removed == []


def test_looks_explicit_detects_h_marker_in_middle_of_stem():
    # real example: H-marker followed by a qualifier, not at the end
    assert looks_explicit_by_naming("イベント／エンエン／拷問Ｈ０１挿入前.png") is True


def test_looks_explicit_detects_insertion_and_ejaculation_words_without_h():
    assert looks_explicit_by_naming("イベント／シィル／学生服挿入.png") is True
    assert looks_explicit_by_naming("イベント／シィル／学生服射精後.png") is True


def test_looks_explicit_does_not_flag_plain_names():
    assert looks_explicit_by_naming("CG／タイトル.png") is False
    assert looks_explicit_by_naming("背景／教室.png") is False


def test_looks_explicit_does_not_false_positive_on_smiling_portraits():
    # "笑う" (smiling) is a grouping qualifier (see DEFAULT_QUALIFIER_SUFFIXES)
    # but not an explicit-content marker -- a smiling portrait variant must
    # not get auto-flagged just because it shares that suffix convention.
    assert looks_explicit_by_naming("_立ち絵／忍者／基本／笑う.png") is False


def test_looks_explicit_does_not_false_positive_on_bare_numeric_suffix():
    # "脱ぐ０１" (undressing, sequential) has no H-marker and isn't one of
    # the explicit qualifier words -- shouldn't trigger on digits alone.
    assert looks_explicit_by_naming("_立ち絵／葉月／脱ぐ０１.png") is False


def test_find_explicit_by_naming_only_for_afa_manifests():
    afa = parse_manifest(FIXTURES / "afa_manifest.txt")
    matches = find_explicit_by_naming(afa)
    assert "イベント／シィルＨ０１.png" in matches
    assert "イベント／シィル挿入.png" in matches
    assert "CG／タイトル.png" not in matches

    ald = parse_manifest(FIXTURES / "ald_manifest.txt")
    assert find_explicit_by_naming(ald) == []  # no naming signal at all for ALD
