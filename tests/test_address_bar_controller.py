from __future__ import annotations

from pathlib import Path

from omnidesk.ui.file_browser import address_bar_controller as address_module
from omnidesk.ui.file_browser.address_bar_controller import AddressBarController


def _controller(
    mocker, qtbot, current_path: Path
) -> tuple[AddressBarController, dict[str, object]]:
    parent = address_module.QWidget()
    qtbot.addWidget(parent)
    calls = {
        "open_file": mocker.Mock(),
        "navigate_to": mocker.Mock(return_value=True),
        "show_warning": mocker.Mock(),
    }
    controller = AddressBarController(
        parent,
        current_path=lambda: current_path,
        open_file=calls["open_file"],
        navigate_to=calls["navigate_to"],
        show_warning=calls["show_warning"],
    )
    return controller, calls


def test_handle_text_opens_file_or_navigates_directory(mocker, qtbot, tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("sample", encoding="utf-8")
    directory = tmp_path / "folder"
    directory.mkdir()
    controller, calls = _controller(mocker, qtbot, tmp_path)

    controller.handle_text(str(file_path))
    controller.handle_text(str(directory))

    calls["open_file"].assert_called_once_with(file_path)
    calls["navigate_to"].assert_called_once_with(directory)


def test_handle_text_treats_missing_path_as_command(mocker, qtbot, tmp_path: Path) -> None:
    controller, _calls = _controller(mocker, qtbot, tmp_path)
    execute = mocker.patch.object(controller, "execute_command")

    controller.handle_text("missing-program --flag")

    execute.assert_called_once_with("missing-program --flag")


def test_execute_command_builds_cmd_batch_and_exe_arguments(
    mocker,
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    controller, _calls = _controller(mocker, qtbot, tmp_path)
    started = mocker.patch.object(
        address_module.QProcess,
        "startDetached",
        return_value=(True, 1234),
    )
    monkeypatch.setenv("COMSPEC", "C:\\Windows\\cmd.exe")

    controller.execute_command("cmd")
    mocker.patch.object(controller, "resolve_program", return_value=("C:\\tools\\job.cmd", True))
    controller.execute_command('job.cmd "two words"')
    controller.resolve_program.return_value = ("C:\\tools\\viewer.exe", False)
    controller.execute_command("viewer.exe --safe")

    assert started.call_args_list == [
        mocker.call("C:\\Windows\\cmd.exe", [], str(tmp_path)),
        mocker.call(
            "C:\\Windows\\cmd.exe", ["/C", "C:\\tools\\job.cmd", "two words"], str(tmp_path)
        ),
        mocker.call("C:\\tools\\viewer.exe", ["--safe"], str(tmp_path)),
    ]


def test_execute_command_reports_not_found(mocker, qtbot, tmp_path: Path) -> None:
    controller, calls = _controller(mocker, qtbot, tmp_path)
    mocker.patch.object(controller, "resolve_program", return_value=(None, False))
    started = mocker.patch.object(
        address_module.QProcess,
        "startDetached",
        return_value=(True, 1234),
    )

    controller.execute_command("missing")

    calls["show_warning"].assert_called_once_with(
        "Command not found",
        "'missing' is not found in current folder or PATH.",
    )
    started.assert_not_called()


def test_execute_command_reports_start_failure_for_all_launch_paths(
    mocker,
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    controller, calls = _controller(mocker, qtbot, tmp_path)
    mocker.patch.object(
        controller,
        "resolve_program",
        side_effect=[("C:\\tools\\job.cmd", True), ("C:\\tools\\viewer.exe", False)],
    )
    started = mocker.patch.object(
        address_module.QProcess,
        "startDetached",
        return_value=(False, 0),
    )
    monkeypatch.setenv("COMSPEC", "C:\\Windows\\cmd.exe")

    controller.execute_command("cmd")
    controller.execute_command("job.cmd")
    controller.execute_command("viewer.exe")

    assert calls["show_warning"].call_args_list == [
        mocker.call("Command", "Failed to start:\ncmd"),
        mocker.call("Command", "Failed to start:\njob.cmd"),
        mocker.call("Command", "Failed to start:\nviewer.exe"),
    ]
    assert started.call_count == 3
