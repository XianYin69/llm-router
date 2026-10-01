"""Mock OpenAI-compatible upstream for local demo/tests: python mock_upstream.py 9099"""
import json
import sys
import time

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "mock-model", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(payload: dict):
    if payload.get("stream"):
        async def gen():
            for tok in ["hello", " from", " mock"]:
                yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": tok}}]}) + "\n\n"
                time.sleep(0.02)
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return {"id": "mock", "object": "chat.completion", "model": payload.get("model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello from mock"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3}}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1] if len(sys.argv) > 1 else 9099))
