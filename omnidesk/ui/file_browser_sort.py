"""ファイル一覧の並べ替えに使う純粋ロジック。

Qt に依存しない比較ヘルパーをここへ集約し、プロキシモデル
(:mod:`omnidesk.ui.file_browser.sort_model`) はここを呼び出すだけにする。
こうすることで並べ替え順の仕様をユニットテストで固定できる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# QFileSystemModel の列番号。名前列のみ「並べ替え方式」（名前順/拡張子順）の影響を受ける。
COLUMN_NAME = 0
COLUMN_SIZE = 1
COLUMN_TYPE = 2
COLUMN_DATE = 3

SortMode = Literal["name", "extension"]

_CHUNK_RE = re.compile(r"(\d+)|(\D+)")


def natural_sort_key(text: str) -> tuple[tuple[int, int, str], ...]:
    """数字を数値として扱う自然順ソート用キーを返す。

    ``file2`` が ``file10`` より前に来るようにし、英字は大小文字を無視する。
    各チャンクは ``(種別, 数値, 文字列)`` の三つ組に正規化し、int と str を
    直接比較してしまう例外を避ける。
    """
    key: list[tuple[int, int, str]] = []
    for match in _CHUNK_RE.finditer(text):
        number, letters = match.group(1), match.group(2)
        if number is not None:
            key.append((0, int(number), ""))
        else:
            key.append((1, 0, letters.casefold()))
    return tuple(key)


@dataclass(frozen=True)
class EntryMeta:
    """並べ替え対象の 1 エントリ分のメタdata。

    Qt の ``QFileInfo`` から組み立てる想定だが、テストでは直接生成できる。
    """

    is_dir: bool
    name: str
    suffix: str
    size: int
    mtime: int


def entry_sort_key(meta: EntryMeta, *, column: int, mode: SortMode):
    """列と並べ替え方式に応じた「副キー」を返す（フォルダ優先は呼び出し側で処理）。

    同一の並べ替え内では全エントリが同じ ``column``/``mode`` を使うため、
    返るタプルの形は揃っており安全に比較できる。
    """
    name_key = natural_sort_key(meta.name)
    if column == COLUMN_SIZE:
        return (meta.size, name_key)
    if column == COLUMN_TYPE:
        return (meta.suffix.casefold(), name_key)
    if column == COLUMN_DATE:
        return (meta.mtime, name_key)
    # 名前列（COLUMN_NAME およびその他の想定外の列はここに集約）
    if mode == "extension":
        return (meta.suffix.casefold(), name_key)
    return (name_key,)


def entry_is_before(
    left: EntryMeta,
    right: EntryMeta,
    *,
    column: int,
    mode: SortMode,
    descending: bool,
) -> bool:
    """``left`` を ``right`` より前に表示すべきなら ``True``。

    Windows Explorer に倣い、フォルダは常にファイルより前へ置く（この判定は
    昇順/降順の影響を受けない）。同種同士の比較だけが ``descending`` で反転する。
    """
    if left.is_dir != right.is_dir:
        return left.is_dir
    left_key = entry_sort_key(left, column=column, mode=mode)
    right_key = entry_sort_key(right, column=column, mode=mode)
    if left_key == right_key:
        return False
    before = left_key < right_key
    return (not before) if descending else before


def toggled_sort_mode(mode: SortMode) -> SortMode:
    """名前順 ↔ 拡張子順 を切り替える。"""
    return "name" if mode == "extension" else "extension"
