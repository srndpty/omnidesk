"""実行中のファイル操作が使うパスの予約。

コピー・移動・削除はワーカースレッドで走るので、その途中でも別の操作を投入できる。
書き込み中のコピー先を削除すると、残りのファイルの書き込みがすべて
``FileNotFoundError`` になり、中途半端なフォルダがゴミ箱に残る。そこで操作は
開始前に使うパスを予約し、重なる予約があれば開始しない。

* **判定と登録は同じロック区間で行う。** 先に調べてから別途登録すると、その間に
  別スレッドが同じパスを使い始められる（TOCTOU）。
* **読み取りと書き込みを区別する。** コピー元は読み取りなので、同じフォルダを
  同時に複数箇所へコピーするのは許す。書き込み（コピー先、移動元・移動先、
  削除・名前変更の対象）は、読み取りとも書き込みとも重なってはいけない。
* **「重なる」とは同じパス・親・子孫のいずれか。** コピー中のフォルダの親を
  消すのも、配下へ別のものを書き込むのも、同じく危険なため。

Qt には依存しない。
"""

from __future__ import annotations

import itertools
import os
import threading
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AccessMode = Literal["read", "write"]

BUSY_PATH_MESSAGE = "Another file operation is still using this item: {path}"


class BusyPathError(Exception):
    """予約しようとしたパスが、実行中の別の操作と重なっている。"""

    def __init__(self, path: Path) -> None:
        super().__init__(BUSY_PATH_MESSAGE.format(path=path))
        self.path = path


def path_text_separator() -> str:
    return "\\" if os.name == "nt" else os.sep


def normalise_path_text(path: Path) -> str:
    """大文字小文字と区切り文字の違いを吸収した、比較用のパス文字列。

    末尾の区切り文字は落とすが、ルート（``C:\\``、``\\\\server\\share\\``、``/``）の
    区切り文字は残す。``C:`` はドライブ相対パスで ``C:\\`` とは別物であり、
    POSIX の ``/`` を空文字列にすると、どのパスとも重ならなくなってしまう。
    """
    separator = path_text_separator()

    def unify(text: str) -> str:
        return os.path.normcase(text).replace("/", separator).replace("\\", separator)

    text = unify(str(path))
    anchor = unify(path.anchor)
    stripped = text.rstrip(separator)
    if anchor and len(stripped) < len(anchor):
        return anchor
    return stripped


def is_same_or_descendant_text(path: str, ancestor: str) -> bool:
    """正規化済みのパス文字列で、``path`` が ``ancestor`` 自身かその配下か。

    ルートは末尾に区切り文字を持つので、区切り文字を二重に付けない。
    """
    if path == ancestor:
        return True
    separator = path_text_separator()
    prefix = ancestor if ancestor.endswith(separator) else ancestor + separator
    return path.startswith(prefix)


def _reservation_key(path: Path) -> str:
    try:
        absolute = path.resolve(strict=False)
    except OSError:
        absolute = path.absolute()
    return normalise_path_text(absolute)


def _overlaps(left: str, right: str) -> bool:
    return is_same_or_descendant_text(left, right) or is_same_or_descendant_text(right, left)


@dataclass(frozen=True)
class _Entry:
    token: int
    key: str
    mode: AccessMode


