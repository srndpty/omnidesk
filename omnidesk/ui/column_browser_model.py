"""Asynchronous one-level-at-a-time model for the column browser."""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import cmp_to_key
from pathlib import Path
from typing import Any, cast

from PyQt6 import sip
from PyQt6.QtCore import (
    QAbstractItemModel,
    QFileInfo,
    QModelIndex,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    pyqtSignal,
)
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QFileIconProvider

from .column_browser_helpers import normalize_directory_key

logger = logging.getLogger(__name__)

_SCAN_BATCH_SIZE = 256
_WINDOWS_LOGICAL_COMPARE: Callable[[str, str], int] | None = None
_NATURAL_PART_RE = re.compile(r"(\d+)")


@dataclass(slots=True)
class _DirectoryEntry:
    path: str
    name: str
    is_dir: bool


class _DirectoryNode:
    __slots__ = (
        "child_keys",
        "children",
        "error",
        "is_dir",
        "loaded",
        "loading",
        "name",
        "parent",
        "path",
        "row",
        "scan_generation",
        "scan_token",
    )

    def __init__(
        self,
        path: Path,
        *,
        parent: _DirectoryNode | None = None,
        is_dir: bool | None = None,
    ) -> None:
        self.path = path
        self.name = path.name or str(path)
        self.parent = parent
        self.row = 0
        self.is_dir = path.is_dir() if is_dir is None else is_dir
        self.children: list[_DirectoryNode] = []
        # children に入っている正規化キーの集合。重複挿入の判定を O(1) にし、巨大
        # ディレクトリで ``child in node.children`` の線形探索（O(n²)）になるのを防ぐ。
        self.child_keys: set[str] = set()
        # ``loaded`` は「スキャンを試行し終えた」ことを表す。成否は ``error`` で区別する。
        # こうすることで、アクセス不能ディレクトリで rowCount→再スキャンの無限ループを
        # 避けつつ、プレースホルダで失敗を表示できる。手動 refresh のときだけ ``loaded``
        # を False に戻して再試行する。
        self.loaded = False
        self.loading = False
        self.error: str | None = None
        self.scan_generation = 0
        self.scan_token: _ScanToken | None = None


class _ScanToken:
    __slots__ = ("cancelled",)

    def __init__(self) -> None:
        self.cancelled = False


class _DirectoryScanSignals(QObject):
    batchReady = pyqtSignal(object)
    finished = pyqtSignal(object)


class _DirectoryScanJob(QRunnable):
    def __init__(
        self,
        path: Path,
        key: str,
        generation: int,
        token: _ScanToken,
        *,
        follow_symlinks: bool,
    ) -> None:
        super().__init__()
        self._path = path
        self._key = key
        self._generation = generation
        self._token = token
        self._follow_symlinks = follow_symlinks
        self.signals = _DirectoryScanSignals()

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        started = time.perf_counter()
        count = 0
        scanned: list[_DirectoryEntry] = []
        error: str | None = None
        # tryTake で取り切れず起動してしまった job でも、os.scandir に入る前にここで
        # 早期終了する。実行中の scandir 自体は止められないが、起動前キャンセルは潰せる。
        if self._token.cancelled:
            self._emit_finished(True, 0, started, None)
            return
        try:
            with os.scandir(self._path) as entries:
                for entry in entries:
                    if self._token.cancelled:
                        logger.info(
                            "Column directory scan cancelled while reading: %s count=%d",
                            self._path,
                            count,
                        )
                        self._emit_finished(True, count, started, None)
                        return
                    try:
                        is_dir = entry.is_dir(follow_symlinks=self._follow_symlinks)
                    except OSError:
                        is_dir = False
                    scanned.append(
                        _DirectoryEntry(
                            path=entry.path,
                            name=entry.name,
                            is_dir=is_dir,
                        )
                    )
                    count += 1
            # sort 自体はキャンセル不可なので、その手前で一度確認して無駄な整列を避ける。
            if self._token.cancelled:
                self._emit_finished(True, count, started, None)
                return
            for batch in _entry_batches(_sort_entries(scanned), _SCAN_BATCH_SIZE):
                if self._token.cancelled:
                    logger.info(
                        "Column directory scan cancelled before publish: %s count=%d",
                        self._path,
                        count,
                    )
                    self._emit_finished(True, count, started, None)
                    return
                self.signals.batchReady.emit((self._key, self._generation, self._token, batch))
        except OSError as exc:
            error = str(exc)
            logger.info("Column directory scan failed: %s error=%s", self._path, exc)
        self._emit_finished(self._token.cancelled, count, started, error)

    def _emit_finished(
        self, cancelled: bool, count: int, started: float, error: str | None
    ) -> None:
        # token を payload に含めることで、generation 再利用・古い signal・キャンセル後
        # 再開が絡んでも、ハンドラ側が必ず正しい job を ``_jobs`` から除去できる。
        self.signals.finished.emit(
            (self._key, self._generation, self._token, cancelled, count, started, error)
        )


