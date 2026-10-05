"""Mock clash external-controller for local demo / e2e: python mock_clash.py 9099

Answers the endpoints SMSocket's ClashController uses, with a secret check, so
the net plane can be exercised without a real clash process:
  GET  /version, /proxies, /proxies/{name}/delay, /rules, /connections, /memory
  PUT  /proxies/{group}            select a node
  PUT|POST /cache/fakeip/flush     drop the DNS cache
Nodes answer with a deterministic delay so egress ranking is predictable.
"""
import sys
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

SECRET = "e2e-secret"
GROUPS = {"PROXY-GRP": ["hk-1", "jp-2", "us-east-2"]}
NOW = {g: nodes[0] for g, nodes in GROUPS.items()}
DELAY = {"hk-1": 120, "jp-2": 90, "us-east-2": 210, "PROXY-GRP": 120}
app = FastAPI(title="mock-clash")


def _denied(request: Request) -> bool:
    got = request.headers.get("authorization", "").replace("Bearer ", "")
    return got != SECRET


@app.get("/version")
async def version():
    return {"meta": {"externalController": True}, "version": "1.18.0-mock"}


@app.get("/proxies")
async def proxies():
    out = {"DIRECT": {"type": "Direct", "now": "DIRECT", "hidden": True, "all": ["DIRECT"]}}
    for g, nodes in GROUPS.items():
        out[g] = {"type": "Selector", "now": NOW[g], "hidden": False, "all": list(nodes),
                  "history": [{"time": "t", "delay": DELAY.get(n, 100)} for n in nodes]}
    for n in {x for v in GROUPS.values() for x in v}:
        out[n] = {"type": "SS", "now": n, "hidden": True, "all": [n],
                  "history": [{"time": "t", "delay": DELAY.get(n, 100)}]}
    return {"proxies": out}


@app.get("/proxies/{name}/delay")
async def delay(name: str, expected: int = 204, timeout: float = 5):
    # clash's real shape is {"delay": N} - the controller reads that key only
    return {"delay": DELAY.get(name, 150)}


@app.put("/proxies/{group}")
async def select(group: str, request: Request):
    body = await request.json()
    node = str(body.get("name") or "")
    if group not in GROUPS or node not in GROUPS[group]:
        return JSONResponse({"error": f"no such node {group}/{node}"}, status_code=400)
    NOW[group] = node
    return {"ok": True, "group": group, "now": node}


@app.get("/rules")
async def rules():
    return {"rules": [{"type": "DOMAIN-SUFFIX", "payload": "test", "proxy": "PROXY-GRP"}]}


@app.get("/connections")
async def connections():
    return {"connections": [], "traffic": 0, "upload": 0, "download": 0}


@app.get("/memory")
async def memory():
    return {"inuse": 12345678}


@app.put("/cache/fakeip/flush")
async def flush_put():
    return {"ok": True, "via": "put"}


@app.post("/cache/fakeip/flush")
async def flush_post():
    return {"ok": True, "via": "post"}


@app.middleware("http")
async def auth(request: Request, call_next):
    if _denied(request):
        return JSONResponse({"error": {"message": "unauthorized"}}, status_code=401)
    return await call_next(request)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9090
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
