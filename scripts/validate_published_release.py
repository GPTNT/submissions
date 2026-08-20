#!/usr/bin/env python3
"""Validate a submission with the published GPTNT release recorded in its manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml

ARCHIVE_NAME = "gptnt.tar.gz"
CHECKSUM_NAME = "gptnt.tar.gz.sha256"
_GITHUB_API = "https://api.github.com"
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
_NETWORK_TIMEOUT_SECONDS = 30


class PublishedReleaseValidationError(RuntimeError):
    """A submission cannot be tied to its declared published GPTNT release."""


@dataclass(frozen=True, kw_only=True)
class PublishedRelease:
    """The GitHub release fields used by authoritative validation."""

    tag_name: str
    draft: bool
    published_at: str | None
    assets: Mapping[str, str]


@dataclass(frozen=True, kw_only=True)
class SubmissionRelease:
    """Release identity read from one submission manifest."""

    tag: str
    commit: str


def _request(url: str, *, token: str | None = None) -> Request:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "GPTNT-submission-validator",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return Request(url, headers=headers)


def fetch_release(
    repository: str, tag: str, *, token: str | None
) -> PublishedRelease | None:
    """Read one tag-specific GitHub release, returning None when it is not published."""
    url = f"{_GITHUB_API}/repos/{quote(repository, safe='/')}/releases/tags/{quote(tag, safe='')}"
    try:
        with urlopen(  # noqa: S310 - fixed GitHub API root
            _request(url, token=token), timeout=_NETWORK_TIMEOUT_SECONDS
        ) as response:
            payload = json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise PublishedReleaseValidationError(
            f"GitHub release lookup for {tag!r} failed with HTTP {error.code}"
        ) from error
    except URLError as error:
        raise PublishedReleaseValidationError(
            f"GitHub release lookup for {tag!r} failed: {error.reason}"
        ) from error

    if not isinstance(payload, dict):
        raise PublishedReleaseValidationError(
            f"GitHub release response for {tag!r} is invalid"
        )
    asset_names = {
        asset["name"]
        for asset in payload.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }
    assets = {
        name: (
            f"https://github.com/{repository}/releases/download/"
            f"{quote(tag, safe='')}/{quote(name, safe='')}"
        )
        for name in asset_names
    }
    return PublishedRelease(
        tag_name=str(payload.get("tag_name", "")),
        draft=payload.get("draft") is True,
        published_at=(
            payload["published_at"]
            if isinstance(payload.get("published_at"), str)
            else None
        ),
        assets=assets,
    )


def read_submission_release(bundle_dir: Path) -> SubmissionRelease:
    """Read the release tag and commit required before the tag-specific validator is installed."""
    manifest_path = bundle_dir / "submission.yaml"
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        provenance = manifest["provenance"]
        tag = provenance["release_tag"]
        commit = provenance["release_commit"]
    except (FileNotFoundError, KeyError, TypeError, yaml.YAMLError) as error:
        raise PublishedReleaseValidationError(
            f"{manifest_path} does not contain release_tag and release_commit"
        ) from error
    if not isinstance(tag, str) or not isinstance(commit, str):
        raise PublishedReleaseValidationError(
            f"{manifest_path} release_tag and release_commit must be strings"
        )
    if _COMMIT_PATTERN.fullmatch(commit) is None:
        raise PublishedReleaseValidationError(
            f"{manifest_path} release_commit {commit!r} is not a complete commit SHA"
        )
    return SubmissionRelease(tag=tag, commit=commit)


def _require_published_release(
    release: PublishedRelease | None, *, expected_tag: str
) -> PublishedRelease:
    if release is None or release.draft or release.published_at is None:
        raise PublishedReleaseValidationError(
            f"GPTNT release {expected_tag!r} is missing or unpublished"
        )
    if release.tag_name != expected_tag:
        raise PublishedReleaseValidationError(
            f"GitHub release tag {release.tag_name!r} does not match submission tag {expected_tag!r}"
        )
    missing = {ARCHIVE_NAME, CHECKSUM_NAME} - release.assets.keys()
    if missing:
        raise PublishedReleaseValidationError(
            f"GPTNT release {expected_tag!r} is missing assets: {', '.join(sorted(missing))}"
        )
    return release


def _download(url: str, destination: Path) -> None:
    try:
        with (
            urlopen(  # noqa: S310 - tag-specific public release URL
                _request(url), timeout=_NETWORK_TIMEOUT_SECONDS
            ) as response,
            destination.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
    except (HTTPError, URLError, OSError) as error:
        raise PublishedReleaseValidationError(
            f"Could not download release asset {destination.name}: {error}"
        ) from error


def _verify_checksum(archive: Path, checksum: Path) -> None:
    fields = checksum.read_text(encoding="utf-8").split()
    valid_record = (
        len(fields) == 2
        and fields[1] == ARCHIVE_NAME
        and re.fullmatch(r"[0-9a-f]{64}", fields[0]) is not None
    )
    if not valid_record:
        raise PublishedReleaseValidationError(
            f"{CHECKSUM_NAME} has an invalid checksum record"
        )
    with archive.open("rb") as archive_file:
        actual = hashlib.file_digest(archive_file, "sha256").hexdigest()
    if actual != fields[0]:
        raise PublishedReleaseValidationError(
            f"{ARCHIVE_NAME} checksum {actual} does not match {fields[0]}"
        )


def _git(repository: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ
            | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "").strip()
        raise PublishedReleaseValidationError(
            f"Embedded release Git metadata failed {' '.join(arguments)!r}: {detail}"
        ) from error
    return result.stdout.strip()


def _verify_embedded_release(repository: Path, submitted: SubmissionRelease) -> None:
    embedded_tag = _git(repository, "describe", "--tags", "--exact-match")
    embedded_commit = _git(repository, "rev-parse", "HEAD")
    tag_commit = _git(repository, "rev-parse", f"{submitted.tag}^{{commit}}")
    tag_type = _git(repository, "cat-file", "-t", f"refs/tags/{submitted.tag}")

    if embedded_tag != submitted.tag or tag_type != "tag":
        raise PublishedReleaseValidationError(
            f"Embedded release_tag {embedded_tag!r} does not match {submitted.tag!r}"
        )
    if embedded_commit != submitted.commit or tag_commit != submitted.commit:
        raise PublishedReleaseValidationError(
            f"Embedded release_commit {embedded_commit!r} does not match {submitted.commit!r}"
        )


def _run_validator(repository: Path, bundle_dir: Path) -> None:
    subprocess.run(
        [
            "uvx",
            "--from",
            os.fspath(repository),
            "gptnt",
            "submission",
            "validate",
            "--format",
            "github",
            os.fspath(bundle_dir.resolve()),
        ],
        check=True,
    )


def validate_published_release(
    bundle_dir: Path,
    *,
    release: PublishedRelease | None,
    workspace: Path,
    validator: Callable[[Path, Path], None] = _run_validator,
) -> SubmissionRelease:
    """Verify and run the validator from the bundle's published GPTNT release."""
    submitted = read_submission_release(bundle_dir)
    published = _require_published_release(release, expected_tag=submitted.tag)

    downloads = workspace / "downloads"
    extracted = workspace / "extracted"
    downloads.mkdir(parents=True)
    extracted.mkdir(parents=True)
    archive = downloads / ARCHIVE_NAME
    checksum = downloads / CHECKSUM_NAME
    _download(published.assets[ARCHIVE_NAME], archive)
    _download(published.assets[CHECKSUM_NAME], checksum)
    _verify_checksum(archive, checksum)

    try:
        with tarfile.open(archive) as release_archive:
            release_archive.extractall(extracted, filter="data")
    except (OSError, tarfile.TarError) as error:
        raise PublishedReleaseValidationError(
            f"Could not extract {ARCHIVE_NAME}: {error}"
        ) from error

    release_repository = extracted / "gptnt"
    if not release_repository.is_dir():
        raise PublishedReleaseValidationError(f"{ARCHIVE_NAME} does not contain gptnt/")
    _verify_embedded_release(release_repository, submitted)
    validator(release_repository, bundle_dir)
    return submitted


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a submission with its recorded published GPTNT release."
    )
    parser.add_argument("bundle", type=Path, help="submission bundle directory")
    parser.add_argument(
        "--repository", default="GPTNT/gptnt", help="GitHub owner/repository"
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> None:
    options = _parser().parse_args(arguments)
    token = os.environ.get("GITHUB_TOKEN")
    try:
        submitted = read_submission_release(options.bundle)
        release = fetch_release(options.repository, submitted.tag, token=token)
        with tempfile.TemporaryDirectory(
            prefix="gptnt-published-release-"
        ) as temporary:
            validate_published_release(
                options.bundle,
                release=release,
                workspace=Path(temporary),
            )
    except (PublishedReleaseValidationError, subprocess.CalledProcessError) as error:
        _parser().exit(1, f"error: {error}\n")
    print(
        f"Validated {options.bundle} with published GPTNT {submitted.tag} at {submitted.commit}."
    )


if __name__ == "__main__":
    main()
