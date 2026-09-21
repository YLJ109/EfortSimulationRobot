# -*- coding: utf-8 -*-
"""正运动学 / 逆运动学 单元测试（不依赖 app 与数据库）。"""
from __future__ import annotations

import numpy as np

from app.services.kinematics import (
    fk_matrix,
    forward_kinematics,
    ikine,
    rpy_to_matrix,
    tcp_of,
)


def test_forward_kinematics_structure():
    r = forward_kinematics([0, 0, 0, 0, 0, 0])
    assert "tcp" in r
    assert len(r["tcp"]) == 3
    assert len(r["frames"]) == 7  # base + 6 关节


def test_tcp_of_returns_tuple():
    t = tcp_of([0, 0, 0, 0, 0, 0])
    assert isinstance(t, tuple)
    assert len(t) == 3


def test_fk_matrix_shape():
    T = fk_matrix([0, 0, 0, 0, 0, 0])
    assert T.shape == (4, 4)
    assert np.allclose(T[:3, :3] @ T[:3, :3].T, np.eye(3), atol=1e-6)  # 旋转部分正交


def test_rpy_identity():
    R = rpy_to_matrix(0, 0, 0)
    assert np.allclose(R, np.eye(3), atol=1e-9)


def test_ik_position_converges_from_origin():
    # 仅位置约束（rot_weight=0）下，从零位初始应能收敛到已知目标的末端位置。
    q_target = [10.0, -20.0, 30.0, 5.0, -15.0, 25.0]
    target = fk_matrix(q_target)
    r = ikine(target, [0.0] * 6, rot_weight=0.0)
    assert r["pos_err"] < 1.0


def test_ik_position_only():
    target = fk_matrix([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    r = ikine(target, [5.0, 5.0, 5.0, 5.0, 5.0, 5.0], rot_weight=0.0)
    assert r["pos_err"] < 1.0
