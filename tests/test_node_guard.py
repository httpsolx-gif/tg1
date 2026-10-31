"""node_guard: правила/скрипты и порядок «применить → подтвердить новым SSH»."""
import asyncio
import subprocess
from types import SimpleNamespace

from common.models.node import Node
from bot.services import node_guard as g
from bot.services.node_runner import NodeUnreachable


def _node(purpose="send", port=None):
    return Node(id=1, login="root", ip="10.0.0.1", password_enc="x", port=port, purpose=purpose)


def test_ruleset_ports():
    send = g.nft_ruleset(22, web=False)
    assert "policy drop" in send and "tcp dport 22 accept" in send and "80, 443" not in send
    assert "80, 443" in g.nft_ruleset(2222, web=True)


def test_sig_follows_role_and_port():
    a, b, c = _node("send"), _node("both"), _node("send", port=2222)
    assert g.desired_sig(a) != g.desired_sig(b) != g.desired_sig(c)
    assert g.desired_sig(a) == g.desired_sig(_node("send", port=22))


def test_scripts_bash_syntax():
    for s in (g.apply_script(22, True), g.confirm_script(22, ["1.2.3.4/32"])):
        r = subprocess.run(["bash", "-n"], input=s, text=True, capture_output=True)
        assert r.returncode == 0, r.stderr


def test_jail_ignores_bot_ip():
    j = g.jail_conf(22, ["5.6.7.8/32"])
    assert "$BOT_IP" in j and "5.6.7.8/32" in j and "port = 22" in j


def _fake(outs, calls):
    async def run(node, cmd, *, input=None, timeout=30):
        calls.append(input)
        o = outs.pop(0)
        if isinstance(o, Exception):
            raise o
        return SimpleNamespace(stdout=o, stderr="", exit_status=0)
    return run


def test_harden_ok(monkeypatch):
    calls = []
    monkeypatch.setattr(g, "_run_on_node", _fake(["APPLIED", "FW_OK\nF2B_OK"], calls))
    ok, info = asyncio.run(g.harden(_node()))
    assert ok, info
    assert "systemd-run" in calls[0] and "nodefw-rollback.timer" in calls[1]


def test_harden_lockout_reports_rollback(monkeypatch):
    calls = []
    monkeypatch.setattr(g, "_run_on_node", _fake(["APPLIED", NodeUnreachable("timeout")], calls))
    ok, info = asyncio.run(g.harden(_node()))
    assert not ok and "откатит" in info
