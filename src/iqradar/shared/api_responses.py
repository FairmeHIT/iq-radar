from __future__ import annotations

from typing import Any

from flask import jsonify


def ok(data: Any):
    return jsonify({"success": True, "data": data, "error": None})


def error(message: str, status_code: int):
    return jsonify({"success": False, "data": None, "error": message}), status_code
