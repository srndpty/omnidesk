# OmniDesk

OmniDesk is a dark-themed, multi-tab file manager for Windows powered by PyQt6. It combines a Windows Explorer feel with modern conveniences like rapid thumbnail generation, drag-and-drop workflows, and a switchable tile/list experience.

## 主な機能
- **マルチタブ UI**: それぞれのタブで独立したルートパスを保持し、ホイールクリックや Ctrl+W で手早く閉じることが可能。
- **タイル / リスト切り替え**: タイル表示ではメディアサムネイルを表示し、リスト表示では詳細情報を確認。切り替えボタンでワンクリック操作。
- **カラムビュー (Finder 風)**: Ctrl+Shift+C でタブビューとカラムビューを切り替え、階層を横方向に素早くナビゲート。
- **サムネイル最適化**: 画像・動画は非同期にロードされ、フォルダには子フォルダ内の最初のメディアのサムネイルを合成表示。
- **ドラッグ＆ドロップ / コンテキストメニュー**: ファイルの移動・コピー・削除・リネームをタイル / リストの両ビューで自然な操作感のまま実行可能。
- **ダークテーマと Windows タイトルバー適用**: アプリ全体とタイトルバーにダークテーマを適用し、統一感のある見た目を実現。

## ショートカット一覧
| ショートカット | アクション |
| --- | --- |
| Ctrl+T | 新しいタブを開く |
| Ctrl+W / Middle Click | 現在のタブを閉じる |
| Ctrl+Shift+T | 直近で閉じたタブを復元 |
| Ctrl+Tab / Ctrl+Shift+Tab | 次/前のタブへ移動 |
| Ctrl+Shift+C | カラムビューとタブビューを切り替え |
| Backspace / Alt+Left / Alt+Right | 戻る / 進む履歴へ移動 |
| Alt+D | アドレスバーへフォーカスし、パスを全選択 |
| Ctrl+A | 全選択 |
| Ctrl+C / Ctrl+X / Ctrl+V | コピー / カット / ペースト |
| Delete | 選択項目を削除 |
| F2 | 選択項目をリネーム |
| Ctrl+N | 現在のフォルダに新規ファイルを作成 |
| Ctrl+Shift+N | 現在のフォルダに新規フォルダを作成 |
| F5 | 表示を更新 |

## その他の操作
- **戻る / 進む**: パスバー左側の矢印ボタン、Alt+Left / Alt+Right、またはマウスの戻る / 進むボタンでタブ内履歴を移動。
- **親ディレクトリへ移動**: パスバー左側の上矢印ボタンで現在フォルダの親へ移動。
- **タブ右クリックメニュー**: タブをピン留めすると上端がオレンジ色になり、ピン留め中は閉じられない状態になります。ピン留め状態は次回起動時にも復元されます。
- **ドラッグ＆ドロップ**: 選択したアイテムを別フォルダへドラッグすると移動。Ctrl キーを押しながらドロップするとコピー。
- **右クリックメニュー**: コピー / カット / ペースト / 削除 / リネームに加え、新しいファイル・フォルダ作成が可能。
- **ビュー切り替えボタン**: アドレスバー右側のボタンでタイル/リストの表示方法を即時に変更。

