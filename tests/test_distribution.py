from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import yaml
from openmcp_sdk.distribution import load_distribution, render_distribution
from openmcp_sdk.release_gate import load_gate

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOL_ORDER = [
    "dotykacka__get_cloud_info",
    "dotykacka__list_orders",
    "dotykacka__get_order",
    "dotykacka__list_products",
    "dotykacka__list_categories",
    "dotykacka__list_warehouses",
    "dotykacka__list_customers",
    "dotykacka__list_employees",
    "dotykacka__sales_summary",
]
EXPECTED_TOOLS = set(EXPECTED_TOOL_ORDER)


def _render(tmp_path: Path) -> tuple[dict, Path]:
    output = tmp_path / "dist"
    result = render_distribution(
        ROOT / "connector.yaml",
        ROOT / "distribution.yaml",
        output,
    )
    return result, output


def test_distribution_renders_only_exact_remote_targets(tmp_path: Path):
    config = load_distribution(ROOT / "distribution.yaml")
    result, output = _render(tmp_path)

    assert config.mode == "remote"
    assert {artifact["target"] for artifact in result["artifacts"]} == {
        "openai_remote",
        "gemini_remote",
    }
    assert result["staging"] == []
    assert not list(output.rglob("*.mcpb"))


def test_openai_handoff_is_fail_closed_and_exact(tmp_path: Path):
    _, output = _render(tmp_path)
    submission = json.loads((output / "openai" / "submission.json").read_text(encoding="utf-8"))

    assert submission["schema"] == "openmcp.openai-plugin-submission.v2"
    assert submission["artifact_kind"] == "operator_handoff"
    assert submission["installable"] is False
    assert submission["submission_state"] == "blocked"
    assert submission["connector"] == {"slug": "dotykacka", "version": "0.1.0"}
    assert submission["mcp_server_url"] is None
    assert submission["workspace_mcp_server_url_template"] == (
        "https://mcp.openmcp.cz/w/{workspace}/mcp"
    )
    assert submission["authentication"] == {
        "type": "oauth2",
        "scopes": ["mcp"],
        "protected_resource_metadata": None,
        "protected_resource_metadata_template": (
            "https://mcp.openmcp.cz/.well-known/"
            "oauth-protected-resource/w/{workspace}/mcp"
        ),
    }
    assert "privacy_policy" not in submission["publisher"]
    assert {tool["name"] for tool in submission["tools"]} == EXPECTED_TOOLS
    assert len(submission["tools"]) == 9
    for tool in submission["tools"]:
        assert tool["description"]
        assert tool["annotations"] == {
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        }
        assert tool["securitySchemes"] == [{"type": "oauth2", "scopes": ["mcp"]}]
    assert submission["submission_gates"] == {
        "concrete_mcp_server_url": "missing",
        "privacy_policy_url": "missing",
        "scan_tools": "required",
        "external_demo_credentials": "required",
        "chatgpt_and_codex_surface_e2e": "required",
        "organization_verification": "required",
        "domain_verification": "required",
        "breaking_tool_schema_changes": "forbidden",
    }
    encoded = json.dumps(submission)
    assert '"command"' not in encoded
    assert '"args"' not in encoded
    assert "refresh_token" not in encoded
    assert "client_secret" not in encoded
    assert "review-dotykacka" not in encoded
    assert "openmcp.cz/soukromi" not in encoded


def test_gemini_bundle_is_remote_only_sensitive_and_exact(tmp_path: Path):
    _, output = _render(tmp_path)
    archive_path = output / "openmcp-dotykacka-0.1.0-gemini.zip"

    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == [
            "GEMINI.md",
            "INSTALL.md",
            "gemini-extension.json",
        ]
        extension = json.loads(archive.read("gemini-extension.json"))
        instructions = archive.read("GEMINI.md").decode("utf-8")
        install = archive.read("INSTALL.md").decode("utf-8")

    assert extension["name"] == "openmcp-dotykacka"
    assert extension["version"] == "0.1.0"
    assert extension["contextFileName"] == "GEMINI.md"
    assert set(extension["mcpServers"]) == {"dotykacka"}
    server = extension["mcpServers"]["dotykacka"]
    assert server == {
        "httpUrl": "${OPENMCP_MCP_URL}",
        "authProviderType": "dynamic_discovery",
        "oauth": {"scopes": ["mcp"]},
        "timeout": 600000,
        "includeTools": EXPECTED_TOOL_ORDER,
    }
    assert extension["settings"] == [
        {
            "name": "OpenMCP workspace URL",
            "description": (
                "Přesná HTTPS adresa pracovního prostoru ve tvaru https://…/w/<workspace>/mcp"
            ),
            "envVar": "OPENMCP_MCP_URL",
            "sensitive": True,
        }
    ]
    encoded = json.dumps(extension)
    for forbidden in ('"command"', '"args"', '"cwd"', '"hooks"', "review-dotykacka"):
        assert forbidden not in encoded
    assert "standardní `/mcp auth` flow" in instructions
    assert "ne přímý instalační formát" in install


def test_remote_artifacts_are_byte_deterministic(tmp_path: Path):
    _, first = _render(tmp_path / "first")
    _, second = _render(tmp_path / "second")

    first_files = {
        path.relative_to(first).as_posix(): path.read_bytes()
        for path in first.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second).as_posix(): path.read_bytes()
        for path in second.rglob("*")
        if path.is_file()
    }
    assert first_files == second_files


