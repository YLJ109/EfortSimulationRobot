# -*- coding: utf-8 -*-
"""L1 示教器档位声明（runmode）+ 底栏链路状态（/api/system/guide 的 links）测试。

覆盖：
  - 鉴权门：声明/撤销都需要控制令牌
  - 声明语义（★ 2026-09-23 实机确认后反转）：AUTO/REMOTE 才允许 PC 点动/下发
    （上位机控制的唯一有效档位）；T1/T2 下控制器忽略上位机指令 → 拒绝并指路
  - 未声明：**放行但留痕**（与围栏互锁同策略），响应里带 mode_warning
  - 声明不持久化 + 超时视为未确认
  - /guide 的 links 任何情况下都齐全（机器人 / 摄像头 / 示教器档位）

★ 注意：runmode / motion / jog 都是模块级全局状态，测试间必须复位，否则串味
  （先前的点动测试就踩过这个坑）。
"""
from __future__ import annotations

import pytest

from app.services.runmode import CLAIM_TTL_SEC, runmode

HOME = [0.0] * 6


@pytest.fixture(autouse=True)
def _reset_state():
    """模块级全局状态复位（声明 / 执行引擎 / 互锁），测试间互不影响。"""
    from app.services import safety_guard
    from app.services.jog import jog
    from app.services.motion import motion

    def _clean():
        # ★ 必须用 reset() 而不是 clear()：clear() 只撤人工声明，**不动控制器实测值**
        #   （业务语义如此），测试里喂过 observe() 之后残留的实测档位会串到下一个用例。
        runmode.reset()
        jog.stop("user")
        motion.reset_estop()
        motion.last_target = None
        safety_guard.update("safe", "", "", 0.0, 1.0)

    _clean()
    yield
    _clean()


def _h(client):
    r = client.post("/api/auth/login", json={"password": "test1234"})
    assert r.status_code == 200, r.text
    return {"X-Control-Token": r.json()["token"]}


