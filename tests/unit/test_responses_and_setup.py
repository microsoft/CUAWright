import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import yaml

from cuawright.core.responses import responses_url, sdk_base_url
from cuawright.desktop import setup
from cuawright.desktop.exceptions import ReleaseError
from cuawright.webwright.config import get_config_from_spec, snapshot_config_specs
from cuawright.webwright.models.openai_model import OpenAIModel
from cuawright.webwright.utils.serialize import recursive_merge


@pytest.mark.parametrize(
    ("value", "base"),
    [
        ("https://api.openai.com/v1/responses", "https://api.openai.com/v1"),
        ("https://gateway.example/api/responses/", "https://gateway.example/api"),
        ("http://127.0.0.1:8000/responses", "http://127.0.0.1:8000/"),
    ],
)
def test_responses_url_contract(value, base):
    assert responses_url(value).endswith("/responses")
    assert sdk_base_url(value) == base


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/v1",
        "https://user@example.com/v1/responses",
        "https://example.com/v1/responses?key=secret",
    ],
)
def test_invalid_responses_url_is_rejected(value):
    with pytest.raises(ValueError):
        responses_url(value)


def test_local_browser_modifier_selects_python_response_contract():
    config = recursive_merge(
        get_config_from_spec("base.yaml"),
        get_config_from_spec("model_openai.yaml"),
        get_config_from_spec("local_browser.yaml"),
    )
    assert config["model"]["response_mode"] == "json_schema"
    assert config["model"]["action_field"] == "python_code"


def test_model_artifacts_redact_endpoint_and_credentials(tmp_path):
    endpoint = "https://gateway.example/v1/responses"
    model = OpenAIModel(
        openai_api_key="secret-value",
        responses_url=endpoint,
        error_log_path=tmp_path / "runtime_errors.jsonl",
    )
    serialized = json.dumps(model.serialize())
    assert endpoint not in serialized
    assert "secret-value" not in serialized
    model._log_gateway_error(
        event="request_failed",
        attempt=1,
        error=RuntimeError(f"POST {endpoint} failed"),
    )
    logged = (tmp_path / "runtime_errors.jsonl").read_text()
    assert endpoint not in logged
    assert "[redacted endpoint]" in logged


def test_browser_config_snapshots_redact_transport_identity(tmp_path):
    config = tmp_path / "model.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "model": {
                    "responses_url": "https://gateway.example/v1/responses",
                    "openai_api_key": "secret",
                    "model_name": "test-model",
                }
            }
        )
    )
    snapshot = snapshot_config_specs(
        [
            str(config),
            "model.responses_url=https://inline.example/v1/responses",
            "model.openai_api_key=inline-secret",
        ],
        tmp_path / "output",
        merged_config=yaml.safe_load(config.read_text()),
    )
    text = "\n".join(path.read_text() for path in snapshot.iterdir())
    assert "gateway.example" not in text
    assert "inline.example" not in text
    assert "secret" not in text
    assert "test-model" in text


def test_setup_dry_run_uses_pinned_release(tmp_path, capsys):
    assert setup.main(["setup", "--root", str(tmp_path), "--dry-run"]) == 0
    values = json.loads(capsys.readouterr().out)
    assert Path(values["source"]) == tmp_path / "source"
    assert setup.release()["source"]["commit"] == (
        "d578d2d4e0dc82b43e270fdaa7fa89d9708cd154"
    )


def test_gated_huggingface_failure_has_approval_instructions(tmp_path, monkeypatch):
    HfHubHTTPError = type("HfHubHTTPError", (RuntimeError,), {})

    def denied(**_):
        raise HfHubHTTPError()

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(hf_hub_download=denied),
    )
    with pytest.raises(ReleaseError, match="hf auth login") as error:
        setup.download_manifest(tmp_path, setup.release())
    assert "osworld_v2_tasks" in str(error.value)
    assert "osworld_v2_assets_gated" in str(error.value)


def test_asset_snapshot_verification_requires_pinned_commit(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "asset.bin").write_bytes(b"asset")
    metadata_file = assets / ".cache/huggingface/download/asset.bin.metadata"
    metadata_file.parent.mkdir(parents=True)
    metadata_file.write_text("pinned-commit\netag\ntime\n")
    release = {"assets": {"file_count": 1, "commit": "pinned-commit"}}
    setup.verify_assets(assets, release)
    metadata_file.write_text("different\netag\ntime\n")
    with pytest.raises(ReleaseError, match="release lock"):
        setup.verify_assets(assets, release)


@pytest.mark.parametrize("valid_checksum", [True, False])
def test_vm_download_uses_immutable_revision_and_keeps_checksum_check(
    tmp_path, monkeypatch, valid_checksum
):
    import zipfile

    archive = tmp_path / "vm.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("Ubuntu.qcow2", b"test-vm-image")
    metadata = setup.release()
    assert metadata["vm"]["commit"] == "6e16459a2feb5a8f1ed65babcfe7a2a6205d049d"
    metadata["vm"]["artifact_size"] = archive.stat().st_size
    metadata["vm"]["artifact_sha256"] = (
        setup.file_hash(archive) if valid_checksum else "0" * 64
    )
    requested = {}

    def download(**kwargs):
        requested.update(kwargs)
        return str(archive)

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=download)
    )
    if valid_checksum:
        image = setup.download_vm(tmp_path / "runtime", metadata)
        assert image.read_bytes() == b"test-vm-image"
    else:
        with pytest.raises(setup.ReleaseError, match="does not match release"):
            setup.download_vm(tmp_path / "runtime", metadata)
    assert requested["revision"] == metadata["vm"]["commit"]
    assert requested["revision"] != metadata["vm"]["tag"]
