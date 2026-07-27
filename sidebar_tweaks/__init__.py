# SPDX-FileCopyrightText: 2026 れいんつ
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""サイドバー(Nパネル)のタブの並び順を入れ替える。

UIは 3Dビューポート > サイドバー(N) > View > 「サイドバーのタブ順」。
バージョンと対応Blenderは blender_manifest.toml が持つ。"""

import json
import traceback

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    StringProperty,
)
from bpy.app.translations import pgettext_iface as _iface
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup, UIList


# ---------------------------------------------------------------- 仕組みのメモ
#
# Blender 5.1 のソースで裏を取った内容。
#
# ■ 動く仕組み
# サイドバーのタブ順は、リージョンが持つパネル型リスト(paneltypes)の並びそのままで決まる。
# 各カテゴリのタブは、そのカテゴリを持つパネル型がリストに最初に現れた位置に置かれる
# (area.cc の region_panels_collect_categories は BLI_addtail のみでソートしない。
#  特定のカテゴリを先頭に固定するような特別扱いも無い)。
# paneltypes への挿入位置は bl_order の昇順で、同じ値の中では登録した順に後ろへ入る。
# つまり、並べたい順に bl_order を連番で振り直してその順に登録し直せばタブ順を作れる。
#
# カテゴリを名乗れるのは「トップレベルかつ poll を通ったパネル」だけ
# (area.cc の panel_add_check が if (panel_type->parent) return false; で弾く)。
# どのパネルが poll を通るかはモードや選択で変わるが、カテゴリごとに連続した bl_order を
# 振っている限りカテゴリ間の相対順は保たれる。
#
# タブ「内」のパネルの上下順は bl_order ではなくパネル構造体の sortorder が決めており、
# これは bl_idname をキーに .blend へ保存される。登録解除しても構造体は名前ごと残るため、
# 同名で登録し直す限りタブ内の並びと開閉状態は保たれる。
#
# ■ 標準タブを動かせない理由
# 1. 「アイテム」タブの主要パネルは C 側で登録されていて Python から見えない。
#    view3d_buttons.cc の view3d_buttons_register() が VIEW3D_PT_transform /
#    VIEW3D_PT_vgroup / VIEW3D_PT_curves を category="Item" で paneltypes の先頭に足す。
#    ソースにも "XXX C panels unavailable through RNA bpy.types!" と明記があり、
#    実際 hasattr(bpy.types, "VIEW3D_PT_transform") は False。登録解除も bl_order 変更も
#    できないので、アイテムタブは動かせない。
# 2. その前に割り込むこともできない。bl_order は PROP_UNSIGNED(hard_min=0)で負値を取れず、
#    挿入ループ(rna_ui.cc)が if (pt_iter->order <= pt->order) break; と同値で break するため、
#    order=0 で登録し直しても必ず C 実装パネルの後ろに入る。
# 3. 唯一の抜け道は使えない。同ループの
#    if ((pt->flag & PANEL_TYPE_NO_HEADER) && !(pt_iter->flag & ...)) continue;
#    により bl_options={'HIDE_HEADER'} のパネルは bl_order を無視して最前列へ飛ぶが、これは
#    順序制御ではなく強制ジャンプで、同じ手を使うアドオン同士が潰し合う。Blender 自身が
#    bl_ui/space_view3d.py に FIXME を残している既知の不整合。
#
# よって標準タブ(Item / Tool / View / Animation …)は常に対象外とし、一覧でも順序を主張しない。


# UI文字列は英語で書き、日本語は _TRANSLATIONS で訳す（Blenderの言語設定に従う）
_SPACES = (
    ("VIEW_3D", "3D Viewport", ""),
    ("IMAGE_EDITOR", "Image / UV Editor", ""),
    ("NODE_EDITOR", "Node Editor", ""),
    ("CLIP_EDITOR", "Movie Clip Editor", ""),
    ("SEQUENCE_EDITOR", "Video Sequencer", ""),
    ("DOPESHEET_EDITOR", "Dope Sheet", ""),
    ("GRAPH_EDITOR", "Graph Editor", ""),
    ("NLA_EDITOR", "Nonlinear Animation", ""),
)
_SPACE_IDS = tuple(item[0] for item in _SPACES)

# {スペース種別: [カテゴリ名, ...]} ユーザーが決めた並び順
_ORDERS = {}

# [(クラス, 元のbl_order), ...] 最初に触る前の状態。「既定に戻す」で使う
_SNAPSHOT = []
_SNAPSHOT_IDS = set()

# 再登録に失敗して未登録のまま残ったクラスの id。復元のときにこれを見て、
# 利用者が自分で無効化したアドオンのパネルまで復活させないようにする
_ORPHANS = set()

# つまんで移動中か。パネルに「移動中」と出すために使う
_GRABBING = False

# 一覧の先頭に置く、動かせない標準タブをまとめた行のラベルと、動かそうとしたときの説明
_LOCKED_LABEL = "Built-in tabs ({})"
_LOCKED_HINT = "Built-in tabs cannot be moved. They always come first"

# プリファレンスを引けないとき（バックグラウンド実行など）に使う既定値
_FALLBACK = {"apply_on_startup": True}

# 日本語訳。キーは上と各クラスで書いた英語の文字列そのもの
_TRANSLATIONS = {
    "ja_JP": {
        # この2つは Blender 本体にも同じ原文があり、本体の訳語が優先される。
        # 辞書を読んだときに実際の表示とずれないよう、本体に合わせて書いておく
        ("*", "3D Viewport"): "3Dビューポート",
        ("*", "Image / UV Editor"): "画像 / UVエディター",
        ("*", "Node Editor"): "ノードエディター",
        ("*", "Movie Clip Editor"): "動画クリップエディター",
        ("*", "Video Sequencer"): "ビデオシーケンサー",
        ("*", "Dope Sheet"): "ドープシート",
        ("*", "Graph Editor"): "グラフエディター",
        ("*", "Nonlinear Animation"): "ノンリニアアニメーション",

        ("*", "Built-in tabs ({})"): "標準タブ ({})",
        ("*", "Built-in tabs cannot be moved. They always come first"):
            "標準タブは動かせません。常にサイドバーの先頭に並びます",

        ("*", "Sidebar Tab Order"): "サイドバーのタブ順",
        ("*", "Options"): "オプション",
        ("*", "Editor to Reorder"): "並び替えるエディタ",
        ("*", "Reorder to lock in this order"): "並び替えると、この順序で確定します",
        ("*", "Moving: click to confirm, Esc to cancel"):
            "移動中  クリックで確定 / Escで取り消し",
        ("*", "Some tabs may not appear in the sidebar"): "サイドバーに出ていないタブもあります",
        ("*", "Reorder from 3D Viewport > Sidebar (N) > View tab"):
            "並び替えは 3Dビューポート > サイドバー(N) > View タブから行います",

        ("*", "Target Editor"): "対象エディタ",
        ("*", "Editor whose tab order is being changed. All editors are set from this panel"):
            "タブ順を並び替えるエディタ。このパネルから全エディタを設定する",
        ("*", "Tab Name"): "タブ名",
        ("*", "Locked"): "固定",
        ("*", "Saved Order"): "保存された並び順",
        ("*", "Tab order per editor, kept as JSON"): "エディタごとのタブ順をJSONで保持する",
        ("*", "Apply on Startup"): "起動時に自動で適用する",
        ("*", "Apply the saved order at startup, after all add-ons are loaded"):
            "Blender起動時、全アドオンの読み込み後に保存した並び順を反映する",

        # オペレーターの bl_label はボタンに出るとき "Operator" コンテキストで引かれる。
        # "*" だけでは英語のまま出るので、ラベルは両方に入れておく
        ("Operator", "Refresh List"): "一覧を更新",
        ("Operator", "Move Tab"): "タブを移動",
        ("Operator", "Grab and Move"): "つまんで移動",
        ("Operator", "Apply Order"): "並び順を適用",
        ("Operator", "Reset to Default"): "既定に戻す",

        ("*", "Refresh List"): "一覧を更新",
        ("*", "Rebuild the list from the tabs currently registered"):
            "今登録されているタブを読み直して一覧を作り直す",
        ("*", "Move Tab"): "タブを移動",
        # この2つも Blender 本体の訳語が優先される。実際の表示に合わせておく
        ("*", "Up"): "上",
        ("*", "Down"): "下",
        ("*", "Move the selected tab up or down and apply it right away"):
            "選択中のタブを上下に移動して、その場で並びを反映する",
        ("*", "Grab and Move"): "つまんで移動",
        ("*", "Click first, then move (not click-and-hold). "
              "Mouse, wheel or arrow keys to move, LMB or Enter to confirm, "
              "RMB or Esc to cancel"):
            "クリックしてから動かす（押しっぱなしではない）。"
            "マウス上下・ホイール・↑↓ で移動し、左クリックかEnterで確定"
            "（右クリックまたはEscで取り消し）",
        ("*", "Apply Order"): "並び順を適用",
        ("*", "Reorder the sidebar tabs to match the list"):
            "一覧の順にサイドバーのタブを並べ直す",
        ("*", "Reset to Default"): "既定に戻す",
        ("*", "Discard the saved order and restore the state from before this session"):
            "保存した並び順を捨てて、このセッションで変更する前の状態に戻す",

        ("*", "Mouse, wheel or arrows: move  |  LMB, Enter: confirm  |  RMB, Esc: cancel"):
            "マウス上下・ホイール・↑↓ で移動  |  左クリック/Enter: 確定  |  右クリック/Esc: 取り消し",
        ("*", "Reordered {} panels"): "{} 枚のパネルを並べ直しました",
        ("*", "Reordered {} panels, but {} could not be updated (see console)"):
            "{} 枚を並べ替えましたが {} 枚を更新できませんでした（詳細はコンソール）",
        ("*", "Restored {} panels, but {} could not be updated (see console)"):
            "{} 枚を元に戻しましたが {} 枚を更新できませんでした（詳細はコンソール）",
        ("*", "Failed to reorder: {}"): "並べ替えに失敗しました: {}",
        ("*", "Restored {} panels (restart Blender to fully return to the startup order)"):
            "{} 枚を元に戻しました（起動時の並びに完全に戻すには Blender を再起動してください）",
        ("*", "Cleared the saved order (defaults apply from the next startup)"):
            "保存した並び順を消しました（次回起動時から既定の並びになります）",
        ("*", "Sidebar Tweaks: {} panels could not be restored. "
              "Restart Blender to get the original order back."):
            "Sidebar Tweaks: {} 枚のパネルを元に戻せませんでした。"
            "Blender を再起動すると元の並びに戻ります。",
    },
}


# ---------------------------------------------------------------- パネルの収集

def _prefs():
    try:
        return bpy.context.preferences.addons[__name__].preferences
    except (KeyError, AttributeError):
        return None


def _opt(name):
    prefs = _prefs()
    if prefs is None:
        return _FALLBACK[name]
    return getattr(prefs, name, _FALLBACK[name])


def _builtin(cls):
    """Blender 標準UI由来のパネルか"""
    return cls.__module__.split(".")[0] in {"bl_ui", "bpy_types"}


def _idname(cls):
    """登録名。bl_idname 未指定のクラスはクラス名が登録名になる"""
    name = getattr(cls, "bl_idname", "")
    if not name:
        rna = getattr(cls, "bl_rna", None)
        name = getattr(rna, "identifier", "") if rna is not None else ""
    return name or cls.__name__


def _iter_panels():
    """登録済みのPanelサブクラスを、クラス定義順に近い順で列挙する"""
    queue = list(bpy.types.Panel.__subclasses__())
    seen = set()
    while queue:
        cls = queue.pop(0)
        key = id(cls)
        if key in seen:
            continue
        seen.add(key)
        queue.extend(cls.__subclasses__())
        yield cls


def _collect(space_type):
    """指定エディタのサイドバーのパネルを集める。

    戻り値は (全パネル, {カテゴリ: [パネル]}, {パネル: 初出インデックス})。
    カテゴリ無しのパネルはタブを作らないが、bl_order の基準値を決めるのに使うため
    第1要素には含める。"""
    sidebar = []
    index = {}
    for cls in _iter_panels():
        if not getattr(cls, "is_registered", False):
            continue
        if getattr(cls, "bl_space_type", "") != space_type:
            continue
        if getattr(cls, "bl_region_type", "") != "UI":
            continue
        index[cls] = len(sidebar)
        sidebar.append(cls)

    by_id = {_idname(cls): cls for cls in sidebar}

    def category_of(cls):
        """カテゴリを持たないサブパネルは、親をたどってそのタブの一員として扱う。

        親だけを動かして子を置き去りにすると、親の登録解除中に子が宙に浮く。"""
        seen = set()
        cur = cls
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            cat = getattr(cur, "bl_category", "")
            if cat:
                return cat
            cur = by_id.get(getattr(cur, "bl_parent_id", ""))
        return ""

    groups = {}
    for cls in sidebar:
        cat = category_of(cls)
        if cat:
            groups.setdefault(cat, []).append(cls)
    return sidebar, groups, index


def _locked_categories(groups):
    """標準UI由来のパネルを含むカテゴリ。既定では並び替えの対象外"""
    return {cat for cat, classes in groups.items() if any(_builtin(c) for c in classes)}


def _guess_order(groups, index):
    """現在のタブ順の推定値。

    paneltypes の実際の並びは Python から読めないため、Blender が挿入位置を決めるのに
    使う bl_order と登録順（クラス定義順で代用）から組み立てる。一度「適用」すれば
    保存した並びが実際の並びと一致するので、この推定が要るのは初回だけ。"""
    def key(cat):
        classes = groups[cat]
        # タブ位置を決めるのはトップレベルのパネル。サブパネルの小さい bl_order に
        # 引きずられないよう除いて見る
        tops = [c for c in classes
                if getattr(c, "bl_category", "") and not getattr(c, "bl_parent_id", "")]
        if not tops:
            tops = classes
        return (min(getattr(c, "bl_order", 0) for c in tops),
                min(index[c] for c in tops))
    return sorted(groups, key=key)


def _flatten(classes, index):
    """カテゴリ内のパネルを「親 → その子」の順に並べた平坦なリストにする。

    サブパネルは親より後に登録しないと bl_parent_id を解決できず、また親より前に
    paneltypes へ入るとタブ位置を先に決めてしまうため、必ず親の直後に置く。"""
    by_id = {}
    for cls in classes:
        by_id[_idname(cls)] = cls

    children = {}
    roots = []
    for cls in classes:
        parent = getattr(cls, "bl_parent_id", "")
        if parent and parent in by_id and by_id[parent] is not cls:
            children.setdefault(parent, []).append(cls)
        else:
            roots.append(cls)

    def sort_key(cls):
        return (getattr(cls, "bl_order", 0), index.get(cls, 0))

    out = []

    def walk(cls):
        out.append(cls)
        for child in sorted(children.get(_idname(cls), ()), key=sort_key):
            if child not in out:
                walk(child)

    for cls in sorted(roots, key=sort_key):
        walk(cls)

    # 親子関係が循環していて漏れたものは末尾へ
    for cls in classes:
        if cls not in out:
            out.append(cls)
    return out


def _parent_first(seq):
    """親が子より前に来るよう並べ直す（それ以外の順序は保つ）。

    サブパネルは親が登録済みでないと登録に失敗する。bl_order を元の値へ戻すときや、
    親が別カテゴリにあるときは _flatten だけでは順序を保証できないため最後に通す。"""
    by_id = {_idname(cls): cls for cls in seq}
    out = []
    placed = set()
    visiting = set()

    def place(cls):
        key = id(cls)
        if key in placed or key in visiting:
            return
        visiting.add(key)
        parent = getattr(cls, "bl_parent_id", "")
        if parent and parent in by_id and by_id[parent] is not cls:
            place(by_id[parent])
        visiting.discard(key)
        if key not in placed:
            placed.add(key)
            out.append(cls)

    for cls in seq:
        place(cls)
    return out


# ---------------------------------------------------------------- 並べ替え本体

def _snapshot(classes):
    for cls in classes:
        if id(cls) not in _SNAPSHOT_IDS:
            _SNAPSHOT_IDS.add(id(cls))
            _SNAPSHOT.append((cls, getattr(cls, "bl_order", 0)))


def _reregister(seq):
    """seq の順に paneltypes へ並ぶよう、全部まとめて登録し直す。

    先に全部を登録解除してから順に登録し直す。途中で失敗してもパネルが消えたまま
    残らないよう、失敗したクラスは元の bl_order に戻してもう一度だけ試す。
    戻り値は並べ直せなかった [(クラス, 例外), ...]。

    登録解除の失敗も戻り値に含める。そのクラスは登録されたまま残るのでパネルは
    消えないが、狙った位置には入らない。黙っていると利用者には成功に見えてしまう。"""
    errors = []
    for cls in reversed(seq):
        if getattr(cls, "is_registered", False):
            try:
                bpy.utils.unregister_class(cls)
            except Exception as err:  # noqa: BLE001
                errors.append((cls, err))
                traceback.print_exc()

    for cls in seq:
        if getattr(cls, "is_registered", False):
            continue
        try:
            bpy.utils.register_class(cls)
        except Exception as err:  # noqa: BLE001
            errors.append((cls, err))
            traceback.print_exc()

    if errors:
        original = {id(c): order for c, order in _SNAPSHOT}
        retry = []
        for cls, err in errors:
            if getattr(cls, "is_registered", False):
                # 登録解除に失敗しただけ。パネルは生きているので登録し直す必要はない
                retry.append((cls, err))
                continue
            try:
                cls.bl_order = original.get(id(cls), 0)
                bpy.utils.register_class(cls)
            except Exception:  # noqa: BLE001
                retry.append((cls, err))
        errors = retry

    # 触ったクラスの登録状態を控える。ここで未登録なのは「こちらが登録し直せなかった」
    # ものだけで、利用者があとで自分で無効化したものは含まれない
    for cls in seq:
        if getattr(cls, "is_registered", False):
            _ORPHANS.discard(id(cls))
        else:
            _ORPHANS.add(id(cls))
    return errors


def _apply(space_type, order):
    """order のカテゴリ順にタブが並ぶよう bl_order を振り直して登録し直す。

    標準タブは Blender の作りから動かせないので常に対象外（冒頭の「仕組みのメモ」参照）。
    戻り値は (動かしたパネル数, エラーのリスト)。"""
    sidebar, groups, index = _collect(space_type)
    if not groups:
        return 0, []

    locked = _locked_categories(groups)

    targets = [cat for cat in order if cat in groups and cat not in locked]
    # 後から有効化されたアドオンのタブは末尾に足す
    for cat in _guess_order(groups, index):
        if cat not in locked and cat not in targets:
            targets.append(cat)

    moving = []
    for cat in targets:
        moving.extend(_flatten(groups[cat], index))
    if not moving:
        return 0, []
    moving = _parent_first(moving)

    _snapshot(moving)

    # 動かさないパネルより後ろに来るよう、連番の開始値をその最大値の次にする
    moved = set(moving)
    fixed_orders = [getattr(c, "bl_order", 0) for c in sidebar if c not in moved]
    base = max(fixed_orders, default=-1) + 1
    for offset, cls in enumerate(moving):
        cls.bl_order = base + offset

    return len(moving), _reregister(moving)


def _restore():
    """bl_order を最初の状態へ戻し、その順で登録し直す。(戻した枚数, エラー) を返す。

    起動時の実際の登録順はアドオンの読み込み順で決まり再現できないため、
    完全に戻すには Blender の再起動が要る。"""
    if not _SNAPSHOT:
        return 0, []
    # 対象は「今も登録されているもの」と「こちらが登録し直せなかったもの」だけ。
    # 利用者があとで自分で無効化したアドオンのパネルを、ここで復活させてはいけない
    seq = [(order, i, cls) for i, (cls, order) in enumerate(_SNAPSHOT)
           if getattr(cls, "is_registered", False) or id(cls) in _ORPHANS]
    seq.sort(key=lambda item: (item[0], item[1]))
    classes = []
    for order, _i, cls in seq:
        cls.bl_order = order
        classes.append(cls)
    # 元の bl_order は子のほうが小さいこともあるので、登録順は親優先に組み直す
    classes = _parent_first(classes)
    errors = _reregister(classes)
    # 戻しきれていないうちにスナップショットを捨てると、もう一度やり直せなくなる
    if not errors:
        _SNAPSHOT.clear()
        _SNAPSHOT_IDS.clear()
        _ORPHANS.clear()
    return len(classes), errors


def _row_height(context):
    """UIListの1行の高さ(px)。Blender内部の widget_unit と同じ式で出す"""
    dpi = getattr(context.preferences.system, "dpi", 72)
    return max(8, int((dpi * 20 + 36) / 72))


def _redraw():
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except AttributeError:
        pass


# ---------------------------------------------------------------- 設定の保存

def _load_orders():
    prefs = _prefs()
    if prefs is None:
        return
    try:
        data = json.loads(prefs.orders_json or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        return
    _ORDERS.clear()
    for space, cats in data.items():
        if space in _SPACE_IDS and isinstance(cats, list):
            _ORDERS[space] = [str(cat) for cat in cats]


def _save_orders():
    """並び順をプリファレンスに書く。

    ここで書けばプリファレンスが変更済みとして記録され、Blender の
    「プリファレンスを自動保存」（既定で有効）が終了時に書き出してくれる。
    save_userpref() は呼ばない。利用者が保存するつもりのなかった他の変更まで
    巻き込んで確定させてしまうため。"""
    prefs = _prefs()
    if prefs is None:
        return
    prefs.orders_json = json.dumps(_ORDERS, ensure_ascii=False)


# ---------------------------------------------------------------- 一覧の同期

def _refresh_list(context):
    """一覧を組み直す。

    標準タブは常に先頭にあり動かせないが、その中での順序は Python から読めない
    （C実装のパネルが混ざるため）。個別に並べると実際と食い違うので、名前も順序も出さず
    1行にまとめる。以降は並び替えできるタブだけを実際の順で並べる。"""
    wm = context.window_manager
    space = wm.sbt_space
    _sidebar, groups, index = _collect(space)
    locked = _locked_categories(groups)

    order = [cat for cat in _ORDERS.get(space, ()) if cat in groups and cat not in locked]
    for cat in _guess_order(groups, index):
        if cat not in locked and cat not in order:
            order.append(cat)

    wm.sbt_tabs.clear()
    if locked:
        item = wm.sbt_tabs.add()
        item.name = _iface(_LOCKED_LABEL).format(len(locked))
        item.locked = True
    for cat in order:
        item = wm.sbt_tabs.add()
        item.name = cat
        item.locked = False
    wm.sbt_index = max(0, min(wm.sbt_index, len(wm.sbt_tabs) - 1))


def _store_list(context):
    wm = context.window_manager
    # 先頭のまとめ行はタブ名ではないので保存しない
    _ORDERS[wm.sbt_space] = [item.name for item in wm.sbt_tabs if not item.locked]
    _save_orders()


def _space_changed(self, context):
    _refresh_list(context)


# ---------------------------------------------------------------- データ定義

class SBT_TabItem(PropertyGroup):
    name: StringProperty(name="Tab Name", default="")
    locked: BoolProperty(name="Locked", default=False)


class SBT_Prefs(AddonPreferences):
    bl_idname = __name__

    orders_json: StringProperty(
        name="Saved Order",
        description="Tab order per editor, kept as JSON",
        default="{}",
    )
    apply_on_startup: BoolProperty(
        name="Apply on Startup",
        description="Apply the saved order at startup, after all add-ons are loaded",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "apply_on_startup")
        layout.label(text="Reorder from 3D Viewport > Sidebar (N) > View tab",
                     icon="INFO")


# ---------------------------------------------------------------- オペレーター

class SBT_OT_refresh(Operator):
    bl_idname = "sbt.refresh"
    bl_label = "Refresh List"
    bl_description = "Rebuild the list from the tabs currently registered"

    def execute(self, context):
        _refresh_list(context)
        return {"FINISHED"}


class SBT_OT_move(Operator):
    bl_idname = "sbt.move"
    bl_label = "Move Tab"
    bl_description = "Move the selected tab up or down and apply it right away"

    direction: EnumProperty(
        items=(("UP", "Up", ""), ("DOWN", "Down", "")),
        default="UP",
    )

    def execute(self, context):
        wm = context.window_manager
        src = wm.sbt_index
        if not (0 <= src < len(wm.sbt_tabs)):
            return {"CANCELLED"}
        if wm.sbt_tabs[src].locked:
            self.report({"INFO"}, _iface(_LOCKED_HINT))
            return {"CANCELLED"}
        if not _shift(context, -1 if self.direction == "UP" else 1):
            return {"CANCELLED"}
        _store_list(context)
        return _run_apply(self, context)


def _shift(context, count):
    """アクティブな項目を count 段ぶん動かし、実際に動けた段数を返す"""
    wm = context.window_manager
    direction = 1 if count > 0 else -1
    moved = 0
    for _ in range(abs(count)):
        src = wm.sbt_index
        dst = src + direction
        if not (0 <= dst < len(wm.sbt_tabs)):
            break
        if wm.sbt_tabs[dst].locked:
            break
        wm.sbt_tabs.move(src, dst)
        wm.sbt_index = dst
        moved += 1
    if moved and context.area is not None:
        context.area.tag_redraw()
    return moved * direction


class SBT_OT_grab(Operator):
    bl_idname = "sbt.grab"
    bl_label = "Grab and Move"
    bl_description = ("Click first, then move (not click-and-hold). "
                      "Mouse, wheel or arrow keys to move, LMB or Enter to confirm, "
                      "RMB or Esc to cancel")
    bl_options = {"BLOCKING"}

    index: IntProperty(default=-1)

    # UIListの項目は押した瞬間ではなく離した瞬間にオペレーターが走る（Blenderの
    # ボタンの仕様）。そのため押しっぱなしのドラッグは受け取れない。代わりに
    # Blenderの移動(G)と同じ「つまむ → 動かす → クリックで確定」にしている。
    def invoke(self, context, event):
        wm = context.window_manager
        if not (0 <= self.index < len(wm.sbt_tabs)):
            return {"CANCELLED"}
        if wm.sbt_tabs[self.index].locked:
            self.report({"INFO"}, _iface(_LOCKED_HINT))
            return {"CANCELLED"}

        global _GRABBING
        wm.sbt_index = self.index
        self._start = [(item.name, item.locked) for item in wm.sbt_tabs]
        self._start_index = self.index
        self._anchor = event.mouse_y
        self._row = _row_height(context)
        _GRABBING = True
        context.workspace.status_text_set(_iface(
            "Mouse, wheel or arrows: move  |  LMB, Enter: confirm  |  RMB, Esc: cancel"))
        if context.area is not None:
            context.area.tag_redraw()
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _end(self, context):
        global _GRABBING
        _GRABBING = False
        context.workspace.status_text_set(None)
        if context.area is not None:
            context.area.tag_redraw()

    def cancel(self, context):
        # Blender 側からモーダルを打ち切られたときも後始末を通す。ここを省くと
        # 「移動中」の表示とステータスバーが出たまま残る
        self._end(context)

    def modal(self, context, event):
        wm = context.window_manager

        if event.type == "MOUSEMOVE":
            delta = self._anchor - event.mouse_y
            if abs(delta) >= self._row:
                # 動かせた分だけ基準をずらす。端で止まったときは基準を残して、
                # 戻す向きへ動かせばすぐ反応するようにする
                self._anchor -= _shift(context, int(delta / self._row)) * self._row
            return {"RUNNING_MODAL"}

        if event.value == "PRESS":
            if event.type in {"UP_ARROW", "WHEELUPMOUSE"}:
                _shift(context, -1)
                return {"RUNNING_MODAL"}
            if event.type in {"DOWN_ARROW", "WHEELDOWNMOUSE"}:
                _shift(context, 1)
                return {"RUNNING_MODAL"}
            if event.type in {"LEFTMOUSE", "RET", "NUMPAD_ENTER"}:
                self._end(context)
                if wm.sbt_index == self._start_index:
                    return {"CANCELLED"}
                _store_list(context)
                return _run_apply(self, context)
            if event.type in {"RIGHTMOUSE", "ESC"}:
                wm.sbt_tabs.clear()
                for name, locked in self._start:
                    item = wm.sbt_tabs.add()
                    item.name = name
                    item.locked = locked
                wm.sbt_index = self._start_index
                self._end(context)
                return {"CANCELLED"}

        return {"RUNNING_MODAL"}


class SBT_OT_apply(Operator):
    bl_idname = "sbt.apply"
    bl_label = "Apply Order"
    bl_description = "Reorder the sidebar tabs to match the list"

    def execute(self, context):
        _store_list(context)
        return _run_apply(self, context)


class SBT_OT_reset(Operator):
    bl_idname = "sbt.reset"
    bl_label = "Reset to Default"
    bl_description = "Discard the saved order and restore the state from before this session"

    def execute(self, context):
        count, errors = _restore()
        _ORDERS.clear()
        _save_orders()
        _refresh_list(context)
        _redraw()
        if errors:
            self.report({"ERROR"}, _iface(
                "Restored {} panels, but {} could not be updated (see console)")
                .format(count - len(errors), len(errors)))
        elif count:
            self.report({"INFO"}, _iface(
                "Restored {} panels (restart Blender to fully return to the startup order)")
                .format(count))
        else:
            self.report({"INFO"}, _iface(
            "Cleared the saved order (defaults apply from the next startup)"))
        return {"FINISHED"}


def _run_apply(op, context):
    wm = context.window_manager
    try:
        count, errors = _apply(wm.sbt_space, _ORDERS.get(wm.sbt_space, []))
    except Exception as err:  # noqa: BLE001
        traceback.print_exc()
        op.report({"ERROR"}, _iface("Failed to reorder: {}").format(err))
        return {"CANCELLED"}

    _refresh_list(context)
    _redraw()
    if errors:
        op.report({"ERROR"}, _iface(
            "Reordered {} panels, but {} could not be updated (see console)")
            .format(count, len(errors)))
    else:
        op.report({"INFO"}, _iface("Reordered {} panels").format(count))
    return {"FINISHED"}


# ---------------------------------------------------------------- UI

class SBT_UL_tabs(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname,
                  index=0, flt_flag=0):
        row = layout.row(align=True)
        if item.locked:
            # 鍵アイコン＋淡色で「動かせない」ことを示す。理由は動かそうとしたときに出す
            row.active = False
            row.label(text="", icon="LOCKED")
        else:
            row.operator("sbt.grab", text="", icon="GRIP", emboss=False).index = index
        row.label(text=item.name)


class SBT_PT_panel(Panel):
    bl_label = "Sidebar Tab Order"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    # 並び替えても迷子にならないよう、動かせない標準タブに置く
    bl_category = "View"
    bl_order = 1000
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager

        # ラベルを上に置く。横に並べると幅を取り合い、長いエディタ名が省略される
        layout.label(text="Editor to Reorder")
        layout.prop(wm, "sbt_space", text="")

        if wm.sbt_space not in _ORDERS:
            # 内部的には「実際の並びが読めないので推定で出している」状態だが、それを
            # そのまま書いても読み手は動きようがない。次の一手だけを示す
            layout.label(text="Reorder to lock in this order", icon="INFO")

        row = layout.row()
        row.template_list("SBT_UL_tabs", "", wm, "sbt_tabs", wm, "sbt_index", rows=8)
        col = row.column(align=True)
        col.operator("sbt.move", text="", icon="TRIA_UP").direction = "UP"
        col.operator("sbt.move", text="", icon="TRIA_DOWN").direction = "DOWN"
        col.separator()
        col.operator("sbt.refresh", text="", icon="FILE_REFRESH")

        # 一覧より上に出すと、移動中だけ一覧の位置がずれて狙いを付けにくくなる
        if _GRABBING:
            # モーダル中はステータスバーにも操作を出しているが、目線は手元のパネルにある
            layout.label(text="Moving: click to confirm, Esc to cancel", icon="GRIP")
        else:
            layout.label(text="Some tabs may not appear in the sidebar", icon="INFO")

        # 動かせばその場で反映されるので、普段このボタンは要らない。起動時の自動適用を
        # 切っている人だけが、保存済みの並びを動かさずに反映する手段として使う
        if not _opt("apply_on_startup"):
            layout.operator("sbt.apply", icon="SORTSIZE")


class SBT_PT_options(Panel):
    bl_label = "Options"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "View"
    bl_parent_id = "SBT_PT_panel"
    bl_order = 1001
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        prefs = _prefs()
        if prefs is not None:
            layout.prop(prefs, "apply_on_startup")
        layout.separator()
        layout.operator("sbt.reset", icon="LOOP_BACK")


# ---------------------------------------------------------------- 起動時の支度

def _startup():
    """全アドオンの読み込みが終わってから、保存した並び順の反映と一覧の用意をする。

    register() の時点では他アドオンがまだ登録されておらず、そこで一覧を作っても
    中身が揃わない。タブ順は .blend ではなくアプリ全体の状態なので、起動時に1回で足りる。"""
    if _opt("apply_on_startup"):
        _load_orders()
        for space, order in _ORDERS.items():
            try:
                _apply(space, order)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
    # 自動適用を切っていても一覧は要る。「一覧を更新」を押すまで空のままにしない
    try:
        _refresh_list(bpy.context)
    except (AttributeError, KeyError):
        # ウィンドウが無い（バックグラウンド実行）ときは一覧そのものが要らない
        pass
    _redraw()
    return None  # タイマーを終了（1回だけ）


# ---------------------------------------------------------------- 登録

classes = (
    SBT_TabItem,
    SBT_Prefs,
    SBT_OT_refresh,
    SBT_OT_move,
    SBT_OT_grab,
    SBT_OT_apply,
    SBT_OT_reset,
    SBT_UL_tabs,
    SBT_PT_panel,
    SBT_PT_options,  # サブパネルなので親より後に登録する
)


def register():
    bpy.app.translations.register(__name__, _TRANSLATIONS)
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.sbt_tabs = CollectionProperty(type=SBT_TabItem)
    bpy.types.WindowManager.sbt_index = IntProperty(default=0)
    bpy.types.WindowManager.sbt_space = EnumProperty(
        name="Target Editor",
        description="Editor whose tab order is being changed. All editors are set from this panel",
        items=_SPACES, default="VIEW_3D", update=_space_changed)
    _load_orders()
    # 起動直後は他アドオンが未登録なので遅延実行する。他アドオンも同じ手を使うことが
    # あるため、後勝ちで確定できるよう少し長めに待つ
    if not bpy.app.timers.is_registered(_startup):
        bpy.app.timers.register(_startup, first_interval=2.0, persistent=True)


def unregister():
    global _GRABBING
    if bpy.app.timers.is_registered(_startup):
        bpy.app.timers.unregister(_startup)
    # モーダル中に無効化されると「移動中」の表示が残る
    if _GRABBING:
        _GRABBING = False
        try:
            bpy.context.workspace.status_text_set(None)
        except (AttributeError, TypeError):
            pass
    # 動かした他アドオンのパネルを戻してから抜ける。放置すると bl_order を書き換えた
    # まま残り、無効化したのに並びが元へ戻らない
    try:
        _count, errors = _restore()
        if errors:
            # 無効化の途中なのでオペレーターの報告は出せない。黙って抜けると、戻せな
            # かったパネルが消えたままなのに手掛かりが何も残らない
            print(_iface(
                "Sidebar Tweaks: {} panels could not be restored. "
                "Restart Blender to get the original order back.").format(len(errors)))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    del bpy.types.WindowManager.sbt_space
    del bpy.types.WindowManager.sbt_index
    del bpy.types.WindowManager.sbt_tabs
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    bpy.app.translations.unregister(__name__)


if __name__ == "__main__":
    register()
