"""Entry point: python run.py [--config config.yaml] [--host --port --reload]"""
from __future__ import annotations

import argparse
import os


def main() -> None:
    ap = argparse.ArgumentParser(description="llm-router gateway")
    ap.add_argument("--config", default=None, help="config file (yaml/json)")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()

    if a.config:
        os.environ["LLMROUTER_CONFIG"] = a.config
    from llm_router.config import load_config
    st = load_config(os.environ.get("LLMROUTER_CONFIG", "config.yaml"))
    host, _, port = st.listen.rpartition(":")
    host = a.host or host or "127.0.0.1"
    port = a.port or int(port or 8000)
    if not st.providers:
        print("no providers configured - copy config.example.yaml to config.yaml")

    import uvicorn
    uvicorn.run("llm_router.gateway:create_app", host=host, port=port,
                factory=True, reload=a.reload)


if __name__ == "__main__":
    main()
