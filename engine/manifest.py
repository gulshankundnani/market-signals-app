"""A build manifest: one build id shared by the files of a publish, with the size and SHA-256 of each, so a partial or mixed publish can be told apart from a complete one."""
import hashlib
import json
import uuid
from pathlib import Path

NAME = "manifest.json"
REQUIRED = ("data.json", "index.json", "state.json")


def new_build_id(now_ts):
    return f"{int(now_ts)}-{uuid.uuid4().hex[:8]}"


def _files(out):
    out = Path(out)
    return sorted(p for p in out.rglob("*.json") if p.name != NAME)


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(out, build_id, generated_at):
    """Hash every JSON file in the folder and write manifest.json last. Returns the manifest."""
    out = Path(out)
    files = {p.relative_to(out).as_posix(): {"bytes": p.stat().st_size, "sha256": _digest(p)} for p in _files(out)}
    manifest = {"v": 1, "build_id": build_id, "generated_at": generated_at, "files": files}
    (out / NAME).write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return manifest


def verify(out):
    """A list of problems (empty when the folder is a complete, untouched build): missing manifest, missing or changed files, files the manifest does not list,
    invalid JSON, and a data.json that belongs to another build."""
    out = Path(out)
    try:
        manifest = json.loads((out / NAME).read_text(encoding="utf-8"))
        listed = manifest["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return ["manifest.json is missing or unreadable"]
    problems = [f"{name} is required but not in the manifest" for name in REQUIRED if name not in listed]
    for name, info in listed.items():
        path = out / name
        if not path.is_file():
            problems.append(f"{name} is listed but missing")
            continue
        if _digest(path) != info.get("sha256"):
            problems.append(f"{name} does not match its recorded hash")
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            problems.append(f"{name} is not valid JSON")
    problems += [f"{p.relative_to(out).as_posix()} is not in the manifest" for p in _files(out) if p.relative_to(out).as_posix() not in listed]
    try:
        if json.loads((out / "data.json").read_text(encoding="utf-8")).get("build_id") != manifest.get("build_id"):
            problems.append("data.json belongs to a different build than the manifest")
    except (OSError, ValueError):
        pass
    return problems
