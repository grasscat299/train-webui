from fastapi.testclient import TestClient

from train_webui.backend.binning import bin_mean

from .conftest import write_project


def _pts(ys, start=0):
    return [(i, float(start + i), y) for i, y in enumerate(ys)]


def test_bin_mean_groups_by_width_and_keeps_partial_last_bin():
    out = bin_mean(_pts([1, 3, 5, 7, 9]), 2)  # x=0..4 → [0,1] [2,3] [4]
    assert out == [(0, 2.0, 2), (1, 6.0, 2), (2, 9.0, 1)]


def test_bin_mean_skips_empty_bins_and_rejects_bad_width():
    out = bin_mean([(1, 0.0, 2.0), (2, 10.0, 4.0)], 5)
    assert [k for k, _, _ in out] == [0, 2]
    for bad in (0, -1):
        try:
            bin_mean([], bad)
            raise AssertionError
        except ValueError:
            pass


def _app_with_plot(app_factory, config, kind="line"):
    app, settings, _ = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": None, "new_group": True}).json()
    rid = run["run_id"]
    app.state.db.update_run(rid, config_toml=config, status="finished")
    T = "2026-01-01T00:00:00+09:00"
    items = [
        {"kind": "plot_def", "label": "train/loss", "plot_kind": kind, "title": None, "xlabel": "step", "ylabel": "loss",
         "xscale": "linear", "yscale": "linear", "xlim": None, "ylim": None, "sent_at": T},
        {"kind": "plot_points", "label": "train/loss", "series": "default",
         "xs": [float(i) for i in range(10)], "ys": [float(i) for i in range(10)], "sent_at": T},
    ]
    resp = client.post("/api/results", json={"run_id": rid, "source": "train", "items": items})
    assert resp.status_code in (200, 204), resp.text
    return client, rid


def test_binned_endpoint_epoch_uses_config_steps_per_epoch(app_factory):
    client, rid = _app_with_plot(app_factory, "[train]\nsteps_per_epoch = 5\n")
    resp = client.get(f"/api/runs/{rid}/plots/binned", params={"label": "train/loss", "bin": "epoch"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["epoch"] is True and body["width"] == 5.0
    # step 0..4 → epoch 1 の平均 2.0、step 5..9 → epoch 2 の平均 7.0
    assert body["series"][0]["points"] == [[1, 2.0], [2, 7.0]]


def test_binned_endpoint_numeric_width_and_errors(app_factory):
    client, rid = _app_with_plot(app_factory, "[train]\nx = 1\n")
    ok = client.get(f"/api/runs/{rid}/plots/binned", params={"label": "train/loss", "bin": "2"}).json()
    assert ok["series"][0]["points"][0] == [2.0, 0.5]  # 区間 [0,2) の平均、x は区間の終わり
    # steps_per_epoch が無い config で epoch は 400
    assert client.get(f"/api/runs/{rid}/plots/binned", params={"label": "train/loss", "bin": "epoch"}).status_code == 400
    assert client.get(f"/api/runs/{rid}/plots/binned", params={"label": "train/loss", "bin": "abc"}).status_code == 400
    assert client.get(f"/api/runs/{rid}/plots/binned", params={"label": "nope", "bin": "2"}).status_code == 404


def test_binned_endpoint_rejects_scatter(app_factory):
    client, rid = _app_with_plot(app_factory, "[train]\nsteps_per_epoch = 5\n", kind="scatter")
    assert client.get(f"/api/runs/{rid}/plots/binned", params={"label": "train/loss", "bin": "epoch"}).status_code == 400
