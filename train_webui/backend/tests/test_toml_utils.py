import pytest

from train_webui.backend.toml_utils import TomlValidationError, extract_group, set_wandb_group


def test_extract_group_ok():
    text = '[wandb]\nproject = "p"\ngroup = "g1"\n'
    assert extract_group(text) == "g1"


def test_extract_group_missing_section():
    with pytest.raises(TomlValidationError):
        extract_group('[train]\nlr = 1\n')


def test_extract_group_invalid_toml():
    with pytest.raises(TomlValidationError):
        extract_group("this is not = [ valid toml")


def test_set_wandb_group_preserves_other_lines_and_comments():
    text = (
        "# comment\n"
        "[train]\n"
        "lr = 1e-3  # 学習率\n"
        "\n"
        "[wandb]\n"
        "project = \"p\"\n"
        "group = \"old\"  # 旧グループ\n"
        "\n"
        "[infer]\n"
        "n = 1\n"
    )
    out = set_wandb_group(text, "new_group")
    assert 'group = "new_group"  # 旧グループ' in out
    assert 'project = "p"' in out
    assert "lr = 1e-3  # 学習率" in out
    assert "[infer]" in out
    assert extract_group(out) == "new_group"


def test_set_wandb_group_escapes_quotes():
    text = '[wandb]\ngroup = "g"\n'
    out = set_wandb_group(text, 'weird"name')
    assert extract_group(out) == 'weird"name'


def test_set_wandb_group_missing_key_raises():
    with pytest.raises(TomlValidationError):
        set_wandb_group('[wandb]\nproject = "p"\n', "g")
