"""Desktop-shortcut entry point — runs via pythonw.exe so no console window
ever appears, starts the Flask server, and opens the browser once it's
ready. Separate from server/app.py's own __main__ block (used for `python -m
server.app` in scripts/docs) so this file can use production-appropriate
settings (no debug/reloader — a reloader would spawn a second process,
which is exactly the kind of extra visible process a desktop app shouldn't
have) without changing that entrypoint's behavior.
"""
import socket
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PORT = 5000
URL = f"http://127.0.0.1:{PORT}"


def _server_already_running():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _open_browser():
    webbrowser.open(URL)


if __name__ == "__main__":
    if _server_already_running():
        # Double-clicking the desktop icon while it's already running should
        # just open another tab, not fail silently trying to bind a taken
        # port (pythonw has no console to show that error in anyway).
        _open_browser()
    else:
        threading.Timer(1.5, _open_browser).start()
        from server.app import create_app
        create_app().run(debug=False, use_reloader=False, port=PORT)
