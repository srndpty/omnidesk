"""1ディレクトリぶんのエントリを保持する、走査ベースのフラットなモデル。

``QFileSystemModel`` を置き換えるために用意した。置き換えの理由は性能で、実測に
基づく。7,500件のフォルダで14件削除すると、``QFileSystemModel`` は
``QFileSystemWatcher`` の通知のたびにディレクトリを再構築し、そのたびに
**GUIスレッドが約1.7秒止まる**（3回発生して合計約5.4秒）。プロキシを外しても
同じだけ止まるため、原因は上に乗せたソートやフィルタではなく
``QFileSystemModel`` 自身にある。

ここでは次の3点で構造的に避ける。

* 走査は ``os.scandir`` をワーカースレッドで1回。``DirEntry`` は走査中に得た
  ``stat`` 結果を保持するので、名前・種別・サイズ・更新日時が追加のsyscallなしで揃う。
* 変更時は全再構築ではなく**差分適用**。消えた行・増えた行・中身が変わった行だけを
  通知するので、GUIスレッドの仕事が O(全件) から O(変更数) になる。
* 監視通知はデバウンスする。1回の削除操作で通知が何件飛んでも、走査は1回にまとまる。

列の並び（名前・サイズ・種類・更新日時）は ``QFileSystemModel`` に合わせてあるので、
ヘッダーや並べ替えのコードはそのまま使える。ツリーではなくフラットな表なので、
ビューへ渡すルートインデックスは常に不正値（``QModelIndex()``）になる。
"""

from __future__ import annotations

import logging
import os
import stat as stat_module
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from PyQt6.QtCore import (
    QAbstractTableModel,
    QDateTime,
    QFileInfo,
    QFileSystemWatcher,
    QLocale,
    QModelIndex,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    pyqtSignal,
)

from .qt_lifetime import own_by_application

logger = logging.getLogger(__name__)

COLUMN_NAME = 0
COLUMN_SIZE = 1
COLUMN_TYPE = 2
COLUMN_MODIFIED = 3
COLUMN_COUNT = 4

# 監視通知をまとめる待ち時間。ゴミ箱への移動は対象1件ごとに通知が飛ぶため、
# まとめないと走査が件数ぶん走る。
WATCH_DEBOUNCE_MS = 200

# これを超える走査時間は体感に出る。再発時に原因を追えるよう記録する。
SLOW_SCAN_WARNING_MS = 400


def is_hidden_entry(name: str, stat_result: os.stat_result) -> bool:
    """一覧から隠すエントリかを返す。

    ``QFileInfo.isHidden()`` と同じ判定にする。旧実装は
    ``QDir.AllEntries | QDir.NoDotAndDotDot`` を指定しており、``QDir.Hidden`` を
    含めていなかったため隠し項目は出ていなかった。走査へ移った際にこの絞り込みが
    抜けると、``.git`` などが一覧に現れて操作・削除の対象になってしまう。

    * Windows: ``FILE_ATTRIBUTE_HIDDEN`` が立っているエントリ。名前が ``.`` で
      始まるだけの項目は**隠さない**（Qt も Windows では属性だけを見るため、
      旧実装でもドットファイルは表示されていた）。
    * それ以外のOS: 名前が ``.`` で始まるエントリ。
    """
    attributes = getattr(stat_result, "st_file_attributes", None)
    if attributes is not None:
        return bool(attributes & stat_module.FILE_ATTRIBUTE_HIDDEN)
    return name.startswith(".")


def normalise_entry_key(path: Path | str) -> str:
    """パスを、ファイルシステムへ問い合わせずに比較用へ正規化する。

    ``Path.resolve()`` はシンボリックリンクを辿るため実I/Oを伴う。走査結果の
    突き合わせは1回の更新で全件ぶん走るので、ここに実I/Oを混ぜてはいけない。
    契約は :func:`omnidesk.ui.file_browser_navigation.navigation_key` と同じ
    「同じ字句的絶対パスが同じ文字列になること」。
    """
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except (OSError, ValueError):
        logger.debug("パスを正規化できません: %s", path, exc_info=True)
        return os.path.normcase(str(path))


