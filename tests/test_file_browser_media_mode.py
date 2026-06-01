"""Tests for file_browser_media_mode helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QSize

from omnidesk.ui.file_browser_media_mode import calculate_grid_size, is_media_heavy_directory


class TestCalculateGridSize:
    def test_basic(self) -> None:
        result = calculate_grid_size(96, 18)
        assert result == QSize(96 + 24, 96 + 24 + 18 * 2)

    def test_single_text_line(self) -> None:
        result = calculate_grid_size(64, 16, text_lines=1)
        assert result == QSize(64 + 24, 64 + 24 + 16)

    def test_custom_padding(self) -> None:
        result = calculate_grid_size(128, 20, padding=16)
        assert result == QSize(128 + 16, 128 + 16 + 20 * 2)

    def test_zero_edge(self) -> None:
        result = calculate_grid_size(0, 14)
        assert result == QSize(24, 24 + 14 * 2)


class TestIsMediaHeavyDirectory:
    EXTENSIONS = {".jpg", ".jpeg", ".png", ".mp4"}

    def test_returns_false_for_empty_directory(self, tmp_path: Path) -> None:
        assert not is_media_heavy_directory(
            tmp_path, self.EXTENSIONS, ratio_threshold=0.5, min_count=3, scan_limit=128
        )

    def test_returns_false_for_no_media_files(self, tmp_path: Path) -> None:
        for i in range(5):
            (tmp_path / f"doc{i}.txt").touch()
        assert not is_media_heavy_directory(
            tmp_path, self.EXTENSIONS, ratio_threshold=0.5, min_count=3, scan_limit=128
        )

    def test_returns_true_when_above_ratio_threshold(self, tmp_path: Path) -> None:
        for i in range(8):
            (tmp_path / f"img{i}.jpg").touch()
        for i in range(2):
            (tmp_path / f"doc{i}.txt").touch()
        assert is_media_heavy_directory(
            tmp_path, self.EXTENSIONS, ratio_threshold=0.5, min_count=3, scan_limit=128
        )

    def test_returns_false_when_below_ratio_threshold(self, tmp_path: Path) -> None:
        for i in range(3):
            (tmp_path / f"img{i}.jpg").touch()
        for i in range(17):
            (tmp_path / f"doc{i}.txt").touch()
        assert not is_media_heavy_directory(
            tmp_path, self.EXTENSIONS, ratio_threshold=0.5, min_count=3, scan_limit=128
        )

    def test_returns_true_when_below_min_count(self, tmp_path: Path) -> None:
        # total_files <= min_count → return True (even if ratio is not checked)
        for i in range(2):
            (tmp_path / f"img{i}.jpg").touch()
        assert is_media_heavy_directory(
            tmp_path, self.EXTENSIONS, ratio_threshold=0.9, min_count=3, scan_limit=128
        )

    def test_returns_false_when_media_count_below_min_count(self, tmp_path: Path) -> None:
        for i in range(2):
            (tmp_path / f"img{i}.jpg").touch()
        for i in range(10):
            (tmp_path / f"doc{i}.txt").touch()
        assert not is_media_heavy_directory(
            tmp_path, self.EXTENSIONS, ratio_threshold=0.5, min_count=3, scan_limit=128
        )

    def test_obeys_scan_limit(self, tmp_path: Path) -> None:
        # Exactly scan_limit files, all media → total_files==scan_limit==min_count → True
        scan_limit = 6
        for i in range(scan_limit):
            (tmp_path / f"img{i}.jpg").touch()
        result = is_media_heavy_directory(
            tmp_path,
            self.EXTENSIONS,
            ratio_threshold=0.5,
            min_count=scan_limit,
            scan_limit=scan_limit,
        )
        assert result is True

    def test_scan_limit_stops_iteration_early(self) -> None:
        """scan_limit=3 の場合、4件目を読もうとしてはならない（逐次走査の退行検出）。"""
        read_count = 0

        def _make_entry(name: str) -> MagicMock:
            e = MagicMock()
            e.is_file.return_value = True
            e.suffix = Path(name).suffix
            return e

        def _counting_iter():
            nonlocal read_count
            for name in ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg", "f.jpg"]:
                read_count += 1
                if read_count > 3:
                    raise AssertionError(
                        f"Read {read_count} entries — scan_limit=3 was not respected"
                    )
                yield _make_entry(name)

        with patch.object(Path, "iterdir", return_value=_counting_iter()):
            is_media_heavy_directory(
                Path("/fake"),
                self.EXTENSIONS,
                ratio_threshold=0.5,
                min_count=3,
                scan_limit=3,
            )

        assert read_count == 3

    def test_handles_os_error(self, tmp_path: Path) -> None:
        non_existent = tmp_path / "does_not_exist"
        assert not is_media_heavy_directory(
            non_existent, self.EXTENSIONS, ratio_threshold=0.5, min_count=3, scan_limit=128
        )

    def test_extension_matching_is_case_insensitive(self, tmp_path: Path) -> None:
        (tmp_path / "IMAGE.JPG").touch()
        (tmp_path / "image.PNG").touch()
        assert is_media_heavy_directory(
            tmp_path, self.EXTENSIONS, ratio_threshold=0.5, min_count=3, scan_limit=128
        )
