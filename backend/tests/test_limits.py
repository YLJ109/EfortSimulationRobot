# -*- coding: utf-8 -*-
"""★ 审计修复 P1-E1：限位校验 4 份实现收敛后的回归钉（审计 §7 推荐的 test_limits.py）。

收敛不是"把代码挪个地方"——必须证明四条路径（预演 control / 下发 motion /
点动 jog / 视觉自动执行 vision_rules）对**同一个输入给出同一个结论**，
否则只是把原来明面上的分叉藏得更深。

    判据一句话：先看长度 → 再看有限性 → 再看区间（见 services/limits.py 顶部）。
"""

from __future__ import annotations

import math

from app.services.limits import (
    clamp, in_limits, load_limits, load_ranges, violations,
)


# --------------------------------------------------------------------------- #
# 1. 四处实现必须同源、同结论
# --------------------------------------------------------------------------- #
def test_four_call_sites_share_one_source():
    """4 份实现收敛后：读的是同一份配置、给出同一批限位。"""
    from app.api import control as control_api
    from app.services import vision_rules as vr
    from app.services.jog import jog
    from app.services.motion import motion

    lim_dicts = load_limits()
    lim_ranges = load_ranges()

    # dict 形态的三处必须**完全相同**（含名字与兜底值）
    assert control_api._limits() == vr._limits() == jog._limit_dicts() == lim_dicts
    # tuple 形态的两处必须完全相同，且与 dict 形态一一对应
    assert motion._limits() == jog._limits() == lim_ranges
    assert lim_ranges == [(e["min"], e["max"]) for e in lim_dicts]

    # 长度恒为 6，且区间自洽
    assert len(lim_dicts) == 6
    assert all(e["min"] <= e["max"] for e in lim_dicts)
    assert all(e["name"] for e in lim_dicts)


def test_four_call_sites_agree_on_verdicts():
    """同一个目标，四处实现必须同时判"内"或同时判"外"。"""
    from app.api import control as control_api
    from app.services import vision_rules as vr
    from app.services.motion import motion

    lim = load_limits()
    good = [0.0] * 6
    bad = [lim[0]["max"] + 10.0] + [0.0] * 5

    assert control_api._in_limits(good, lim) is True
    assert motion._in_limits(good) is True
    assert vr._in_limits(good, lim) is True
    assert in_limits(good) is True

    assert control_api._in_limits(bad, lim) is False
    assert motion._in_limits(bad) is False
    assert vr._in_limits(bad, lim) is False
    assert in_limits(bad) is False
    # 越界必须能报出"是哪个轴"
    assert violations(bad)[0]["joint"] == lim[0]["name"]

    # 点动夹紧后的目标必须落回限位内（这是 limit_clamped 的正确含义）
    clamped, _ = clamp(bad)
    assert motion._in_limits(clamped) is True


# --------------------------------------------------------------------------- #
# 2. 非法输入：NaN / Inf / 长度不齐 —— 这三类是原来各处判据分叉的重灾区
# --------------------------------------------------------------------------- #
def test_nan_and_inf_rejected_everywhere():
    """NaN 与任何数比较都是 False —— 老实现换个写法就会把它判成"在限内"。"""
    from app.api import control as control_api
    from app.services import vision_rules as vr
    from app.services.motion import motion

    nan_q = [float("nan")] + [0.0] * 5
    inf_q = [float("inf")] + [0.0] * 5

    for q in (nan_q, inf_q):
        assert in_limits(q) is False
        assert motion._in_limits(q) is False
        assert control_api._in_limits(q, load_limits()) is False
        assert vr._in_limits(q, load_limits()) is False

    # 报告里必须点名"非法值"，不能默默当成"超限"
    assert violations(nan_q)[0]["reason"].startswith("非法值")


def test_length_mismatch_rejected():
    """原实现用 zip：q 比限位短时后面的轴会被**静默漏查**。"""
    assert in_limits([0.0] * 3) is False          # 短
    assert in_limits([0.0] * 7) is False          # 长
    assert in_limits(None) is False               # 非序列
    assert violations([0.0] * 3)[0]["reason"].startswith("长度")

    from app.services.motion import motion
    assert motion._in_limits([0.0] * 3) is False


def test_unconvertible_value_rejected():
    """None / 字符串混进关节角 → 判非法，而不是靠 float() 在下发途中才炸。"""
    assert in_limits([None] + [0.0] * 5) is False
    assert in_limits(["abc"] + [0.0] * 5) is False
    assert violations([None] + [0.0] * 5)[0]["reason"].startswith("非法值")


# --------------------------------------------------------------------------- #
# 3. 边界与夹紧（test_jog.py 的 limit_clamped 断言靠这套语义保着）
# --------------------------------------------------------------------------- #
def test_boundary_values():
    lim = load_limits()
    inside = [lim[0]["min"], lim[1]["max"], 0.0, 0.0, 0.0, 0.0]
    assert in_limits(inside) is True              # 正好压线 = 在限内
    assert violations(inside) == []

    over = list(inside)
    over[0] = lim[0]["min"] - 0.01
    assert in_limits(over) is False
    assert violations(over)[0]["joint"] == lim[0]["name"]

    hairline = list(inside)
    hairline[0] = lim[0]["min"] - 1e-9            # 只差浮点尾巴
    assert in_limits(hairline) is True            # TOL(1e-6) 吃掉
    assert violations(hairline) == []


def test_clamp_semantics():
    lim = load_ranges()

    out, clamped = clamp([lim[0][0] - 5.0] + [0.0] * 5)
    assert clamped is True
    assert out[0] == lim[0][0]                    # 夹到边界
    assert in_limits(out) is True

    # ★ 正好在边界上**不算**夹紧 —— test_jog 钉着 limit_clamped=False
    out2, clamped2 = clamp([lim[1][1]] + [0.0] * 5)
    assert clamped2 is False
    assert out2[0] == lim[1][1]                    # 第 0 项（J2 的最大值）没被动

    # ★ 非有限值：保持原样、不标记夹紧（夹紧=凭空捏位置，更危险）
    nan_q = [float("nan")] + [0.0] * 5
    out3, clamped3 = clamp(nan_q)
    assert math.isnan(out3[0])
    assert clamped3 is False
    # 但下发前照样被拒 —— 这条路是 fail-safe 的
    assert in_limits(out3) is False

    # 非数值输入：一个字都不动
    out4, clamped4 = clamp([None] + [0.0] * 5)
    assert out4[0] is None and clamped4 is False


def test_violations_and_in_limits_never_disagree():
    """恒等式：`violations(q) == []` ⇔ `in_limits(q) is True`（含长度不齐）。"""
    lim = load_limits()
    samples = [
        [0.0] * 6,
        [float("nan")] + [0.0] * 5,
        [float("-inf")] + [0.0] * 5,
        [lim[0]["max"] + 1.0] + [0.0] * 5,
        [lim[0]["min"] - 1e-9] + [0.0] * 5,      # 容差内 → 两边都"无违规"
        [None] + [0.0] * 5,
        [0.0] * 3,
        [0.0] * 7,
    ]
    for q in samples:
        assert (violations(q, lim) == []) == in_limits(q, lim), f"不一致: {q!r}"
