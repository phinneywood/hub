"""Fictional dependency archives; no downloads, installs or service access."""
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

import macos_wheelhouse as w


class WheelhouseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pins = self.root / "requirements.lock"
        self.pins.write_text("fictional==1.0\n")
        self.wheels = self.root / "wheels"
        self.wheels.mkdir()
        wheel = self.wheels / "fictional-1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as z:
            z.writestr("fictional-1.0.dist-info/METADATA", "Name: fictional\nVersion: 1.0\n")
            z.writestr("fictional/__init__.py", "# fictional\n")
        self.manifest = {"schema": 1, "target": w.TARGET,
                         "requirements_sha256": w.sha256(self.pins.read_bytes()),
                         "wheels": [w.inspect_wheel(wheel)]}
        self.lock = self.root / "wheelhouse.json"
        self.lock.write_bytes(w.encoded(self.manifest))
        self.archive = self.root / w.ARCHIVE

    def test_deterministic_archive_and_verified_hashes(self):
        w.validate_manifest(self.manifest, self.pins)
        w.build_archive(self.wheels, self.manifest, self.archive)
        before = self.archive.read_bytes()
        w.build_archive(self.wheels, self.manifest, self.archive)
        self.assertEqual(before, self.archive.read_bytes())
        self.assertEqual(w.verify_archive(self.archive, self.lock, self.pins), self.manifest)

    def test_changed_wheel_rejected(self):
        next(self.wheels.iterdir()).write_bytes(b"changed")
        with self.assertRaises((ValueError, zipfile.BadZipFile)):
            w.build_archive(self.wheels, self.manifest, self.archive)

    def test_unexpected_wheel_rejected(self):
        (self.wheels / "extra.whl").write_bytes(b"extra")
        with self.assertRaises(ValueError):
            w.build_archive(self.wheels, self.manifest, self.archive)

    def test_changed_source_lock_rejected(self):
        self.pins.write_text("fictional==2.0\n")
        with self.assertRaises(ValueError):
            w.validate_manifest(self.manifest, self.pins)

    def test_changed_target_and_duplicate_package_rejected(self):
        with self.assertRaises(ValueError):
            w.validate_manifest({**self.manifest, "target": {**w.TARGET, "platform": "other"}}, self.pins)
        with self.assertRaises(ValueError):
            w.validate_manifest({**self.manifest, "wheels": self.manifest["wheels"] * 2}, self.pins)

    def test_requirements_forbid_urls_options_ranges_and_duplicates(self):
        for line in ("fictional>=1.0", "--extra-index-url https://example.invalid", "fictional @ https://example.invalid/a.whl", "fictional==1.0\nfictional==1.0"):
            self.pins.write_text(line)
            with self.subTest(line=line), self.assertRaises(ValueError):
                w.requirements(self.pins)

    def test_archive_tamper_extra_member_and_links_rejected(self):
        for kind in ("tamper", "extra", "link"):
            w.build_archive(self.wheels, self.manifest, self.archive)
            with tarfile.open(self.archive) as original:
                bodies = {m.name: original.extractfile(m).read() for m in original.getmembers()}
            if kind == "tamper":
                bodies[w.HASH_LOCK] += b"injected\n"
            if kind == "extra":
                bodies["extra"] = b"bad"
            with tarfile.open(self.archive, "w") as tar:
                for name, body in bodies.items():
                    info = tarfile.TarInfo(name)
                    if kind == "link" and name == w.MANIFEST:
                        info.type, info.linkname = tarfile.SYMTYPE, "/outside"
                        tar.addfile(info)
                    else:
                        info.size = len(body)
                        tar.addfile(info, io.BytesIO(body))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                w.verify_archive(self.archive, self.lock, self.pins)

    def test_hash_lock_contains_only_exact_reviewed_pins(self):
        expected = f"fictional==1.0 --hash=sha256:{self.manifest['wheels'][0]['sha256']}\n"
        self.assertEqual(w.hash_lock(self.manifest).decode(), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
