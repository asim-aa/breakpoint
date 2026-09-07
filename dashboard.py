"""A small local read-only web dashboard over the same SQLite data the CLI
already prints to a terminal — for demoing this project without a
screen full of `breakpoint history` text. Doesn't trigger new runs;
`breakpoint run` stays the way to actually use the system."""

import json

from flask import Flask, abort, render_template

import memory
import storage


def create_app(db_path: str = storage.DB_PATH) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path

    @app.route("/")
    def index():
        rows = storage.list_history(limit=100, db_path=app.config["DB_PATH"])
        return render_template("index.html", rows=rows)

    @app.route("/runs/<int:spec_id>")
    def run_detail(spec_id):
        detail = storage.get_run_detail(spec_id, db_path=app.config["DB_PATH"])
        if detail is None:
            abort(404)
        try:
            spec_pretty = json.dumps(json.loads(detail["spec_json"]), indent=2)
        except (json.JSONDecodeError, TypeError):
            spec_pretty = detail["spec_json"]
        return render_template("run_detail.html", detail=detail, spec_pretty=spec_pretty)

    @app.route("/patterns")
    def patterns():
        rows = memory.list_patterns(db_path=app.config["DB_PATH"])
        all_spec_ids = sorted({sid for p in rows for sid in p["example_spec_ids"]})
        requests = storage.get_requests_by_ids(all_spec_ids, db_path=app.config["DB_PATH"])
        return render_template("patterns.html", patterns=rows, requests=requests)

    @app.route("/leaderboard")
    def leaderboard():
        rows = storage.get_leaderboard(db_path=app.config["DB_PATH"])
        return render_template("leaderboard.html", rows=rows)

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=5050)
