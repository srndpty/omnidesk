## 基本方針
- 返答は日本語で行ってください。
- コードのコメント・docstring など、人間が読む想定の文章は日本語で統一してください。
- 変更前に周辺コードを読み、既存の設計・命名・テスト方針に合わせてください。
- 既存の未コミット変更は利用者の作業として扱い、明示依頼なしに戻さないでください。
- god class化を避け、UIイベント処理・純粋ロジック・ファイル操作・設定/ログ処理を適宜分離してください。
- 動作変更を伴うリファクタリングでは、先に小さなヘルパーへ切り出し、同等挙動をテストで固定してください。

## 品質ゲート
- 通常の作業完了前に、可能な範囲で `.\scripts\check.ps1` を実行してください。
- `scripts/check.ps1` は以下を順に実行します。
  - `pytest -q`
  - `python -m ruff check . --no-cache`
  - `python -m ruff format . --check`
  - `python -m pyright`
  - `git diff --check`
- 並列実行を試す場合は `.\scripts\check-parallel.ps1` を使ってください。ただし通常の品質ゲートは `check.ps1` を優先してください。Qtテストがあるため、xdistは安定性優先で `-n 2` に固定しています。
- `python -m pyright` が見つからない場合は、venv内で `python -m pip install -r requirements-dev.txt` を実行してください。
- カバレッジ確認が必要な場合は、PowerShellで以下を使ってください。
  - `$env:COVERAGE_FILE='tmp/.coverage'; pytest --cov=omnidesk --cov-report=term-missing --cov-report=xml:tmp\coverage.xml`
- Ruffの `.ruff_cache` 書き込み警告は、終了コードが0なら品質ゲート失敗として扱わなくてよいです。

## テスト方針
- UI全体のE2Eより、Qtイベントループを使う部品テストと、UIに依存しないヘルパーの純粋テストを優先してください。
- ファイル操作は `tmp_path` を使い、リポジトリ直下に一時ファイルを残さないでください。
- Qt signal待機は自前の `QEventLoop` より `pytest-qt` の `qtbot.waitSignal()` を優先してください。
- mock/patchには、読みやすくなる場合は `pytest-mock` の `mocker` fixtureを使ってください。
- 実ファイルシステムを使う必要がないファイル操作テストでは、`pyfakefs` の `fs` fixtureを検討してください。QtやOS連携が絡む場合は `tmp_path` のままで構いません。
- 時刻依存のログ・キャッシュ挙動は `freezegun.freeze_time()` で固定してください。
- `pytest-timeout` を導入済みです。長いQtテストが正当な場合だけ、局所的に `@pytest.mark.timeout(...)` を使ってください。
- テストダブルは実際に呼ばれるQt APIを満たしてください。たとえば `QFileInfo` の代替には `isDir()` だけでなく、必要に応じて `isFile()` や `absoluteFilePath()` も実装してください。
- 壊れやすいマウスドラッグや描画E2Eを増やすより、矩形交差判定・選択候補・パス解決などを小ヘルパーへ切り出してテストしてください。

## アーキテクチャ方針
- `FileBrowserTab` はQt widget、signal、view更新に寄せてください。
- ファイル操作は `omnidesk/ui/file_operations.py` へ集約し、UI側は短いエラー表示、サービス層は詳細ログを担当してください。
- パス解決・選択復元・メディア表示判定・D&D判定・可視アイテム計算は、既存の `file_browser_*.py` ヘルパーへ追加してください。
- 新しい危険操作の防御は、UI確認だけに頼らずサービス層でも拒否してください。
- サムネイル関連は、UIモデル・ジョブ管理・キャッシュの責務境界を保ってください。
- 型検査は `pyproject.toml` の `[tool.pyright]` に含まれる低依存モジュールから段階的に広げてください。

## ログとエラー処理
- デバッグ用の `print()` は追加しないでください。`logging.getLogger(__name__)` を使ってください。
- ログは `omnidesk/utils/logging_config.py` で設定され、既定では `~/.omnidesk/logs/omnidesk.log` に出力されます。
- ログレベルは `OMNIDESK_LOG_LEVEL` で変更できます。
- 例外を握りつぶす場合でも、ユーザー操作やファイル操作に関係する失敗はログに残してください。
- UIに出すメッセージは短く、詳細はログへ寄せてください。

## PyQt6 に関する注意
- PyQt6では `QFileSystemModel` と `QShortcut` は `PyQt6.QtGui` 内にあります。
  - NG: `from PyQt6.QtWidgets import QFileSystemModel`
  - OK: `from PyQt6.QtGui import QFileSystemModel`
- `PyQt6.QtConcurrent` は存在しません。非同期処理には `QThreadPool` + `QRunnable` などを使ってください。
- `QMediaPlayer` に `setMuted()` はありません。音声出力は `QAudioOutput` が担当します。
- `QMimeData` は `PyQt6.QtCore`、`QAction` は `PyQt6.QtGui` です。

## 依存とビルド
- 通常の開発環境は `python -m pip install -r requirements-dev.txt` で準備してください。
- 依存追加・更新は `requirements-dev.in` または `requirements.in` を編集し、`.\scripts\compile-requirements.ps1` で `requirements.txt` を再生成してください。
- CIはWindows上でRuff、Pyright、pytest、coverage、PyInstaller smoke buildを実行します。
- Windowsビルド確認は `.\scripts\build-windows.ps1` を使ってください。`build_windows.bat` は互換用ラッパーです。
- PyInstallerを手動実行する場合は以下を使ってください。
  - `pyinstaller --clean --noconfirm --workpath tmp\pyinstaller-build --distpath dist packaging\pyinstaller\OmniDesk.spec`
