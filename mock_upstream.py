"""Mock upstream for local demo/tests: python mock_upstream.py 9099

Serves both OpenAI-compatible and Anthropic-shaped endpoints so the router can be
exercised without real API keys:
  POST /v1/chat/completions   (style: openai)
  POST /v1/messages           (style: anthropic)
Any key starting with "bad" returns 401 to demo failover/cooldown.
"""
import json
import sys
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="mock-upstream")


def _denied(request: Request) -> bool:
    key = request.headers.get("authorization", "").replace("Bearer ", "") \
        or request.headers.get("x-api-key", "")
    return key.startswith("bad")


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [
        {"id": "mock-model", "object": "model"},
        {"id": "claude-3-5-sonnet-latest", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    if _denied(request):
        return JSONResponse({"error": {"message": "invalid api key"}}, status_code=401)
    payload = await request.json()
    if payload.get("stream"):
        async def gen():
            for tok in ["hello", " from", " mock"]:
                yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": tok}}]}) + "\n\n"
                time.sleep(0.02)
            yield "data: " + json.dumps({"choices": [], "usage": {
                "prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return {"id": "mock", "object": "chat.completion", "model": payload.get("model"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hello from mock"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}


@app.post("/v1/messages")
async def messages(request: Request):
    if _denied(request):
        return JSONResponse({"type": "error", "error": {
            "type": "authentication_error", "message": "invalid x-api-key"}}, status_code=401)
    payload = await request.json()
    if payload.get("stream"):
        async def gen():
            def ev(o):
                return f"event: x\ndata: {json.dumps(o)}\n\n"
            yield ev({"type": "message_start",
                      "message": {"usage": {"input_tokens": 7, "output_tokens": 0}}})
            for tok in ["hi", " from", " anthropic-mock"]:
                yield ev({"type": "content_block_delta", "delta": {"type": "text_delta", "text": tok}})
            yield ev({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                      "usage": {"output_tokens": 4}})
            yield ev({"type": "message_stop"})
        return StreamingResponse(gen(), media_type="text/event-stream")
    return {"id": "msg_mock", "type": "message", "role": "assistant",
            "model": payload.get("model"),
            "content": [{"type": "text", "text": "hello from anthropic mock"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 7, "output_tokens": 4}}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1",
                port=int(sys.argv[1] if len(sys.argv) > 1 else 9099))
