from __future__ import annotations

import hashlib
import subprocess
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import yaml

from scripts.validate_published_release import (
    ARCHIVE_NAME,
    CHECKSUM_NAME,
    PublishedRelease,
    PublishedReleaseValidationError,
    validate_published_release,
)

_TAG = "v2.0.0"


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class PublishedReleaseValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.release_repository = self.root / "release-source"
        self.release_repository.mkdir()
        _git(self.release_repository, "init", "--quiet")
        _git(self.release_repository, "config", "user.name", "Release Test")
        _git(self.release_repository, "config", "user.email", "release@example.com")
        (self.release_repository / "pyproject.toml").write_text(
            "[project]\nname = 'gptnt'\nversion = '2.0.0'\n"
        )
        _git(self.release_repository, "add", "pyproject.toml")
        _git(self.release_repository, "commit", "--quiet", "-m", "release")
        _git(self.release_repository, "tag", "-a", _TAG, "-m", f"release {_TAG}")
        self.release_commit = _git(self.release_repository, "rev-parse", "HEAD")

        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.archive = self.assets / ARCHIVE_NAME
        with tarfile.open(self.archive, "w:gz") as archive:
            archive.add(self.release_repository, arcname="gptnt")
        self.checksum = self.assets / CHECKSUM_NAME
        self._write_checksum()

        self.bundle = self.root / "submission"
        self.bundle.mkdir()
        self._write_manifest(self.release_commit)
        self.release = PublishedRelease(
            tag_name=_TAG,
            draft=False,
            published_at="2026-08-20T00:00:00Z",
            assets={
                ARCHIVE_NAME: self.archive.as_uri(),
                CHECKSUM_NAME: self.checksum.as_uri(),
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_checksum(self) -> None:
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.checksum.write_text(f"{digest}  {ARCHIVE_NAME}\n")

    def _write_manifest(self, commit: str) -> None:
        (self.bundle / "submission.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": 2,
                    "provenance": {
                        "gptnt_version": "2.0.0",
                        "release_tag": _TAG,
                        "release_commit": commit,
                        "protected_content_modified": False,
                    },
                }
            )
        )

    def test_prepared_published_release_runs_its_validator(self) -> None:
        calls: list[tuple[Path, Path]] = []

        def record_validator(repository: Path, bundle: Path) -> None:
            self.assertTrue((repository / ".git").is_dir())
            calls.append((repository, bundle))

        submitted = validate_published_release(
            self.bundle,
            release=self.release,
            workspace=self.root / "success",
            validator=record_validator,
        )

        self.assertEqual((submitted.tag, submitted.commit), (_TAG, self.release_commit))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], self.bundle)

    def test_rejects_owned_release_policies(self) -> None:
        cases = (
            ("bad checksum", "checksum", False, self.release_commit),
            ("commit mismatch", "commit", False, "f" * 40),
            ("unpublished release", "unpublished", True, self.release_commit),
        )
        for name, expected, is_draft, submitted_commit in cases:
            with self.subTest(name=name):
                self._write_checksum()
                self._write_manifest(submitted_commit)
                release = replace(self.release, draft=is_draft)
                if name == "bad checksum":
                    self.checksum.write_text(f"{'0' * 64}  {ARCHIVE_NAME}\n")

                with self.assertRaisesRegex(PublishedReleaseValidationError, expected):
                    validate_published_release(
                        self.bundle,
                        release=release,
                        workspace=self.root / name.replace(" ", "-"),
                        validator=lambda _repository, _bundle: self.fail(
                            "validator ran"
                        ),
                    )
