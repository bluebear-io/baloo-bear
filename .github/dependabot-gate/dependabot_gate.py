#!/usr/bin/env python3
# Vendored from bluebear-io/blueden .github/actions/dependabot-gate/dependabot_gate.py,
# which has the tests; keep this copy identical to it apart from these two lines.
"""Deterministic guardrails for Dependabot pull requests.

A change passes only when every changed file is a supported dependency file and
every added dependency version is pinned exactly, is not a semver-major update,
and was published at least --min-age-days ago. Anything that cannot be evaluated
is a violation, so the gate fails closed.
"""

import argparse
import copy
import difflib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol

import tomllib

DEFAULT_MIN_AGE_DAYS = 28

NPM = "npm"
PYPI = "pypi"
GO = "go"
GITHUB_ACTIONS = "github-actions"
DOCKER = "docker"

EXACT_SEMVER = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
EXACT_PEP440 = re.compile(r"===?\s*([0-9][^\s,;]*)")
FULL_SHA = re.compile(r"[0-9a-f]{40}")
FULL_DOCKER_TAG = re.compile(r"v?\d+\.\d+\.\d+(?:[-_.][0-9A-Za-z.-]+)?")
GO_MAJOR_SUFFIX = re.compile(r"[./]v(\d+)$")

NPM_DEPENDENCY_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies")
NPM_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml")
PYTHON_LOCKFILES = ("poetry.lock", "uv.lock")
DOCKER_HUB_HOSTS = ("docker.io", "index.docker.io", "registry-1.docker.io")


class GateError(Exception):
    pass


@dataclass(frozen=True, order=True)
class Dependency:
    ecosystem: str
    name: str
    version: str
    ref: str | None = None

    def __str__(self) -> str:
        pinned = f"@{self.ref}" if self.ref else ""
        return f"{self.ecosystem}:{self.name} {self.version}{pinned}"


@dataclass(frozen=True)
class Update:
    dependency: Dependency
    source: str
    major: int | None
    previous_majors: tuple[int | None, ...] = ()


@dataclass
class FileResult:
    updates: list[Update] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    requires_lockfile_from: tuple[str, ...] = ()


@dataclass
class GateResult:
    files: list[str]
    updates: list[Update]
    violations: list[str]

    @property
    def passed(self) -> bool:
        return not self.violations


class PublishDates(Protocol):
    def published_at(self, dependency: Dependency) -> datetime: ...


def major_of(version: str) -> int | None:
    match = re.search(r"\d+", version)
    return int(match.group(0)) if match else None


def _normalize_python_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _not_pinned(path: str, name: str, spec: Any) -> str:
    return f"{path}: {name} is not pinned to an exact version ({spec!r})"


