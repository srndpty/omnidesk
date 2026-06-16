"""Finder 風カラムブラウザで使う、UIに依存しない純粋ロジックと定数。"""

from __future__ import annotations

import os
from pathlib import Path

LOADING_PLACEHOLDER = "読み込み中…"
EMPTY_PLACEHOLDER = "（空のフォルダ）"


def column_placeholder_text(*, row_count: int, loaded: bool) -> str | None:
    """空の列に重ねて表示する文言を返す。

    読み込み中のディレクトリと、読み込みが終わって中身が無いディレクトリを
    区別し、両者が見た目で見分けられるようにする。
    """
    if row_count > 0:
        return None
    return EMPTY_PLACEHOLDER if loaded else LOADING_PLACEHOLDER


def clamp_scroll_maximum(content_right: int, viewport_width: int) -> int:
    """見えている列だけをちょうど覆う水平スクロール最大値を返す。

    ``content_right`` はコンテンツ座標での「一番右の可視列の右端」。ビューポート
    より広い分はスクロール可能で、それ以外は到達不要なデッドスペース。
    """
    return max(0, content_right - viewport_width)


def viewport_right_to_content_right(scroll_value: int, viewport_right: int) -> int:
    """ビューポート相対の右端座標をコンテンツ座標の右端へ変換する。"""
    return scroll_value + viewport_right


def normalize_directory_key(path: str) -> str:
    """Qt/OS による表記揺れを吸収してディレクトリパスを比較するための正規化キー。"""
    return os.path.normcase(os.path.normpath(path))


def is_same_or_ancestor_path(ancestor: str, child: str) -> bool:
    """``ancestor`` が ``child`` と同一、または親ディレクトリなら True。"""
    if not ancestor or not child:
        return False
    ancestor_key = normalize_directory_key(ancestor)
    child_key = normalize_directory_key(child)
    try:
        return os.path.commonpath([ancestor_key, child_key]) == ancestor_key
    except ValueError:
        return False


def paste_destination(selected: Path) -> Path:
    """選択中アイテムに対して貼り付け先となるディレクトリを返す。

    フォルダへの貼り付けはその中へ、ファイルへの貼り付けは同じ階層（親）へ。
    """
    return selected if selected.is_dir() else selected.parent
