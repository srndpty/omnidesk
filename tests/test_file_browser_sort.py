"""並べ替え用の純粋ロジック（:mod:`omnidesk.ui.file_browser_sort`）のテスト。"""

from __future__ import annotations

from omnidesk.ui.file_browser_sort import (
    COLUMN_DATE,
    COLUMN_NAME,
    COLUMN_SIZE,
    EntryMeta,
    entry_is_before,
    natural_sort_key,
    toggled_sort_mode,
)


def _file(name: str, *, suffix: str | None = None, size: int = 0, mtime: int = 0) -> EntryMeta:
    if suffix is None:
        suffix = name.rsplit(".", 1)[1] if "." in name else ""
    return EntryMeta(is_dir=False, name=name, suffix=suffix, size=size, mtime=mtime)


def _dir(name: str) -> EntryMeta:
    return EntryMeta(is_dir=True, name=name, suffix="", size=0, mtime=0)


def _sorted_names(entries: list[EntryMeta], *, column: int, mode, descending: bool) -> list[str]:
    import functools

    def cmp(a: EntryMeta, b: EntryMeta) -> int:
        if entry_is_before(a, b, column=column, mode=mode, descending=descending):
            return -1
        if entry_is_before(b, a, column=column, mode=mode, descending=descending):
            return 1
        return 0

    return [e.name for e in sorted(entries, key=functools.cmp_to_key(cmp))]


def test_natural_sort_key_orders_numbers_numerically() -> None:
    names = ["file10", "file2", "file1"]
    assert sorted(names, key=natural_sort_key) == ["file1", "file2", "file10"]


def test_natural_sort_key_is_case_insensitive() -> None:
    assert natural_sort_key("ABC") == natural_sort_key("abc")


def test_name_mode_sorts_alphabetically_folders_first() -> None:
    entries = [_file("b.txt"), _dir("zeta"), _file("a.png"), _dir("alpha")]
    result = _sorted_names(entries, column=COLUMN_NAME, mode="name", descending=False)
    assert result == ["alpha", "zeta", "a.png", "b.txt"]


def test_extension_mode_groups_by_extension() -> None:
    entries = [_file("b.txt"), _file("a.png"), _file("c.txt"), _file("d.png")]
    result = _sorted_names(entries, column=COLUMN_NAME, mode="extension", descending=False)
    # 拡張子（png < txt）でまとまり、同一拡張子内は名前順
    assert result == ["a.png", "d.png", "b.txt", "c.txt"]


def test_extension_mode_keeps_folders_first() -> None:
    entries = [_file("a.zip"), _dir("docs"), _file("b.txt")]
    result = _sorted_names(entries, column=COLUMN_NAME, mode="extension", descending=False)
    assert result[0] == "docs"


def test_descending_reverses_within_kind_but_keeps_folders_first() -> None:
    entries = [_file("b.txt"), _dir("alpha"), _file("a.txt"), _dir("zeta")]
    result = _sorted_names(entries, column=COLUMN_NAME, mode="name", descending=True)
    # フォルダは常に先頭。フォルダ群もファイル群もそれぞれ降順になる。
    assert result == ["zeta", "alpha", "b.txt", "a.txt"]


def test_size_column_sorts_numerically() -> None:
    entries = [_file("a", size=100), _file("b", size=20), _file("c", size=3)]
    result = _sorted_names(entries, column=COLUMN_SIZE, mode="name", descending=False)
    assert result == ["c", "b", "a"]


def test_date_column_sorts_by_mtime() -> None:
    entries = [_file("new", mtime=300), _file("old", mtime=100), _file("mid", mtime=200)]
    result = _sorted_names(entries, column=COLUMN_DATE, mode="name", descending=False)
    assert result == ["old", "mid", "new"]


def test_entry_is_before_is_stable_for_equal_keys() -> None:
    left = _file("same.txt")
    right = _file("same.txt")
    assert entry_is_before(left, right, column=COLUMN_NAME, mode="name", descending=False) is False
    assert entry_is_before(right, left, column=COLUMN_NAME, mode="name", descending=False) is False


def test_toggled_sort_mode() -> None:
    assert toggled_sort_mode("name") == "extension"
    assert toggled_sort_mode("extension") == "name"
