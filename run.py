"""Entry point: python run.py [--host 0.0.0.0] [--port 8000]"""
import argparse
import os

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="llm-router gateway")
    ap.add_argument("--config", default=os.environ.get("LLMROUTER_CONFIG", "config.yaml"))
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    os.environ["LLMROUTER_CONFIG"] = a.config
    uvicorn.run("llmrouter.app:app", host=a.host, port=a.port, reload=a.reload)


if __name__ == "__main__":
    main()
