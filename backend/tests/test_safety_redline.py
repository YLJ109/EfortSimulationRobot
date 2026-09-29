# -*- coding: utf-8 -*-
"""四条安全红线的回归测试（全维度审查 2026-09-25）。

红线：
  R1 轴锁：**生产默认 = 操作员 J1~J6 全轴可动**（robot.yaml enable=false）；
           「仅 J6」是**给 AI 做真机验证的临时护栏**（EFORT_J6_ONLY=1 或临时改 true），
           开启后 J1~J5 一律拒绝、仿真同限。测试环境由 conftest._pin_joint_lock_off
           统一钉为关闭，以便用例只测机制（用例体内 monkeypatch 可覆盖）。
  R2 速度下限：任何下发 speed_pct 必须 >= 5（Pydantic 边界 + 后端 clamp）。
  R3 真机不可误写：测试环境 EFORT_REAL_MOTION=0 / simulate=always → 永远走 sim，不写 192.168.1.12:502。
  R4 程序白名单：/api/ready 只允许加载白名单内的程序号，operator/管理员令牌都不能越权跑任意程序；
           且点动/吸放必须共用同一个真实存在的服务程序号 200（拆号=来回切程序=5005）。

全部用 TestClient + monkeypatch，不连真机、不写寄存器。
"""
from __future__ import annotations

import pytest

from app.core import safety_const as sc
from app.utils import joints as joints_mod


def _admin(client) -> dict:
    r = client.post("/api/auth/login", json={"password": "test1234"})
    assert r.status_code == 200, r.text
    return {"X-Control-Token": r.json()["token"]}


# ---------------------------------------------------------------- 假 Modbus
class _FakeMB:
    """假 Modbus 客户端：任何调用都**不出网**。

    ★ 为什么必须有它：`ModbusRobot.read_regs/rc_snapshot` 会**真的开 TCP socket**
      连 192.168.1.12:502（modbus.py 里没有 simulate 短路）。而 /api/ready 的测试
      为了验证白名单会 monkeypatch 掉总闸，一旦这样，_ready() 就会去真机
      「清报警 / 上伺服 / 加载 / 运行」。所以 ready 类测试必须把 _ready._mb 换成这个假对象。
      （真实事故：本文件早先只 monkeypatch 总闸，测试会真的碰控制器。）
    """

    def __init__(self, alarm1=0, servo=1, prog=200, loaded=1, run=1):
        self.calls = []
        self.alarm1 = alarm1
        self.servo = servo
        self.prog = prog
        self.loaded = loaded
        self.run = run

    def rc_snapshot(self):
        self.calls.append("snapshot")
        bits = {
            "servo": self.servo, "alarm": 1 if self.alarm1 else 0,
            "prog_loaded": self.loaded, "run": self.run,
            "manual": 0, "auto": 1, "remote": 0, "estop": 0,
        }
        return {
            "ok": True, "status_word": 0, "mode": "auto", "bits": bits,
            "prog": self.prog, "alarm1": self.alarm1, "alarm2": 0,
            "jog_trig": False, "speed_pct": 5, "joints": [0.0] * 6,
        }, None

    def rc_command(self, cmd):
        self.calls.append(("cmd", cmd))
        return True, None

    def write_reg(self, addr, val):
        self.calls.append(("write", addr, val))
        return val, None


@pytest.fixture
def fake_ready_mb(monkeypatch):
    """把 /api/ready 用的就绪服务换成假 Modbus（不出网），返回假对象供断言调用记录。"""
    import app.api.robot as robot_api
    fake = _FakeMB()
    monkeypatch.setattr(robot_api._ready, "_mb", fake, raising=True)
    return fake


