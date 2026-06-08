import os
from pathlib import Path

from flask import Flask
from app.views.routes import bp


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.environ.get("GMOV_SECRET_KEY", "gmov-dev-session-key")
    app.config["GMOV_SESSION_ROOT"] = Path(os.environ.get("GMOV_SESSION_ROOT", Path(__file__).resolve().parent / "runtime_sessions"))
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
    app.register_blueprint(bp)
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