def _changed_line_blocks(old: str, new: str) -> list[tuple[list[str], list[str]]]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    return [
        (old_lines[i1:i2], new_lines[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def _lock_updates(
    ecosystem: str,
    source: str,
    old_pairs: set[tuple[str, str]],
    new_pairs: set[tuple[str, str]],
) -> list[Update]:
    old_by_name: dict[str, set[str]] = defaultdict(set)
    for name, version in old_pairs:
        old_by_name[name].add(version)
    return [
        Update(
            Dependency(ecosystem, name, version),
            source,
            major_of(version),
            tuple(major_of(previous) for previous in sorted(old_by_name.get(name, ()))),
        )
        for name, version in sorted(new_pairs - old_pairs)
    ]


def check_npm_manifest(path: str, old: str, new: str) -> FileResult:
    result = FileResult(requires_lockfile_from=NPM_LOCKFILES)
    old_data = json.loads(old)
    new_data = json.loads(new)

    def without_dependencies(data: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in data.items() if k not in NPM_DEPENDENCY_SECTIONS}

    if without_dependencies(old_data) != without_dependencies(new_data):
        result.violations.append(f"{path}: changes outside {', '.join(NPM_DEPENDENCY_SECTIONS)}")
    for section in NPM_DEPENDENCY_SECTIONS:
        old_section = old_data.get(section, {})
        new_section = new_data.get(section, {})
        for name in sorted(set(old_section) | set(new_section)):
            old_spec = old_section.get(name)
            new_spec = new_section.get(name)
            if old_spec == new_spec or new_spec is None:
                continue
            if not isinstance(new_spec, str) or not EXACT_SEMVER.fullmatch(new_spec):
                result.violations.append(_not_pinned(path, name, new_spec))
                continue
            previous = (major_of(old_spec),) if isinstance(old_spec, str) else ()
            result.updates.append(
                Update(Dependency(NPM, name, new_spec), path, major_of(new_spec), previous)
            )
    return result


def _npm_lock_pairs(content: str) -> set[tuple[str, str]]:
    data = json.loads(content)
    if data.get("lockfileVersion") not in (2, 3):
        raise GateError(f"unsupported lockfileVersion {data.get('lockfileVersion')!r}")
    pairs: set[tuple[str, str]] = set()
    for key, entry in data.get("packages", {}).items():
        if "node_modules/" not in key or entry.get("link"):
            continue
        version = entry.get("version")
        if not isinstance(version, str) or not version:
            raise GateError(f"package {key!r} has no version")
        pairs.add((key.rsplit("node_modules/", 1)[1], version))
    return pairs


def check_npm_lock(path: str, old: str, new: str) -> FileResult:
    return FileResult(updates=_lock_updates(NPM, path, _npm_lock_pairs(old), _npm_lock_pairs(new)))


PNPM_PACKAGE_KEY = re.compile(r"^  '?/?((?:@[^/@\s']+/)?[^@\s'(/]+)@([^(\s':]+)(?:\(.*\))?'?:\s*$")


def _pnpm_lock_pairs(content: str) -> set[tuple[str, str]]:
    version_match = re.search(r"^lockfileVersion:\s*'?([0-9.]+)'?", content, re.M)
    if not version_match or float(version_match.group(1)) < 6:
        raise GateError("unsupported pnpm lockfileVersion")
    pairs: set[tuple[str, str]] = set()
    in_packages = False
    for line in content.splitlines():
        if line and not line.startswith(" "):
            in_packages = line.rstrip() == "packages:"
            continue
        if not in_packages or not line.startswith("  ") or line.startswith("   "):
            continue
        match = PNPM_PACKAGE_KEY.match(line)
        if not match:
            raise GateError(f"cannot parse pnpm package key {line.strip()!r}")
        pairs.add((match.group(1), match.group(2)))
    return pairs


def check_pnpm_lock(path: str, old: str, new: str) -> FileResult:
    return FileResult(
        updates=_lock_updates(NPM, path, _pnpm_lock_pairs(old), _pnpm_lock_pairs(new))
    )


REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")


def _parse_requirements(content: str) -> tuple[dict[str, str], set[str]]:
    requirements: dict[str, str] = {}
    options: set[str] = set()
    logical_lines: list[str] = []
    buffer = ""
    for raw_line in content.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw_line).rstrip()
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        logical_lines.append(buffer + line)
        buffer = ""
    if buffer:
        logical_lines.append(buffer)
    for logical_line in logical_lines:
        line = logical_line.strip()
        if not line:
            continue
        if line.startswith("-"):
            options.add(line)
            continue
        match = REQUIREMENT.match(line)
        if not match:
            raise GateError(f"cannot parse requirement {line!r}")
        spec = match.group(3).split(";", 1)[0].split(" --", 1)[0].strip()
        requirements[_normalize_python_name(match.group(1))] = spec
    return requirements, options


def check_requirements(path: str, old: str, new: str) -> FileResult:
    result = FileResult()
    old_requirements, old_options = _parse_requirements(old)
    new_requirements, new_options = _parse_requirements(new)
    if old_options != new_options:
        result.violations.append(f"{path}: requirement options changed")
    for name in sorted(new_requirements):
        old_spec = old_requirements.get(name)
        new_spec = new_requirements[name]
        if old_spec == new_spec:
            continue
        match = EXACT_PEP440.fullmatch(new_spec)
        if not match:
            result.violations.append(_not_pinned(path, name, new_spec))
            continue
        previous = (major_of(old_spec),) if old_spec is not None else ()
        result.updates.append(
            Update(
                Dependency(PYPI, name, match.group(1)),
                path,
                major_of(match.group(1)),
                previous,
            )
        )
    return result


PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^;]*)")


