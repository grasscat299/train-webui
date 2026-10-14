"""結果の読み込みを軽くする仕組み（差分取得・間引き・step の line の省略・ETag）。"""
from fastapi.testclient import TestClient

from .test_labels_and_delete import _create_cli_run

T = "2026-01-01T00:00:00+09:00"


def _post(client, run_id, items):
    assert client.post("/api/results", json={"run_id": run_id, "source": "train", "items": items}).status_code == 204


def _metrics(n, label="train/lr"):
    return [{"kind": "metric", "label": label, "value": float(i), "step": i, "sent_at": T} for i in range(n)]


def test_results_after_id_returns_only_newer(app_factory):
    app, _, _ = app_factory()
    client = TestClient(app)
    rid = _create_cli_run(client)
    _post(client, rid, _metrics(5))
    all_rows = client.get(f"/api/runs/{rid}/results").json()
    last = all_rows[2]["id"]
    newer = client.get(f"/api/runs/{rid}/results", params={"after_id": last}).json()
    assert [r["id"] for r in newer] == [r["id"] for r in all_rows[3:]]


def test_results_max_points_downsamples_big_metric_only(app_factory):
    app, _, _ = app_factory()
    client = TestClient(app)
    rid = _create_cli_run(client)
    _post(client, rid, _metrics(100) + _metrics(3, "small"))
    rows = client.get(f"/api/runs/{rid}/results", params={"max_points": 20, "after_id": 10**9}).json()
    big = [r for r in rows if r["label"] == "train/lr"]
    assert 0 < len(big) <= 20 and all(r["downsampled"] for r in big)  # after_id に関わらず間引き済みの全体
    assert [r["value"] for r in big][0] == 0.0 and [r["value"] for r in big][-1] == 99.0
    assert not [r for r in rows if r["label"] == "small"]  # 小さい label は差分（ここでは無し）


def test_plots_omit_step_lines(app_factory):
    app, _, _ = app_factory()
    client = TestClient(app)
    rid = _create_cli_run(client)
    d = lambda label, xl: {"kind": "plot_def", "label": label, "plot_kind": "line", "title": None, "xlabel": xl,  # noqa: E731
                           "ylabel": "y", "xscale": "linear", "yscale": "linear", "xlim": None, "ylim": None, "sent_at": T}
    p = lambda label: {"kind": "plot_points", "label": label, "series": "default", "xs": [1, 2], "ys": [1, 2], "sent_at": T}  # noqa: E731
    _post(client, rid, [d("train/loss", "step"), d("epoch/loss", "epoch"), p("train/loss"), p("epoch/loss")])
    by = {x["label"]: x for x in client.get(f"/api/runs/{rid}/plots", params={"omit_step_lines": "true"}).json()}
    assert by["train/loss"]["omitted"] is True and by["train/loss"]["series"][0]["points"] == []
    assert by["train/loss"]["series"][0]["total_points"] == 2 and by["train/loss"]["last_point_id"] is not None
    assert by["epoch/loss"]["omitted"] is False and len(by["epoch/loss"]["series"][0]["points"]) == 2
    raw = client.get(f"/api/runs/{rid}/plots", params={"label": "train/loss"}).json()[0]
    assert raw["omitted"] is False and len(raw["series"][0]["points"]) == 2


def test_etag_304_until_new_data(app_factory):
    app, _, _ = app_factory()
    client = TestClient(app)
    rid = _create_cli_run(client)
    _post(client, rid, _metrics(3))
    for path in (f"/api/runs/{rid}/results", f"/api/runs/{rid}/plots"):
        first = client.get(path)
        tag = first.headers["etag"]
        assert client.get(path, headers={"If-None-Match": tag}).status_code == 304
    _post(client, rid, _metrics(1))
    tag = client.get(f"/api/runs/{rid}/results").headers["etag"]
    _post(client, rid, _metrics(1))
    assert client.get(f"/api/runs/{rid}/results", headers={"If-None-Match": tag}).status_code == 200
