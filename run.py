"""Entry point: python run.py [--config config.yaml] [--host --port --reload]
                       [--no-key | --rotate-key]

Startup prints a banner in the console: web dashboard URL, OpenAI-compatible
base URL, and the client key in effect (randomly generated on first run and
reused afterwards, stored in router.key next to the config).
"""
from __future__ import annotations
import argparse
import os


def main() -> None:
    ap = argparse.ArgumentParser(description="llm-router gateway")
    ap.add_argument("--config", default=None, help="config file (yaml/json)")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--no-key", action="store_true",
                    help="start without client key auth (open gateway)")
    ap.add_argument("--rotate-key", action="store_true",
                    help="mint a fresh random key (overwrites router.key)")
    a = ap.parse_args()
    if a.config:
        os.environ["LLMROUTER_CONFIG"] = a.config
    if a.no_key:
        os.environ["LLMROUTER_NO_KEY"] = "1"
    cfg = os.environ.get("LLMROUTER_CONFIG", "config.yaml")
    if a.rotate_key:
        from llm_router.keys import key_path
        try:
            key_path(cfg).unlink()
        except OSError:
            pass
    from llm_router.config import load_config
    st = load_config(cfg)
    host, _, port = st.listen.rpartition(":")
    host = a.host or host or "127.0.0.1"
    port = a.port or int(port or 8000)
    if not st.providers:
        print("no providers configured - copy config.example.yaml to config.yaml")
    banner(st, host, port)
    import uvicorn
    uvicorn.run("llm_router.gateway:create_app", host=host, port=port,
                factory=True, reload=a.reload)


def banner(st, host: str, port: int) -> None:
    """Console banner: web address + key (the user asked for both on startup)."""
    shown = "http://127.0.0.1:%d" % port if host in ("0.0.0.0", "", "*") \
        else "http://%s:%d" % (host, port)
    key = st.master_keys[0] if st.master_keys else None
    label = {"generated": "随机生成（已存 router.key，重启复用）",
             "reused": "复用 router.key",
             "env": "来自环境变量",
             "config": "来自配置文件 master_keys",
             "disabled": "未启用（--no-key，任何人可调用）"}.get(st.key_source, st.key_source)
    models = len(st.settings_model_index()) if hasattr(st, "settings_model_index") \
        else len(st.model_index())
    print("=" * 56)
    print("  llm-router 模型路由网关")
    print("  网页端   %s/" % shown)
    print("  API 地址 %s/v1" % shown)
    print("  密钥     %s" % (key or "-"))
    print("           %s" % label)
    print("  提供商   %d 个 · 上游密钥 %d 把 · 模型 %d 个"
          % (len(st.providers), sum(len(p.keys) for p in st.providers), models))
    print("=" * 56, flush=True)


if __name__ == "__main__":
    main()