def test_release_gate_paths_match_rendered_artifacts(tmp_path: Path):
    result, _ = _render(tmp_path)
    gate = load_gate(ROOT / "release-gate.yaml")

    expected = {f"release/dist/{artifact['path']}" for artifact in result["artifacts"]}
    assert {artifact.artifact for artifact in gate.artifacts} == expected
    assert gate.source_repository == "https://github.com/mcp-open/dotykacka-mcp"
    assert gate.high_approvals == "release/high-approvals.yaml"
    assert gate.trivy_version == "0.72.0"
    assert gate.high_approvers == []
    assert all(
        artifact.scan_targets
        == [
            "build-inputs/runtime/requirements.txt",
            "build-inputs/release/requirements.txt",
        ]
        and artifact.contents_manifest.endswith(".contents.json")
        and "release/runtime-requirements.in" in artifact.lockfiles
        and "release/python-requirements.in" in artifact.lockfiles
        and "release/runtime-requirements.lock" in artifact.lockfiles
        and "release/python-requirements.lock" in artifact.lockfiles
        and "release/toolchain.lock" in artifact.lockfiles
        for artifact in gate.artifacts
    )
    assert yaml.safe_load((ROOT / gate.high_approvals).read_text(encoding="utf-8")) == []


def test_all_workflow_actions_are_commit_pinned():
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    assert workflows
    for workflow_path in workflows:
        workflow = workflow_path.read_text(encoding="utf-8")
        uses = re.findall(
            r"^\s*(?:-\s*)?uses:\s*([^\s#]+)",
            workflow,
            flags=re.MULTILINE,
        )
        assert uses, workflow_path
        for reference in uses:
            assert re.search(r"@[0-9a-f]{40}$", reference), (
                workflow_path,
                reference,
            )


def test_ci_and_release_use_reviewed_snapshot_and_main_only_mutations():
    workflows = {
        path.name: path.read_text(encoding="utf-8")
        for path in (ROOT / ".github/workflows").glob("*.yml")
    }
    assert workflows
    for name, workflow in workflows.items():
        assert "repository: mcp-open/openmcp-sdk" not in workflow, name
        if name in {"ci.yml", "distribution-release.yml", "sdk-canary.yml"}:
            assert "release/materialize_sdk.py" in workflow, name

    ci = workflows["ci.yml"]
    assert "SLUG: dotykacka" in ci
    assert "runs-on: self-hosted" not in ci
    assert "component-built" not in ci
    assert "OPENMCP_CI_TOKEN" not in ci
    assert re.search(r"^\s{2}(?:build|deploy):\s*$", ci, re.MULTILINE) is None

    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for required in (
        "**/.github",
        "**/tests",
        "**/release/vendor",
        "**/release/evidence",
    ):
        assert required in dockerignore

    release = workflows["distribution-release.yml"]
    assert "Require main release identity" in release
    assert "refs/heads/main" in release
    assert "openmcp_sdk.cli build-contents-manifest" in release
    assert "openmcp_sdk.cli bind-trivy-report" in release
    assert "--scanners vuln,secret,misconfig" in release
    assert "id-token: write" in release
    assert "persist-credentials: false" in release
    assert "OPENMCP_SDK_DEPLOY_KEY" not in release
    assert "--require-hashes" in release
    assert "sdk_ref=$(cat .sdk-ref)" in release
    assert 'sdk_archive="release/vendor/openmcp-sdk-${sdk_ref}.tar.gz"' in release
    assert '--lock "$sdk_archive=$sdk_archive"' in release
    assert not re.search(
        r"--lock release/vendor/openmcp-sdk-[0-9a-f]{40}\.tar\.gz=",
        release,
    )

    assert "--require-hashes" in ci
    assert "--no-deps --no-build-isolation" in ci

    canary = workflows["sdk-canary.yml"]
    assert "--require-hashes" in canary
    assert "--no-deps --no-build-isolation" in canary


def test_dependency_and_container_inputs_are_pinned():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert (
        "FROM python:3.13-alpine@sha256:"
        "399babc8b49529dabfd9c922f2b5eea81d611e4512e3ed250d75bd2e7683f4b0"
    ) in dockerfile
    assert (ROOT / ".sdk-ref").read_text(encoding="utf-8").strip() == (
        "2f041cfa33d4116cf06fb4f26169d16001cf968b"
    )
    assert "--require-hashes" in dockerfile
    assert "--no-deps --no-build-isolation" in dockerfile

    for relative in (
        "release/runtime-requirements.lock",
        "release/python-requirements.lock",
    ):
        requirements = (ROOT / relative).read_text(encoding="utf-8")
        package_lines = [
            line
            for line in requirements.splitlines()
            if line and not line.startswith(("#", " ", "-"))
        ]
        assert len(package_lines) >= 70
        assert all(line.endswith(" \\") for line in package_lines), relative
        assert requirements.count("--hash=sha256:") >= len(package_lines), relative

    lock = json.loads((ROOT / ".github/mcpb-tools/package-lock.json").read_text(encoding="utf-8"))
    assert lock["packages"]["node_modules/@anthropic-ai/mcpb"]["version"] == "2.1.2"
    assert lock["packages"]["node_modules/tmp"]["version"] == "0.2.7"


def test_readme_does_not_claim_external_release_gates_are_complete():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "installable: false" in readme
    assert "review workspace existuje" in readme
    assert "privacy policy" in readme
    assert "ChatGPT web i mobile" in readme
    assert "nesmí označit jako publikovaný" in readme
