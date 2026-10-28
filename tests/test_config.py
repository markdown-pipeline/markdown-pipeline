"""MD-8: pipeline definition model and persistence."""
import yaml

from mdpipeline.brain import init_brain
from mdpipeline.config import (
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    DEFAULT_TARGET,
    Pipeline,
    Source,
    load_config,
    save_config,
)


def test_new_pipeline_gets_defaults(tmp_path):
    p = Pipeline.new("biz-mng", [str(tmp_path / "docs")])
    assert p.include == ["**/*.md"] == DEFAULT_INCLUDE
    assert p.target == "raw/{pipeline}/{source}/{relpath}" == DEFAULT_TARGET
    assert p.on_source_delete == "delete"
    assert (p.guard_threshold_percent, p.guard_min_files) == (40, 5)
    assert p.paused is False
    for rule in (".git/**", "node_modules/**", ".obsidian/**", "**/.*/**"):
        assert rule in DEFAULT_EXCLUDE and rule in p.exclude


def test_label_defaults_to_folder_name_and_order_is_kept(tmp_path):
    p = Pipeline.new("p", [str(tmp_path / "docs"), str(tmp_path / "specs")])
    assert [s.label for s in p.sources] == ["docs", "specs"]
    assert [s.path for s in p.sources] == [str(tmp_path / "docs"), str(tmp_path / "specs")]


def test_explicit_labels_are_kept(tmp_path):
    p = Pipeline.new("p", [Source(str(tmp_path / "a"), "alpha")])
    assert p.sources[0].label == "alpha"


def test_trailing_slash_and_tilde_are_normalised(tmp_path):
    p = Pipeline.new("p", [str(tmp_path / "docs") + "/"])
    assert p.sources[0].path == str(tmp_path / "docs")
    assert p.sources[0].label == "docs"


def test_config_round_trip_preserves_everything_and_several_pipelines(brain_dir, tmp_path):
    brain = init_brain(brain_dir)
    cfg = load_config(brain)
    assert cfg.pipelines == []
    a = Pipeline.new("a", [str(tmp_path / "x"), str(tmp_path / "y")], on_source_delete="keep",
                     guard_threshold_percent=25, guard_min_files=3, exclude=["z/**"], target="raw/a/{relpath}")
    b = Pipeline.new("b", [str(tmp_path / "w")])
    b.paused = True
    cfg.pipelines += [a, b]
    save_config(brain, cfg)
    again = load_config(brain)
    assert again.pipelines == [a, b]


def test_saved_config_is_readable_yaml_with_sources_as_path_label(brain_dir, tmp_path):
    brain = init_brain(brain_dir)
    cfg = load_config(brain)
    cfg.pipelines.append(Pipeline.new("a", [str(tmp_path / "x")]))
    save_config(brain, cfg)
    data = yaml.safe_load(brain.config_path.read_text())
    p = data["pipelines"][0]
    assert p["name"] == "a"
    assert p["sources"] == [{"path": str(tmp_path / "x"), "label": "x"}]
    assert p["on_source_delete"] == "delete"
    assert p["guard"] == {"threshold_percent": 40, "min_files": 5}


def test_label_can_be_renamed_after_creation(tmp_path):
    p = Pipeline.new("p", [str(tmp_path / "docs")])
    p.sources[0].label = "handbook"
    assert p.sources[0].label == "handbook"


def test_get_pipeline_by_name(brain_dir, tmp_path):
    brain = init_brain(brain_dir)
    cfg = load_config(brain)
    cfg.pipelines.append(Pipeline.new("a", [str(tmp_path / "x")]))
    assert cfg.get("a").name == "a"
    assert cfg.get("nope") is None
