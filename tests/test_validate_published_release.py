from __future__ import annotations

import hashlib
import os
import subprocess
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.validate_published_release import (
    ARCHIVE_NAME,
    CHECKSUM_NAME,
    PublishedRelease,
    PublishedReleaseValidationError,
    _commit_from_annotated_tag,
    _run_validator,
    _verify_embedded_release,
    read_submission_release,
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

    def test_release_validator_requires_the_installed_lock_match(self) -> None:
        """The released validator compares each bundle to its installed suite registry."""
        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "private-token"}),
            patch("scripts.validate_published_release.subprocess.run") as run,
        ):
            _run_validator(self.release_repository, self.bundle)

        self.assertEqual(run.call_count, 2)
        freeze_command = run.call_args_list[0].args[0]
        validate_command = run.call_args_list[1].args[0]
        expected_prefix = [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(self.release_repository),
            "gptnt",
        ]
        self.assertEqual(freeze_command[:6], expected_prefix)
        self.assertEqual(validate_command[:6], expected_prefix)
        self.assertEqual(freeze_command[-4:], ["suite", "freeze", "--check", "--force"])
        self.assertIn("--require-installed-lock-match", validate_command)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["cwd"], self.release_repository)
            self.assertNotIn("CONFIGS", call.kwargs["env"])
            self.assertNotIn("ROOT", call.kwargs["env"])
            self.assertNotIn("GITHUB_TOKEN", call.kwargs["env"])

    def test_rejects_a_bundle_that_contains_a_symlink(self) -> None:
        """A submission bundle must not make the released validator read outside the bundle."""
        outside_file = self.root / "outside.txt"
        outside_file.write_text("outside")
        (self.bundle / "linked.txt").symlink_to(outside_file)

        with self.assertRaisesRegex(PublishedReleaseValidationError, "symlink"):
            validate_published_release(
                self.bundle,
                release=self.release,
                workspace=self.root / "symlink",
                validator=lambda _repository, _bundle: self.fail("validator ran"),
            )

    def test_rejects_a_non_release_tag(self) -> None:
        """Submission provenance must select a normal GPTNT release tag."""
        self._write_manifest(self.release_commit)
        manifest_path = self.bundle / "submission.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["provenance"]["release_tag"] = "nightly"
        manifest_path.write_text(yaml.safe_dump(manifest))

        with self.assertRaisesRegex(PublishedReleaseValidationError, "release_tag"):
            read_submission_release(self.bundle)

    def test_annotated_tag_must_resolve_to_a_complete_commit(self) -> None:
        """The remote Git reference is an independent release-asset trust anchor."""
        reference = {"object": {"type": "tag", "sha": "a" * 40}}
        tag_object = {"object": {"type": "commit", "sha": self.release_commit}}

        assert (
            _commit_from_annotated_tag(_TAG, reference, tag_object)
            == self.release_commit
        )
        with self.assertRaisesRegex(PublishedReleaseValidationError, "annotated tag"):
            _commit_from_annotated_tag(_TAG, {"object": {"type": "commit"}}, tag_object)
        with self.assertRaisesRegex(PublishedReleaseValidationError, "complete commit"):
            _commit_from_annotated_tag(
                _TAG, reference, {"object": {"type": "commit", "sha": "short"}}
            )

    def test_rejects_an_embedded_release_tree_that_is_not_its_tag(self) -> None:
        """The release archive must contain exactly the remote tag's source tree."""
        (self.release_repository / "injected.py").write_text("raise RuntimeError\n")
        submitted = read_submission_release(self.bundle)

        with self.assertRaisesRegex(
            PublishedReleaseValidationError, "does not match its tag tree"
        ):
            _verify_embedded_release(self.release_repository, submitted)
