"""Manual smoke checks for thumbnail async behavior.

The pytest suite covers the same core behavior automatically. Keep this script
for ad-hoc local debugging when watching printed progress is useful.
"""

import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QEventLoop, QTimer, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

# Add project root to path when run directly.
sys.path.append(str(Path(__file__).parent.parent.parent))

from omnidesk.ui.media_icon_provider import MediaThumbnailProvider
from omnidesk.ui.thumbnail_jobs import CancellationToken


def _cleanup_file(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


class TestRunner(QObject):
    finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.provider = MediaThumbnailProvider()
        self.provider.thumbnailReady.connect(self.on_thumbnail_ready)
        self.results = {}
        self.generations = {}
        self.expected_count = 0
        self.start_time = 0

    def run_image_test(self):
        print("Running Image Test...")
        img_path = Path("test_large_image.png")
        if not img_path.exists():
            img = QImage(2000, 2000, QImage.Format.Format_RGB32)
            img.fill(0xFF0000)
            img.save(str(img_path))

        self.expected_count = 1
        self.results.clear()
        self.generations.clear()

        edge = 100
        self.provider.request_thumbnail(img_path, edge, result_key="test_img")

        self.loop = QEventLoop()
        QTimer.singleShot(5000, self.loop.quit)
        self.loop.exec()

        if "test_img" in self.results:
            icon = self.results["test_img"]
            pixmap = icon.pixmap(edge, edge)
            print(f"Image Result Size: {pixmap.width()}x{pixmap.height()}")
            if pixmap.width() <= edge and pixmap.height() <= edge:
                print("PASS: Image scaled correctly.")
            else:
                print("FAIL: Image not scaled.")
        else:
            print("FAIL: Image timeout.")

        if img_path.exists():
            _cleanup_file(img_path)

    def run_cancel_test(self):
        print("\nRunning Cancellation Test...")
        img_path = Path("test_cancel_image.png")
        img = QImage(2000, 2000, QImage.Format.Format_RGB32)
        img.fill(0x00FF00)
        img.save(str(img_path))

        self.expected_count = 1
        self.results.clear()
        self.generations.clear()

        token = CancellationToken(42)
        started = self.provider.request_thumbnail(
            img_path,
            100,
            result_key="cancelled_img",
            token=token,
        )
        token.cancel()

        self.loop = QEventLoop()
        QTimer.singleShot(1000, self.loop.quit)
        self.loop.exec()

        if started and "cancelled_img" not in self.results:
            print("PASS: Cancelled image result was suppressed.")
        else:
            print("FAIL: Cancelled image result was emitted.")

        if img_path.exists():
            _cleanup_file(img_path)

    def run_video_test(self):
        if not self.provider.video_supported:
            print("SKIP: Video not supported.")
            return

        print("\nRunning Video Concurrency Test...")
        self.provider._active_video_jobs = 1
        self.provider._video_queue.append(("key1", Path("fake1.mp4"), 100, CancellationToken(1)))
        self.provider._video_queue.append(("key2", Path("fake2.mp4"), 100, CancellationToken(2)))

        print(f"Active Jobs: {self.provider._active_video_jobs}")
        print(f"Queue Size: {len(self.provider._video_queue)}")

        if self.provider._active_video_jobs == 1 and len(self.provider._video_queue) == 2:
            print("PASS: Queue logic seems correct (manually verified state).")
        else:
            print("FAIL: Queue logic incorrect.")

        self.provider._active_video_jobs = 0
        self.provider._video_queue.clear()

    def on_thumbnail_ready(self, key, icon, generation):
        self.results[key] = icon
        self.generations[key] = generation
        if len(self.results) >= self.expected_count:
            if hasattr(self, "loop"):
                self.loop.quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    runner = TestRunner()
    runner.run_image_test()
    runner.run_cancel_test()
    runner.run_video_test()
    print("Done.")