class PathReservations:
    """パスの予約台帳。スレッドから安全に使える。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[_Entry] = []
        self._tokens = itertools.count(1)

    # ------------------------------------------------------------------
    # 照会
    # ------------------------------------------------------------------
    def busy_path(self, path: Path, mode: AccessMode = "write") -> Path | None:
        """``path`` を ``mode`` で使おうとしたとき、妨げになる予約があれば返す。

        UIが操作を投入する前の目安に使う。最終的な判断は予約時に改めて行う。
        """
        key = _reservation_key(path)
        with self._lock:
            conflict = self._conflict_locked(key, mode)
        return Path(conflict) if conflict is not None else None

    # ------------------------------------------------------------------
    # 予約
    # ------------------------------------------------------------------
    @contextmanager
    def reserve(
        self, *, reads: Iterable[Path] = (), writes: Iterable[Path] = ()
    ) -> Generator[None]:
        """まとめて予約し、ブロックを抜けたら（例外でも）解除する。

        1つでも重なれば何も予約せずに :class:`BusyPathError` を投げる。
        """
        items: list[tuple[str, AccessMode, Path]] = []
        for mode, paths in (("read", reads), ("write", writes)):
            access: AccessMode = "read" if mode == "read" else "write"
            items.extend((_reservation_key(path), access, path) for path in paths)
        token = self._acquire(items)
        try:
            yield
        finally:
            self._release(token)

    @contextmanager
    def reserve_new_child(
        self,
        candidates: Iterable[Path],
        *,
        exists: Callable[[Path], bool],
        reads: Iterable[Path] = (),
    ) -> Generator[Path]:
        """``candidates`` のうち、存在せず予約もされていない最初のパスを書き込み用に予約する。

        同じフォルダを同じ場所へ同時に2回コピーすると、どちらも「- Copy 1」を
        選んでしまう。名前の決定と予約を同じロック区間で行い、2つ目には
        次の候補を割り当てる。

        候補ごとの判定は次のとおり。

        * 候補そのもの（またはその配下）に予約があれば、読み取り・書き込みを問わず
          「使われている名前」とみなして次の候補へ進む。まだ存在しないコピー先候補が
          別の操作の書き込み先になっているのが通常の場合で、読み取り予約でも同じく
          避ける（その名前で書き込むと、読んでいる操作の対象を変えてしまうため）。
        * 候補の親（コピー先のフォルダ）が使用中なら、どの候補も使えないので
          :class:`BusyPathError` を投げる。
        * 候補が尽きたら ``ValueError``。

        防げるのは OmniDesk 内部の操作どうしの競合だけ。``exists`` で確かめてから
        実際に作るまでの間に外部のプロセスが同じ名前を作った場合は、その後の
        ファイル操作が失敗し、エラーとして報告される。
        """
        read_items = [(_reservation_key(path), path) for path in reads]
        with self._lock:
            for key, path in read_items:
                if self._conflict_locked(key, "read") is not None:
                    raise BusyPathError(path)
            chosen: Path | None = None
            for candidate in candidates:
                key = _reservation_key(candidate)
                conflict = self._conflict_locked(key, "write")
                if conflict is not None and not is_same_or_descendant_text(conflict, key):
                    # 親が使用中。名前を変えても避けられない。
                    raise BusyPathError(candidate)
                if conflict is not None or exists(candidate):
                    continue
                chosen = candidate
                break
            if chosen is None:
                raise ValueError("No free destination name is available")
            token = next(self._tokens)
            self._entries.extend(_Entry(token, key, "read") for key, _ in read_items)
            self._entries.append(_Entry(token, _reservation_key(chosen), "write"))
        try:
            yield chosen
        finally:
            self._release(token)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _acquire(self, items: list[tuple[str, AccessMode, Path]]) -> int:
        with self._lock:
            for key, mode, path in items:
                if self._conflict_locked(key, mode) is not None:
                    raise BusyPathError(path)
            token = next(self._tokens)
            self._entries.extend(_Entry(token, key, mode) for key, mode, _ in items)
        return token

    def _release(self, token: int) -> None:
        with self._lock:
            self._entries = [entry for entry in self._entries if entry.token != token]

    def _conflict_locked(self, key: str, mode: AccessMode) -> str | None:
        for entry in self._entries:
            if mode == "read" and entry.mode == "read":
                continue
            if _overlaps(key, entry.key):
                return entry.key
        return None


# アプリ全体で共有する台帳。複数のタブ・ドロップ先からの操作が同じ台帳を見る。
RESERVATIONS = PathReservations()
