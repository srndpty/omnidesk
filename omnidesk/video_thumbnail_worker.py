"""動画サムネイルをアプリ本体から分離して生成するワーカー。"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PyQt6.QtWidgets import QApplication

EXIT_TIMED_OUT = 4
# 親プロセスが持つ既定のタイムアウト（5秒）より十分長く、かつ無限には
# ならない値。親が異常終了しても、このワーカーが孤児として残り続けない。
SELF_TIMEOUT_MS = 30_000


class VideoThumbnailWorker(QObject):
    """動画の最初の有効フレームをPNGとして保存する。"""

    def __init__(self, source: Path, edge: int, output: Path) -> None:
        super().__init__()
        self._source = source
        self._edge = edge
        self._output = output
        self._complete = False
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(0.0)
        self._player.setAudioOutput(self._audio)
        self._sink = QVideoSink(self)
        self._player.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._handle_frame)

    def start(self) -> None:
        """動画の読み込みを開始する。"""
        # 有効なフレームが永遠に来ない動画（破損・未対応コーデックなど）でも
        # 必ず終了するよう、自前のタイムアウトを張る。親が先に落ちた場合に
        # 子プロセスが残り続けるのを防ぐ意味もある。
        QTimer.singleShot(SELF_TIMEOUT_MS, self._handle_timeout)
        self._player.setSource(QUrl.fromLocalFile(str(self._source)))
        self._player.setPosition(0)
        self._player.play()

    def _handle_timeout(self) -> None:
        if self._complete:
            return
        self._complete = True
        self._player.stop()
        QApplication.exit(EXIT_TIMED_OUT)

    def _handle_frame(self, frame) -> None:  # type: ignore[no-untyped-def]
        if self._complete or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        scaled = image.scaled(
            self._edge,
            self._edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._complete = True
        self._player.stop()
        exit_code = 0 if scaled.save(str(self._output), "PNG") else 3
        QApplication.exit(exit_code)


def run(arguments: list[str]) -> int:
    """コマンドライン引数を検証してワーカーを実行する。"""
    if len(arguments) != 3:
        return 2
    source = Path(arguments[0])
    output = Path(arguments[2])
    try:
        edge = int(arguments[1])
    except ValueError:
        return 2
    if not source.is_file() or edge <= 0:
        return 2

    existing = QApplication.instance()
    app = (
        existing if isinstance(existing, QApplication) else QApplication([sys.argv[0], *arguments])
    )
    worker = VideoThumbnailWorker(source, edge, output)
    QTimer.singleShot(0, worker.start)
    return app.exec()


def main() -> None:
    """モジュール実行用エントリポイント。"""
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
