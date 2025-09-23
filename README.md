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
| Ctrl+Tab / Ctrl+Shift+Tab | 次/前のタブへ移動 |
| Ctrl+Shift+C | カラムビューとタブビューを切り替え |
| Backspace / Alt+Up | 親ディレクトリへ移動 |
| Alt+D | アドレスバーへフォーカスし、パスを全選択 |
| Ctrl+A | 全選択 |
| Ctrl+C / Ctrl+X / Ctrl+V | コピー / カット / ペースト |
| Delete | 選択項目を削除 |
| F2 | 選択項目をリネーム |
| Ctrl+N | 現在のフォルダに新規ファイルを作成 |
| Ctrl+Shift+N | 現在のフォルダに新規フォルダを作成 |
| F5 | 表示を更新 |

## その他の操作
- **ドラッグ＆ドロップ**: 選択したアイテムを別フォルダへドラッグすると移動。Ctrl キーを押しながらドロップするとコピー。
- **右クリックメニュー**: コピー / カット / ペースト / 削除 / リネームに加え、新しいファイル・フォルダ作成が可能。
- **ビュー切り替えボタン**: アドレスバー右側のボタンでタイル/リストの表示方法を即時に変更。

## はじめかた
```bash
poetry install
poetry run python main.py
```

必要に応じて `.venv` を利用した仮想環境でも動作します。
