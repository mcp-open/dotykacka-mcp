from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest
from release.materialize_sdk import MaterializationError, materialize

ROOT = Path(__file__).resolve().parents[1]
TEST_REF = "a" * 40


def _write_snapshot(
    tmp_path: Path,
    extra_member: tarfile.TarInfo | None = None,
) -> tuple[Path, Path]:
    root = tmp_path / "connector"
    vendor = root / "release" / "vendor"
    vendor.mkdir(parents=True)
    (root / ".sdk-ref").write_text(f"{TEST_REF}\n", encoding="utf-8")
    archive_path = vendor / f"openmcp-sdk-{TEST_REF}.tar.gz"

    with tarfile.open(archive_path, "w:gz") as archive:
        for name, content in (
            ("pyproject.toml", b"[project]\nname='openmcp-sdk'\n"),
            ("openmcp_sdk/cli.py", b"def main():\n    return 0\n"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        if extra_member is not None:
            archive.addfile(extra_member)

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (vendor / "openmcp-sdk.sha256").write_text(
        f"{digest}  {archive_path.name}\n",
        encoding="utf-8",
    )
    return root, archive_path


def test_materializes_the_repository_bound_snapshot(tmp_path: Path):
    output = tmp_path / "sdk"
    ref = materialize(ROOT, output)

    assert ref == (ROOT / ".sdk-ref").read_text(encoding="utf-8").strip()
    assert (output / "pyproject.toml").is_file()
    assert (output / "openmcp_sdk" / "cli.py").is_file()
    http_transport = (
        output / "openmcp_sdk" / "transport" / "http.py"
    ).read_text(encoding="utf-8")
    distribution = (output / "openmcp_sdk" / "distribution.py").read_text(
        encoding="utf-8"
    )
    assert "stateless_http=True" in http_transport
    assert '"schema": "openmcp.openai-plugin-submission.v2"' in distribution
    assert '"openWorldHint": False' in distribution
    assert not any(path.is_symlink() for path in output.rglob("*"))


def test_rejects_a_tampered_vendored_archive(tmp_path: Path):
    root, archive_path = _write_snapshot(tmp_path)
    with archive_path.open("ab") as archive:
        archive.write(b"tampered")

    with pytest.raises(MaterializationError, match="checksum mismatch"):
        materialize(root, tmp_path / "sdk")


@pytest.mark.parametrize(
    "member",
    [
        tarfile.TarInfo("../outside"),
        tarfile.TarInfo("openmcp_sdk/linked.py"),
    ],
)
def test_rejects_unsafe_archive_members(
    tmp_path: Path,
    member: tarfile.TarInfo,
):
    if member.name.endswith("linked.py"):
        member.type = tarfile.SYMTYPE
        member.linkname = "cli.py"
    root, _ = _write_snapshot(tmp_path, member)

    with pytest.raises(MaterializationError):
        materialize(root, tmp_path / "sdk")


def test_rejects_a_non_empty_output_directory(tmp_path: Path):
    root, _ = _write_snapshot(tmp_path)
    output = tmp_path / "sdk"
    output.mkdir()
    (output / "unrelated").write_text("keep", encoding="utf-8")

    with pytest.raises(MaterializationError, match="not empty"):
        materialize(root, output)
