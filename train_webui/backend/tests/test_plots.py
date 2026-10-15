from fastapi.testclient import TestClient

from .test_labels_and_delete import _create_cli_run

T = "2026-01-01T00:00:00+09:00"


def _post(client, run_id, items):
    return client.post("/api/results", json={"run_id": run_id, "source": "infer", "items": items})


def _def(label="infer/xy", **kw):
    item = {"kind": "plot_def", "label": label, "plot_kind": "scatter", "title": "t", "xlabel": "x",
            "ylabel": "y", "xscale": "linear", "yscale": "log", "xlim": None, "ylim": [0.001, 1.0], "sent_at": T}
    item.update(kw)
    return item


def _pts(xs, ys, label="infer/xy", series="pred"):
    return {"kind": "plot_points", "label": label, "series": series, "xs": xs, "ys": ys, "sent_at": T}


def test_define_and_add_points(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run_id = _create_cli_run(client)
    assert _post(client, run_id, [_def(), _pts([1, 2], [0.5, 0.4]), _pts([3], [0.3], series="target")]).status_code == 204

    plots = client.get(f"/api/runs/{run_id}/plots").json()
    assert len(plots) == 1
    p = plots[0]
    assert p["kind"] == "scatter" and p["defined"] is True
    assert p["yscale"] == "log" and p["xlim"] is None and p["ylim"] == [0.001, 1.0]
    assert [s["name"] for s in p["series"]] == ["pred", "target"]
    assert p["series"][0]["points"] == [[1.0, 0.5], [2.0, 0.4]]
    assert p["series"][0]["downsampled"] is False and p["series"][0]["total_points"] == 2

    labels = client.get("/api/labels", params={"run_ids": run_id}).json()
    assert labels == [{"label": "infer/xy", "kind": "plot"}]


def test_points_without_def_use_default_then_redefine_keeps_points(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run_id = _create_cli_run(client)
    _post(client, run_id, [_pts([1], [1])])
    p = client.get(f"/api/runs/{run_id}/plots").json()[0]
    assert p["kind"] == "line" and p["defined"] is False and p["xlabel"] == ""

    _post(client, run_id, [_def()])
    p = client.get(f"/api/runs/{run_id}/plots").json()[0]
    assert p["kind"] == "scatter" and p["defined"] is True
    assert p["series"][0]["points"] == [[1.0, 1.0]]


def test_after_id_returns_only_new_points(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run_id = _create_cli_run(client)
    _post(client, run_id, [_pts([1, 2], [1, 2]), _pts([9], [9], series="other")])
    first = client.get(f"/api/runs/{run_id}/plots").json()[0]
    _post(client, run_id, [_pts([3], [3])])
    diff = client.get(f"/api/runs/{run_id}/plots", params={"after_id": first["last_point_id"]}).json()[0]
    by_name = {s["name"]: s for s in diff["series"]}
    assert by_name["pred"]["points"] == [[3.0, 3.0]]
    assert by_name["other"]["points"] == []
    assert diff["last_point_id"] > first["last_point_id"]


def test_downsampled_series_returns_whole_decimated(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run_id = _create_cli_run(client)
    xs = list(range(50))
    _post(client, run_id, [_pts(xs, xs), _pts([0], [0], series="small")])
    last = client.get(f"/api/runs/{run_id}/plots").json()[0]["last_point_id"]
    p = client.get(f"/api/runs/{run_id}/plots", params={"after_id": last, "max_points": 10}).json()[0]
    by_name = {s["name"]: s for s in p["series"]}
    assert by_name["pred"]["downsampled"] is True
    assert by_name["pred"]["total_points"] == 50
    assert 0 < len(by_name["pred"]["points"]) <= 10
    assert by_name["pred"]["points"][0] == [0.0, 0.0] and by_name["pred"]["points"][-1] == [49.0, 49.0]
    assert by_name["small"]["downsampled"] is False and by_name["small"]["points"] == []


def test_max_points_below_4_is_rejected(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run_id = _create_cli_run(client)
    assert client.get(f"/api/runs/{run_id}/plots", params={"max_points": 3}).status_code == 422


def test_label_kind_conflict_400(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run_id = _create_cli_run(client)
    _post(client, run_id, [{"kind": "metric", "label": "train/loss", "step": 1, "value": 1.0, "sent_at": T}])
    assert _post(client, run_id, [_pts([1], [1], label="train/loss")]).status_code == 400
    _post(client, run_id, [_pts([1], [1], label="infer/xy")])
    resp = _post(client, run_id, [{"kind": "metric", "label": "infer/xy", "step": 1, "value": 1.0, "sent_at": T}])
    assert resp.status_code == 400


def test_invalid_points_400(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run_id = _create_cli_run(client)
    assert _post(client, run_id, [_pts([1, 2], [1])]).status_code == 400
    assert _post(client, run_id, [{"kind": "plot_def", "label": "a", "sent_at": T}]).status_code == 400


def test_plots_unknown_run_404_and_delete_removes(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    assert client.get("/api/runs/nope/plots").status_code == 404
    run_id = _create_cli_run(client)
    _post(client, run_id, [_def(), _pts([1], [1])])
    client.post(f"/api/runs/{run_id}/finish", json={"status": "finished", "error": None})
    assert client.delete(f"/api/runs/{run_id}").status_code == 204
    assert app.state.db.list_plots(run_id) == []
    assert app.state.db.max_plot_point_id(run_id, "infer/xy") is None


def test_results_max_points_decimates_only_metrics(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run_id = _create_cli_run(client)
    items = [{"kind": "metric", "label": "train/lr", "step": i, "value": float(i % 7), "sent_at": T} for i in range(500)]
    items.append({"kind": "metric", "label": "few", "step": 0, "value": 1.0, "sent_at": T})
    assert _post(client, run_id, items).status_code == 204
    full = client.get(f"/api/runs/{run_id}/results").json()
    assert len(full) == 501
    cut = client.get(f"/api/runs/{run_id}/results", params={"max_points": 50}).json()
    lr = [r for r in cut if r["label"] == "train/lr"]
    assert len(lr) <= 50 and lr[0]["step"] == 0 and lr[-1]["step"] == 499  # 最初と最後は残る
    assert len([r for r in cut if r["label"] == "few"]) == 1  # 少ないラベルはそのまま
