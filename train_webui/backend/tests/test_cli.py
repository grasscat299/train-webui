import base64

from fastapi.testclient import TestClient

from train_webui import cli

from .conftest import write_project

T = "2026-01-01T00:00:00+09:00"


def _setup(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "src/proj_a")
    client = TestClient(app)
    runs = []
    for lr in ("1e-3", "1e-4"):
        run_id = client.post("/api/runs", json={
            "project": "src/proj_a", "group": "g1", "config_toml": f'[train]\nlr = {lr}\n[wandb]\ngroup = "g1"\n',
            "hostname": "h", "pid": 1,
        }).json()["run_id"]
        client.post("/api/results", json={"run_id": run_id, "source": "train", "items": [
            {"kind": "plot_def", "label": "epoch/loss", "plot_kind": "line", "xlabel": "epoch", "ylabel": "loss",
             "xscale": "linear", "yscale": "log", "sent_at": T},
            {"kind": "plot_points", "label": "epoch/loss", "series": "train", "xs": [1, 2, 3], "ys": [0.5, 0.2, 0.3],
             "sent_at": T},
            {"kind": "metric", "label": "train/lr", "step": 1, "value": 0.001, "sent_at": T},
            {"kind": "image", "label": "infer/xy", "image_b64": base64.b64encode(b"\x89PNG").decode(), "sent_at": T},
        ]})
        client.post(f"/api/runs/{run_id}/finish", json={"status": "finished", "error": None})
        runs.append(run_id)
    client.put("/api/groups", json={"project": "src/proj_a", "group": "g1", "group_name": "小さいモデル"})
    client.put(f"/api/runs/{runs[1]}/comment", json={"comment": "lr を 1e-4 に下げた"})
    return settings, runs


def test_ls_and_show(app_factory, capsys):
    settings, runs = _setup(app_factory)
    db = str(settings.server.db_path)

    assert cli.main(["--db", db, "ls"]) == 0
    out = capsys.readouterr().out
    assert "src/proj_a" in out and "小さいモデル  (group_id: g1)" in out and runs[0] in out

    # project はパスの末尾、group は group_name でもよい。run 省略時は最後の run（v2）
    assert cli.main(["--db", db, "show", "proj_a", "小さいモデル"]) == 0
    out = capsys.readouterr().out
    assert "/ v2" in out
    assert "-lr = 1e-3" in out and "+lr = 1e-4" in out  # 前の run からの config 差分
    assert "train: 3点" in out and "最小 (2, 0.2)" in out and "最後 (3, 0.3)" in out
    assert "train/lr" in out
    assert f"images/{runs[1]}/" in out
    assert "lr を 1e-4 に下げた" in out


def test_plot_and_compare(app_factory, capsys):
    settings, runs = _setup(app_factory)
    db = str(settings.server.db_path)

    assert cli.main(["--db", db, "plot", "proj_a", "g1", "epoch/loss", "v1"]) == 0
    out = capsys.readouterr().out
    assert "[train] 全3点" in out and "2\t0.2" in out

    assert cli.main(["--db", db, "compare", "proj_a", "g1", "g1", "--run1", "v1", "--run2", "v2"]) == 0
    out = capsys.readouterr().out
    assert "-lr = 1e-3" in out and "+lr = 1e-4" in out
    assert "epoch/loss[train]" in out


def test_unknown_names_report_candidates(app_factory, capsys):
    settings, runs = _setup(app_factory)
    db = str(settings.server.db_path)
    assert cli.main(["--db", db, "show", "proj_a", "nope"]) == 1
    assert "候補" in capsys.readouterr().err
    assert cli.main(["--db", db, "show", "proj_a", "g1", "v9"]) == 1
