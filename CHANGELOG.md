# Changelog

## Unreleased

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
