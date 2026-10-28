"""Unix-socket server the zsh plugin talks to.

Wire protocol (kept trivial so zsh can speak it with zsocket, no JSON):
  request:  <type>[:<option>]\\0<cwd>\\0<payload>\\0
            e.g. "explain:120" asks for output wrapped to 120 columns.
  response: UTF-8 text, then the server closes the connection.
            Errors are prefixed with "ERR: ".
"""
import asyncio
import fcntl
import glob
import logging
import os
import signal
import sys

from . import __version__, explain, generate, llm, suggest, why

SOCK_PATH = os.environ.get("AIT_SOCK", os.path.expanduser("~/.ai-terminal.sock"))
PID_PATH = SOCK_PATH + ".pid"
LOCK_PATH = SOCK_PATH + ".lock"
# Newest modification time of the daemon's own code. The plugin compares it with the
# files on disk and restarts a daemon that is running older code.
CODE_MTIME = int(max(os.path.getmtime(p) for p in glob.glob(os.path.join(os.path.dirname(__file__), "*.py"))))
LOG_PATH = os.path.expanduser("~/Library/Logs/ai-terminal.log")
MAX_REQUEST = 256 * 1024

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
    kind, _, option = kind.partition(":")
    if kind == "ping":
        return f"pong {__version__} ({llm.active_provider()}) build={CODE_MTIME}"
    if kind == "models":
        return await llm.probe_nvidia_models()
    if kind == "explain":
        return await explain.explain(payload, cwd, int(option) if option.isdigit() else 80)
    if kind == "generate":
        return await generate.generate(payload, cwd)
    if kind == "suggest":
        return await suggest.suggest(payload, cwd)
    if kind == "why":
        return await why.why(payload, cwd, int(option) if option.isdigit() else 80)
    raise ValueError(f"unknown request type: {kind}")


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        kind, cwd, payload = await asyncio.wait_for(_read_request(reader), timeout=5)
        if not kind.startswith("suggest"):          # suggestions fire on every pause
            log.info("request %s", kind)
        reply = await _dispatch(kind, cwd, payload)
    except asyncio.CancelledError:
        reply = ""
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
    except (ConnectionError, OSError):
        pass                    # the shell stopped waiting (e.g. user kept typing)
    finally:
        writer.close()


async def _serve() -> None:
    # Only one daemon per socket: if another one holds the lock, leave it running.
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.info("another aitd already serves %s; exiting", SOCK_PATH)
        return
    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)
    old_umask = os.umask(0o077)          # socket is private from the moment it exists
    try:
        server = await asyncio.start_unix_server(_handle, path=SOCK_PATH)
    finally:
        os.umask(old_umask)
    os.chmod(SOCK_PATH, 0o600)
    with open(PID_PATH, "w") as f:      # lets `ait stop` stop exactly this daemon
        f.write(str(os.getpid()))
    log.info("aitd %s listening on %s", __version__, SOCK_PATH)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async with server:
        await stop.wait()
    for path in (SOCK_PATH, PID_PATH):
        if os.path.exists(path):
            os.unlink(path)


def main() -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(
        filename=None if "--foreground" in sys.argv else LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(_serve())