def _entry_batches(
    entries: list[_DirectoryEntry],
    batch_size: int,
) -> list[list[_DirectoryEntry]]:
    return [entries[index : index + batch_size] for index in range(0, len(entries), batch_size)]


def _sort_entries(entries: list[_DirectoryEntry]) -> list[_DirectoryEntry]:
    return sorted(entries, key=cmp_to_key(_compare_entries))


def _compare_entries(left: _DirectoryEntry, right: _DirectoryEntry) -> int:
    if left.is_dir != right.is_dir:
        return -1 if left.is_dir else 1
    return _compare_names(left.name, right.name)


def _compare_names(left: str, right: str) -> int:
    compare = _windows_logical_compare()
    if compare is not None:
        result = compare(left, right)
        if result:
            return result
    left_key = _fallback_natural_sort_key(left)
    right_key = _fallback_natural_sort_key(right)
    if left_key < right_key:
        return -1
    if left_key > right_key:
        return 1
    return (left > right) - (left < right)


def _windows_logical_compare() -> Callable[[str, str], int] | None:
    global _WINDOWS_LOGICAL_COMPARE
    if os.name != "nt":
        return None
    if _WINDOWS_LOGICAL_COMPARE is not None:
        return _WINDOWS_LOGICAL_COMPARE
    try:
        import ctypes

        shlwapi = ctypes.WinDLL("Shlwapi")
        compare = shlwapi.StrCmpLogicalW
        compare.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        compare.restype = ctypes.c_int
    except (AttributeError, OSError):
        logger.debug("Could not load StrCmpLogicalW for column browser sorting", exc_info=True)
        return None

    def logical_compare(left: str, right: str) -> int:
        return int(compare(left, right))

    _WINDOWS_LOGICAL_COMPARE = logical_compare
    return _WINDOWS_LOGICAL_COMPARE


def _fallback_natural_sort_key(name: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in _NATURAL_PART_RE.split(name)
        if part
    )


