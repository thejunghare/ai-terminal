"""Unix-socket server the zsh plugin talks to.

Wire protocol (kept trivial so zsh can speak it with zsocket, no JSON):
  request:  <type>\\0<cwd>\\0<payload>\\0
  response: UTF-8 text, then the server closes the connection.
            Errors are prefixed with "ERR: ".
"""
import asyncio
import logging
import os
import signal
import sys

from . import __version__, explain, llm

SOCK_PATH = os.environ.get("AIT_SOCK", os.path.expanduser("~/.ai-terminal.sock"))
LOG_PATH = os.path.expanduser("~/Library/Logs/ai-terminal.log")
MAX_REQUEST = 64 * 1024

log = logging.getLogger("aitd")


async def _read_request(reader: asyncio.StreamReader) -> list[str]:
    buf = b""
    while buf.count(b"\0") < 3:
        chunk = await reader.read(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > MAX_REQUEST:
            raise ValueError("request too large")
    parts = buf.split(b"\0")
    if len(parts) < 4:
        raise ValueError("malformed request")
    return [p.decode("utf-8", "replace") for p in parts[:3]]


async def _dispatch(kind: str, cwd: str, payload: str) -> str:
    if kind == "ping":
        return f"pong {__version__} ({llm.active_provider()})"
    if kind == "explain":
        return await explain.explain(payload, cwd)
    raise ValueError(f"unknown request type: {kind}")


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        kind, cwd, payload = await asyncio.wait_for(_read_request(reader), timeout=5)
        log.info("request %s", kind)
        reply = await _dispatch(kind, cwd, payload)
    except llm.MissingKey as e:
        reply = f"ERR: {e}"
    except llm.LLMError as e:
        log.warning("llm error: %s", e)
        reply = f"ERR: {e}"
    except Exception as e:  # never let one bad request kill the daemon
        log.exception("request failed")
        reply = f"ERR: {e}"
    try:
        writer.write(reply.encode())
        await writer.drain()
    finally:
        writer.close()


async def _serve() -> None:
    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)
    server = await asyncio.start_unix_server(_handle, path=SOCK_PATH)
    os.chmod(SOCK_PATH, 0o600)
    log.info("aitd %s listening on %s", __version__, SOCK_PATH)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async with server:
        await stop.wait()
    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)


def main() -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(
        filename=None if "--foreground" in sys.argv else LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(_serve())