def _home(client, h):
    r = client.post("/api/control/move", json={"joints": HOME}, headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True, r.text


def _claim(client, h, mode):
    return client.post("/api/control/run-mode", json={"mode": mode}, headers=h)


# ---------------------------------------------------------------- 鉴权
def test_run_mode_requires_token(client):
    assert client.get("/api/control/run-mode").status_code == 401
    assert client.post("/api/control/run-mode", json={"mode": "T1"}).status_code == 401
    assert client.delete("/api/control/run-mode").status_code == 401


# ---------------------------------------------------------------- 声明语义
def test_claim_auto_allows_jog(client):
    """★ 核心断言（2026-09-23 实机确认后反转）：AUTO 是上位机控制的唯一有效档位，
    声明为 AUTO 后点动必须放行。"""
    h = _h(client)
    r = _claim(client, h, "AUTO")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["mode"] == "AUTO"
    assert d["confirmed"] is True and d["joggable"] is True

    _home(client, h)
    st = client.post("/api/control/jog/step",
                     json={"joint": 1, "dir": 1, "angle_deg": 1}, headers=h)
    assert st.status_code == 200, st.text
    assert st.json()["ok"] is True
    assert "mode_warning" not in st.json()   # 已声明 → 不该有"未声明"提示


def test_claim_t1_blocks_jog(client):
    """★ T1/T2 下控制器忽略上位机指令（实机确认）——软件层直接拒绝并指路。"""
    h = _h(client)
    assert _claim(client, h, "T1").json()["joggable"] is False
    _home(client, h)

    st = client.post("/api/control/jog/step",
                     json={"joint": 1, "dir": 1, "angle_deg": 1}, headers=h)
    assert st.status_code == 409, st.text
    body = st.json()
    msg = str(body.get("message") or body.get("detail") or "")
    assert "T1" in msg, body
    assert ("AUTO" in msg) or ("远程" in msg), body   # 必须给出下一步动作

    st2 = client.post("/api/control/jog/start",
                      json={"joint": 1, "dir": 1, "speed_dps": 5}, headers=h)
    assert st2.status_code == 409, st2.text
    assert runmode.state()["joggable"] is False

    # 切回 AUTO 后立刻恢复可点动
    assert _claim(client, h, "AUTO").json()["joggable"] is True
    st3 = client.post("/api/control/jog/step",
                      json={"joint": 1, "dir": 1, "angle_deg": 1}, headers=h)
    assert st3.status_code == 200 and st3.json()["ok"] is True


def test_remote_mode_allows(client):
    h = _h(client)
    assert _claim(client, h, "REMOTE").json()["joggable"] is True
    _home(client, h)
    r = client.post("/api/control/jog/step",
                    json={"joint": 1, "dir": 1, "angle_deg": 1}, headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True


def test_unclaimed_allows_but_warns(client):
    """未声明 → 放行（否则真机联调前没法用）但必须留痕。"""
    h = _h(client)
    _home(client, h)
    st = client.post("/api/control/jog/step",
                     json={"joint": 1, "dir": 1, "angle_deg": 1}, headers=h)
    assert st.status_code == 200, st.text
    assert st.json()["ok"] is True
    assert "未声明" in st.json().get("mode_warning", "")

    ev = client.get("/api/events", params={"category": "control", "limit": 50}, headers=h)
    assert ev.status_code == 200, ev.text
    actions = [e["action"] for e in ev.json()["items"]]
    assert "control.jog_mode_unclaimed" in actions, actions


def test_invalid_mode_rejected(client):
    h = _h(client)
    r = _claim(client, h, "T9")
    assert r.status_code == 400, r.text
    assert runmode.state()["confirmed"] is False   # 非法输入不得污染状态


def test_claim_expires(client, monkeypatch):
    """声明超时 → 视为未确认（防止"昨天的声明"给今天授权）。"""
    h = _h(client)
    assert _claim(client, h, "T1").json()["confirmed"] is True
    monkeypatch.setattr("app.services.runmode.CLAIM_TTL_SEC", -1)
    st = runmode.state()
    assert st["confirmed"] is False and st["stale"] is True
    assert st["mode"] == "" and st["claimed_mode"] == "T1"
    # 过期后不再拦（因为已不算"确定危险"），但会带提醒
    _home(client, h)
    r = client.post("/api/control/jog/step", json={"joint": 1, "dir": 1}, headers=h)
    assert r.status_code == 200
    assert "过期" in r.json().get("mode_warning", "")
    monkeypatch.setattr("app.services.runmode.CLAIM_TTL_SEC", CLAIM_TTL_SEC)


def test_clear_resets(client):
    h = _h(client)
    _claim(client, h, "AUTO")
    r = client.delete("/api/control/run-mode", headers=h)
    assert r.status_code == 200
    assert r.json()["confirmed"] is False
    assert r.json()["mode"] == ""


def test_claim_emits_event(client):
    h = _h(client)
    _claim(client, h, "T2")
    ev = client.get("/api/events", params={"category": "control", "limit": 50}, headers=h)
    assert ev.status_code == 200
    items = ev.json()["items"]
    hit = [e for e in items if e["action"] == "control.run_mode"]
    assert hit, [e["action"] for e in items]
    assert "T2" in hit[0]["message"]


# ---------------------------------------------------------------- 底栏链路状态
def test_guide_links_always_complete(client):
    """★ 底栏三个状态灯的数据源必须**任何情况下都齐全** —— 这正是本次改动的目的：
    「不管有没有自动模式都要显示」，所以 run_mode 不能因为"没声明"就缺席。"""
    r = client.get("/api/system/guide")
    assert r.status_code == 200, r.text
    d = r.json()
    links = d.get("links")
    assert isinstance(links, dict), d
    for k in ("robot", "camera", "run_mode"):
        assert k in links, list(links)

    rob = links["robot"]
    assert set(("ok", "state", "level", "label", "detail", "host", "port")) <= set(rob)
    # 测试环境强制 simulate=always → 必须是"强制模拟"(warn)，
    # ★ 不能是 "sim"(err)：那是"真机掉线降级"，会给现场天天误报警，两者必须分开
    assert rob["state"] == "sim_forced" and rob["simulated"] is True
    assert rob["level"] == "warn"
    assert "always" in rob["detail"]

    cam = links["camera"]
    assert set(("ok", "state", "level", "label", "detail", "base")) <= set(cam)
    # 测试环境没有相机服务 → down，且必须给出"怎么恢复"的提示
    assert cam["state"] in ("down", "idle", "error", "ready")
    assert cam["detail"]

    rm = links["run_mode"]
    assert set(("mode", "confirmed", "label", "level", "joggable")) <= set(rm)
    assert rm["confirmed"] is False and rm["label"] == "未确认"
    assert rm["level"] == "warn"


def test_robot_link_state_machine(monkeypatch):
    """★ 机器人链路的四态状态机逐档钉死。

    这是"按状态取色"的经典坑：三态写法会把"强制模拟"和"掉线降级"合并，
    结果是**真故障被长期的黄色/红色噪声掩盖**。这里把四档的 state+level 都断言出来。
    """
    import app.api.system as sysmod

    cases = [
        # (connected, simulated, simulate, 期望 state, 期望 level)
        (True, False, "never", "real", "ok"),
        (False, True, "always", "sim_forced", "warn"),
        (False, True, "auto", "sim", "err"),
        (False, False, "auto", "offline", "err"),
        (False, False, "never", "offline", "err"),
    ]
    for conn, sim, mode, want_state, want_level in cases:
        monkeypatch.setattr(sysmod.collector, "connected", conn)
        monkeypatch.setattr(sysmod.collector, "simulated", sim)
        r = sysmod._robot_link("10.0.0.1", 502, mode)
        assert r["state"] == want_state, (conn, sim, mode, r)
        assert r["level"] == want_level, (conn, sim, mode, r)
        assert r["label"] and r["detail"], r
    assert sysmod._robot_link("10.0.0.1", 502, "always")["ok"] is False


def test_guide_links_reflect_claim(client):
    h = _h(client)
    _claim(client, h, "T1")
    d = client.get("/api/system/guide").json()
    rm = d["links"]["run_mode"]
    assert rm["confirmed"] is True and rm["mode"] == "T1"
    assert rm["joggable"] is False and rm["level"] == "err"
    # 档位是"常态提示"：level=info → 不许抢主提示条（否则后端一重启它永远霸屏）
    mod = [c for c in d["checks"] if c["key"] == "run_mode"]
    assert mod and mod[0]["level"] == "info"
    if d["primary"]:
        assert d["primary"]["key"] != "run_mode"


def test_guide_links_cached_camera(client, monkeypatch):
    """相机探测必须带缓存：相机服务没起时不能每次自检都等满超时。"""
    import app.api.system as sysmod
    from app.services import camera_client

    sysmod._LINK_CAM["data"] = None
    sysmod._LINK_CAM["ts"] = 0.0
    calls = {"n": 0}
    orig = camera_client.get_json

    def _wrap(path, timeout=None):
        calls["n"] += 1
        return orig(path, timeout)

    monkeypatch.setattr(camera_client, "get_json", _wrap)
    client.get("/api/system/guide")
    first = calls["n"]
    assert first >= 1
    client.get("/api/system/guide")
    assert calls["n"] == first, "第二次自检不该再打相机服务（缓存未生效）"


# ---------------------------------------------------------------- 控制器实测档位（自动确认）
# ★ 用户反馈的落点：「示教器上面 auto 模式就已确认」「都可以控制了，不用跑机器人」——
#   旋钮实际打在哪一档，控制器状态字里最清楚，不该再让操作员手动声明一遍。
def test_observe_auto_auto_confirms():
    """★ 核心：控制器实测 auto → 直接算已确认、可点动，**无需人工声明**。"""
    r = runmode.observe("auto")
    assert r["changed"] is True and r["mode"] == "AUTO"
    st = runmode.state()
    assert st["confirmed"] is True
    assert st["source"] == "controller"
    assert st["mode"] == "AUTO" and st["joggable"] is True
    assert st["observed_mode"] == "AUTO" and st["observed_raw"] == "auto"
    assert st["observed_age_sec"] is not None and st["observed_age_sec"] <= 1
    assert "实测" in st["desc"]
    assert runmode.level() == "ok"


def test_observe_remote_allows():
    runmode.observe("remote")
    st = runmode.state()
    assert st["source"] == "controller" and st["joggable"] is True
    assert runmode.level() == "ok"


def test_observe_manual_blocks_and_says_t1t2():
    """★ 控制器只报"手动档"三个 bit，分不出 T1/T2 —— 文案必须如实说明，不假装知道。"""
    runmode.observe("manual")
    st = runmode.state()
    assert st["confirmed"] is True and st["source"] == "controller"
    assert st["joggable"] is False
    assert st["label"] == "手动档（T1/T2）"
    assert "分不出" in st["desc"] or "T1" in st["desc"]
    assert runmode.level() == "err"

    ok, why, _st = runmode.check_jog()
    assert ok is False
    assert "手动档" in why and ("AUTO" in why or "远程" in why)


def test_observe_unknown_keeps_previous():
    """★ unknown（三模式位全 0，典型是上电/切换瞬间）视为"这拍没读到"，
    保留上一次实测值让它按 TTL 自然过期 —— 见 unknown 就清空会让界面在上电瞬间
    黄一下再变绿，比"最多 30s 的旧值"更误导人。"""
    runmode.observe("auto")
    r = runmode.observe("unknown")
    assert r["changed"] is False and r["mode"] == ""
    st = runmode.state()
    assert st["mode"] == "AUTO" and st["source"] == "controller"


def test_observe_expires(monkeypatch):
    """控制器掉线/断电后灯必须自己回"未确认"，不能拿着半小时前的绿色继续骗人。"""
    runmode.observe("auto")
    assert runmode.state()["confirmed"] is True
    monkeypatch.setattr("app.services.runmode.OBSERVE_TTL_SEC", -1)
    st = runmode.state()
    assert st["confirmed"] is False and st["mode"] == "" and st["source"] == ""
    assert st["observed_mode"] == "" and st["observed_age_sec"] is None
    assert runmode.level() == "warn"


def test_controller_observation_beats_manual_claim():
    """★ 实测优先：控制器连上时，人工声明只作备注，不得改变判定 ——
    "旋钮在 T1、界面声明 AUTO"这种自相矛盾的状态不能被当成放行依据。"""
    runmode.claim("T1", actor="op")
    assert runmode.state()["source"] == "manual"

    runmode.observe("auto")
    st = runmode.state()
    assert st["source"] == "controller" and st["mode"] == "AUTO"
    assert st["joggable"] is True
    assert st["claimed_mode"] == "T1"      # 声明仍在，仅作界面提示


def test_clear_keeps_observation():
    """clear() 只撤人工声明，**不动实测值**：实测来自硬件，清掉等于假装读不到旋钮位置。"""
    runmode.observe("auto")
    runmode.claim("T1", actor="op")
    runmode.clear()
    st = runmode.state()
    assert st["source"] == "controller" and st["mode"] == "AUTO"
    assert st["claimed_mode"] == ""


def test_observed_auto_allows_jog_without_claim(client):
    """★ 端到端：控制器实测 auto 时，**不声明**也能点动，且不该带"未声明"提示。"""
    h = _h(client)
    runmode.observe("auto")
    _home(client, h)
    st = client.post("/api/control/jog/step",
                     json={"joint": 1, "dir": 1, "angle_deg": 1}, headers=h)
    assert st.status_code == 200, st.text
    assert st.json()["ok"] is True
    assert "mode_warning" not in st.json()


def test_observed_manual_blocks_jog(client):
    """★ 端到端：控制器实测手动档 → 点动被拒，且错误信息指向"拨到 AUTO/远程"。"""
    h = _h(client)
    runmode.observe("manual")
    _home(client, h)
    st = client.post("/api/control/jog/step",
                     json={"joint": 1, "dir": 1, "angle_deg": 1}, headers=h)
    assert st.status_code == 409, st.text
    body = st.json()
    msg = str(body.get("message") or body.get("detail") or "")
    assert "手动档" in msg, body
    assert ("AUTO" in msg) or ("远程" in msg), body


def test_feed_run_mode_wires_collector_to_runmode(client):
    """★ 链路完整性：采集循环喂档位 → runmode 自动确认 → 底栏灯变绿。

    这是"实测自动确认"的整条链路守卫：collector.feed_run_mode 是唯一的入口，
    谁把它从 _publish_rc_status 里摘掉，这里就会红。
    """
    from app.services.collector import feed_run_mode

    feed_run_mode("auto", status_word=2)
    st = runmode.state()
    assert st["confirmed"] is True and st["source"] == "controller"

    rm = client.get("/api/system/guide").json()["links"]["run_mode"]
    assert rm["confirmed"] is True and rm["mode"] == "AUTO"
    assert rm["source"] == "controller" and rm["level"] == "ok"
    assert rm["observed_age_sec"] is not None

    h = _h(client)
    ev = client.get("/api/events", params={"category": "control", "limit": 50}, headers=h)
    actions = [e["action"] for e in ev.json()["items"]]
    assert "control.run_mode_observed" in actions, actions