## はじめかた
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m omnidesk
```

ログは既定で `~/.omnidesk/logs/omnidesk.log` に保存されます。詳細ログが必要な場合は `OMNIDESK_LOG_LEVEL=DEBUG` を設定してください。

すでに `.venv` を作成済みで `python -m pyright` が見つからない場合は、依存を更新してください。

```powershell
python -m pip install -r requirements-dev.txt
```

## テスト

pytest と pytest-qt を使った自動テストを用意しています。ハング対策として pytest-timeout も有効化しており、既定では各テスト30秒、セッション全体300秒で停止します。

CI相当の品質確認は次のスクリプトで一括実行できます。

```powershell
.\scripts\check.ps1
```

このスクリプトは以下を順番に実行し、失敗した時点で停止します。

- `pytest -q`
- `python -m ruff check . --no-cache`
- `python -m ruff format . --check`
- `python -m pyright`
- `git diff --check`

個別に実行する場合:

```powershell
python -m pytest
python -m ruff check . --no-cache
python -m ruff format . --check
python -m pyright
git diff --check
```

pytest-xdist による並列実行を試す場合:

```powershell
.\scripts\check-parallel.ps1
```

Qtを使うテストがあるため、このスクリプトは安定性優先で `-n 2` に固定しています。

依存を更新する場合は `requirements-dev.in` を編集し、pip-toolsで `requirements.txt` を再生成します。

```powershell
.\scripts\compile-requirements.ps1
```

テストでは必要に応じて `pytest-mock` の `mocker` fixture、`pyfakefs` の `fs` fixture、`freezegun.freeze_time()` を使っています。

型検査はまず副作用の薄いヘルパー層だけを対象にしています。対象は `pyproject.toml` の `[tool.pyright]` で段階的に広げます。

カバレッジを確認する場合:

```bash
pytest --cov=omnidesk --cov-report=term-missing
```

pre-commit を使う場合:

```powershell
pre-commit install
pre-commit run --all-files
```

古い非同期サムネイル確認スクリプトは `tests/manual/verify_async_behavior.py` に移動しています。

## Windows 向けビルド

1. 開発用依存パッケージをインストールします。
   ```bash
   pip install -r requirements-dev.txt
   ```
2. リポジトリのルートで PyInstaller を実行します。標準ビルドは起動速度を優先した onedir 形式です。
   ```bash
   pyinstaller --clean --noconfirm --workpath tmp\pyinstaller-build --distpath dist packaging\pyinstaller\OmniDesk.spec
   ```
   または `.\scripts\build-windows.ps1` を実行すると、Ruff・Pyright・pytest・PyInstaller の順に実行し、配布用の `dist/OmniDesk.zip` も生成します。`build_windows.bat` は互換用ラッパーとして同じスクリプトを呼び出します。
3. 成功すると `dist/OmniDesk/OmniDesk.exe` が生成されます。`.\scripts\build-windows.ps1` を実行した場合は、配布用の `dist/OmniDesk.zip` も生成されます。`_internal` フォルダも同じディレクトリに置いたまま配布してください。初回起動時は Windows SmartScreen により警告が表示される場合があります。
4. ローカル環境でビルド済みの `dist\OmniDesk` を `C:\Program Files\OmniDesk` に配置して使う場合は、次を実行します。未管理者権限で実行した場合は UAC で昇格してからコピーします。
   ```powershell
   .\install_windows.bat
   ```
   ビルドからインストールまで一度に行う場合は、ビルドを通常ユーザーで実行してからコピー時だけ昇格します。
   ```powershell
   .\install_windows.bat -Build
   ```
   既定では `C:\Program Files\OmniDesk` の中身を、現在の `dist\OmniDesk` の内容で置き換えます。既存の同名ディレクトリに手動で置いたファイルは削除されます。配置先を変える場合は、`Program Files` 直下の `OmniDesk` または `OmniDesk-*` という名前のアプリ用ディレクトリを指定してください。`C:\Program Files` 自体、他アプリのディレクトリ、他アプリ配下のディレクトリは指定できません。
   ```powershell
   .\install_windows.bat -Destination "C:\Program Files\OmniDesk-dev"
   ```
5. 単体exeが必要な場合は、別ターゲットとして次を実行します。
   ```bash
   pyinstaller --clean --noconfirm --workpath tmp\pyinstaller-build-onefile --distpath dist packaging\pyinstaller\OmniDesk-onefile.spec
   ```
   成功すると `dist/OmniDesk-onefile.exe` が生成されます。単体exeは配布しやすい一方、起動時に一時展開が必要なため onedir 形式より起動が遅くなることがあります。

## リリース手順

1. `.\scripts\check.ps1`
2. `python -m pytest --cov=omnidesk --cov-report=term-missing`
3. `.\scripts\build-windows.ps1`
4. `CHANGELOG.md` を更新し、生成された `dist/OmniDesk/OmniDesk.exe` と `dist/OmniDesk.zip` を確認します。