# ---------------------------------------------------------------- R2 纯函数
def test_clamp_speed_boundaries():
    """★ R2：速度夹取到 [5,100]，非法值回落到 5。"""
    assert sc.clamp_speed(None) == sc.SPEED_MIN
    assert sc.clamp_speed(0) == sc.SPEED_MIN
    assert sc.clamp_speed(4) == sc.SPEED_MIN
    assert sc.clamp_speed(5) == 5
    assert sc.clamp_speed(50) == 50
    assert sc.clamp_speed(999) == sc.SPEED_MAX
    assert sc.clamp_speed("abc") == sc.SPEED_MIN


# ---------------------------------------------------------------- B-12 解析不静默归零
def test_parse_joints_rejects_garbage():
    """★ B-12：点位 joints 解析失败必须抛 ValueError（原实现 except→归零会送机器人去全零位）。"""
    for bad in ["[1,2,", "not a list", "[a,b,c,d,e,f]", "", None]:
        with pytest.raises(ValueError):
            joints_mod.parse_joints(bad, where="单测")
    # 长度不足 6
    with pytest.raises(ValueError):
        joints_mod.parse_joints("[1,2,3]", where="单测")


def test_parse_joints_accepts_valid():
    out = joints_mod.parse_joints("[1.5, 2, 3, 4, 5, 6]", where="单测")
    assert out == [1.5, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert all(isinstance(x, float) for x in out)


# ---------------------------------------------------------------- R1 轴锁
def test_joint_lock_off_in_test_env_allows_all():
    """★ R1：测试环境轴锁被统一钉为关闭（conftest._pin_joint_lock_off）→ 全轴可动。

    这里断言的是"测试夹具生效"，不是"现场默认"。现场默认见下一条。
    """
    st = sc.joint_lock_state()
    assert st["enabled"] is False
    assert st["locked"] == []


def test_shipped_config_allows_full_axis_for_operator():
    """★★ R1：**生产默认必须是「操作员全轴可动」**（joint_lock.enabled=false）★★

    用户口径（2026-09-29 再确认）：
      · 轴锁是**给 AI 做真机验证用的临时护栏**，不是给操作员设的限制；
      · 操作员在安全前提下 J1–J6 **全轴可控** ——「仅 J6」绝不能作为生产默认。
    事故背景：曾把 enabled 误当生产默认改成 true 并长期留着，
      结果**操作员自己点动 J1 被拒**（"轴锁模式已开启：J1 不可动（仅 J6 可动）"）。
    本用例把"默认必须关闭"钉死；同时校验"模式开启时"的参数仍正确
    （AI 验证期间会临时打开，那时 locked/only/容差/仿真同限都要对）。

    直接读 config/robot.yaml 原始文本（绕开测试环境的钉关夹具与覆盖层），
    一旦有人把它改回 true 就报红。
    """
    import os
    import yaml as _yaml
    from app.core.config import project_root

    path = os.path.join(project_root(), "config", "robot.yaml")
    with open(path, "r", encoding="utf-8") as f:
        raw = _yaml.safe_load(f)
    jl = (raw.get("motion") or {}).get("joint_lock") or {}
    assert jl.get("enabled") is False, (
        "生产默认必须是 false（操作员全轴可动）。AI 真机验证请用环境变量 "
        "EFORT_J6_ONLY=1 或**临时**改 true，验证完必须改回 —— "
        "否则操作员会被自己的系统拒之门外")
    # 模式开启时（AI 验证）的参数必须仍然正确
    assert [int(x) for x in (jl.get("locked_joints") or [])] == [1, 2, 3, 4, 5]
    assert int(jl.get("only_joint")) == 6
    assert jl.get("apply_in_sim") is True, "模式开启时仿真须同限，否则'真机不能动、仿真能动'"
    assert 0 < float(jl.get("tolerance_deg")) <= 5.0


def test_joint_lock_mode_still_usable_when_enabled(monkeypatch):
    """★ R1：护栏本身没废 —— 打开后（AI 真机验证口径）仍严格只放行 J6。"""
    import app.core.safety_const as sc
    monkeypatch.setattr(sc, "joint_lock_enabled", lambda: True)
    st = sc.joint_lock_state()
    assert st["enabled"] is True
    assert st["locked"] == [1, 2, 3, 4, 5]
    assert st["only"] == 6


def test_shipped_config_has_no_stale_program_number():
    """★★ R4/R1：点动与吸放必须**共用同一个常驻服务程序号**，且该号在就绪白名单内。

    历史教训（2026-09-29）：点动/吸放被拆成两个号后，每按一次按钮 Web 端都要
    「停当前程序 → 加载另一个号 → 运行」；而被切过去的号在控制器上并不存在
    → 反复报 5005（加载的程序不存在）。当前现场该号为 **210**（三合一），
    200 是旧的纯点动程序（留作回退）。
      本用例把"两者必须相等 + 必须在白名单里"钉死，防止有人再把它们拆开
    —— 注意不写死具体数字，因为现场换号（200↔210）属正常运维，不该报红。
    """
    import os
    import yaml as _yaml
    from app.core.config import project_root

    path = os.path.join(project_root(), "config", "robot.yaml")
    with open(path, "r", encoding="utf-8") as f:
        raw = _yaml.safe_load(f)
    mo = raw.get("motion") or {}
    jog_prog = int((mo.get("jog") or {}).get("service_program") or 0)
    vac_prog = int((mo.get("vacuum") or {}).get("service_program") or 0)
    assert jog_prog > 0, "点动服务程序号必须显式配置（0 = 未指定，加载会 5005）"
    assert vac_prog == jog_prog, (
        "吸放必须与点动共用同一个常驻程序号；拆号 = 每按按钮来回切程序 → 反复 5005")
    allowed = [int(x) for x in ((mo.get("ready") or {}).get("allowed_programs") or [])]
    assert jog_prog in allowed, "服务程序号必须在 /api/ready 白名单内，否则一键就绪会被拦"
    assert 411 not in allowed, "产线/测试程序不得进入就绪白名单"


def test_move_allows_j1_when_lock_off(client):
    """★ R1：轴锁关闭（测试夹具口径）下移动 J1 应成功（sim）。"""
    r = client.post("/api/control/move",
                    json={"joints": [10, 0, 0, 0, 0, 0], "speed_pct": 5},
                    headers=_admin(client))
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True


def test_joint_lock_enabled_blocks_j1_allows_j6(client, monkeypatch):
    """★ R1：轴锁开启（AI 测试模式）下，J1 被拒、仅 J6 放行。"""
    import app.services.motion as motion
    import app.services.collector as collector_mod
    monkeypatch.setattr(motion, "joint_lock_enabled", lambda: True)
    monkeypatch.setattr(motion, "joint_lock_apply_in_sim", lambda: True)
    # 提供确定性的当前位姿（全零），否则 fail-safe 会无条件拒绝
    monkeypatch.setattr(collector_mod.collector, "get_latest",
                        lambda: {f"j{i}": 0.0 for i in range(1, 7)})

    # J1 偏离 → 拒绝
    r1 = client.post("/api/control/move",
                     json={"joints": [10, 0, 0, 0, 0, 0], "speed_pct": 5},
                     headers=_admin(client))
    assert r1.status_code == 200, r1.text
    assert r1.json().get("ok") is False
    assert r1.json().get("joint_lock") is True

    # 仅 J6 偏离 → 放行
    r6 = client.post("/api/control/move",
                     json={"joints": [0, 0, 0, 0, 0, 10], "speed_pct": 5},
                     headers=_admin(client))
    assert r6.status_code == 200, r6.text
    assert r6.json().get("ok") is True


# ---------------------------------------------------------------- R2 速度下限（API 边界）
def test_move_speed_below_floor_rejected(client):
    """★ R2：speed_pct < 5 必须被 Pydantic 边界拒（422）。"""
    r = client.post("/api/control/move",
                    json={"joints": [0, 0, 0, 0, 0, 0], "speed_pct": 1},
                    headers=_admin(client))
    assert r.status_code == 422, r.text


def test_move_speed_floor_accepted(client):
    """★ R2：speed_pct == 5 通过。"""
    r = client.post("/api/control/move",
                    json={"joints": [0, 0, 0, 0, 0, 0], "speed_pct": 5},
                    headers=_admin(client))
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True


# ---------------------------------------------------------------- R4 程序白名单
def test_ready_rejects_unlisted_program(client, monkeypatch, fake_ready_mb):
    """★ R4：/api/ready 加载白名单外的程序号必须被拒，**且在任何 Modbus 操作之前**。

    测试环境真实下发总闸关闭，/api/ready 会先被「真实下发未开启」闸门挡住，
    所以这里 monkeypatch 绕过闸门，专门验证白名单这一层逻辑。
    ★ 必须同时用假 Modbus：绕过闸门后若实现顺序写错（先清报警/上伺服），
      就会真的读写 192.168.1.12。fake_ready_mb 保证不出网，并记录调用供断言。
    """
    import app.services.motion as motion
    import app.services.rc_ready as rc_ready
    monkeypatch.setattr(motion, "real_write_enabled", lambda: True)
    monkeypatch.setattr(rc_ready, "real_write_enabled", lambda: True)
    r = client.post("/api/ready", json={"prog": 9999}, headers=_admin(client))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("ok") is False
    assert "allowed_programs" in d
    assert 9999 not in d["allowed_programs"]
    assert "不在就绪白名单" in (d.get("error") or "")
    # ★ 关键：白名单拒绝发生在任何 Modbus 操作（含读快照）之前
    assert fake_ready_mb.calls == [], f"白名单拒绝前不该有任何 Modbus 调用: {fake_ready_mb.calls}"


def test_ready_allows_whitelisted_program(client, monkeypatch, fake_ready_mb):
    """★ R4：白名单内的程序号（200）不被白名单拦截。

    ★ 用假 Modbus：否则 prog=200 会一路走到「清报警/上伺服/加载/运行」，真的碰控制器。
    """
    import app.services.motion as motion
    import app.services.rc_ready as rc_ready
    monkeypatch.setattr(motion, "real_write_enabled", lambda: True)
    monkeypatch.setattr(rc_ready, "real_write_enabled", lambda: True)
    r = client.post("/api/ready", json={"prog": 200}, headers=_admin(client))
    assert r.status_code == 200, r.text
    d = r.json()
    # 关键：绝不能是「白名单」原因的拒绝
    assert "不在就绪白名单" not in (d.get("error") or "")


# ---------------------------------------------------------------- 就绪流程：停机时机
def test_ready_does_not_stop_healthy_running_program(client, monkeypatch, fake_ready_mb):
    """★★ 回归：程序已正常运行且**无报警**时，就绪流程不得发 CMD_STOP ★★

    缺陷实景（2026-09-29 真机）：为"清报警必须先停机"新增的 STOP 被写成**无条件**执行
    —— 于是"程序本来正常在跑、没有报警"这条最常见的路径也被停掉；而 STOP 之后
    加载状态已被打断，`_prog_ok` 却认为已完成加载会跳过 CMD_LOAD，紧接着 CMD_RUN
    起不来（实测：运行位 2.5s 不置位，一键就绪直接失败）。
    本条把"无报警绝不停机"钉死。
    """
    import app.services.motion as motion
    import app.services.rc_ready as rc_ready
    monkeypatch.setattr(motion, "real_write_enabled", lambda: True)
    monkeypatch.setattr(rc_ready, "real_write_enabled", lambda: True)
    # 假控制器：200 已在运行、无报警
    fake = fake_ready_mb
    r = client.post("/api/ready", json={"prog": 200}, headers=_admin(client))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("ok") is True, d
    cmds = [c[1] for c in fake.calls if isinstance(c, tuple) and c[0] == "cmd"]
    assert rc_ready.CMD_STOP not in cmds, (
        "无报警时不该发 CMD_STOP（实测会把健康的常驻程序停掉→运行位起不来）: %s" % cmds)
    steps = [s["step"] for s in d.get("steps", [])]
    assert "stop" not in steps, "无报警时不该出现 stop 步骤: %s" % steps


def test_ready_stops_before_clearing_alarm(client, monkeypatch, fake_ready_mb):
    """★★ 回归：**有报警**时必须「先 CMD_STOP 停机、再 CMD_CLEAR 清报警」★★

    真机复现：程序还在跑时清报警，报警 0.5s 内就被它重新顶上来
    （实测 alarm=1/run=1 → STOP 后 run=0 → 此时 CLEAR 才生效且不复现）。
    顺序错了就会假报"清报警后仍处于报警状态(5005)"，并把矛头指向示教器。
    """
    import app.api.robot as robot_api
    import app.services.motion as motion
    import app.services.rc_ready as rc_ready
    monkeypatch.setattr(motion, "real_write_enabled", lambda: True)
    monkeypatch.setattr(rc_ready, "real_write_enabled", lambda: True)

    class _MB:
        """最小控制器模型：STOP 后运行位真的落下；报警恒清不掉（只为验证**顺序**）。"""

        def __init__(self):
            self.calls = []
            self.run = 1

        def rc_snapshot(self):
            self.calls.append("snapshot")
            return {
                "ok": True, "status_word": 0, "mode": "auto",
                "bits": {"servo": 1, "alarm": 1, "prog_loaded": 1, "run": self.run,
                         "manual": 0, "auto": 1, "remote": 0, "estop": 0},
                "prog": 200, "alarm1": 5005, "alarm2": 0,
                "jog_trig": False, "speed_pct": 5, "joints": [0.0] * 6,
            }, None

        def rc_command(self, cmd):
            self.calls.append(("cmd", cmd))
            if cmd == rc_ready.CMD_STOP:
                self.run = 0     # ★ 停机后运行位落下（否则流程走不到清报警，测不出顺序）
            return True, None

        def write_reg(self, addr, val):
            self.calls.append(("write", addr, val))
            return val, None

    fake = _MB()
    monkeypatch.setattr(robot_api._ready, "_mb", fake, raising=True)
    r = client.post("/api/ready", json={"prog": 200}, headers=_admin(client))
    assert r.status_code == 200, r.text
    cmds = [c[1] for c in fake.calls if isinstance(c, tuple) and c[0] == "cmd"]
    assert rc_ready.CMD_STOP in cmds, "有报警且程序在跑时必须先停机: %s" % cmds
    assert rc_ready.CMD_CLEAR in cmds, "必须尝试清报警: %s" % cmds
    assert cmds.index(rc_ready.CMD_STOP) < cmds.index(rc_ready.CMD_CLEAR), (
        "顺序必须是 STOP 早于 CLEAR，实测反了就会把 5005 一次次顶回来: %s" % cmds)


# ---------------------------------------------------------------- 急停结果可见（B-06）
def test_estop_returns_stopped(client):
    """★ B-06：急停必须返回 stopped=true（不能「以为停了实际没停」）。"""
    r = client.post("/api/control/estop", headers=_admin(client))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("stopped") is True
    # 复位后 stopped 回到 false
    r2 = client.post("/api/control/estop/reset", headers=_admin(client))
    assert r2.status_code == 200, r2.text
    assert r2.json().get("stopped") is False


# ---------------------------------------------------------------- R3 真机不可误写（环境保证）
def test_real_motion_disabled_in_test_env():
    """★ R3：测试环境必须强制 simulate / 关闭真实下发，杜绝误写 192.168.1.12:502。"""
    import os
    assert os.environ.get("EFORT_REAL_MOTION") == "0", "测试环境 EFORT_REAL_MOTION 必须为 0"
    assert os.environ.get("EFORT_SIMULATE") == "always", "测试环境 EFORT_SIMULATE 必须为 always"
    from app.services import motion as motion
    # 任何 command 的 real 形参在测试环境都不应走到真机写
    assert motion.real_write_enabled() is False