def _pyproject_dependencies(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    remainder = copy.deepcopy(data)
    dependencies: dict[str, Any] = {}

    def add_pep508(group: str, entries: Any) -> None:
        for entry in entries:
            if not isinstance(entry, str):
                dependencies[f"{group}:{json.dumps(entry, sort_keys=True)}"] = entry
                continue
            match = PEP508.match(entry)
            if not match:
                raise GateError(f"cannot parse dependency {entry!r}")
            dependencies[f"{group}:{_normalize_python_name(match.group(1))}"] = match.group(
                3
            ).strip()

    project = remainder.get("project", {})
    add_pep508("project", project.pop("dependencies", []))
    for group, entries in project.pop("optional-dependencies", {}).items():
        add_pep508(f"optional-{group}", entries)
    for group, entries in remainder.pop("dependency-groups", {}).items():
        add_pep508(f"group-{group}", entries)

    poetry = remainder.get("tool", {}).get("poetry", {})
    poetry_sections = {
        "poetry": poetry.pop("dependencies", {}),
        "poetry-dev": poetry.pop("dev-dependencies", {}),
    }
    for group, group_data in poetry.get("group", {}).items():
        poetry_sections[f"poetry-{group}"] = group_data.pop("dependencies", {})
    for group, entries in poetry_sections.items():
        for name, spec in entries.items():
            dependencies[f"{group}:{_normalize_python_name(name)}"] = spec
    return dependencies, remainder


def _pyproject_exact_version(spec: Any) -> str | None:
    if isinstance(spec, dict):
        spec = spec.get("version")
    if not isinstance(spec, str):
        return None
    match = EXACT_PEP440.fullmatch(spec.strip())
    if match:
        return match.group(1)
    return spec if EXACT_SEMVER.fullmatch(spec) else None


def check_pyproject(path: str, old: str, new: str) -> FileResult:
    result = FileResult(requires_lockfile_from=PYTHON_LOCKFILES)
    old_dependencies, old_rest = _pyproject_dependencies(tomllib.loads(old))
    new_dependencies, new_rest = _pyproject_dependencies(tomllib.loads(new))
    if old_rest != new_rest:
        result.violations.append(f"{path}: changes outside dependency declarations")
    for key in sorted(new_dependencies):
        old_spec = old_dependencies.get(key)
        new_spec = new_dependencies[key]
        if old_spec == new_spec:
            continue
        name = key.split(":", 1)[1]
        if name == "python":
            result.violations.append(f"{path}: Python version constraint changed")
            continue
        version = _pyproject_exact_version(new_spec)
        if version is None:
            result.violations.append(_not_pinned(path, name, new_spec))
            continue
        old_text = old_spec.get("version") if isinstance(old_spec, dict) else old_spec
        previous = (major_of(old_text),) if isinstance(old_text, str) else ()
        result.updates.append(
            Update(Dependency(PYPI, name, version), path, major_of(version), previous)
        )
    return result


def _python_lock_pairs(content: str) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for package in tomllib.loads(content).get("package", []):
        source = package.get("source", {})
        if isinstance(source, dict) and source.keys() & {"editable", "virtual", "directory"}:
            continue
        name = package.get("name")
        version = package.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise GateError(f"locked package without name or version: {package!r}")
        pairs.add((_normalize_python_name(name), version))
    return pairs


def check_python_lock(path: str, old: str, new: str) -> FileResult:
    return FileResult(
        updates=_lock_updates(PYPI, path, _python_lock_pairs(old), _python_lock_pairs(new))
    )


def _go_family_and_major(module: str, version: str) -> tuple[str, int | None]:
    suffix = GO_MAJOR_SUFFIX.search(module)
    if suffix:
        return module[: suffix.start()], int(suffix.group(1))
    return module, major_of(version)


def _go_updates(
    source: str, old_pairs: set[tuple[str, str]], new_pairs: set[tuple[str, str]]
) -> list[Update]:
    old_majors: dict[str, set[int | None]] = defaultdict(set)
    for module, version in old_pairs:
        family, major = _go_family_and_major(module, version)
        old_majors[family].add(major)
    updates = []
    for module, version in sorted(new_pairs - old_pairs):
        family, major = _go_family_and_major(module, version)
        previous = tuple(old_majors.get(family, ()))
        updates.append(Update(Dependency(GO, module, version), source, major, previous))
    return updates


def _parse_go_mod(content: str) -> tuple[set[tuple[str, str]], list[str]]:
    requires: set[tuple[str, str]] = set()
    directives: list[str] = []
    in_require = False
    for raw_line in content.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        if in_require:
            if line == ")":
                in_require = False
                continue
            fields = line.split()
        elif line == "require (":
            in_require = True
            continue
        elif line.startswith("require "):
            fields = line.split()[1:]
        else:
            directives.append(" ".join(line.split()))
            continue
        if len(fields) != 2:
            raise GateError(f"cannot parse go.mod require {raw_line.strip()!r}")
        requires.add((fields[0], fields[1]))
    return requires, directives


def check_go_mod(path: str, old: str, new: str) -> FileResult:
    result = FileResult(requires_lockfile_from=("go.sum",))
    old_requires, old_directives = _parse_go_mod(old)
    new_requires, new_directives = _parse_go_mod(new)
    if old_directives != new_directives:
        result.violations.append(f"{path}: changes outside require directives")
    result.updates = _go_updates(path, old_requires, new_requires)
    return result


def _go_sum_pairs(content: str) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for line in content.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 3:
            raise GateError(f"cannot parse go.sum line {line!r}")
        pairs.add((fields[0], fields[1].removesuffix("/go.mod")))
    return pairs


def check_go_sum(path: str, old: str, new: str) -> FileResult:
    return FileResult(updates=_go_updates(path, _go_sum_pairs(old), _go_sum_pairs(new)))


USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*['\"]?([^'\"\s#]+)['\"]?\s*(?:#\s*(\S+).*)?$")


def _action_repository(action: str) -> str:
    return "/".join(action.split("/")[:2])


def check_github_actions(path: str, old: str, new: str) -> FileResult:
    result = FileResult()
    for removed, added in _changed_line_blocks(old, new):
        previous_versions: dict[str, list[str]] = defaultdict(list)
        for line in removed:
            match = USES_LINE.match(line)
            if not match:
                result.violations.append(f"{path}: non-dependency line removed: {line.strip()!r}")
                continue
            if match.group(2):
                action = match.group(1).partition("@")[0]
                previous_versions[_action_repository(action)].append(match.group(2))
        for line in added:
            match = USES_LINE.match(line)
            if not match:
                result.violations.append(f"{path}: non-dependency line added: {line.strip()!r}")
                continue
            action, _, ref = match.group(1).partition("@")
            if action.startswith(("./", "docker://")) or "/" not in action:
                result.violations.append(f"{path}: unsupported action reference {match.group(1)!r}")
                continue
            if not FULL_SHA.fullmatch(ref):
                result.violations.append(
                    f"{path}: {action} is not pinned to a full commit SHA ({ref!r})"
                )
                continue
            version = match.group(2)
            if not version:
                result.violations.append(
                    f"{path}: {action}@{ref} has no version comment to evaluate"
                )
                continue
            repository = _action_repository(action)
            result.updates.append(
                Update(
                    Dependency(GITHUB_ACTIONS, repository, version, ref),
                    path,
                    major_of(version),
                    tuple(major_of(v) for v in previous_versions.get(repository, [])),
                )
            )
    return result


DOCKER_FROM = re.compile(
    r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+\S+)?\s*$", re.IGNORECASE
)
COMPOSE_IMAGE = re.compile(r"^\s*image:\s*['\"]?([^'\"\s#]+)['\"]?\s*(?:#.*)?$")


