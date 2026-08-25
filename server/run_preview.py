"""Dev-only entry point for Claude Code's preview tooling.

Never used by Kevin's normal startup path (Budget-Tracker-starten.bat /
`python -m server.app` both go through server/app.py's own __main__
directly) — this exists solely so preview verification never touches his
real data/budget.db while the app is being developed.
"""
import os

from server.app import create_app

if __name__ == "__main__":
    create_app(db_path="data/preview.db").run(
        debug=True, port=int(os.environ.get("PORT", 5000))
    )
