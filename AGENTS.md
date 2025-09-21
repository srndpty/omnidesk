## 依頼者からの要望
- 返答は日本語で行ってください
- 適宜コードのファイルを分割し、god classにならないようにしてください

## PyQt6 に関する注意
- PyQt6 では QFileSystemModelとQShortcut は PyQt6.QtGui 内に移動されているため留意してください。
  NG: from PyQt6.QtWidgets import QFileSystemModel
  OK: from PyQt6.QtGui import QFileSystemModel
- `PyQt6.QtConcurrent` は存在しません。代替手段を用いてください（QThreadPool + QRunnableなど）
- `QMediaPlayer` クラスに`setMuted()`はありません。PyQt6ではマルチメディア関連のアーキテクチャが変更されました。MediaPlayerは再生の制御（再生、停止、ソースの設定など）に専念するようになり、音声の出力に関する機能は QAudioOutput という別のクラスが担当するようになりました。
- QMimeDataはPyQt6.QtCore、QActionはPyQt6.QtGuiの中です