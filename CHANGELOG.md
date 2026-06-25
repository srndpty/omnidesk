# Changelog

## Unreleased

- Added a Windows Explorer-style "並べ替え" (sort by) submenu to the file list context menu with mutually exclusive 名前順 / 拡張子順 options, implemented via a sort proxy model so files can be ordered by their true extension.
- Reorganized the main window features into a proper menu bar (ファイル / 編集 / 表示 / ヘルプ), removed the old toolbar, and pinned only the view-switch button to the top-right corner of the menu bar.
- Added project metadata, centralized pytest/coverage/Ruff configuration, and local pre-commit hooks.
- Added GitHub Actions CI for linting, tests, coverage, and Windows PyInstaller smoke builds.
- Added rotating file logging under the OmniDesk user configuration directory.
- Introduced typed settings and a file operation service layer while preserving existing behavior.
- Added per-tab back/forward navigation history for toolbar buttons, shortcuts, and mouse navigation buttons.
- Replaced tab navigation text buttons with arrow icon buttons and removed the main toolbar Go Up action.
- Added a tab context menu for pinning and closing tabs, with pinned tabs marked by an orange top accent.
- Prevented pinned tabs from being closed until they are unpinned.
- Persisted pinned tab state across app restarts.
- Added Ctrl+Shift+T to reopen the most recently closed tab.
- Added Backspace as a back-navigation shortcut and restored focus to the folder left behind when navigating back to its parent.
- Switched the standard Windows build to PyInstaller onedir output, added a separate onefile spec, and package the onedir build as a zip.
- Moved PyInstaller specs under packaging, added a PowerShell build script, and added a package module entry point.
