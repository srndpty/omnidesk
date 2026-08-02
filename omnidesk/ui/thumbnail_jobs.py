"""Small cancellable jobs used by the thumbnail pipeline."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, Qt, pyqtSignal
from PyQt6.QtGui import QImage

logger = logging.getLogger(__name__)


class CancellationToken:
    """Cooperative cancellation flag for QRunnable jobs."""

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class ThumbnailJobSignals(QObject):
    """サムネイル系ジョブが共有する、GUIスレッド常駐のシグナル置き場。

    以前は ``QRunnable`` ごとに ``QObject`` を持たせていたが、
    ``setAutoDelete(True)`` のジョブはワーカースレッドで破棄されるため、
    GUIスレッドで生成した ``QObject`` を別スレッドで壊すことになっていた。
    Qt が禁じている操作で、接続解除とキュー配送が競合したときに
    ネイティブクラッシュを起こす。所有者が1つだけ持つ形にして経路ごと無くす。
    """

    folder_scanned = pyqtSignal(str, int, object)  # key, generation, Path | None
    cache_loaded = pyqtSignal(str, int, object, bool)  # key, generation, QImage|None, is_dir


class FolderScanJob(QRunnable):
    """Find the first supported media file directly inside a folder."""

    def __init__(
        self,
        key: str,
        path: Path,
        extensions: set[str],
        token: CancellationToken,
        signals: ThumbnailJobSignals,
        *,
        scan_limit: int = 128,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._key = key
        self._path = path
        self._extensions = extensions
        self._token = token
        self._scan_limit = max(1, scan_limit)
        self.signals = signals

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        image_path: Path | None = None
        try:
            for scanned, entry in enumerate(self._path.iterdir(), start=1):
                if self._token.cancelled:
                    return
                if entry.is_file() and entry.suffix.lower() in self._extensions:
                    image_path = entry
                    break
                if scanned >= self._scan_limit:
                    break
        except OSError:
            logger.exception("Failed to scan folder for thumbnail: %s", self._path)
            image_path = None
        if not self._token.cancelled:
            self.signals.folder_scanned.emit(self._key, self._token.generation, image_path)


class CacheLoadJob(QRunnable):
    """Load a cached PNG into QImage outside the UI thread."""

    def __init__(
        self,
        key: str,
        cache_path: Path,
        token: CancellationToken,
        signals: ThumbnailJobSignals,
        *,
        is_dir: bool = False,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._key = key
        self._cache_path = cache_path
        self._token = token
        self._is_dir = is_dir
        self.signals = signals

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        image: QImage | None = None
        if self._cache_path.exists() and not self._token.cancelled:
            loaded = QImage(str(self._cache_path), "PNG")
            if not loaded.isNull():
                image = loaded
                with suppress(OSError):
                    os.utime(self._cache_path, None)
            else:
                logger.warning("Failed to load thumbnail cache image: %s", self._cache_path)
        if not self._token.cancelled:
            self.signals.cache_loaded.emit(
                self._key,
                self._token.generation,
                image,
                self._is_dir,
            )


class CacheSaveJob(QRunnable):
    """Persist a thumbnail image without blocking repaint or scrolling."""

    def __init__(
        self,
        cache_path: Path,
        image: QImage,
        cleanup: Callable[[], None] | None = None,
        commit: Callable[[Path, Path], bool] | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._cache_path = cache_path
        self._image = image
        self._cleanup = cleanup
        self._commit = commit

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._cache_path.with_name(f"{self._cache_path.name}.{id(self)}.tmp")
        try:
            if not self._image.save(str(temp_path), "PNG"):
                return
            committed = (
                self._commit(temp_path, self._cache_path)
                if self._commit is not None
                else self._default_commit(temp_path, self._cache_path)
            )
            if committed and self._cleanup is not None:
                self._cleanup()
        finally:
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _default_commit(temp_path: Path, cache_path: Path) -> bool:
        temp_path.replace(cache_path)
        return True


def scaled_image(image: QImage, edge: int) -> QImage:
    if image.isNull():
        return image
    return image.scaled(
        edge,
        edge,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
