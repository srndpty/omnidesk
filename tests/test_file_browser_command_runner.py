"""アドレスバーコマンド解析ヘルパーの純粋テスト。"""

from __future__ import annotations

import pytest

from omnidesk.ui.file_browser.command_runner import parse_address_command


def test_parse_address_command_splits_arguments() -> None:
    assert parse_address_command("zapall -f") == ["zapall", "-f"]


def test_parse_address_command_strips_surrounding_quotes() -> None:
    assert parse_address_command('"C:\\Program Files\\app.exe" --flag') == [
        "C:\\Program Files\\app.exe",
        "--flag",
    ]
    assert parse_address_command("'quoted value'") == ["quoted value"]


def test_parse_address_command_keeps_single_character_tokens() -> None:
    # 2文字未満のトークンはクォート除去の対象外。
    assert parse_address_command("a b") == ["a", "b"]


def test_parse_address_command_returns_empty_for_blank_input() -> None:
    assert parse_address_command("") == []


def test_parse_address_command_raises_on_unterminated_quote() -> None:
    with pytest.raises(ValueError):
        parse_address_command('open "unterminated')