def parse_image(reference: str) -> tuple[str, str | None, str | None]:
    name, _, digest = reference.partition("@")
    tag = None
    if name.rfind(":") > name.rfind("/"):
        name, tag = name.rsplit(":", 1)
    return name, tag, digest or None


def _check_image_lines(path: str, old: str, new: str, pattern: re.Pattern[str]) -> FileResult:
    result = FileResult()
    for removed, added in _changed_line_blocks(old, new):
        previous_majors: dict[str, list[int | None]] = defaultdict(list)
        for line in removed:
            match = pattern.match(line)
            if not match:
                result.violations.append(f"{path}: non-dependency line removed: {line.strip()!r}")
                continue
            name, tag, _ = parse_image(match.group(1))
            if tag:
                previous_majors[name].append(major_of(tag))
        for line in added:
            match = pattern.match(line)
            if not match:
                result.violations.append(f"{path}: non-dependency line added: {line.strip()!r}")
                continue
            reference = match.group(1)
            if "$" in reference:
                result.violations.append(f"{path}: image reference uses a variable ({reference!r})")
                continue
            name, tag, digest = parse_image(reference)
            if tag is None or tag == "latest":
                result.violations.append(
                    f"{path}: {name} must use an exact version tag, not {tag or 'no tag'}"
                )
                continue
            if digest is None and not FULL_DOCKER_TAG.fullmatch(tag):
                result.violations.append(
                    f"{path}: {name}:{tag} is neither an exact version tag nor pinned by digest"
                )
                continue
            result.updates.append(
                Update(
                    Dependency(DOCKER, name, tag, digest),
                    path,
                    major_of(tag),
                    tuple(previous_majors.get(name, [])),
                )
            )
    return result


