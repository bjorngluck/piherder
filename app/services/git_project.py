"""Attach an existing compose project to a git remote and update the checkout.

The operator picks a branch or a tag. A tracked local edit or a local commit
stops the update until they keep the local files or take the remote copies.
Untracked files are left in place. This does not deploy the stack.
"""
from __future__ import annotations

import re
import shlex
from typing import Any

from ..models import Server
from .service_templates.host_sync import project_path_for
from .ssh import get_ssh_client, run_command

_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_URL = re.compile(r"^(https://|ssh://|git@)[^\s'\"]+$")
_PROJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_USERINFO = re.compile(r"(https://|ssh://)([^/\s@]+)@")


class GitProjectError(ValueError):
    pass


def validate_project(name: str) -> str:
    text = (name or "").strip()
    if not _PROJECT.match(text) or ".." in text:
        raise GitProjectError("Project name is not valid")
    return text


def _authority(url: str) -> str:
    if url.startswith("https://"):
        rest = url[len("https://"):]
    elif url.startswith("ssh://"):
        rest = url[len("ssh://"):]
    else:
        return ""
    return rest.split("/", 1)[0]


def redact_git_url(text: str) -> str:
    """Hide a token embedded in an https or ssh URL. A plain git@ user stays."""

    def repl(match: re.Match[str]) -> str:
        scheme, userinfo = match.group(1), match.group(2)
        if ":" in userinfo:
            return scheme
        return match.group(0)

    return _USERINFO.sub(repl, text or "")


def validate_url(url: str) -> str:
    text = (url or "").strip()
    if not _URL.match(text):
        raise GitProjectError("Use an https, ssh, or git@ URL")
    if text.startswith("https://") and "@" in _authority(text):
        raise GitProjectError(
            "Do not put a user or token in the https URL. "
            "Use a deploy key or a credential helper."
        )
    return text


def validate_ref(name: str) -> str:
    text = (name or "").strip()
    if not _REF.match(text) or ".." in text or text.endswith(".lock"):
        raise GitProjectError("Branch or tag is not valid")
    return text


def parse_git_report(text: str) -> dict[str, Any]:
    """Turn the host script output into a page model."""
    report: dict[str, Any] = {
        "repo": False,
        "url": "",
        "branch": "",
        "head": "",
        "subject": "",
        "default_branch": "",
        "behind": 0,
        "ahead": 0,
        "dirty": [],
        "branches": [],
        "tags": [],
        "fetch_ok": True,
        "result": "",
        "detail": "",
    }
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "repo":
            report["repo"] = value == "1"
        elif key == "url":
            report["url"] = redact_git_url(value)
        elif key == "branch":
            report["branch"] = value
        elif key == "head":
            report["head"] = value
        elif key == "subject":
            report["subject"] = value[:200]
        elif key == "default":
            report["default_branch"] = value
        elif key == "behind":
            report["behind"] = int(value or 0)
        elif key == "ahead":
            report["ahead"] = int(value or 0)
        elif key == "dirty" and value:
            report["dirty"].append(value)
        elif key == "refbranch" and value and value != "HEAD":
            report["branches"].append(value)
        elif key == "tag" and value:
            name, _, flag = value.partition(" ")
            report["tags"].append({"name": name, "newer": flag == "newer"})
        elif key == "fetch":
            report["fetch_ok"] = value == "ok"
        elif key == "result":
            report["result"] = value
        elif key == "detail":
            report["detail"] = redact_git_url(value)[:500]
    return report


def _run(server: Server, script: str, timeout: int = 180) -> str:
    client = get_ssh_client(server)
    try:
        status, out, err = run_command(client, script, timeout=timeout)
    finally:
        client.close()
    if status not in (0,):
        tail = redact_git_url((err or out or "git failed").strip())
        raise GitProjectError(tail[-300:] or "git failed")
    return out


def _enter(path: str) -> str:
    """cd into the project and read that one checkout.

    The ownership override is this folder only. Repo hooks, fsmonitor, and
    a repo sshCommand are not used.
    """
    quoted = shlex.quote(path)
    return f"""
cd {quoted} || {{ echo result=missing; exit 0; }}
git() {{ command git -c safe.directory={quoted} -c core.fsmonitor= -c core.hooksPath=/dev/null -c core.sshCommand=ssh "$@"; }}
"""


