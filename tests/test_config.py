"""config.py — table-driven: every validation rule gets an assertion
(spec §7)."""

import pytest

from config import AppConfig, ConfigError, load_config, rewrite_path


# pseudocode — each test feeds a raw dict/yaml string and asserts the outcome.

def test_valid_config_loads(cfg_dict, tmp_path):
    # write cfg_dict to tmp_path/config.yaml, load_config(path) -> AppConfig
    # assert all sections present with expected values
    ...


def _invalid(name, patch):
    """Helper: deep-copy a valid dict, apply ONE mutation, write, load,
    expect ConfigError. Each row below is one spec §5 rule."""
    ...


@pytest.mark.parametrize("patch,expected_substr", [
    (lambda d: d["paths"].__setitem__("host_prefix", "data/videos"), "absolute"),   # relative prefix
    (lambda d: d["paths"].__setitem__("allowed_extensions", ["mp4"]), "'.'"),        # missing dot
    (lambda d: d["paths"].__setitem__("allowed_extensions", []), "non-empty"),
    (lambda d: d["server"].__setitem__("max_queue_size", 0), "ge=1"),
    (lambda d: d["frames"].__setitem__("fps_sample_rate", 0), "gt=0"),
    (lambda d: d["frames"].__setitem__("frame_format", "tiff"), "literal"),
    (lambda d: d["models"].__setitem__("base_url", "ftp://x"), "http(s)"),
    (lambda d: d["models"].__setitem__("summary_model", "  "), "non-empty"),
    (lambda d: d["llm"].__setitem__("temperature_video", 3.0), "le=2"),
    (lambda d: d["llm"].__setitem__("max_retries", -1), "ge=0"),
    (lambda d: d["jobs"].__setitem__("ttl_seconds", 0), "gt=0"),
    (lambda d: d["output_schema"]["fields"].__setitem__("confidence_note",
                                                        {"type": "string", "max_length": 200}),
     "only confidence_note may be optional"),            # removing optional
    (lambda d: d["output_schema"]["fields"].pop("summary", None), "exactly"),
])
def test_invalid_configs_fail_fast(patch, expected_substr):
    # pseudocode: each asserts ConfigError with message matching expected_substr
    ...


def test_env_interpolation_resolves(tmp_path, monkeypatch):
    # monkeypatch.setenv("TEST_KEY", "s3cr3t");
    # config with llm.api_key="${TEST_KEY}" -> load -> cfg.llm.api_key == "s3cr3t"
    ...


def test_env_interpolation_missing_var_fails(tmp_path, monkeypatch):
    # unset var -> ConfigError naming the var
    ...


def test_rewrite_path_inside_root(cfg):
    # rewrite_path(cfg.paths, "/data/videos/sub/clip.mp4") == /srv/videos/sub/clip.mp4
    ...


def test_rewrite_path_outside_root_rejected(cfg):
    # /data/videos/../elsewhere.mp4 -> PathOutsideRoot
    ...


def test_rewrite_path_sibling_prefix_rejected(cfg):
    # /data/videos2/clip.mp4 (shares the text prefix) -> PathOutsideRoot
    ...
