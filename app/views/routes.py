from __future__ import annotations

import uuid
from pathlib import Path

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from app.controllers.gmov_controller import GMOVController

bp = Blueprint("routes", __name__)


def _session_id() -> str:
    """Return a stable browser-session id stored in the Flask session cookie."""
    sid = session.get("gmov_session_id")
    if not sid:
        sid = uuid.uuid4().hex
        session["gmov_session_id"] = sid
    return sid


def _session_root() -> Path:
    root = Path(current_app.config.get("GMOV_SESSION_ROOT", Path(current_app.root_path).parent / "runtime_sessions"))
    sid = _session_id()
    path = root / sid
    (path / "fdos").mkdir(parents=True, exist_ok=True)
    (path / "uploads").mkdir(parents=True, exist_ok=True)
    return path


def _controller() -> GMOVController:
    return GMOVController(_session_root() / "fdos")


@bp.route("/", methods=["GET"])
def index():
    ctl = _controller()
    manifest = ctl.model_manifest()
    manifest["session_id"] = _session_id()
    return render_template("index.html", manifest=manifest)


@bp.route("/upload", methods=["POST"])
def upload():
    ctl = _controller()
    files = request.files.getlist("fdo_zip")
    for f in files:
        if f and f.filename.endswith(".zip"):
            ctl.repo.import_zip(f)
    return redirect(url_for("routes.index"))


@bp.route("/fdo-info/<model_dir>/", methods=["GET"])
def fdo_info(model_dir):
    # Serve only top-level FDO information pages from this browser session.
    safe_dir = Path(model_dir).name
    obj_dir = _session_root() / "fdos" / safe_dir
    info_page = obj_dir / "index.html"
    if not info_page.exists() or not info_page.is_file():
        return "No information page is available for this FDO in this GMOV session.", 404
    return send_from_directory(obj_dir, "index.html")


@bp.route("/api/models", methods=["GET"])
def models():
    return jsonify(_controller().model_manifest())


@bp.route("/api/capabilities", methods=["GET"])
def capabilities():
    # Backward-compatible endpoint name.
    return jsonify(_controller().model_manifest())


@bp.route("/api/execute", methods=["POST"])
def execute():
    payload = request.get_json(force=True)
    try:
        ctl = _controller()
        operation_id = payload.get("operation_id") or payload.get("service_id")
        out = ctl.repo.execute(operation_id, payload.get("inputs", {}), payload.get("model_dir"))
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/ask", methods=["POST"])
def ask():
    payload = request.get_json(force=True)
    try:
        ctl = _controller()
        out = ctl.handle_natural_language(payload.get("message", ""), payload.get("result_history", []))
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/compute", methods=["POST"])
def compute():
    payload = request.get_json(force=True)
    try:
        ctl = _controller()
        out = ctl.handle_constrained_compute(payload.get("message", ""))
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/unload", methods=["POST"])
def unload():
    payload = request.get_json(force=True)
    try:
        ctl = _controller()
        return jsonify(ctl.repo.unload_model(payload.get("model_dir", "")))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/unload-all", methods=["POST"])
def unload_all():
    try:
        ctl = _controller()
        return jsonify(ctl.repo.unload_all())
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/session", methods=["GET"])
def session_info():
    root = _session_root()
    return jsonify({
        "session_id": _session_id(),
        "model_workspace": str(root / "fdos"),
        "session_isolated": True,
    })
