"""Toolbar construction helpers for the file browser tab."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QToolButton


def _configure_arrow_button(
    button: QToolButton,
    *,
    text: str,
    accessible_name: str,
    tooltip: str,
) -> None:
    button.setText(text)
    button.setAccessibleName(accessible_name)
    button.setToolTip(tooltip)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    button.setFixedSize(QSize(28, 28))
    button.setStyleSheet(
        """
        QToolButton {
            font-size: 16px;
            font-weight: 600;
            padding: 0;
            border: 1px solid #3a3f46;
            border-radius: 4px;
            color: #e6e8eb;
            background: #24282e;
        }
        QToolButton:hover {
            background: #303640;
            border-color: #59616d;
        }
        QToolButton:pressed {
            background: #1c2026;
        }
        QToolButton:disabled {
            color: #6f7680;
            background: #1b1e23;
            border-color: #2b3037;
        }
        """
    )
