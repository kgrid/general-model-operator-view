from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, send_from_directory, url_for
from app.controllers.gmov_controller import GMOVController
from app.models.fair_do import FDO_DIR

bp = Blueprint("routes", __name__)
ctl = GMOVController()


@bp.route("/", methods=["GET"])
def index():
    manifest = ctl.model_manifest()
    return render_template("index.html", manifest=manifest)


@bp.route("/upload", methods=["POST"])
def upload():
    files = request.files.getlist("fdo_zip")
    for f in files:
        if f and f.filename.endswith(".zip"):
            ctl.repo.import_zip(f)
    return redirect(url_for("routes.index"))


@bp.route("/fdo-info/<model_dir>/", methods=["GET"])
def fdo_info(model_dir):
    # Serve only top-level FDO information pages, never arbitrary nested files.
    safe_dir = Path(model_dir).name
    obj_dir = FDO_DIR / safe_dir
    info_page = obj_dir / "index.html"
    if not info_page.exists() or not info_page.is_file():
        return "No information page is available for this FDO.", 404
    return send_from_directory(obj_dir, "index.html")


@bp.route("/api/models", methods=["GET"])
def models():
    return jsonify(ctl.model_manifest())


@bp.route("/api/capabilities", methods=["GET"])
def capabilities():
    # Backward-compatible endpoint name.
    return jsonify(ctl.model_manifest())


@bp.route("/api/execute", methods=["POST"])
def execute():
    payload = request.get_json(force=True)
    try:
        operation_id = payload.get("operation_id") or payload.get("service_id")
        out = ctl.repo.execute(operation_id, payload.get("inputs", {}), payload.get("model_dir"))
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/ask", methods=["POST"])
def ask():
    payload = request.get_json(force=True)
    try:
        out = ctl.handle_natural_language(payload.get("message", ""), payload.get("result_history", []))
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/compute", methods=["POST"])
def compute():
    payload = request.get_json(force=True)
    try:
        out = ctl.handle_constrained_compute(payload.get("message", ""))
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/unload", methods=["POST"])
def unload():
    payload = request.get_json(force=True)
    try:
        return jsonify(ctl.repo.unload_model(payload.get("model_dir", "")))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/unload-all", methods=["POST"])
def unload_all():
    try:
        return jsonify(ctl.repo.unload_all())
    except Exception as e:
        return jsonify({"error": str(e)}), 400
