# -*- coding: utf-8 -*-
"""点位关节角解析：失败一律拒绝，绝不"静默归零"。

★ 为什么单独一个文件
  全维度审查 2026-09-25 发现同一种反模式出现在 4 处（control.py:341/617/656、
  vision_rules.py:147）：

      try:
          joints = json.loads(p.joints or "[]")
      except Exception:
          joints = [0.0] * 6        # ← 然后照样把这个"全零位"下发出去

  点位数据被手工改坏 / 字段为空 / 长度不对时，机器人会被送向**全零位**——
  这是仅次于"绕过轴锁"的第二危险模式。判据必须只有一份，且必须是 fail-safe。
"""
from __future__ import annotations

import json
import math
from typing import Any, List


def parse_joints(raw: Any, *, where: str = "") -> List[float]:
    """解析点位关节角。任何异常都抛 ValueError（调用方应当拒绝下发）。

    :param raw: JSON 字符串或已是 list/tuple
    :param where: 用于错误定位的前缀，例如 "点位「P1」"
    :raises ValueError: 空 / 非 JSON / 长度≠6 / 非数值 / NaN / Inf
    """
    if raw is None:
        raise ValueError(f"{where}关节角为空")
    if isinstance(raw, (list, tuple)):
        v = list(raw)
    else:
        s = str(raw).strip()
        if not s:
            raise ValueError(f"{where}关节角为空")
        try:
            v = json.loads(s)
        except Exception as e:
            raise ValueError(f"{where}关节角 JSON 解析失败：{e}")
    if not isinstance(v, (list, tuple)) or len(v) != 6:
        got = len(v) if hasattr(v, "__len__") else type(v).__name__
        raise ValueError(f"{where}关节角应为长度 6 的数组，实际 {got}")
    out: List[float] = []
    for i, x in enumerate(v):
        try:
            f = float(x)
        except (TypeError, ValueError):
            raise ValueError(f"{where}关节角 J{i+1} 非数值：{x!r}")
        if not math.isfinite(f):
            raise ValueError(f"{where}关节角 J{i+1} 为 NaN/Inf")
        out.append(f)
    return out