def _inspect_script(path: str) -> str:
    return _enter(path) + """
if [ ! -d .git ]; then
  echo repo=0
  exit 0
fi
echo repo=1
url=$(git remote get-url origin 2>/dev/null || true)
printf 'url=%s\\n' "$url"
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)
printf 'branch=%s\\n' "$branch"
printf 'head=%s\\n' "$(git rev-parse --short HEAD 2>/dev/null || true)"
printf 'subject=%s\\n' "$(git log -1 --format=%s 2>/dev/null || true)"
git status --porcelain | while IFS= read -r line; do
  code=$(printf '%s' "$line" | cut -c1-2)
  file=$(printf '%s' "$line" | cut -c4-)
  case "$code" in
    '??') ;;
    *) printf 'dirty=%s\\n' "$file" ;;
  esac
done
if [ -n "$url" ]; then
  if git fetch --prune origin >/dev/null 2>&1; then
    echo fetch=ok
  else
    echo fetch=fail
  fi
fi
default=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || true)
default=${default#origin/}
printf 'default=%s\\n' "$default"
git for-each-ref --format='refbranch=%(refname:strip=3)' refs/remotes/origin
headfull=$(git rev-parse HEAD)
git for-each-ref --format='%(refname:strip=2)' refs/tags | while IFS= read -r name; do
  [ -n "$name" ] || continue
  sha=$(git rev-parse "$name^{commit}" 2>/dev/null || true)
  [ -n "$sha" ] || continue
  if git merge-base --is-ancestor "$headfull" "$sha" && [ "$sha" != "$headfull" ]; then
    printf 'tag=%s newer\\n' "$name"
  else
    printf 'tag=%s\\n' "$name"
  fi
done
if [ "$branch" != HEAD ] && git rev-parse --verify --quiet "origin/$branch" >/dev/null; then
  printf 'behind=%s\\n' "$(git rev-list --count HEAD.."origin/$branch")"
  printf 'ahead=%s\\n' "$(git rev-list --count "origin/$branch"..HEAD)"
fi
"""


def inspect_project(server: Server, project: str) -> dict[str, Any]:
    name = validate_project(project)
    path = project_path_for(server, name)
    report = parse_git_report(_run(server, _inspect_script(path)))
    report["project"] = name
    report["path"] = path
    if not report["default_branch"]:
        for candidate in ("main", "master"):
            if candidate in report["branches"]:
                report["default_branch"] = candidate
                break
    return report


def _attach_script(path: str, url: str) -> str:
    return f"""
mkdir -p {shlex.quote(path)}
""" + _enter(path) + f"""
if [ -d .git ]; then
  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin {shlex.quote(url)}
  else
    git remote add origin {shlex.quote(url)}
  fi
else
  git init
  git remote add origin {shlex.quote(url)}
fi
if git fetch --prune origin; then
  echo result=attached
else
  echo result=fetch-fail
fi
"""


def attach_remote(server: Server, project: str, url: str) -> dict[str, Any]:
    name = validate_project(project)
    remote = validate_url(url)
    path = project_path_for(server, name)
    report = parse_git_report(_run(server, _attach_script(path, remote)))
    report["project"] = name
    report["path"] = path
    return report


def _update_script(path: str, kind: str, name: str, choice: str) -> str:
    return _enter(path) + f"""
if [ ! -d .git ]; then
  echo result=missing
  exit 0
fi
if ! git fetch --prune origin; then
  echo result=fetch-fail
  exit 0
fi
kind={shlex.quote(kind)}
name={shlex.quote(name)}
choice={shlex.quote(choice)}
if [ "$kind" = tag ]; then
  target=$(git rev-parse "$name^{{commit}}")
else
  target=$(git rev-parse "origin/$name")
fi
dirty=$(git status --porcelain | awk 'substr($0,1,2) != "??" {{ print }}')
ahead=$(git rev-list --count "$target"..HEAD)
if [ -n "$dirty" ] || [ "$ahead" != 0 ]; then
  if [ "$choice" != keep ] && [ "$choice" != take ]; then
    echo result=conflict
    printf '%s\\n' "$dirty" | while IFS= read -r line; do
      [ -n "$line" ] || continue
      printf 'dirty=%s\\n' "$(printf '%s' "$line" | cut -c4-)"
    done
    printf 'ahead=%s\\n' "$ahead"
    exit 0
  fi
fi
set -e
if [ "$choice" = take ]; then
  if [ "$kind" = tag ]; then
    git checkout --detach "$target"
    git reset --hard "$target"
  else
    git checkout -B "$name" "$target"
    git reset --hard "$target"
  fi
  echo result=updated
elif [ "$ahead" != 0 ] && [ "$choice" = keep ]; then
  echo result=local-commits
  printf 'ahead=%s\\n' "$ahead"
else
  stashed=0
  if [ -n "$dirty" ]; then
    git stash push -m piherder-keep >/dev/null
    stashed=1
  fi
  if [ "$kind" = tag ]; then
    git checkout --detach "$target"
  elif git show-ref --verify --quiet "refs/heads/$name"; then
    git checkout "$name"
    git merge --ff-only "$target"
  else
    git checkout -b "$name" "$target"
  fi
  if [ "$stashed" = 1 ]; then
    if git stash pop; then
      echo result=updated
    else
      echo result=stash-conflict
    fi
  else
    echo result=updated
  fi
fi
printf 'head=%s\\n' "$(git rev-parse --short HEAD)"
"""


def update_project(
    server: Server,
    project: str,
    *,
    branch: str = "",
    tag: str = "",
    choice: str = "",
) -> dict[str, Any]:
    name = validate_project(project)
    picked_tag = (tag or "").strip()
    picked_branch = (branch or "").strip()
    if picked_tag:
        kind, ref = "tag", validate_ref(picked_tag)
    elif picked_branch:
        kind, ref = "branch", validate_ref(picked_branch)
    else:
        raise GitProjectError("Pick a branch or a tag")
    if choice not in ("", "keep", "take"):
        raise GitProjectError("Choose keep or take")
    path = project_path_for(server, name)
    report = parse_git_report(_run(server, _update_script(path, kind, ref, choice or "stop")))
    report["project"] = name
    report["path"] = path
    report["chosen"] = ref
    report["kind"] = kind
    return report
