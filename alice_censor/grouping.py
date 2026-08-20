"""Scene-group computation for manifest entries.

Two strategies, matching the two manifest styles.

- AFA-style (descriptive names). Strip trailing sequence and qualifier suffixes
  from the filename stem (H01-H13, 挿入前/挿入/射精/射精後/笑う, ...) to
  recover a stable "scene" key. This is treated as authoritative, though
  users can still override it, since naming isn't *always* consistent.

- ALD-style (opaque sequential IDs like cg21051.png). There is no naming
  signal at all, so we only offer a *suggested* clustering based on numeric
  proximity of IDs sharing a prefix within the same directory. This is
  explicitly non-authoritative, a starting point for manual merge and
  split rather than a scene boundary detector.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .manifest import Manifest, ManifestFormat
from .paths import normalize_separators, split_dir_and_stem

# Suffixes stripped (longest match first) from a filename stem to recover
# the base scene name. Edit or extend this list per game as needed, since
# naming conventions vary between titles.
DEFAULT_QUALIFIER_SUFFIXES: tuple[str, ...] = (
    "挿入前",
    "挿入後",
    "挿入",
    "射精後",
    "射精",
    "笑う",
)

# Trailing sequence markers like "H01".."H13". AliceSoft games commonly use
# the FULLWIDTH letter/digits (Ｈ０１, U+FF28 + U+FF10-FF19) rather than
# ASCII. Confirmed against a real extracted manifest, where the ASCII form
# never appears at all. `\d` already matches fullwidth digits (they're
# Unicode category Nd), so only the H needs both forms listed explicitly.
# Digits are optional (`\d{0,3}`, not `\d{1,3}`) because the CG-viewer's
# thumbnail counterpart for a whole H-scene is typically named with a bare
# trailing H and no variant number at all (e.g. "大制裁Ｈ.png" alongside the
# actual scene frames "大制裁Ｈ０１挿入前.png".."大制裁Ｈ０７.png").
# Confirmed against a real game's asset set. Anchored to end-of-string, so
# this only matches a name that *ends* in bare H, not "H" appearing
# incidentally mid-name (e.g. a romanized character name).
_H_SUFFIX_RE = re.compile(r"[HhＨｈ]\d{0,3}$")

# Bare trailing sequence numbers with no "H", e.g. "脱ぐ０１".."脱ぐ０８" or
# "買い食い０１".."買い食い０８", also seen in real manifests, for
# non-explicit sequential variants using the same naming convention.
_BARE_NUM_SUFFIX_RE = re.compile(r"\d{1,3}$")

# Trailing separators left behind after stripping a suffix.
_TRAILING_SEP_CHARS = "_-　 "

# Words that specifically indicate explicit content, for auto-flagging.
# Deliberately narrower than DEFAULT_QUALIFIER_SUFFIXES above. That list
# also includes purely cosmetic expression variants like "笑う" (smiling),
# which is fine to strip when GROUPING portrait variants together but would
# be a false positive if used to flag images for censorship. A smiling
# portrait isn't explicit just because it shares the suffix-stripping
# convention with actually-explicit scene names.
#
# Checked against the whole path, not just the stem, because some games
# put the explicit signal in a *folder* name instead of the filename (e.g.
# "イベント／ユラン／和姦／０１.png", where the individual files are just
# plain sequential numbers with no marker of their own). Edit or extend this
# list per game as needed, same as DEFAULT_QUALIFIER_SUFFIXES above. It's
# a naming heuristic, not an exhaustive dictionary.
_EXPLICIT_QUALIFIER_WORDS: tuple[str, ...] = (
    "挿入前",
    "挿入後",
    "挿入",
    "射精後",
    "射精",
    "和姦",
    "レイプ",
)

# H+digits marker, deliberately NOT anchored, since a qualifier can follow
# it (e.g. "拷問Ｈ０１挿入前" has the marker mid-stem). Requiring at least
# one digit here (unlike _H_SUFFIX_RE above) keeps this search-anywhere
# form low-risk for false positives. Three-plus characters (H + digits)
# appearing by coincidence is rare, whereas a bare single "H" could easily
# appear mid-word in an unrelated name.
_EXPLICIT_H_MARKER_RE = re.compile(r"[HhＨｈ]\d{1,3}")

# Bare "H" with no digit, e.g. the in-game CG-viewer thumbnail counterpart
# for a whole H-scene ("大制裁Ｈ.png", see _H_SUFFIX_RE above), easy to
# miss entirely if only the full-size scene frames get censored, since the
# thumbnail still spoils the uncensored content in the game's own gallery.
# Anchored to end-of-string, unlike _EXPLICIT_H_MARKER_RE, so a bare H is a
# much weaker signal than H+digits, so it's only trusted when it's not just
# incidental to some other word in the name.
_BARE_H_SUFFIX_RE = re.compile(r"[HhＨｈ]$")


def looks_explicit_by_naming(path: str) -> bool:
    """Heuristic. Does this AFA-style manifest path look like an explicit
    scene, per the H01-H13 / explicit-qualifier naming convention described
    in this module's docstring? Not authoritative, being a naming
    heuristic rather than a content classifier, but a solid first pass for
    bulk-flagging before manual review.

    The H-marker checks are stem-only (a folder literally ending in H01
    isn't a real naming convention this game uses), but the qualifier-word
    check runs against the whole path, since some games put the signal
    in a folder name instead of the filename (e.g. a "和姦" folder full of
    plainly-numbered files with no marker of their own)."""
    _, stem, _ext = split_dir_and_stem(path)
    if _EXPLICIT_H_MARKER_RE.search(stem):
        return True
    if _BARE_H_SUFFIX_RE.search(stem):
        return True
    normalized_path = normalize_separators(path)
    return any(word in normalized_path for word in _EXPLICIT_QUALIFIER_WORDS)


def find_explicit_by_naming(manifest: Manifest) -> list[str]:
    """Every AFA-style manifest path that looks explicit by naming
    convention. Always empty for ALD-style manifests, whose opaque
    sequential IDs carry no naming signal to detect anything from."""
    if manifest.archive_format != ManifestFormat.AFA:
        return []
    return [path for path in manifest.paths() if looks_explicit_by_naming(path)]


def strip_variant_suffix(
    stem: str, qualifiers: tuple[str, ...] = DEFAULT_QUALIFIER_SUFFIXES
) -> tuple[str, list[str]]:
    """Iteratively strip trailing sequence/qualifier suffixes from a
    filename stem. Returns (base, [suffixes removed, in original order]).

    Never strips down to an empty string. If that would happen, the
    original stem is returned unmodified (better to under-group than to
    collapse unrelated files into one bucket).
    """
    working = stem
    removed: list[str] = []
    ordered_qualifiers = sorted(qualifiers, key=len, reverse=True)

    changed = True
    while changed:
        changed = False
        for q in ordered_qualifiers:
            if working.endswith(q) and len(working) > len(q):
                working = working[: -len(q)]
                removed.append(q)
                changed = True
                break
        else:
            m = _H_SUFFIX_RE.search(working)
            if m is None:
                m = _BARE_NUM_SUFFIX_RE.search(working)
            if m and len(working) > (m.end() - m.start()):
                removed.append(working[m.start() :])
                working = working[: m.start()]
                changed = True

        trimmed = working.rstrip(_TRAILING_SEP_CHARS)
        if trimmed != working and trimmed:
            working = trimmed
            changed = True

    if not working:
        return stem, []
    return working, list(reversed(removed))


_PURELY_NUMERIC_RE = re.compile(r"\d+")


def afa_scene_group_key(path: str) -> str:
    """Compute a stable scene-group key for one AFA-style manifest path."""
    dir_path, stem, _ext = split_dir_and_stem(path)
    base, suffixes = strip_variant_suffix(stem)
    if base == stem and not suffixes and dir_path and _PURELY_NUMERIC_RE.fullmatch(stem):
        # The filename is *only* a sequence number with no descriptive
        # text at all, say "０１.png".."０８.png". strip_variant_suffix
        # correctly refuses to strip that down to nothing (it would
        # otherwise collapse every bare-numbered file anywhere into one
        # bucket), but that means each number was falling back to keeping
        # itself as its own singleton group. A real example is a scene's name
        # lives in the *folder* instead of the filename (e.g.
        # "イベント／ユラン／和姦／０１.png".."０８.png", where "和姦" is
        # the scene and the files are just plain numbers). Group by the
        # folder itself in that specific case instead.
        return dir_path
    return f"{dir_path}/{base}" if dir_path else base


# ===== ALD-style opaque-ID clustering

_ALD_ID_RE = re.compile(r"^(?P<prefix>\D*)(?P<num>\d+)(?P<suffix>\D*)$")


@dataclass
class AldIdInfo:
    prefix: str
    num: int
    suffix: str
    width: int  # zero-padded digit width, for round-tripping display


def parse_ald_id(stem: str) -> AldIdInfo | None:
    m = _ALD_ID_RE.match(stem)
    if not m:
        return None
    return AldIdInfo(
        prefix=m.group("prefix"), num=int(m.group("num")), suffix=m.group("suffix"),
        width=len(m.group("num")),
    )


@dataclass
class GroupInfo:
    key: str
    members: list[str] = field(default_factory=list)
    authoritative: bool = True  # False for ALD numeric-proximity clusters


def compute_ald_clusters(paths: list[str], gap_threshold: int = 1) -> dict[str, GroupInfo]:
    """Suggest clusters for opaque ALD-style paths by numeric proximity.

    Files are bucketed by (directory, alpha prefix, alpha suffix), sorted by
    their numeric ID, then split into runs wherever the gap between
    consecutive IDs exceeds `gap_threshold`. Files with names that don't
    parse as <prefix><digits><suffix> each get their own singleton group.

    This is a *suggestion*, so callers should let users merge and split
    freely.
    """
    buckets: dict[tuple[str, str, str], list[tuple[int, str]]] = {}
    unparsed: list[str] = []

    for path in paths:
        dir_path, stem, _ext = split_dir_and_stem(path)
        info = parse_ald_id(stem)
        if info is None:
            unparsed.append(path)
            continue
        buckets.setdefault((dir_path, info.prefix, info.suffix), []).append((info.num, path))

    groups: dict[str, GroupInfo] = {}
    for (dir_path, prefix, suffix), items in buckets.items():
        items.sort(key=lambda t: t[0])
        cluster: list[tuple[int, str]] = []
        clusters: list[list[tuple[int, str]]] = []
        for num, path in items:
            if cluster and num - cluster[-1][0] > gap_threshold:
                clusters.append(cluster)
                cluster = []
            cluster.append((num, path))
        if cluster:
            clusters.append(cluster)

        for c in clusters:
            lo, hi = c[0][0], c[-1][0]
            key_parts = [p for p in (dir_path, f"{prefix}{lo}-{hi}{suffix}") if p]
            key = "cluster:" + "/".join(key_parts)
            groups[key] = GroupInfo(
                key=key, members=[p for _n, p in c], authoritative=False
            )

    for path in unparsed:
        key = f"cluster:{path}"
        groups[key] = GroupInfo(key=key, members=[path], authoritative=False)

    return groups


def compute_afa_groups(paths: list[str]) -> dict[str, GroupInfo]:
    groups: dict[str, GroupInfo] = {}
    for path in paths:
        key = afa_scene_group_key(path)
        groups.setdefault(key, GroupInfo(key=key, authoritative=True)).members.append(path)
    return groups


def compute_groups(manifest: Manifest, ald_gap_threshold: int = 1) -> dict[str, GroupInfo]:
    """Compute suggested scene groups for every entry in a manifest."""
    paths = manifest.paths()
    if manifest.archive_format == ManifestFormat.AFA:
        return compute_afa_groups(paths)
    return compute_ald_clusters(paths, gap_threshold=ald_gap_threshold)
