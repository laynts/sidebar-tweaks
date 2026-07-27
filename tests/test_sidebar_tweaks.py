"""サイドバーのタブ並び替えの検証。

    blender --background --factory-startup --python tests/test_sidebar_tweaks.py

ダミーのパネルを登録した合成環境で検証する。--factory-startup は、インストール済みの
拡張機能ではなくリポジトリ内のソースを読ませるために要る（下で sys.path にルートを足す）。
全項目通ると ALL PASSED、失敗すると失敗項目を並べて終了コード1で終わる。
後半で登録エラーのトレースバックが1件出るのは想定どおり（存在しない親を指すパネルを
わざと混ぜ、1枚落ちても他が巻き添えにならないことを確かめている）。

タブの実際の並び（paneltypes の順）は Python から読めないため、ここで固定できるのは
その手前まで——「並べたい順に bl_order が連番で振られ、全部が登録され直していること」。
実際のタブ位置は GUI での目視確認が要る。壊れやすいのはむしろ再登録の副作用
（パネルが消える、タブ内の並びが崩れる、他アドオンが足した描画関数が飛ぶ）なので、
そちらを重点的に確かめる。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bpy

import sidebar_tweaks as sbt

fails = []


def check(label, cond, detail=""):
    print(("OK  " if cond else "NG  ") + label + (" | " + str(detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


# ---------------------------------------------------------- ダミーパネルの用意

CAT_A = "ZZテストA"
CAT_B = "ZZテストB"
CAT_C = "ZZテストC"
TEST_CATS = (CAT_A, CAT_B, CAT_C)


def make_panel(name, category, order, parent=None):
    attrs = {
        "bl_label": name,
        "bl_space_type": "VIEW_3D",
        "bl_region_type": "UI",
        "bl_order": order,
        "draw": lambda self, context: None,
    }
    if category is not None:
        attrs["bl_category"] = category
    if parent is not None:
        attrs["bl_parent_id"] = parent
    return type(name, (bpy.types.Panel,), attrs)


# カテゴリAは親子構造つき。子の bl_order をわざと親より小さくして、
# 平坦化で必ず親→子の順になることを確かめる
A_TOP = make_panel("ZZTEST_PT_a_top", CAT_A, 10)
A_SUB = make_panel("ZZTEST_PT_a_sub", CAT_A, 0, parent="ZZTEST_PT_a_top")
A_OTHER = make_panel("ZZTEST_PT_a_other", CAT_A, 20)
B_1 = make_panel("ZZTEST_PT_b_1", CAT_B, 5)
# カテゴリを書かないサブパネル。親のタブの一員として一緒に動かさないと、
# 親を登録解除している間だけ宙に浮く
B_SUB = make_panel("ZZTEST_PT_b_sub", None, 8, parent="ZZTEST_PT_b_1")
B_2 = make_panel("ZZTEST_PT_b_2", CAT_B, 15)
C_1 = make_panel("ZZTEST_PT_c_1", CAT_C, 7)
C_2 = make_panel("ZZTEST_PT_c_2", CAT_C, 3)

DUMMIES = (A_TOP, A_SUB, A_OTHER, B_1, B_SUB, B_2, C_1, C_2)
ORIGINAL = {cls: cls.bl_order for cls in DUMMIES}

for cls in DUMMIES:
    bpy.utils.register_class(cls)
check("ダミーパネル登録", all(cls.is_registered for cls in DUMMIES))

sbt.register()
check("アドオン登録", sbt.SBT_PT_panel.is_registered and sbt.SBT_PT_options.is_registered)

_s, _g, _i = sbt._collect("VIEW_3D")
check("カテゴリ未指定のサブパネルを親のタブに含める", B_SUB in _g.get(CAT_B, ()),
      [c.__name__ for c in _g.get(CAT_B, ())])

# 他アドオンが標準パネルへ描画を足している状況を再現する
_extra_calls = []


def _extra_draw(self, context):
    _extra_calls.append(1)


A_OTHER.append(_extra_draw)
check("描画関数の追加", _extra_draw in getattr(A_OTHER.draw, "_draw_funcs", []))


# ---------------------------------------------------------- 補助

def order_of(cls):
    """bl_order を明示していないパネルは属性自体が無いので既定値0で読む"""
    return getattr(cls, "bl_order", 0)


def cat_ranges():
    """{カテゴリ: (bl_orderの最小, 最大)}"""
    _s, gs, _i = sbt._collect("VIEW_3D")
    out = {}
    for cat in TEST_CATS:
        orders = [order_of(c) for c in gs.get(cat, ())]
        if orders:
            out[cat] = (min(orders), max(orders))
    return out


# 標準タブ（bl_ui由来）が動かされないことの基準値
_sidebar, groups, _index = sbt._collect("VIEW_3D")
builtin_before = {cls: order_of(cls) for cls in _sidebar if sbt._builtin(cls)}
check("Blender標準パネルを検出", bool(builtin_before), len(builtin_before))


def sequence_ok(order):
    """order の並びどおりにカテゴリが重なりなく連なっているか"""
    ranges = cat_ranges()
    if len(ranges) != len(order):
        return False, ranges
    prev = -1
    for cat in order:
        lo, hi = ranges[cat]
        if lo <= prev:
            return False, ranges
        prev = hi
    return True, ranges


# ---------------------------------------------------------- 並べ替え

WANT = [CAT_C, CAT_A, CAT_B]
count, errors = sbt._apply("VIEW_3D", WANT)
check("適用が例外を出さない", not errors, errors)
check("ダミー全枚数が対象に入る", count >= len(DUMMIES), (count, len(DUMMIES)))
check("適用後も全ダミーが登録済み", all(cls.is_registered for cls in DUMMIES),
      [c.__name__ for c in DUMMIES if not c.is_registered])

ok, ranges = sequence_ok(WANT)
check("指定した順にカテゴリが並ぶ", ok, ranges)
check("カテゴリ内の相対順が保たれる（A）", A_TOP.bl_order < A_OTHER.bl_order,
      (A_TOP.bl_order, A_OTHER.bl_order))
check("カテゴリ内の相対順が保たれる（C: 元は c_2 が先）", C_2.bl_order < C_1.bl_order,
      (C_2.bl_order, C_1.bl_order))
check("サブパネルは親の直後", A_TOP.bl_order < A_SUB.bl_order < A_OTHER.bl_order,
      (A_TOP.bl_order, A_SUB.bl_order, A_OTHER.bl_order))
check("カテゴリ未指定のサブパネルも親の直後",
      B_1.bl_order < B_SUB.bl_order < B_2.bl_order,
      (B_1.bl_order, B_SUB.bl_order, B_2.bl_order))

check("標準パネルのbl_orderは無変更",
      all(order_of(cls) == order for cls, order in builtin_before.items()),
      [cls.__name__ for cls, order in builtin_before.items() if order_of(cls) != order])
if builtin_before:
    check("ダミーは標準パネルより後ろへ回る",
          min(cls.bl_order for cls in DUMMIES) > max(builtin_before.values()),
          (min(cls.bl_order for cls in DUMMIES), max(builtin_before.values())))

check("追加した描画関数が残る", _extra_draw in getattr(A_OTHER.draw, "_draw_funcs", []),
      getattr(A_OTHER.draw, "_draw_funcs", None))

# 冪等性: 同じ指定を2回流しても結果が動かない
snapshot = {cls: cls.bl_order for cls in DUMMIES}
sbt._apply("VIEW_3D", WANT)
check("同じ指定の再適用で値が変わらない",
      all(cls.bl_order == snapshot[cls] for cls in DUMMIES),
      {c.__name__: (snapshot[c], c.bl_order) for c in DUMMIES if c.bl_order != snapshot[c]})

# 並べ替え直し
WANT2 = [CAT_B, CAT_C, CAT_A]
sbt._apply("VIEW_3D", WANT2)
ok, ranges = sequence_ok(WANT2)
check("並べ替え直しが効く", ok, ranges)
check("並べ替え直しでも全ダミーが登録済み", all(cls.is_registered for cls in DUMMIES))

# 一覧に無いカテゴリは末尾に足される（後からアドオンを有効にした場合）
sbt._apply("VIEW_3D", [CAT_B])
ranges = cat_ranges()
check("未知のカテゴリは末尾に回る",
      ranges[CAT_B][1] < ranges[CAT_A][0] and ranges[CAT_B][1] < ranges[CAT_C][0], ranges)


# ---------------------------------------------------------- 一覧の用意と移動

# 一覧は起動時の支度で用意する。ここが空のままだと、初めてパネルを開いた人は
# 「一覧を更新」を押すまで何も見えない
wm = bpy.context.window_manager
check("register だけでは一覧が空", len(wm.sbt_tabs) == 0, len(wm.sbt_tabs))
sbt._startup()
check("起動時の支度で一覧が用意される", len(wm.sbt_tabs) > 0, len(wm.sbt_tabs))

# つまんで移動（モーダル）の中身は _shift。マウス操作の部分は自動では試せないので、
# 段数ぶんの移動と、端・標準タブでの止まり方だけを固定する
names = [item.name for item in wm.sbt_tabs]
check("一覧にテスト用タブが載る", all(cat in names for cat in TEST_CATS), names)

# 標準タブは動かせず、その中の順序も Python から読めないので、名前も順序も出さずに
# 先頭の1行へまとめる。ここが崩れると一覧と実際の食い違いが再発する
locked_count = sum(1 for item in wm.sbt_tabs if item.locked)
check("標準タブは先頭の1行にまとまる",
      locked_count == 1 and wm.sbt_tabs[0].locked,
      [(i.name, i.locked) for i in wm.sbt_tabs])
check("まとめ行に標準タブ名を出さない",
      not any(i.name in ("View", "Tool", "Item", "Animation") for i in wm.sbt_tabs),
      [i.name for i in wm.sbt_tabs])

sbt._store_list(bpy.context)
saved = sbt._ORDERS.get("VIEW_3D", [])
check("保存にまとめ行を含めない",
      wm.sbt_tabs[0].name not in saved and all(cat in saved for cat in TEST_CATS), saved)

# 先頭の動かせる項目を起点にする（末尾を選ぶと下へ動かせず判定にならない）
first = locked_count
room = len(wm.sbt_tabs) - 1 - first
check("動かせる余地がある", room >= 2, (len(wm.sbt_tabs), first))

wm.sbt_index = first
target = wm.sbt_tabs[first].name
before = [item.name for item in wm.sbt_tabs]
moved = sbt._shift(bpy.context, 1)
after = [item.name for item in wm.sbt_tabs]
check("1段下へ動く",
      moved == 1 and wm.sbt_index == first + 1 and after[first + 1] == target,
      (before, after))

sbt._shift(bpy.context, -1)
check("戻すと元の並びになる", [item.name for item in wm.sbt_tabs] == before,
      [item.name for item in wm.sbt_tabs])

wm.sbt_index = len(wm.sbt_tabs) - 1
check("末尾からは下へ動かない", sbt._shift(bpy.context, 1) == 0)

wm.sbt_index = first
check("標準タブの手前で止まる", sbt._shift(bpy.context, -1) == 0,
      [(i.name, i.locked) for i in wm.sbt_tabs])

wm.sbt_index = first
check("まとめて複数段動かせる", sbt._shift(bpy.context, 2) == 2, wm.sbt_index)

wm.sbt_index = first
check("行き過ぎは端で頭打ちになる",
      sbt._shift(bpy.context, 999) == room and wm.sbt_index == len(wm.sbt_tabs) - 1,
      (room, wm.sbt_index))


# ---------------------------------------------------------- 既定に戻す

restored, restore_errors = sbt._restore()
check("戻した枚数を返す", restored >= len(DUMMIES), restored)
check("戻すときにエラーが出ない", not restore_errors, restore_errors)
check("bl_orderが元の値に戻る",
      all(cls.bl_order == ORIGINAL[cls] for cls in DUMMIES),
      {c.__name__: (ORIGINAL[c], c.bl_order) for c in DUMMIES if c.bl_order != ORIGINAL[c]})
check("戻した後も全ダミーが登録済み", all(cls.is_registered for cls in DUMMIES))
check("スナップショットが空になる", not sbt._SNAPSHOT)


# ---------------------------------------------------------- 失敗時の巻き戻し

# 存在しない親を指すと登録に失敗する。1枚落ちても他が巻き添えにならないことを見る。
# ここから先のトレースバック1件は想定どおり（これ以外が出たら不具合）
print(">>> ここで登録エラーのトレースバックが1件出るのは想定どおり")
A_SUB.bl_parent_id = "ZZTEST_PT_does_not_exist"
count, errors = sbt._apply("VIEW_3D", WANT)
check("登録できないパネルを報告する", len(errors) == 1, [(c.__name__, str(e)) for c, e in errors])
check("失敗したのは当該パネルだけ", errors and errors[0][0] is A_SUB,
      [c.__name__ for c, _e in errors])
survivors = [cls for cls in DUMMIES if cls is not A_SUB]
check("他のパネルは全て登録されたまま", all(cls.is_registered for cls in survivors),
      [c.__name__ for c in survivors if not c.is_registered])
ok, ranges = sequence_ok(WANT)
check("1枚落ちても残りの並びは成立する", ok, ranges)

# 登録に失敗して未登録のまま残ったパネルを、そのまま復元へ通す。
# ここで戻せないと、アドオンを無効化してもそのパネルが消えたままになる
check("失敗したパネルは未登録のまま", not A_SUB.is_registered)
A_SUB.bl_parent_id = "ZZTEST_PT_a_top"
restored, restore_errors = sbt._restore()
check("復元は未登録のパネルも登録し直す", A_SUB.is_registered, restored)
check("復元が通ればエラーは無い", not restore_errors, restore_errors)
check("復元でbl_orderも元へ戻る",
      all(cls.bl_order == ORIGINAL[cls] for cls in DUMMIES),
      {c.__name__: (ORIGINAL[c], c.bl_order) for c in DUMMIES if c.bl_order != ORIGINAL[c]})


# ------------------------------------------ 利用者が自分で外したアドオンは戻さない

# 並べ替えた後に、利用者がそのアドオンを無効化することがある。あとから Sidebar Tweaks を
# 外したときに、それを復活させてしまってはいけない
sbt._apply("VIEW_3D", WANT)
bpy.utils.unregister_class(C_2)
check("外したパネルは未登録", not C_2.is_registered)
restored, restore_errors = sbt._restore()
check("利用者が外したパネルは復活しない", not C_2.is_registered, restored)
check("外れていても他は元へ戻る",
      all(cls.bl_order == ORIGINAL[cls] for cls in DUMMIES if cls is not C_2),
      {c.__name__: (ORIGINAL[c], c.bl_order)
       for c in DUMMIES if c is not C_2 and c.bl_order != ORIGINAL[c]})
bpy.utils.register_class(C_2)
C_2.bl_order = ORIGINAL[C_2]

# ------------------------------------------------ オペレーター経由の「既定に戻す」

# _restore() 単体は上で見ているが、報告の分岐を通るのはオペレーター側。
# ここを通しておかないと、件数の計算や ERROR/INFO の切り分けが壊れても気付けない
sbt._apply("VIEW_3D", WANT)
check("戻す前に並べ替えてある", any(cls.bl_order != ORIGINAL[cls] for cls in DUMMIES))
res = bpy.ops.sbt.reset()
check("既定に戻すオペレーターが通る", res == {"FINISHED"}, res)
check("オペレーター経由でもbl_orderが戻る",
      all(cls.bl_order == ORIGINAL[cls] for cls in DUMMIES),
      {c.__name__: (ORIGINAL[c], c.bl_order) for c in DUMMIES if c.bl_order != ORIGINAL[c]})
check("オペレーター経由で保存データも消える", not sbt._ORDERS, sbt._ORDERS)


# ---------------------------------------------------------- 無効化したときの復元

# アドオンを外したら、動かした他アドオンのパネルは元へ戻っていなければならない。
# 戻さないまま抜けると bl_order を書き換えた状態が残り、無効化したのに並びが戻らない
sbt._apply("VIEW_3D", WANT)
check("無効化の前に並べ替えてある",
      any(cls.bl_order != ORIGINAL[cls] for cls in DUMMIES))

sbt.unregister()
check("unregisterでbl_orderが元へ戻る",
      all(cls.bl_order == ORIGINAL[cls] for cls in DUMMIES),
      {c.__name__: (ORIGINAL[c], c.bl_order) for c in DUMMIES if c.bl_order != ORIGINAL[c]})
check("unregister後も他アドオンのパネルは登録済み",
      all(cls.is_registered for cls in DUMMIES),
      [c.__name__ for c in DUMMIES if not c.is_registered])
check("アドオン自身のパネルは登録解除される",
      not sbt.SBT_PT_panel.is_registered and not sbt.SBT_PT_options.is_registered)


# ---------------------------------------------------------- 後片付け

for cls in reversed(DUMMIES):
    if cls.is_registered:
        bpy.utils.unregister_class(cls)
check("後片付け完了", not any(cls.is_registered for cls in DUMMIES))

print("=" * 50)
if fails:
    print("FAILED: {} 件".format(len(fails)))
    for f in fails:
        print(" - " + f)
    sys.exit(1)
print("ALL PASSED")
