"""Command-line client for shells without Unix sockets (bash).

    python -m aitd.client <type[:option]> [--start] < payload

Sends one request to the daemon, prints the reply, exits 1 if it starts with "ERR:".
With --start, launches the daemon first if it isn't running.
"""
import os
import socket
import subprocess
import sys
import time

# Same default as server.SOCK_PATH. Not imported from there: that pulls in the
# provider SDKs and adds over a second to every call.
SOCK_PATH = os.environ.get("AIT_SOCK", os.path.expanduser("~/.ai-terminal.sock"))
TIMEOUT = float(os.environ.get("AIT_TIMEOUT", "60"))


def _connect() -> socket.socket | None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(SOCK_PATH)
        return s
    except OSError:
        s.close()
        return None


def _start_daemon() -> None:
    env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    subprocess.Popen([sys.executable, "-m", "aitd"], env=env, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.05)
        if os.path.exists(SOCK_PATH):
            return


def request(kind: str, payload: str, start: bool = False) -> str:
    sock = _connect()
    if sock is None and start:
        _start_daemon()
        sock = _connect()
    if sock is None:
        return "ERR: cannot reach the Shelldon daemon"
    with sock:
        sock.settimeout(TIMEOUT)
        sock.sendall(f"{kind}\0{os.getcwd()}\0{payload}\0".encode())
        chunks = []
        try:
            while chunk := sock.recv(65536):
                chunks.append(chunk)
        except TimeoutError:
            return f"ERR: no reply from daemon (timed out after {TIMEOUT:.0f}s?)"
    return b"".join(chunks).decode("utf-8", "replace") or "ERR: no reply from daemon"


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--start"]
    if not args:
        sys.exit(__doc__)
    payload = "" if sys.stdin.isatty() else sys.stdin.read()
    reply = request(args[0], payload, start="--start" in sys.argv)
    sys.stdout.write(reply)
    sys.exit(1 if reply.startswith("ERR:") else 0)


if __name__ == "__main__":
    main()
