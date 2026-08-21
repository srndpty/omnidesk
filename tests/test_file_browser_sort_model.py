"""SortedFileSystemModel をタブ経由で動かす結合テスト。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt

from omnidesk.ui.file_browser import sort_model as sort_model_module
from omnidesk.ui.file_browser_tab import FileBrowserTab


def _make_files(directory: Path) -> None:
    (directory / "sub").mkdir()
    for name in ("b.txt", "a.png", "c.txt", "d.png"):
        (directory / name).write_text("x", encoding="utf-8")


def _visible_names(tab: FileBrowserTab) -> list[str]:
    model = tab._model
    root = model.index(str(tab.current_path()))
    names = []
    for row in range(model.rowCount(root)):
        index = model.index(row, 0, root)
        names.append(index.data(Qt.ItemDataRole.DisplayRole))
    return names


def _wait_for_entries(qtbot, tab: FileBrowserTab, expected: int) -> None:
    qtbot.waitUntil(lambda: len(_visible_names(tab)) >= expected, timeout=3000)


def test_default_sort_is_name_with_folders_first(qtbot, tmp_path: Path) -> None:
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    assert tab.sort_mode() == "name"
    names = _visible_names(tab)
    assert names[0] == "sub"  # フォルダが先頭
    assert names[1:] == ["a.png", "b.txt", "c.txt", "d.png"]


def test_extension_sort_groups_by_extension(qtbot, tmp_path: Path) -> None:
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    tab.set_sort_mode("extension")
    qtbot.waitUntil(
        lambda: _visible_names(tab) == ["sub", "a.png", "d.png", "b.txt", "c.txt"],
        timeout=3000,
    )


def test_sort_mode_can_toggle_back_to_name(qtbot, tmp_path: Path) -> None:
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    tab.set_sort_mode("extension")
    tab.set_sort_mode("name")
    assert tab.sort_mode() == "name"
    qtbot.waitUntil(
        lambda: _visible_names(tab) == ["sub", "a.png", "b.txt", "c.txt", "d.png"],
        timeout=3000,
    )


def test_menu_sort_overrides_prior_size_column_sort(qtbot, tmp_path: Path) -> None:
    # 「サイズ」列で並べ替えた後でも、メニューの「拡張子順」は名前列で並べ替える。
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.png").write_text("xxxxxxxxxx", encoding="utf-8")  # 大きい
    (tmp_path / "d.png").write_text("x", encoding="utf-8")  # 小さい
    (tmp_path / "b.txt").write_text("xxx", encoding="utf-8")
    (tmp_path / "c.txt").write_text("x", encoding="utf-8")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    # ヘッダーの「サイズ」列(1)で降順ソートしておく。
    tab._tree_view.sortByColumn(1, Qt.SortOrder.DescendingOrder)
    tab.set_sort_mode("extension")

    qtbot.waitUntil(
        lambda: _visible_names(tab) == ["sub", "a.png", "d.png", "b.txt", "c.txt"],
        timeout=3000,
    )
    # 以降の refresh でも拡張子順が維持される（ヘッダーが列0へ戻っている）。
    assert tab._tree_view.header().sortIndicatorSection() == 0


def test_reselecting_same_mode_restores_name_column_after_header_sort(
    qtbot, tmp_path: Path
) -> None:
    # 「名前順」のままサイズ列で並べ替えた後、メニューで同じ「名前順」を選び直すと
    # 名前列の並びへ戻る（同一方式の再選択でも列0へ戻す）。
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("xxxxxxxxxx", encoding="utf-8")  # 大きい
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")  # 小さい
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 3)

    assert tab.sort_mode() == "name"
    tab._tree_view.sortByColumn(1, Qt.SortOrder.AscendingOrder)
    # サイズ昇順では b.txt(小) が a.txt(大) より前に来る。
    qtbot.waitUntil(lambda: _visible_names(tab) == ["sub", "b.txt", "a.txt"], timeout=3000)

    tab.set_sort_mode("name")  # 同じ方式の再選択

    qtbot.waitUntil(lambda: _visible_names(tab) == ["sub", "a.txt", "b.txt"], timeout=3000)
    assert tab._header.sortIndicatorSection() == 0


def test_build_sort_menu_reflects_current_mode(qtbot, tmp_path: Path) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)

    tab.set_sort_mode("extension")
    menu = tab.build_sort_menu(tab)
    actions = menu.actions()
    labels = {action.text(): action.isChecked() for action in actions}
    assert labels == {"名前順": False, "拡張子順": True}


def test_entry_meta_is_built_once_per_row_during_sort(qtbot, tmp_path: Path, mocker) -> None:
    """並べ替えのメタdata生成が要素数に比例することを固定する。

    キャッシュが失われると比較のたびに ``QFileInfo`` を作り直し、大量ファイルの
    フォルダで GUI スレッドが固まる。比較回数は O(N log N) なので、生成回数が
    要素数を超えたらキャッシュが効いていない。
    """
    entry_count = 60
    for index in range(entry_count):
        (tmp_path / f"f{index:03d}.txt").write_text("x", encoding="utf-8")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, entry_count)

    tab._model._clear_meta_cache()
    spy = mocker.spy(sort_model_module, "_build_entry_meta")
    # invalidate() は set_sort_mode と同じく全件の再比較を強制する。
    tab._model.invalidate()
    tab._model.invalidate()

    assert spy.call_count > 0  # 実際に比較が走っていること
    assert spy.call_count <= entry_count


def test_thumbnail_only_data_changed_keeps_sort_cache(qtbot, tmp_path: Path) -> None:
    """サムネイル完成通知では並べ替えキャッシュを捨てない。

    ``DecorationRole`` だけの ``dataChanged`` でキャッシュを捨てると、
    サムネイル生成のたびにキャッシュが無効化されて意味がなくなる。
    """
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    tab._model.sort(0, Qt.SortOrder.AscendingOrder)
    assert tab._model._meta_cache

    source = tab._source_model
    index = source.index(str(tmp_path / "a.png"))
    source.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
    assert tab._model._meta_cache

    # 名前やサイズが変わり得る通知（ロール指定なし）では捨てる。ただし
    # 捨てるのは通知された行だけ（全体を捨てると再走査のたびに全件を作り直す）。
    # プロキシは通知を受けて並べ替え直すので、通知された行のメタdataは作り直され得る。
    # 見たいのは「他の行を巻き添えにしない」こと。
    before = dict(tab._model._meta_cache)
    source.dataChanged.emit(index, index, [])
    after = tab._model._meta_cache

    assert index.internalId() in before
    untouched = {
        entry_id: meta for entry_id, meta in before.items() if entry_id != index.internalId()
    }
    assert untouched
    for entry_id, meta in untouched.items():
        assert after[entry_id] is meta


def test_wide_data_changed_clears_the_whole_sort_cache(qtbot, tmp_path: Path) -> None:
    """広範囲の通知は、行単位で引くよりまとめて捨てる。

    範囲が広いと1行ずつ ``source.index()`` を引くほうが高くつく。
    """
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)
    tab._model.sort(0, Qt.SortOrder.AscendingOrder)
    assert tab._model._meta_cache

    source = tab._source_model
    root = source.index(str(tmp_path))
    # 5件のディレクトリで広範囲扱いになるよう、しきい値だけ下げる。
    tab._model.ROW_SCOPED_INVALIDATION_LIMIT = 1
    before = dict(tab._model._meta_cache)
    source.dataChanged.emit(source.index(0, 0, root), source.index(2, 0, root), [])
    after = tab._model._meta_cache

    # 通知範囲外の行も含めて、キャッシュはいちど全部捨てられている
    # （並べ替え直しで作り直された分は別インスタンスになる）。
    for entry_id, meta in before.items():
        assert after.get(entry_id) is not meta


def test_data_changed_with_unresolvable_range_clears_the_whole_sort_cache(
    qtbot, tmp_path: Path
) -> None:
    """範囲を絞り込めない通知では、安全側に倒して全部捨てること。"""
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)
    tab._model.sort(0, Qt.SortOrder.AscendingOrder)
    assert tab._model._meta_cache

    from PyQt6.QtCore import QModelIndex

    tab._source_model.dataChanged.emit(QModelIndex(), QModelIndex(), [])

    assert not tab._model._meta_cache


def test_externally_added_file_lands_in_the_right_position(qtbot, tmp_path: Path) -> None:
    """並べ替え済みのフォルダへ外部からファイルが増えても、位置が正しいこと。

    メタdata/ソートキーは ``internalId()`` で引くキャッシュに載せているため、
    行の増減で取り違えが起きないことを固定しておく。
    """
    for name in ("a.txt", "c.txt", "e.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 3)
    assert _visible_names(tab) == ["a.txt", "c.txt", "e.txt"]

    (tmp_path / "b.txt").write_text("x", encoding="utf-8")

    qtbot.waitUntil(
        lambda: _visible_names(tab) == ["a.txt", "b.txt", "c.txt", "e.txt"],
        timeout=10000,
    )


def test_deleting_then_adding_files_does_not_mix_up_metadata(qtbot, tmp_path: Path) -> None:
    """削除直後に別ファイルを追加しても、メタdataが取り違えられないこと。

    ``internalId()`` はノード破棄後に再利用され得るため、行削除ではキャッシュを
    まとめて捨てている。その前提が崩れていないかを確認する。
    """
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 3)

    (tmp_path / "b.txt").unlink()
    qtbot.waitUntil(lambda: _visible_names(tab) == ["a.txt", "c.txt"], timeout=10000)

    (tmp_path / "bb.txt").write_text("x", encoding="utf-8")

    qtbot.waitUntil(
        lambda: _visible_names(tab) == ["a.txt", "bb.txt", "c.txt"],
        timeout=10000,
    )
    # 名前とファイル情報の対応がずれていないこと。
    for name in ("a.txt", "bb.txt", "c.txt"):
        index = tab._model.index(str(tmp_path / name))
        assert index.isValid()
        assert tab._model.fileInfo(index).fileName() == name


def test_renaming_a_file_reorders_it(qtbot, tmp_path: Path) -> None:
    """リネームでも並び順とメタdataが追従すること。"""
    for name in ("a.txt", "m.txt", "z.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 3)

    (tmp_path / "m.txt").rename(tmp_path / "zz.txt")

    qtbot.waitUntil(
        lambda: _visible_names(tab) == ["a.txt", "z.txt", "zz.txt"],
        timeout=10000,
    )


def test_file_path_and_file_info_map_through_proxy(qtbot, tmp_path: Path) -> None:
    target = tmp_path / "a.png"
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    index = tab._model.index(str(target))
    assert index.isValid()
    assert Path(tab._model.filePath(index)) == target
    assert tab._model.fileInfo(index).fileName() == "a.png"


def test_removed_paths_are_hidden_before_the_source_model_catches_up(qtbot, tmp_path: Path) -> None:
    """削除済みの行を、元モデルの再走査を待たずに消すこと。

    元モデルはディレクトリの変更通知を受けてから走査し直すため、行が実際に消える
    までは待ちが入る（旧実装の QFileSystemModel では7,500件のフォルダで実測950ms）。
    ユーザーから見ると「OKを押したのに反映されない」ラグになる。
    """
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    hidden = tab._model.hide_removed_paths([tmp_path / "b.txt"])

    assert hidden == 1
    # 元モデルはまだ b.txt を持っているが、表示からは消えている。
    assert tab._source_model.index(str(tmp_path / "b.txt")).isValid()
    assert _visible_names(tab) == ["sub", "a.png", "c.txt", "d.png"]


def test_hiding_the_same_path_twice_does_not_refilter_again(qtbot, tmp_path: Path) -> None:
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    assert tab._model.hide_removed_paths([tmp_path / "b.txt"]) == 1
    assert tab._model.hide_removed_paths([tmp_path / "b.txt"]) == 0


def test_hidden_rows_are_released_when_the_source_model_drops_them(qtbot, tmp_path: Path) -> None:
    """元モデルが追いついたら、伏せる指定を持ち越さないこと。

    残したままだと、同名のファイルを作り直したときに見えなくなる。
    """
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    target = tmp_path / "b.txt"
    tab._model.hide_removed_paths([target])
    target.unlink()

    # 保険のタイマー（5秒）ではなく、元モデルの rowsRemoved で解除されること。
    qtbot.waitUntil(lambda: not tab._model._hidden_keys, timeout=3000)
    assert not tab._model._hidden_reconcile_timer.isActive()
    assert _visible_names(tab) == ["sub", "a.png", "c.txt", "d.png"]

    # 伏せる指定が外れているので、作り直せば再び現れる。
    target.write_text("x", encoding="utf-8")
    qtbot.waitUntil(lambda: "b.txt" in _visible_names(tab), timeout=5000)


def test_reconciliation_timeout_asks_for_a_rescan_instead_of_unhiding(
    qtbot, tmp_path: Path
) -> None:
    """決着がつかないとき、経過時間で伏せる指定を解除しないこと。

    元モデルの再走査が遅い（UNC、スピンダウンした外付け、遅いファイルサーバー）と、
    時間で解除する実装では削除済みのファイルが画面に戻り、走査が終わってから
    また消える、というちらつきになる。解除の判断は走査結果に委ねる。
    """
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)
    target = tmp_path / "b.txt"
    target.unlink()
    tab._model.hide_removed_paths([target])
    assert "b.txt" not in _visible_names(tab)
    # 走査が始まらないようにして、要求そのものだけを観察する
    # （＝再走査がいつまでも終わらない状況）。
    scans: list[bool] = []
    tab._source_model._start_scan = lambda: scans.append(True)

    tab._model._request_hidden_row_reconciliation()

    # 走査をやり直させるだけで、伏せた行は戻さない。
    assert scans == [True]
    assert "b.txt" not in _visible_names(tab)
    assert tab._model._hidden_keys


def test_hidden_rows_are_released_once_a_later_scan_reports(qtbot, tmp_path: Path) -> None:
    """伏せたあとに始まった走査が終われば、伏せる指定を解除すること。

    走査に残っているエントリは本当に残っている（削除に失敗した、作り直された）。
    """
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    # ファイルは消さずに伏せるだけ（＝削除に失敗した状況）。
    tab._model.hide_removed_paths([tmp_path / "b.txt"])
    assert "b.txt" not in _visible_names(tab)

    tab._source_model.refresh()

    qtbot.waitUntil(lambda: "b.txt" in _visible_names(tab), timeout=5000)
    assert not tab._model._hidden_keys


def test_a_scan_started_before_hiding_does_not_release_hidden_rows(qtbot, tmp_path: Path) -> None:
    """伏せる前に始まった走査の結果を、解除の根拠にしないこと。

    その走査はまだ削除前の状態を見ている可能性がある。判定は世代の比較だけで
    決まるので、実際の走査の速さに左右されないよう世代を直接与えて確かめる。
    """
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)
    tab._source_model.stop_watching()
    tab._model.hide_removed_paths([tmp_path / "b.txt"])
    hidden_at = tab._model._hidden_since_generation

    # 伏せた時点と同じ世代（＝伏せる前に始まっていた走査）の完了。
    tab._source_model._last_completed_generation = hidden_at
    tab._model._reconcile_hidden_rows()

    assert tab._model._hidden_keys
    assert "b.txt" not in _visible_names(tab)

    # 伏せたあとに始まった走査の完了なら、根拠になる。
    tab._source_model._last_completed_generation = hidden_at + 1
    tab._model._reconcile_hidden_rows()

    assert not tab._model._hidden_keys
    assert "b.txt" in _visible_names(tab)


def test_navigating_away_clears_hidden_rows(qtbot, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "b.txt").write_text("x", encoding="utf-8")
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)
    tab._model.hide_removed_paths([tmp_path / "b.txt"])

    tab.navigate_to(other)
    _wait_for_entries(qtbot, tab, 1)

    # 同名でも別ディレクトリのファイルは伏せない。
    assert tab._model._hidden_keys == set()
    assert _visible_names(tab) == ["b.txt"]


def test_filter_rejects_by_name_before_resolving_paths(qtbot, tmp_path: Path, mocker) -> None:
    """伏せていない行でフルパスを解決しないこと。

    ``invalidateFilter()`` は全行に対して ``filterAcceptsRow`` を呼ぶ。全行で
    パス正規化すると、7,500件でGUIスレッドを数十ms消費してしまう。
    """
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)
    spy = mocker.spy(sort_model_module, "navigation_key")

    tab._model.hide_removed_paths([tmp_path / "b.txt"])

    resolved = [call.args[0] for call in spy.call_args_list]
    # 伏せる対象の1件ぶんと、名前が一致した行の照合だけ。他の4行では解決しない。
    assert len(resolved) <= 2


def test_each_new_hide_advances_the_reconciliation_boundary(qtbot, tmp_path: Path) -> None:
    """伏せるたびに、解除の境界を現在の世代へ進めること。

    最初に伏せた時点で境界を固定すると、そのあとに始まった走査（＝2件目を削除する
    前の状態を見ている）が2件目まで解除してしまい、削除済みのファイルが一時的に
    再表示される。
    """
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)
    source = tab._source_model
    source.stop_watching()

    tab._model.hide_removed_paths([tmp_path / "b.txt"])
    first_boundary = tab._model._hidden_since_generation

    # 2件目を伏せる前に、走査が1つ始まったことにする。
    source._generation = first_boundary + 1
    tab._model.hide_removed_paths([tmp_path / "c.txt"])

    assert tab._model._hidden_since_generation == first_boundary + 1

    # その走査（＝2件目の削除前に始まったもの）の完了では解除しない。
    source._last_completed_generation = first_boundary + 1
    tab._model._reconcile_hidden_rows()

    assert tab._model._hidden_keys
    assert "b.txt" not in _visible_names(tab)
    assert "c.txt" not in _visible_names(tab)

    # さらに後で始まった走査の完了なら、まとめて解除できる。
    source._last_completed_generation = first_boundary + 2
    tab._model._reconcile_hidden_rows()

    assert not tab._model._hidden_keys


def test_deactivating_stops_the_reconciliation_timer(qtbot, tmp_path: Path) -> None:
    """見えていないタブでは、突き合わせタイマーも走査を起こさないこと。

    このタイマーは元モデルへ再走査を促すので、watcher とは別経路で
    「見えていないタブは監視も再走査もしない」という契約を破り得る。
    """
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)
    tab._model.hide_removed_paths([tmp_path / "b.txt"])
    assert tab._model._hidden_reconcile_timer.isActive()

    tab._model.stop_watching()
    assert not tab._model._hidden_reconcile_timer.isActive()

    generation_before = tab._source_model.scan_generation
    # 止まっていても、直接発火させて再走査が起きないことを確かめる。
    tab._model._hidden_reconcile_timer.timeout.emit()
    qtbot.wait(50)

    # タイマー経由の再走査は促されるが、監視が止まっている間は開始されない。
    assert tab._source_model.scan_generation == generation_before


def test_resuming_restarts_the_reconciliation_timer_when_rows_are_still_hidden(
    qtbot, tmp_path: Path
) -> None:
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)
    tab._model.hide_removed_paths([tmp_path / "b.txt"])
    tab._model.stop_watching()

    tab._model.resume_watching()

    assert tab._model._hidden_reconcile_timer.isActive()


def test_size_sorting_follows_an_external_size_change(qtbot, tmp_path: Path) -> None:
    """サイズ列で並べ替え中に中身が変わったら、表示順が追従すること。

    プロキシは ``dataChanged`` を受けて並べ替え直す。その前に古いソートキーを
    捨てておかないと、古いキーのまま位置が決まり、表示順が更新されないまま残る。
    """
    (tmp_path / "a.txt").write_bytes(b"x" * 10)
    (tmp_path / "b.txt").write_bytes(b"x" * 20)
    (tmp_path / "c.txt").write_bytes(b"x" * 30)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 3)
    tab._model.sort(1, Qt.SortOrder.AscendingOrder)
    assert _visible_names(tab) == ["a.txt", "b.txt", "c.txt"]

    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    tab._source_model.refresh()

    qtbot.waitUntil(
        lambda: _visible_names(tab) == ["b.txt", "c.txt", "a.txt"],
        timeout=5000,
    )