def check_dockerfile(path: str, old: str, new: str) -> FileResult:
    return _check_image_lines(path, old, new, DOCKER_FROM)


def check_compose(path: str, old: str, new: str) -> FileResult:
    return _check_image_lines(path, old, new, COMPOSE_IMAGE)


Checker = Callable[[str, str, str], FileResult]


def checker_for(path: str) -> Checker | None:
    posix = PurePosixPath(path)
    name = posix.name
    if name == "package.json":
        return check_npm_manifest
    if name in ("package-lock.json", "npm-shrinkwrap.json"):
        return check_npm_lock
    if name == "pnpm-lock.yaml":
        return check_pnpm_lock
    if name.endswith((".txt", ".in")) and (
        "requirements" in name or posix.parent.name == "requirements"
    ):
        return check_requirements
    if name == "pyproject.toml":
        return check_pyproject
    if name in PYTHON_LOCKFILES:
        return check_python_lock
    if name == "go.mod":
        return check_go_mod
    if name == "go.sum":
        return check_go_sum
    if name.endswith((".yml", ".yaml")) and (
        path.startswith(".github/workflows/") or name in ("action.yml", "action.yaml")
    ):
        return check_github_actions
    if name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".Dockerfile"):
        return check_dockerfile
    if re.fullmatch(r"(docker-)?compose(\.[\w-]+)?\.ya?ml", name):
        return check_compose
    return None


