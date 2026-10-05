"""Minimal absolute-URI HTTP proxy for the e2e run: python mock_proxy.py 7890

httpx talks to an `http://` proxy by sending `GET http://host/path HTTP/1.1`, so
this is all that is needed to prove a call really left through the clash
mixed-port path: it forwards to the target and pipes the answer back.
"""
import asyncio
import sys


async def copy(reader, writer):
    try:
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (OSError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


def split_target(url: str) -> tuple[str, int, str]:
    if url.startswith("http://"):
        hostport, _, path = url[len("http://"):].partition("/")
        host, _, port = hostport.partition(":")
        return host or "127.0.0.1", int(port or 80), "/" + path
    return "127.0.0.1", 80, url


async def handle(reader, writer):
    try:
        line = await asyncio.wait_for(reader.readline(), 10)
        parts = line.decode("latin-1").split()
        if len(parts) < 2:
            writer.close()
            return
        method, url = parts[0], parts[1]
        keep = []
        while True:
            h = await asyncio.wait_for(reader.readline(), 10)
            if h in (b"\r\n", b"\n", b""):
                break
            name = h.split(b":", 1)[0].lower()
            if name in (b"proxy-connection", b"connection"):
                continue
            keep.append(h)
        host, port, path = split_target(url)
        head = (f"{method} {path} HTTP/1.1\r\n".encode() + b"".join(keep)
                + b"connection: close\r\n\r\n")
        try:
            rd, wr = await asyncio.open_connection(host, port)
        except OSError:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\ncontent-length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        wr.write(head)
        await wr.drain()
        await asyncio.gather(copy(rd, writer), copy(reader, wr))
    except (OSError, asyncio.TimeoutError, ValueError):
        try:
            writer.close()
        except OSError:
            pass


async def main(port: int) -> None:
    server = await asyncio.start_server(handle, "127.0.0.1", port)
    print("mock proxy on 127.0.0.1:%d" % port, flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 7890))