@dataclass(slots=True)
class DirectoryEntry:
    """1エントリぶんの、走査時に確定した情報。

    ``entry_id`` は行の同一性を表す。行が生き残っている限り変わらず、いちど捨てた
    値は二度と使い回さない。上に乗るプロキシは、これを並べ替えキャッシュのキーに
    使える（``QFileSystemModel`` の ``internalId()`` と違い、アドレスの再利用で
    別のエントリを指してしまうことがない）。
    """

    entry_id: int
    key: str
    path: str
    name: str
    suffix: str
    is_dir: bool
    size: int
    mtime_ms: int

    def matches(self, other: DirectoryEntry) -> bool:
        """並べ替えや表示に効く値が同じかを返す。"""
        return (
            self.is_dir == other.is_dir
            and self.size == other.size
            and self.mtime_ms == other.mtime_ms
            and self.name == other.name
        )

    def update_from(self, other: DirectoryEntry) -> None:
        """``entry_id`` を保ったまま中身を更新する。"""
        self.path = other.path
        self.name = other.name
        self.suffix = other.suffix
        self.is_dir = other.is_dir
        self.size = other.size
        self.mtime_ms = other.mtime_ms


class EntryIdAllocator:
    """行IDを配る、スレッド安全な採番器。

    走査ジョブは同時に複数走り得る（遅いディレクトリの走査中に、F5や監視由来の
    再走査が重なる）。ジェネレーターを共有すると複数スレッドから同時に ``next()``
    が呼ばれ、``ValueError: generator already executing`` になる。Pythonの
    ジェネレーターは並行実行に対応していない。

    IDは使い回さない（詳細は :class:`DirectoryEntry` の ``entry_id``）。
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._next = 1

    def allocate(self) -> int:
        with self._lock:
            value = self._next
            self._next += 1
            return value


class DirectoryScanSignals(QObject):
    """走査ジョブが共有する、GUIスレッド常駐のシグナル置き場。

    ``QRunnable`` ごとに ``QObject`` を持たせると ``setAutoDelete(True)`` により
    ワーカースレッドで破棄され、Qtが禁じている操作になる（``ThumbnailJobSignals``
    と同じ理由）。
    """

    scanned = pyqtSignal(str, int, object, object)  # path, generation, entries, error


class DirectoryScanJob(QRunnable):
    """``os.scandir`` で1ディレクトリを読み、エントリ一覧を返す。"""

    def __init__(
        self,
        path: str,
        generation: int,
        signals: DirectoryScanSignals,
        entry_ids: EntryIdAllocator,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._generation = generation
        self._entry_ids = entry_ids
        self.signals = signals

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        entries: list[DirectoryEntry] = []
        error: str | None = None
        try:
            with os.scandir(self._path) as scanned:
                for item in scanned:
                    entry = self._build_entry(item)
                    if entry is not None:
                        entries.append(entry)
        except OSError as exc:
            error = str(exc)
            logger.info("ディレクトリの走査に失敗しました: %s error=%s", self._path, exc)
        except Exception as exc:
            # 想定外の失敗でも、必ず結果を返して終わること。ここで抜けると
            # 完了通知が出ず、一覧も再読込の保留状態も更新されないまま止まる。
            error = str(exc)
            entries = []
            logger.exception("ディレクトリの走査で想定外の失敗: %s", self._path)
        self.signals.scanned.emit(self._path, self._generation, entries, error)

    def _build_entry(self, item: os.DirEntry[str]) -> DirectoryEntry | None:
        """``DirEntry`` から1エントリを作る。

        種別とメタdataで、リンクを辿るかどうかの方針を分けている。

        * ``is_dir`` は**辿る**。ディレクトリへのシンボリックリンクは、開ける対象
          として扱いたい（フォルダアイコン・フォルダプレビュー・ダブルクリックでの
          移動が、実体のディレクトリと同じように働く）。辿らないと、
          ディレクトリへのリンクがファイル扱いになって開けなくなる。
          追加のsyscallが要るのはリンクだったときだけ。
        * ``stat`` は**辿らない**。リンク自身の情報で表示には足り、リンク切れの
          エントリも一覧から消えずに残る。``DirEntry`` が走査時に得た情報を
          そのまま使えるので、エントリごとの追加syscallも発生しない。

        隠し項目は ``None`` を返して一覧から落とす（:func:`is_hidden_entry`）。
        """
        try:
            stat_result = item.stat(follow_symlinks=False)
        except OSError:
            # 走査中に消えた、あるいは読めないエントリ。一覧から落とす。
            logger.debug("エントリを読めませんでした: %s", item.path, exc_info=True)
            return None
        try:
            is_dir = item.is_dir(follow_symlinks=True)
        except OSError:
            # リンク切れなど。開ける先が無いのでファイル扱いにする。
            logger.debug("リンク先を判定できませんでした: %s", item.path, exc_info=True)
            is_dir = False
        name = item.name
        if is_hidden_entry(name, stat_result):
            return None
        return DirectoryEntry(
            entry_id=self._entry_ids.allocate(),
            key=normalise_entry_key(item.path),
            path=item.path,
            name=name,
            suffix=Path(name).suffix.lower().lstrip("."),
            is_dir=is_dir,
            size=0 if is_dir else stat_result.st_size,
            mtime_ms=int(stat_result.st_mtime * 1000),
        )


def contiguous_descending_ranges(rows: list[int]) -> list[tuple[int, int]]:
    """行番号の集合を、末尾から順に消せる連続範囲へまとめる。

    後ろから消せば、前の範囲の行番号がずれない。まとめることで
    ``beginRemoveRows`` の回数を減らし、ビュー側の更新も1回で済ませる。
    """
    if not rows:
        return []
    ordered = sorted(set(rows))
    ranges: list[tuple[int, int]] = []
    first = previous = ordered[0]
    for row in ordered[1:]:
        if row == previous + 1:
            previous = row
            continue
        ranges.append((first, previous))
        first = previous = row
    ranges.append((first, previous))
    return list(reversed(ranges))


class DirectoryModel(QAbstractTableModel):
    """1ディレクトリぶんのエントリを、フラットな表として提供する。"""

    directoryLoaded = pyqtSignal(str)
    scanFailed = pyqtSignal(str, str)  # path, error

    HEADERS = ("Name", "Size", "Type", "Date Modified")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._entries: list[DirectoryEntry] = []
        self._row_by_key: dict[str, int] = {}
        self._root_path = ""
        # 空文字は「まだどこも開いていない」を表す。normalise_entry_key("") は
        # カレントディレクトリになってしまうので、番兵として別扱いにする。
        self._root_key = ""
        self._generation = 0
        self._last_completed_generation = 0
        # 監視を張るまでの取りこぼしを拾うため、ディレクトリを開いた直後だけ
        # 追いつき走査を1回入れる（下記 _handle_scan_result 参照）。
        self._needs_catch_up_scan = False
        self._entry_ids = EntryIdAllocator()
        self._locale = QLocale()
        self._type_names: dict[str, str] = {}
        # 走査ジョブはワーカースレッドで破棄されるため、シグナル用QObjectは
        # ジョブに持たせず1つだけ用意し、寿命は QApplication に預ける。
        self._scan_signals = own_by_application(DirectoryScanSignals())
        self._scan_signals.scanned.connect(self._handle_scan_result)
        pool = QThreadPool.globalInstance()
        assert pool is not None
        self._scan_pool = pool
        self._watcher = QFileSystemWatcher(self)
        # 監視の希望状態。走査中に非アクティブ化されても、遅れて届いた結果が
        # 監視を復活させないようにするためのフラグ（下記 _watch_current_root 参照）。
        self._watching_enabled = True
        self._watcher.directoryChanged.connect(self._handle_directory_changed)
        self._watch_timer = QTimer(self)
        self._watch_timer.setSingleShot(True)
        self._watch_timer.setInterval(WATCH_DEBOUNCE_MS)
        self._watch_timer.timeout.connect(self._rescan_current_root)

    # ------------------------------------------------------------------
    # QAbstractItemModel
    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802 - Qt override
        return 0 if parent is not None and parent.isValid() else len(self._entries)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802 - Qt override
        return 0 if parent is not None and parent.isValid() else COLUMN_COUNT

    def index(  # noqa: N802 - Qt override
        self,
        row_or_path: int | str,
        column: int = 0,
        parent: QModelIndex | None = None,
    ) -> QModelIndex:
        """行番号、またはパスからインデックスを返す。

        パス版は ``QFileSystemModel.index(path)`` および
        :class:`omnidesk.ui.column_browser_model.ColumnBrowserModel` と同じ規約。
        """
        if isinstance(row_or_path, str):
            return self.index_for_path(row_or_path)
        if parent is not None and parent.isValid():
            return QModelIndex()
        if not (0 <= row_or_path < len(self._entries)):
            return QModelIndex()
        if not (0 <= column < COLUMN_COUNT):
            return QModelIndex()
        # どの列のインデックスにも entry_id を載せる。載せないと
        # ``siblingAtColumn()`` で id が落ち、行の同一性を見失う。
        return self.createIndex(row_or_path, column, self._entries[row_or_path].entry_id)

    def headerData(  # noqa: N802 - Qt override
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < COLUMN_COUNT
        ):
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        entry = self.entry(index)
        if entry is None:
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return self._display_text(entry, index.column())
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == COLUMN_SIZE:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def _display_text(self, entry: DirectoryEntry, column: int) -> str:
        if column == COLUMN_NAME:
            return entry.name
        if column == COLUMN_SIZE:
            return "" if entry.is_dir else self._locale.formattedDataSize(entry.size)
        if column == COLUMN_TYPE:
            return self._type_name(entry)
        if column == COLUMN_MODIFIED:
            moment = QDateTime.fromMSecsSinceEpoch(entry.mtime_ms)
            return self._locale.toString(moment, QLocale.FormatType.ShortFormat)
        return ""

    def _type_name(self, entry: DirectoryEntry) -> str:
        """種類の表示名を、拡張子ごとに1回だけ作って使い回す。

        シェルへ問い合わせると、エントリごとにレジストリ参照が走る。ここは
        表示用の短い文字列で足りるため、拡張子から組み立てる。
        """
        if entry.is_dir:
            return "File Folder"
        cached = self._type_names.get(entry.suffix)
        if cached is None:
            cached = f"{entry.suffix.upper()} File" if entry.suffix else "File"
            self._type_names[entry.suffix] = cached
        return cached

    # ------------------------------------------------------------------
    # エントリへのアクセス
    # ------------------------------------------------------------------
    def entry(self, index: QModelIndex) -> DirectoryEntry | None:
        """インデックスに対応するエントリを返す。無ければ ``None``。"""
        if not index.isValid():
            return None
        row = index.row()
        if not (0 <= row < len(self._entries)):
            return None
        return self._entries[row]

    def entry_for_key(self, key: str) -> DirectoryEntry | None:
        row = self._row_by_key.get(key)
        return None if row is None else self._entries[row]

    def entries(self) -> list[DirectoryEntry]:
        """現在のエントリ一覧（コピー）を返す。"""
        return list(self._entries)

    # ------------------------------------------------------------------
    # QFileSystemModel 互換のアクセサ
    # ------------------------------------------------------------------
    def index_for_path(self, path: Path | str) -> QModelIndex:
        """パスから、名前列のインデックスを返す。"""
        row = self._row_by_key.get(normalise_entry_key(path))
        if row is None:
            return QModelIndex()
        return self.createIndex(row, COLUMN_NAME, self._entries[row].entry_id)

    def filePath(self, index: QModelIndex) -> str:  # noqa: N802 - Qt-style API
        entry = self.entry(index)
        return "" if entry is None else entry.path

    def fileName(self, index: QModelIndex) -> str:  # noqa: N802 - Qt-style API
        entry = self.entry(index)
        return "" if entry is None else entry.name

    def isDir(self, index: QModelIndex) -> bool:  # noqa: N802 - Qt-style API
        entry = self.entry(index)
        return bool(entry is not None and entry.is_dir)

    def fileInfo(self, index: QModelIndex) -> QFileInfo:  # noqa: N802 - Qt-style API
        """互換用の ``QFileInfo``。

        ``QFileInfo`` は問い合わせのたびに実I/Oを伴い得るので、行ごとに回る経路では
        :meth:`entry` を使うこと。ここは呼び出し頻度の低い箇所のための橋渡し。
        """
        return QFileInfo(self.filePath(index))

    def rootPath(self) -> str:  # noqa: N802 - Qt-style API
        return self._root_path

    def setRootPath(self, path: str) -> QModelIndex:  # noqa: N802 - Qt-style API
        """表示するディレクトリを切り替え、走査を開始する。

        フラットな表なので、ビューへ渡すルートインデックスは常に不正値になる。

        **別のディレクトリへ移るときは、走査の完了を待たずに旧行を捨てる。**
        残したままにすると、``rootPath()`` は新しいディレクトリを指しているのに
        並んでいる行は旧ディレクトリのもの、という状態が走査中ずっと続く。
        フラットな表ではビュー側のルートインデックスが旧行を隔離してくれないため、
        その間に削除やリネームを実行すると、画面に見えているのとは違う
        ディレクトリのファイルを操作してしまう。低速なドライブでは数百ms〜秒単位で
        この窓が開く。

        同じディレクトリの読み直し（``refresh``）では行を保持し、差分だけを
        反映する。選択もスクロール位置も保たれる。
        """
        new_root = str(path)
        root_changed = normalise_entry_key(new_root) != self._root_key
        self._root_path = new_root
        self._root_key = normalise_entry_key(new_root)
        self._watch_timer.stop()
        self._unwatch_all()
        if root_changed:
            self._clear_entries()
        self._needs_catch_up_scan = True
        self._start_scan()
        return QModelIndex()

    def _clear_entries(self) -> None:
        """表示中の行を即座に空にする。"""
        if not self._entries:
            return
        self.beginResetModel()
        self._entries = []
        self._row_by_key = {}
        self.endResetModel()

    def refresh(self) -> None:
        """現在のディレクトリを読み直す。"""
        if self._root_path:
            self._start_scan()

    @property
    def scan_generation(self) -> int:
        """最後に**開始した**走査の世代。"""
        return self._generation

    @property
    def is_watching(self) -> bool:
        """ディレクトリの変更を監視しているか。

        止まっている間は「表示されていない」とみなしてよい。再走査を促す側は、
        これを見て余計な走査を起こさないようにする。
        """
        return self._watching_enabled

    @property
    def last_completed_scan_generation(self) -> int:
        """最後に**反映を終えた**走査の世代。

        「ある時点より後に始まった走査が終わったか」を判定するために使う。
        ある時刻を根拠に状態を決めるのではなく、走査の完了を根拠にしたい箇所で
        突き合わせる（:meth:`SortedFileSystemModel.hide_removed_paths` 参照）。
        """
        return self._last_completed_generation

    # ------------------------------------------------------------------
    # 走査
    # ------------------------------------------------------------------
    def _start_scan(self) -> None:
        self._generation += 1
        job = DirectoryScanJob(
            self._root_path,
            self._generation,
            self._scan_signals,
            self._entry_ids,
        )
        self._scan_pool.start(job)

    def _handle_scan_result(
        self, path: str, generation: int, entries: object, error: object
    ) -> None:
        if generation != self._generation or path != self._root_path:
            # 別のディレクトリへ移ったあとに届いた古い結果。
            return
        if error is not None:
            self._apply_entries([])
            self._last_completed_generation = generation
            self.scanFailed.emit(path, str(error))
            self.directoryLoaded.emit(path)
            return
        assert isinstance(entries, list)
        self._apply_entries(entries)
        self._last_completed_generation = generation
        self._watch_current_root()
        self._start_catch_up_scan_if_needed()
        self.directoryLoaded.emit(path)

    def _apply_entries(self, scanned: list[DirectoryEntry]) -> None:
        """走査結果を差分で反映する。

        全入れ替え（``modelReset``）にすると、ビューは選択もスクロール位置も
        作り直すことになり、上に乗るプロキシも並べ替えキャッシュを全部捨てる。
        7,500件のフォルダではそれが体感できる停止になるため、消えた行・増えた行・
        中身が変わった行だけを通知する。
        """
        scanned_by_key: dict[str, DirectoryEntry] = {entry.key: entry for entry in scanned}

        removed_rows = [
            row for row, entry in enumerate(self._entries) if entry.key not in scanned_by_key
        ]
        for first, last in contiguous_descending_ranges(removed_rows):
            self.beginRemoveRows(QModelIndex(), first, last)
            del self._entries[first : last + 1]
            # ``endRemoveRows()`` が rowsRemoved を出す時点で、内部の索引も
            # 新しい行構成に一致していなければならない。受け手（プロキシなど）は
            # その場で index_for_path() を引く。
            self._rebuild_row_index()
            self.endRemoveRows()

        for row, existing in enumerate(self._entries):
            fresh = scanned_by_key[existing.key]
            if existing.matches(fresh):
                continue
            # entry_id を保ったまま更新する。行の同一性が保たれるので、
            # 上のプロキシは他の行のキャッシュを捨てずに済む。
            existing.update_from(fresh)
            self.dataChanged.emit(
                self.index(row, COLUMN_NAME),
                self.index(row, COLUMN_COUNT - 1),
            )

        known = {entry.key for entry in self._entries}
        added = [entry for entry in scanned if entry.key not in known]
        if added:
            first = len(self._entries)
            self.beginInsertRows(QModelIndex(), first, first + len(added) - 1)
            self._entries.extend(added)
            self._rebuild_row_index()
            self.endInsertRows()

    def _rebuild_row_index(self) -> None:
        self._row_by_key = {entry.key: row for row, entry in enumerate(self._entries)}

    # ------------------------------------------------------------------
    # 監視
    # ------------------------------------------------------------------
    def _unwatch_all(self) -> None:
        watched = self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)

    def _start_catch_up_scan_if_needed(self) -> None:
        """ディレクトリを開いた直後だけ、追いつき用の走査を1回入れる。

        監視は走査が成功してから張る（そうしないと、到達できないパスの登録で
        GUIスレッドが止まり得る）。その結果、``os.scandir`` が走っている間から
        監視を張り終えるまでの短い時間に作られた・消えたエントリは、走査結果にも
        変更通知にも入らない。放っておくと次の外部変更かF5まで一覧が古いままになる。

        そこで開いた直後に一度だけ走査し直して、この隙間を埋める。走査はワーカー
        スレッドで走り、差分が無ければ通知も出ないのでGUI側の負荷はほぼ無い。
        監視由来の再走査ではフラグを立てないので、走査が連鎖することはない。
        """
        if not self._needs_catch_up_scan:
            return
        self._needs_catch_up_scan = False
        if self._watching_enabled and self._root_path:
            self._start_scan()

    def _watch_current_root(self) -> None:
        """走査が成功したディレクトリを監視対象にする。

        以前は ``setRootPath`` の中で ``os.path.isdir()`` を確かめてから登録して
        いたが、それはGUIスレッドの同期I/Oで、UNCや切断されたリムーバブル
        ドライブではナビゲーション自体がそこで止まり得た。走査が成功していれば
        ディレクトリであることは確定しているので、その後に登録すれば確認は要らない。

        ``_watching_enabled`` を見るのは、走査中に :meth:`stop_watching` が
        呼ばれた場合に備えるため。走査の投入と結果の到着の間にタブが非アクティブ
        化されると、遅れて届いた成功結果がここで監視を復活させてしまい、
        「見えていないタブは監視も再走査もしない」という設計が崩れる。
        """
        if not self._watching_enabled or not self._root_path:
            return
        if self._root_path in self._watcher.directories():
            return
        self._watcher.addPath(self._root_path)

    def _handle_directory_changed(self, path: str) -> None:
        _ = path
        # ``stop_watching()`` の時点で Qt 側に配送待ちの通知が残っていることがある。
        # watcher を外しただけではそれが届いてしまい、非表示のタブで走査が1回走る。
        if not self._watching_enabled:
            return
        # 1回の削除操作でも対象1件ごとに通知が飛ぶ。走査を1回にまとめる。
        self._watch_timer.start()

    def _rescan_current_root(self) -> None:
        # デバウンス待ちの間に監視が止められた場合に備えて、ここでも見る。
        if self._watching_enabled and self._root_path:
            self._start_scan()

    def stop_watching(self) -> None:
        """監視とデバウンスを止める（破棄前・非表示時に使う）。

        進行中の走査は止めない（結果自体は反映してよい）が、その完了で監視が
        復活しないようにフラグを倒す。
        """
        self._watching_enabled = False
        self._watch_timer.stop()
        self._unwatch_all()

    def resume_watching(self) -> None:
        """監視を張り直す。"""
        self._watching_enabled = True
        self._watch_current_root()


def entry_keys(entries: Iterable[DirectoryEntry]) -> set[str]:
    """エントリ集合の正規化キー集合を返す。"""
    return {entry.key for entry in entries}