def _http_json(url: str, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "bluebear-dependabot-gate",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise GateError(f"GET {url} failed with HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise GateError(f"GET {url} failed: {error}") from error


def _parse_time(value: Any, context: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise GateError(f"no publish time for {context}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise GateError(f"publish time for {context} has no timezone: {value!r}")
    return parsed


def _go_escape(value: str) -> str:
    return re.sub(r"[A-Z]", lambda match: "!" + match.group(0).lower(), value)


class GitHubApi:
    def __init__(self, token: str | None) -> None:
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def get(self, path: str) -> Any:
        return _http_json(f"https://api.github.com{path}", self._headers)

    def get_all(self, path: str) -> list[Any]:
        separator = "&" if "?" in path else "?"
        results: list[Any] = []
        page = 1
        while True:
            response = self.get(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(response, list):
                raise GateError(f"GET {path} did not return a list")
            results.extend(response)
            if len(response) < 100:
                return results
            page += 1


DEPENDABOT_LOGIN = "dependabot[bot]"
DEPENDABOT_COMMITTERS = (DEPENDABOT_LOGIN, "web-flow")


def pull_request_violations(github: GitHubApi, repository: str, number: int) -> list[str]:
    pull_request = github.get(f"/repos/{repository}/pulls/{number}")
    author = (pull_request.get("user") or {}).get("login")
    violations = []
    if author != DEPENDABOT_LOGIN:
        violations.append(f"pull request #{number} is authored by {author}, not {DEPENDABOT_LOGIN}")
    for commit in github.get_all(f"/repos/{repository}/pulls/{number}/commits"):
        commit_author = (commit.get("author") or {}).get("login")
        committer = (commit.get("committer") or {}).get("login")
        verified = commit.get("commit", {}).get("verification", {}).get("verified") is True
        if (
            commit_author != DEPENDABOT_LOGIN
            or committer not in DEPENDABOT_COMMITTERS
            or not verified
        ):
            violations.append(
                f"commit {commit.get('sha')} is not a verified Dependabot commit "
                f"(author {commit_author}, committer {committer}, verified {verified})"
            )
    return violations


class RegistryPublishDates:
    def __init__(self, github: GitHubApi) -> None:
        self._github = github

    def published_at(self, dependency: Dependency) -> datetime:
        lookups = {
            NPM: self._npm,
            PYPI: self._pypi,
            GO: self._go,
            GITHUB_ACTIONS: self._github_action,
            DOCKER: self._docker,
        }
        return lookups[dependency.ecosystem](dependency)

    def _npm(self, dependency: Dependency) -> datetime:
        name = urllib.parse.quote(dependency.name, safe="@")
        data = _http_json(f"https://registry.npmjs.org/{name}")
        return _parse_time(data.get("time", {}).get(dependency.version), str(dependency))

    def _pypi(self, dependency: Dependency) -> datetime:
        name = urllib.parse.quote(dependency.name, safe="")
        version = urllib.parse.quote(dependency.version, safe="")
        data = _http_json(f"https://pypi.org/pypi/{name}/{version}/json")
        uploads = [
            _parse_time(file.get("upload_time_iso_8601"), str(dependency))
            for file in data.get("urls", [])
        ]
        if not uploads:
            raise GateError(f"no uploaded files for {dependency}")
        return min(uploads)

    def _go(self, dependency: Dependency) -> datetime:
        module = _go_escape(dependency.name)
        version = _go_escape(dependency.version)
        data = _http_json(f"https://proxy.golang.org/{module}/@v/{version}.info")
        return _parse_time(data.get("Time"), str(dependency))

    def _github_action(self, dependency: Dependency) -> datetime:
        tag = urllib.parse.quote(dependency.version, safe="")
        commit = self._github.get(f"/repos/{dependency.name}/commits/{tag}")
        if commit.get("sha") != dependency.ref:
            raise GateError(
                f"tag {dependency.version} points to {commit.get('sha')}, not the pinned SHA"
            )
        release = self._github.get(f"/repos/{dependency.name}/releases/tags/{tag}")
        return _parse_time(release.get("published_at"), str(dependency))

    def _docker(self, dependency: Dependency) -> datetime:
        parts = dependency.name.split("/")
        # ECR Public's docker/library mirrors the Docker Hub official images; the digest
        # check below confirms the mirrored image is the one Docker Hub dates.
        if parts[:3] == ["public.ecr.aws", "docker", "library"] and len(parts) == 4:
            parts = ["library", parts[3]]
        if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
            if parts[0] not in DOCKER_HUB_HOSTS:
                raise GateError(f"registry {parts[0]} has no supported publish-date lookup")
            parts = parts[1:]
        if len(parts) == 1:
            parts = ["library", parts[0]]
        if len(parts) != 2:
            raise GateError("unsupported Docker Hub repository name")
        namespace, repository = (urllib.parse.quote(part, safe="") for part in parts)
        tag = urllib.parse.quote(dependency.version, safe="")
        data = _http_json(
            f"https://hub.docker.com/v2/namespaces/{namespace}/repositories/{repository}/tags/{tag}"
        )
        if dependency.ref:
            digests = {data.get("digest")}
            digests.update(image.get("digest") for image in data.get("images", []))
            if dependency.ref not in digests:
                raise GateError(f"digest is not the current digest of tag {dependency.version}")
        # tag_last_pushed moves on every re-push, so a rebuilt tag counts as a new release.
        return _parse_time(data.get("tag_last_pushed"), str(dependency))


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return completed.stdout


def _file_at(repo: Path, revision: str, path: str) -> str | None:
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}:{path}"], cwd=repo, capture_output=True
    )
    if exists.returncode != 0:
        return None
    return _git(repo, "show", f"{revision}:{path}")


def _lockfile_violations(path: str, lockfiles: tuple[str, ...], changed: set[str]) -> list[str]:
    directory = PurePosixPath(path).parent
    # npm workspaces keep one lockfile at the workspace root, above the member manifests.
    candidates = [directory, *directory.parents] if lockfiles == NPM_LOCKFILES else [directory]
    for candidate in candidates:
        if any(str(candidate / lockfile) in changed for lockfile in lockfiles):
            return []
    return [f"{path}: manifest changed without its lockfile ({' or '.join(lockfiles)})"]


def _major_violations(update: Update) -> list[str]:
    dependency = update.dependency
    if update.major is None or any(major is None for major in update.previous_majors):
        return [f"{update.source}: cannot determine the semver major of {dependency}"]
    previous = [major for major in update.previous_majors if major is not None]
    if previous and update.major > max(previous):
        return [
            f"{update.source}: {dependency} is a semver-major update "
            f"(from major {max(previous)} to {update.major})"
        ]
    return []


def evaluate(
    repo: Path,
    base: str,
    head: str,
    publish_dates: PublishDates,
    now: datetime,
    min_age_days: int = DEFAULT_MIN_AGE_DAYS,
) -> GateResult:
    merge_base = _git(repo, "merge-base", base, head).strip()
    files = [
        line
        for line in _git(repo, "diff", "--name-only", "--no-renames", merge_base, head).splitlines()
        if line
    ]
    violations: list[str] = []
    updates: list[Update] = []
    if not files:
        violations.append("the change touches no files")
    changed = set(files)
    for path in files:
        checker = checker_for(path)
        if checker is None:
            violations.append(f"{path}: not a supported dependency file")
            continue
        old = _file_at(repo, merge_base, path)
        new = _file_at(repo, head, path)
        if old is None or new is None:
            violations.append(
                f"{path}: dependency files may only be modified, not added or removed"
            )
            continue
        try:
            file_result = checker(path, old, new)
        except (GateError, ValueError, KeyError, TypeError, AttributeError) as error:
            violations.append(f"{path}: cannot evaluate: {error!r}")
            continue
        violations.extend(file_result.violations)
        updates.extend(file_result.updates)
        if file_result.requires_lockfile_from and file_result.updates:
            violations.extend(
                _lockfile_violations(path, file_result.requires_lockfile_from, changed)
            )

    for update in updates:
        violations.extend(_major_violations(update))

    minimum_age = timedelta(days=min_age_days)
    for dependency in sorted({update.dependency for update in updates}):
        try:
            published = publish_dates.published_at(dependency)
        except GateError as error:
            violations.append(f"{dependency}: cannot determine release age: {error}")
            continue
        age = now - published
        if age < minimum_age:
            violations.append(
                f"{dependency}: published {published.date().isoformat()}, "
                f"{age.days} days ago (minimum {min_age_days})"
            )
    return GateResult(files, updates, violations)


def report(result: GateResult, min_age_days: int) -> str:
    title = "Dependabot gate passed" if result.passed else "Dependabot gate failed"
    lines = [
        f"## {'✅' if result.passed else '❌'} {title}",
        "",
        "Rules: no semver-major updates, exact pins only with the lockfile updated "
        f"alongside its manifest, every changed version released at least {min_age_days} "
        "days ago.",
        "",
    ]
    if result.violations:
        lines.append("### Violations")
        lines.extend(f"- {violation}" for violation in result.violations)
        lines.append("")
    dependencies = sorted({update.dependency for update in result.updates})
    lines.append(f"### Changed dependency versions ({len(dependencies)})")
    lines.extend(f"- `{dependency}`" for dependency in dependencies)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--min-age-days", type=int, default=DEFAULT_MIN_AGE_DAYS)
    parser.add_argument(
        "--pull-request",
        type=int,
        help="also require every commit of this pull request to be a verified Dependabot commit",
    )
    args = parser.parse_args()

    github = GitHubApi(os.environ.get("GITHUB_TOKEN"))
    result = evaluate(
        Path(args.repo_dir),
        args.base,
        args.head,
        RegistryPublishDates(github),
        datetime.now(timezone.utc),
        args.min_age_days,
    )
    if args.pull_request is not None:
        repository = os.environ.get("GITHUB_REPOSITORY")
        if not repository:
            raise RuntimeError("GITHUB_REPOSITORY is required with --pull-request")
        try:
            result.violations.extend(pull_request_violations(github, repository, args.pull_request))
        except GateError as error:
            result.violations.append(f"cannot verify pull request commits: {error}")
    text = report(result, args.min_age_days)
    print(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(text)
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
