"""Small cancellable jobs used by the thumbnail pipeline."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, Qt, pyqtSignal
from PyQt6.QtGui import QImage


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


class FolderScanSignals(QObject):
    found = pyqtSignal(str, int, object)  # key, generation, Path | None


class FolderScanJob(QRunnable):
    """Find the first supported media file directly inside a folder."""

    def __init__(
        self,
        key: str,
        path: Path,
        extensions: set[str],
        token: CancellationToken,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._key = key
        self._path = path
        self._extensions = extensions
        self._token = token
        self.signals = FolderScanSignals()

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        image_path: Path | None = None
        try:
            entries = sorted(self._path.iterdir(), key=lambda p: p.name)
            for entry in entries:
                if self._token.cancelled:
                    return
                if entry.is_file() and entry.suffix.lower() in self._extensions:
                    image_path = entry
                    break
        except OSError:
            image_path = None
        if not self._token.cancelled:
            self.signals.found.emit(self._key, self._token.generation, image_path)


class CacheLoadSignals(QObject):
    loaded = pyqtSignal(str, int, object)  # key, generation, QImage | None


class CacheLoadJob(QRunnable):
    """Load a cached PNG into QImage outside the UI thread."""

    def __init__(self, key: str, cache_path: Path, token: CancellationToken) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._key = key
        self._cache_path = cache_path
        self._token = token
        self.signals = CacheLoadSignals()

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        image: QImage | None = None
        if self._cache_path.exists() and not self._token.cancelled:
            loaded = QImage(str(self._cache_path), "PNG")
            if not loaded.isNull():
                image = loaded
                try:
                    os.utime(self._cache_path, None)
                except OSError:
                    pass
        if not self._token.cancelled:
            self.signals.loaded.emit(self._key, self._token.generation, image)


class CacheSaveJob(QRunnable):
    """Persist a thumbnail image without blocking repaint or scrolling."""

    def __init__(
        self,
        cache_path: Path,
        image: QImage,
        cleanup: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._cache_path = cache_path
        self._image = image
        self._cleanup = cleanup

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        if self._image.save(str(self._cache_path), "PNG") and self._cleanup is not None:
            self._cleanup()


def scaled_image(image: QImage, edge: int) -> QImage:
    if image.isNull():
        return image
    return image.scaled(
        edge,
        edge,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
