from train_webui.backend.discovery import discover_projects

from .conftest import write_project


def test_discovers_valid_project(tmp_path):
    write_project(tmp_path, "proj_a")
    assert discover_projects(tmp_path) == ["proj_a"]


def test_excludes_wrong_choices(tmp_path):
    write_project(tmp_path, "proj_a", choices=("train", "infer", "eval"))
    assert discover_projects(tmp_path) == []


def test_excludes_missing_config(tmp_path):
    write_project(tmp_path, "proj_a", config_name=None)
    assert discover_projects(tmp_path) == []


def test_config_toml_alone_is_enough(tmp_path):
    write_project(tmp_path, "proj_a", config_name="config.toml")
    assert discover_projects(tmp_path) == ["proj_a"]


def test_nested_dirs_and_sort_order(tmp_path):
    write_project(tmp_path, "b_proj")
    write_project(tmp_path, "a_dir/sub_proj")
    assert discover_projects(tmp_path) == ["a_dir/sub_proj", "b_proj"]


def test_excludes_venv_and_train_webui_dirs(tmp_path):
    write_project(tmp_path, "venv/proj_inside")
    write_project(tmp_path, "train_webui/proj_inside")
    assert discover_projects(tmp_path) == []


def test_choices_as_set_literal_also_matches(tmp_path):
    d = tmp_path / "proj_a"
    d.mkdir()
    (d / "main.py").write_text(
        "import argparse\n"
        "p = argparse.ArgumentParser()\n"
        'p.add_argument("--mode", choices={"infer", "train"}, required=True)\n',
        encoding="utf-8",
    )
    (d / "config.example.toml").write_text('[wandb]\ngroup = "g"\n', encoding="utf-8")
    assert discover_projects(tmp_path) == ["proj_a"]
