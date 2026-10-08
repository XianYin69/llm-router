"""DSM 信封 ↔ EGC 出网字节的**整条**转换基准（独立脚本·可单独跑）。

    python -B tests/bench_dsm_egc.py

为什么单独成文件、且由测试起子进程跑：同进程测量会被套件的 CPU/GC 噪声污染
（实测同一段转换：隔离 p95 0.77ms、满载 6.15ms）——那种红灯是假的，绿灯也可能是假的。

规模＝真实量级：40 工具 schema ＋ 20 轮工具往返（出网约 15 KB）。
链路＝信封 → dsm.materialize → egc.egress →（提供商回声短 id）→ egc.restore_ids
→ dsm.encode_response，即「DSM 进、DSM 出」的完整一跳。
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from SMSocket import dsm, egc  # noqa: E402

TOOLS = [{"type": "function", "function": {
    "name": "ztool_%02d" % i, "description": "工具 %d：带中文说明的动作。" % i,
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string", "description": "路径"}}, "required": ["path"]}}}
    for i in range(40)]
SYSTEM = "内核规则。\n当前时间：2026-10-06 19:20\n" * 40


def build():
    tmp = tempfile.mkdtemp()
    store = dsm.SchemaStore(os.path.join(tmp, "s.json"))
    reg = dsm.register_schema("", SYSTEM, TOOLS, store)
    msgs = [{"role": "user", "content": "读配置"}]
    for i in range(20):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": "tc%d" % i, "type": "function",
             "function": {"name": "ztool_01",
                          "arguments": json.dumps({"path": "配置/中文%d.json" % i})}}]})
        msgs.append({"role": "tool", "tool_call_id": "tc%d" % i,
                     "content": json.dumps({"ok": True, "note": "结果%d" % i})})
    msgs.append({"role": "user", "content": "结论？"})
    env = {"v": 1, "sid": "s1", "lane": "l1", "sch": reg["ref"], "x": {"sms.model": "demo"},
           "d": [list(dsm.encode_turn(m)) for m in msgs]}
    return store, env


def main(iters=200):
    store, env = build()

    def one():
        t0 = time.perf_counter()
        body = dsm.materialize(env, "demo", "openai-chat", store=store, use_session=False)
        w = egc.egress(body, lane="l1")
        tc = next(t for m in w.body["messages"] for t in (m.get("tool_calls") or []))
        resp = {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": tc["id"], "type": "function",
                            "function": {"name": "ztool_01", "arguments": "{}"}}]}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        egc.restore_ids(resp, w.ids)
        out = dsm.encode_response(resp, env, alias="demo")
        return (time.perf_counter() - t0) * 1000.0, out, w

    for _ in range(30):                       # 预热（schema store / memo 都要热）
        _, last, _w = one()
    assert last["answer"][0]["id"] == "tc0", last["answer"][0]["id"]   # 无损：原 id 回家
    xs = [one()[0] for _ in range(iters)]
    q = lambda p: round(sorted(xs)[int(p * (len(xs) - 1))], 3)          # noqa: E731
    print("MS %s %s %s" % (q(0.5), q(0.95), round(max(xs), 3)))
    print("WIRE %d" % _w.bytes_out)
    return q(0.95)


if __name__ == "__main__":
    p95 = main()
    sys.exit(0 if p95 < 5.0 else 1)