class _ColumnFileSystemModel(QAbstractItemModel):
    """Small async filesystem model tailored to ``QColumnView``.

    ``QFileSystemModel`` still does substantial view-facing work for very large
    directories. This model only loads one directory level on demand, does the
    filesystem enumeration in a worker thread, and inserts rows in small batches
    so rapid sibling navigation can cancel stale scans.
    """

    directoryLoaded = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._nodes_by_key: dict[str, _DirectoryNode] = {}
        self._root_path = Path.home()
        self._scan_pool = QThreadPool(self)
        self._scan_pool.setMaxThreadCount(4)
        self._jobs: dict[_ScanToken, _DirectoryScanJob] = {}
        self._icon_provider = QFileIconProvider()
        self._icon_cache: dict[str, QIcon] = {}
        # symlink/junction/reparse point を辿るかどうか。スキャンの is_dir 判定に渡す。
        self._resolve_symlinks = False
        self.destroyed.connect(self._cancel_all_scans)

    # QFileSystemModel-compatible no-ops used by ColumnBrowser setup.
    def setFilter(self, _filters: Any) -> None:  # noqa: N802
        return None

    def setResolveSymlinks(self, enable: bool) -> None:  # noqa: N802
        self._resolve_symlinks = bool(enable)

    def setReadOnly(self, _enable: bool) -> None:  # noqa: N802
        return None

    def canFetchMore(self, _parent: QModelIndex | None = None) -> bool:  # noqa: N802
        return False

    def fetchMore(self, _parent: QModelIndex | None = None) -> None:  # noqa: N802
        return None

    # QAbstractItemModel implementation.
    def columnCount(self, _parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 1

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        node = self._node_from_index(parent or QModelIndex())
        if node is None:
            return 0
        if node.is_dir and not node.loaded and not node.loading:
            self._start_scan(node)
        return len(node.children)

    def index(  # noqa: N802
        self,
        row_or_path: int | str,
        column: int = 0,
        parent: QModelIndex | None = None,
    ) -> QModelIndex:
        if isinstance(row_or_path, str):
            return self._index_for_path(Path(row_or_path))
        if column != 0:
            return QModelIndex()
        parent_node = self._node_from_index(parent or QModelIndex())
        if parent_node is None or row_or_path < 0 or row_or_path >= len(parent_node.children):
            return QModelIndex()
        child = parent_node.children[row_or_path]
        return self.createIndex(row_or_path, column, child)

    def parent(self, index: QModelIndex) -> QModelIndex:  # noqa: N802
        node = self._node_from_index(index)
        if node is None or node.parent is None:
            return QModelIndex()
        return self.createIndex(node.parent.row, 0, node.parent)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> object:
        node = self._node_from_index(index)
        if node is None:
            return None
        if role == int(Qt.ItemDataRole.DisplayRole):
            return node.name
        if role == int(Qt.ItemDataRole.DecorationRole):
            return self._icon_for_node(node)
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return str(node.path)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def hasChildren(self, parent: QModelIndex | None = None) -> bool:  # noqa: N802
        node = self._node_from_index(parent or QModelIndex())
        return bool(node is not None and node.is_dir)

    # QFileSystemModel-compatible helpers used by the column browser.
    def setRootPath(self, path: str) -> QModelIndex:  # noqa: N802
        target = Path(path)
        self._root_path = target
        return self._ensure_node_index(target, is_dir=True)

    def filePath(self, index: QModelIndex) -> str:  # noqa: N802
        node = self._node_from_index(index)
        return str(node.path) if node is not None else ""

    def fileInfo(self, index: QModelIndex) -> QFileInfo:  # noqa: N802
        return QFileInfo(self.filePath(index))

    def isDir(self, index: QModelIndex) -> bool:  # noqa: N802
        node = self._node_from_index(index)
        if node is None:
            return False
        return node.is_dir

    def refresh(self, index: QModelIndex) -> None:
        node = self._node_from_index(index)
        if node is None or not node.is_dir:
            return
        # キャンセルで中断済みの partial children は _cancel_scan が片付ける。読み込み済み
        # （token なし）の children はここで通知付きにクリアしてから再スキャンする。
        self._cancel_scan(node)
        self._clear_children(node)
        node.loaded = False
        node.error = None
        self._start_scan(node)

    def cancel_scans_except(self, allowed_paths: set[Path]) -> None:
        allowed_keys = {normalize_directory_key(str(path)) for path in allowed_paths}
        for key, node in list(self._nodes_by_key.items()):
            if key in allowed_keys:
                continue
            if node.scan_token is not None:
                logger.info("Cancelling stale column scan: %s", node.path)
                self._cancel_scan(node)

    def is_directory_loaded(self, index: QModelIndex) -> bool:
        node = self._node_from_index(index)
        return bool(node is not None and node.loaded)

    def directory_error(self, index: QModelIndex) -> str | None:
        node = self._node_from_index(index)
        return node.error if node is not None else None

    def _node_from_index(self, index: QModelIndex) -> _DirectoryNode | None:
        if not index.isValid():
            return None
        node = index.internalPointer()
        return node if isinstance(node, _DirectoryNode) else None

    def _icon_for_node(self, node: _DirectoryNode) -> QIcon:
        if node.is_dir:
            cache_key = "dir"
            icon_type = QFileIconProvider.IconType.Folder
        else:
            suffix = node.path.suffix.lower()
            cache_key = f"file:{suffix}"
            icon_type = QFileIconProvider.IconType.File
        cached = self._icon_cache.get(cache_key)
        if cached is not None:
            return cached
        icon = self._icon_provider.icon(QFileInfo(str(node.path)))
        if icon.isNull():
            icon = self._icon_provider.icon(icon_type)
        self._icon_cache[cache_key] = icon
        return icon

    def _index_for_path(self, path: Path) -> QModelIndex:
        # public ``index(str)`` 用のパス→index 解決。スキャンで親の children に挿入済みの
        # node は正しい row を持つ。未挿入（loaded 親に存在しない子・親が未読み込みなど）の
        # 場合は detached な node を作って row=0 の index を返す。これは filePath/refresh
        # などパス解決用で、本体（ColumnBrowser）では view へ渡さず model メソッドにしか
        # 渡していない（setRootIndex に渡す root index は setRootPath 由来）。可視行への
        # 追加は必ず beginInsertRows 経由なので、detached node が列に紛れ込むことはない。
        return self._index_for_node(self._ensure_node(path))

    def _index_for_node(self, node: _DirectoryNode) -> QModelIndex:
        return self.createIndex(node.row, 0, node)

    def _ensure_node_index(self, path: Path, *, is_dir: bool | None = None) -> QModelIndex:
        node = self._ensure_node(path, is_dir=is_dir)
        return self._index_for_node(node)

    def _ensure_node(self, path: Path, *, is_dir: bool | None = None) -> _DirectoryNode:
        key = normalize_directory_key(str(path))
        existing = self._nodes_by_key.get(key)
        if existing is not None:
            if is_dir is not None:
                existing.is_dir = is_dir
            return existing
        parent_path = path.parent
        parent_node = None if parent_path == path else self._ensure_node(parent_path, is_dir=True)
        # children への挿入は必ずスキャンの beginInsertRows 経由で行う。ここで loaded
        # 親へ直接 append すると、model 通知なしに row が増え、ソート順も壊れるため、
        # node を登録するだけに留める（row は親が未挿入なら 0 のまま）。
        node = _DirectoryNode(path, parent=parent_node, is_dir=is_dir)
        self._nodes_by_key[key] = node
        return node

    def _start_scan(self, node: _DirectoryNode) -> None:
        # rowCount() からも呼ばれる（paint 中など）。ここで構造変更（beginRemoveRows）を
        # 行うと model/view が壊れるため、partial children のクリアは _cancel_scan 側に
        # 寄せ、ここでは行追加（beginInsertRows）を伴う非同期スキャンの起動だけ行う。
        if not node.is_dir or node.loading:
            return
        token = _ScanToken()
        node.scan_generation += 1
        node.scan_token = token
        node.loading = True
        node.error = None
        key = normalize_directory_key(str(node.path))
        job = _DirectoryScanJob(
            node.path,
            key,
            node.scan_generation,
            token,
            follow_symlinks=self._resolve_symlinks,
        )
        # autoDelete を切り、job の寿命を _jobs（Python 参照）で管理する。これがないと
        # 実行完了後に C++ 側が破棄され、_jobs に残った wrapper への tryTake が
        # "wrapped C/C++ object has been deleted" で落ちる。finished で pop する。
        job.setAutoDelete(False)
        job.signals.batchReady.connect(self._handle_scan_batch)
        job.signals.finished.connect(self._handle_scan_finished)
        self._jobs[token] = job
        logger.info(
            "Column directory scan started: %s generation=%d", node.path, node.scan_generation
        )
        self._scan_pool.start(job)

    def _clear_children(self, node: _DirectoryNode) -> None:
        """node の children を通知付きで空にする。paint 経路から呼ばないこと。"""
        if not node.children:
            return
        self.beginRemoveRows(self._index_for_node(node), 0, len(node.children) - 1)
        node.children.clear()
        node.child_keys.clear()
        self.endRemoveRows()

    def _cancel_scan(self, node: _DirectoryNode) -> None:
        token = node.scan_token
        node.scan_token = None
        node.loading = False
        if token is None:
            return
        token.cancelled = True
        # まだ起動していない queued job は pool から取り除き、_jobs からも消す。これで
        # 巨大フォルダのスキャンが順番待ちのまま新 root のスキャンを塞ぐのを防ぐ。
        job = self._jobs.get(token)
        if job is not None and not sip.isdeleted(job) and self._scan_pool.tryTake(job):
            self._jobs.pop(token, None)
        # キャンセルで中断した partial children を通知付きで掃除する（rowCount/paint 経路
        # ではなくナビゲーション・refresh 経路から呼ばれるので beginRemoveRows は安全）。
        self._clear_children(node)

    def _cancel_all_scans(self) -> None:
        # destroyed 経由でも呼ばれる。構造変更（beginRemoveRows）や、破棄中のスレッド
        # プールへのアクセス（tryTake）は避け、token のキャンセルだけ行う。
        for node in self._nodes_by_key.values():
            if node.scan_token is not None:
                node.scan_token.cancelled = True
                node.scan_token = None
            node.loading = False

    def _handle_scan_batch(self, payload: object) -> None:
        if sip.isdeleted(self):
            return
        key, generation, token, entries = cast(
            tuple[str, int, _ScanToken, list[_DirectoryEntry]], payload
        )
        node = self._nodes_by_key.get(key)
        if node is None or generation != node.scan_generation or node.scan_token is not token:
            return
        new_nodes: list[_DirectoryNode] = []
        for entry in entries:
            entry_key = normalize_directory_key(entry.path)
            child = self._nodes_by_key.get(entry_key)
            if child is None:
                child = _DirectoryNode(Path(entry.path), parent=node, is_dir=entry.is_dir)
                child.name = entry.name
                self._nodes_by_key[entry_key] = child
            else:
                child.parent = node
                child.name = entry.name
                child.is_dir = entry.is_dir
            # 既に children に入っているキーは O(1) で弾く（line search を避ける）。
            if entry_key in node.child_keys:
                continue
            child.row = len(node.children) + len(new_nodes)
            new_nodes.append(child)
            node.child_keys.add(entry_key)
        if not new_nodes:
            return
        first = len(node.children)
        last = first + len(new_nodes) - 1
        self.beginInsertRows(self._index_for_node(node), first, last)
        node.children.extend(new_nodes)
        self.endInsertRows()
        logger.debug("Column directory scan batch inserted: %s rows=%d", node.path, len(new_nodes))

    def _handle_scan_finished(self, payload: object) -> None:
        if sip.isdeleted(self):
            return
        key, generation, token, cancelled, count, started, error = cast(
            tuple[str, int, _ScanToken, bool, int, float, str | None],
            payload,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        # payload 由来の token で必ず除去する。これにより、古い job の finished が
        # 新しい job の参照を消したり、キャンセル済み token が残留したりしない。
        self._jobs.pop(token, None)
        node = self._nodes_by_key.get(key)
        if node is None or generation != node.scan_generation or node.scan_token is not token:
            # キャンセル済み・世代交代・別 job に置き換え済み。状態には触れない。
            logger.info(
                "Column directory scan ignored: %s generation=%d cancelled=%s count=%d "
                "elapsed_ms=%d",
                "" if node is None else node.path,
                generation,
                cancelled,
                count,
                elapsed_ms,
            )
            return
        node.loading = False
        node.scan_token = None
        # 成否に関わらず「試行は完了した」とみなす。失敗時も loaded=True にすることで
        # rowCount→再スキャンの無限ループを防ぎ、error はプレースホルダ表示に使う。
        node.loaded = True
        node.error = error
        logger.info(
            "Column directory scan finished: %s count=%d elapsed_ms=%d error=%s",
            node.path,
            count,
            elapsed_ms,
            error,
        )
        self.directoryLoaded.emit(str(node.path))
