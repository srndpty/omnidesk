from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from omnidesk.ui.tab_container import TabContainer


def test_tab_bar_is_not_closable_and_elides_from_right(qtbot) -> None:
    container = TabContainer()
    qtbot.addWidget(container)

    tab_bar = container._tabs.tabBar()

    assert not container._tabs.tabsClosable()
    assert tab_bar.elideMode() == Qt.TextElideMode.ElideRight


def test_close_current_tab_uses_shared_close_path(qtbot) -> None:
    container = TabContainer()
    qtbot.addWidget(container)

    container._tabs.addTab(QWidget(), "one")
    container._tabs.addTab(QWidget(), "two")
    container._tabs.setCurrentIndex(1)

    container.close_current_tab()

    assert container._tabs.count() == 1
    assert container._tabs.tabText(0) == "one"
