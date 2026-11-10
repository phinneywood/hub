"""Prepare a reviewed CPython 3.12/macOS ARM64 wheelhouse; never installs it.

Proposal generation downloads exact-version public wheels from the standard PyPI
index. Subsequent builds require the reviewed committed manifest and each hash.
The deterministic archive contains wheels, the manifest and hash-locked requirements.
"""
import argparse
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import zipfile

TARGET = {"implementation": "cp", "python": "3.12", "abi": "cp312", "platform": "macosx_11_0_arm64"}
ARCHIVE = "macos-arm64-wheelhouse.tar"
MANIFEST = "wheelhouse.json"
HASH_LOCK = "requirements-hashed.lock"
LIMIT = 64 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def requirements(path):
    found = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9][A-Za-z0-9_.!+-]*", line),
                "Requirements must contain only exact name==version pins")
        name, version = line.split("==")
        name = canonical(name)
        require(name not in found, "Duplicate requirement")
        found[name] = version
    require(0 < len(found) <= 100, "Unexpected requirement count")
    return found


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def hash_lock(manifest):
    return "".join(f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}\n"
                   for w in sorted(manifest["wheels"], key=lambda w: w["name"])).encode()


def validate_manifest(manifest, pins):
    require(set(manifest) == {"schema", "target", "requirements_sha256", "wheels"}, "Unknown manifest fields")
    require(manifest["schema"] == 1 and manifest["target"] == TARGET, "Unsupported wheelhouse target")
    require(manifest["requirements_sha256"] == sha256(pins.read_bytes()), "Wheelhouse source lock mismatch")
    expected = requirements(pins)
    actual, filenames = {}, set()
    for wheel in manifest["wheels"]:
        require(set(wheel) == {"name", "version", "filename", "sha256"}, "Unknown wheel fields")
        name, filename = wheel["name"], wheel["filename"]
        require(name == canonical(name) and name not in actual, "Duplicate or noncanonical package")
        require(re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", filename) and filename not in filenames,
                "Unsafe or duplicate wheel filename")
        require(re.fullmatch(r"[0-9a-f]{64}", wheel["sha256"]), "Invalid wheel hash")
        actual[name] = wheel["version"]
        filenames.add(filename)
    require(actual == expected, "Wheelhouse does not exactly cover the source requirements")
    return manifest


def inspect_wheel(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= LIMIT, "Invalid wheel")
    with zipfile.ZipFile(path) as z:
        members = z.infolist()
        require(sum(m.file_size for m in members) <= LIMIT, "Expanded wheel too large")
        names = [m.filename for m in members]
        require(len(names) == len(set(names)), "Duplicate wheel entry")
        metadata = [n for n in names if re.fullmatch(r"[^/]+\.dist-info/METADATA", n)]
        require(len(metadata) == 1, "Expected one wheel metadata record")
        info = BytesParser().parsebytes(z.read(metadata[0]))
        require(info.get("Name") and info.get("Version"), "Wheel metadata lacks identity")
        return {"name": canonical(info["Name"]), "version": info["Version"],
                "filename": path.name, "sha256": sha256(path.read_bytes())}


def build_archive(wheels, manifest, output):
    expected = {w["filename"]: w for w in manifest["wheels"]}
    require({p.name for p in wheels.iterdir()} == set(expected), "Wheel directory is not closed")
    data = {MANIFEST: encoded(manifest), HASH_LOCK: hash_lock(manifest)}
    for name, record in expected.items():
        path = wheels / name
        require(inspect_wheel(path) == record, "Wheel metadata or digest differs from reviewed manifest")
        data[name] = path.read_bytes()
    require(sum(map(len, data.values())) <= LIMIT, "Wheelhouse too large")
    with tarfile.open(output, "w", format=tarfile.USTAR_FORMAT) as tar:
        for name, body in sorted(data.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(body), 0o644, 0
            entry.uid = entry.gid = 0
            entry.uname = entry.gname = ""
            tar.addfile(entry, io.BytesIO(body))


def verify_archive(archive, lock, pins):
    manifest = validate_manifest(json.loads(lock.read_text()), pins)
    require(archive.is_file() and not archive.is_symlink() and archive.stat().st_size <= LIMIT, "Invalid wheelhouse archive")
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        expected = {w["filename"]: w["sha256"] for w in manifest["wheels"]}
        expected.update({MANIFEST: sha256(encoded(manifest)), HASH_LOCK: sha256(hash_lock(manifest))})
        require(len(members) == len(expected) and {m.name for m in members} == set(expected), "Wheelhouse archive is not closed")
        require(sum(m.size for m in members) <= LIMIT, "Expanded wheelhouse too large")
        for member in members:
            require(member.isfile() and "/" not in member.name and "\\" not in member.name,
                    "Wheelhouse links, directories and paths are forbidden")
            require(sha256(tar.extractfile(member).read()) == expected[member.name], "Wheelhouse archive hash mismatch")
    return manifest


def download(pins, output, lock=None):
    output.mkdir(parents=True, exist_ok=False)
    wheels = output / "wheels"
    wheels.mkdir()
    (output / "home").mkdir()
    env = {"HOME": str(output / "home"), "PATH": os.defpath, "PIP_CONFIG_FILE": os.devnull,
           "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8", "TMPDIR": str(output)}
    manifest = validate_manifest(json.loads(lock.read_text()), pins) if lock else None
    requested = output / HASH_LOCK if manifest else pins
    if manifest:
        requested.write_bytes(hash_lock(manifest))
    command = [sys.executable, "-I", "-m", "pip", "--isolated", "download", "--disable-pip-version-check",
               "--no-cache-dir", "--index-url", "https://pypi.org/simple", "--no-deps", "--only-binary=:all:",
               "--implementation", TARGET["implementation"], "--python-version", TARGET["python"],
               "--abi", TARGET["abi"], "--platform", TARGET["platform"], "--dest", str(wheels), "-r", str(requested)]
    if manifest:
        command.append("--require-hashes")
    else:
        requirements(pins)
    subprocess.run(command, check=True, env=env, timeout=600)
    observed = {"schema": 1, "target": TARGET, "requirements_sha256": sha256(pins.read_bytes()),
                "wheels": sorted((inspect_wheel(p) for p in wheels.iterdir()), key=lambda w: w["name"])}
    validate_manifest(observed, pins)
    require(manifest is None or observed == manifest, "Downloaded wheels differ from reviewed manifest")
    (output / MANIFEST).write_bytes(encoded(observed))
    (output / HASH_LOCK).write_bytes(hash_lock(observed))
    build_archive(wheels, observed, output / ARCHIVE)
    verify_archive(output / ARCHIVE, output / MANIFEST, pins)
    print(f"Verified {len(observed['wheels'])} exact wheels for {TARGET['platform']}; no installation occurred.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["propose", "build", "verify"])
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    if args.command == "verify":
        require(args.lock and args.archive, "verify requires --lock and --archive")
        manifest = verify_archive(args.archive, args.lock, args.requirements)
        print(json.dumps({"target": manifest["target"], "wheels": len(manifest["wheels"]),
                          "archive_sha256": sha256(args.archive.read_bytes())}))
    else:
        require(args.output_dir, "An output directory is required")
        require(args.command == "propose" or args.lock, "build requires a reviewed --lock manifest")
        download(args.requirements.resolve(), args.output_dir.resolve(), args.lock.resolve() if args.lock else None)


if __name__ == "__main__":
    main()
