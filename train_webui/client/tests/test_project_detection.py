"""CLIから手動実行した場合のproject/group検出（呼び出し元ファイルからの探索）。"""
from __future__ import annotations

import train_webui.client as client
from train_webui.client import _core


def test_determine_project_and_group_from_config_toml(monkeypatch, tmp_path):
    monkeypatch.setattr(_core, "_repo_root", lambda: tmp_path)
    project_dir = tmp_path / "src" / "world_model_v1"
    project_dir.mkdir(parents=True)
    (project_dir / "main.py").write_text("", encoding="utf-8")
    (project_dir / "config.toml").write_text(
        '[other]\nx = 1\n\n[wandb]\nproject = "p"\ngroup = "wm_v1_d128_L6"  # comment\n',
        encoding="utf-8",
    )
    nested = project_dir / "sub" / "caller.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("", encoding="utf-8")

    project, group, config_text = _core._determine_project_and_group(str(nested))

    assert project == "src/world_model_v1"
    assert group == "wm_v1_d128_L6"
    assert "wm_v1_d128_L6" in config_text


def test_determine_project_and_group_none_when_not_found(tmp_path):
    lone_file = tmp_path / "somewhere" / "script.py"
    lone_file.parent.mkdir(parents=True)
    lone_file.write_text("", encoding="utf-8")

    project, group, config_text = _core._determine_project_and_group(str(lone_file))

    assert project is None
    assert group is None
    assert config_text is None


def test_public_init_uses_real_caller_file_location(monkeypatch, fake_server, tmp_path):
    """train_webui.client.init() を呼んだ *ファイルの場所* からprojectが推定されることを確認する。"""
    project_dir = tmp_path / "scratch" / "MyProj"
    project_dir.mkdir(parents=True)
    (project_dir / "main.py").write_text("", encoding="utf-8")
    (project_dir / "config.toml").write_text('[wandb]\ngroup = "g1"\n', encoding="utf-8")

    caller_path = project_dir / "run_from_cli.py"
    caller_path.write_text(
        "import train_webui.client as client\nclient.init(new_run=True)\n", encoding="utf-8",
    )

    monkeypatch.setattr(_core, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("TRAIN_WEBUI_URL", fake_server.url)

    captured = {}

    def handle_create(match, body, query):
        captured.update(body)
        return 200, {"run_id": "20260109_000000", "warning": None}

    fake_server.route("POST", r"^/api/runs$", handle_create)

    code = compile(caller_path.read_text(encoding="utf-8"), str(caller_path), "exec")
    exec(code, {"__name__": "run_from_cli"})

    assert captured["project"] == "scratch/MyProj"
    assert captured["group"] == "g1"
