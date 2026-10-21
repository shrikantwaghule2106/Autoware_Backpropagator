"""Per-release data files follow their schema (src/adviewer/data/schema); a new release is
checked the same way, so a typo (a layer name, a missing URL) fails here, not in a user's graph."""
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from adviewer.model import Layer

DATA = Path(__file__).resolve().parents[1] / "src" / "adviewer" / "data"
SCHEMAS = {p.name.removesuffix(".schema.json"): json.loads(p.read_text(encoding="utf-8")) for p in (DATA / "schema").glob("*.schema.json")}
FILES = sorted(p for p in DATA.glob("autoware-*/*.yaml"))


def _as_json(path: Path):  # YAML int keys (mode numbers) become strings, as in JSON
    return json.loads(json.dumps(yaml.safe_load(path.read_text(encoding="utf-8"))))


@pytest.mark.parametrize("path", FILES, ids=[f"{p.parent.name}/{p.name}" for p in FILES])
def test_release_data_follows_its_schema(path: Path) -> None:
    schema = SCHEMAS[path.stem]
    Draft202012Validator.check_schema(schema)
    errors = [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in Draft202012Validator(schema).iter_errors(_as_json(path))]
    assert not errors, errors


def test_every_data_file_has_a_schema_and_layers_match_the_model() -> None:
    assert {p.stem for p in FILES} <= set(SCHEMAS)
    layer_enum = SCHEMAS["layers"]["properties"]["packages"]["additionalProperties"]["enum"]
    assert layer_enum == [layer.value for layer in Layer]  # a new Layer needs the schema updated too


def test_a_typo_is_caught() -> None:
    data = _as_json(DATA / "autoware-1.9.0" / "layers.yaml")
    data["packages"]["autoware_mrm_handler"] = "System"  # layers are lower case
    assert list(Draft202012Validator(SCHEMAS["layers"]).iter_errors(data))
