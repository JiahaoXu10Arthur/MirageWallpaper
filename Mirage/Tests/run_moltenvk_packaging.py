__copyright__ = "Copyright © 2026 王孝慈. All rights reserved."

import argparse
import copy
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("moltenvk_build", PROJECT / "scripts/build_moltenvk.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)
UUIDS = {"arm64": "12345678-1111-2222-3333-444444444444", "x86_64": "87654321-1111-2222-3333-444444444444"}


def manifest(mode="production"):
    version, checksum = build.PINS[mode]
    entry = {"sha256_unsigned": "a" * 64, "uuids": UUIDS.copy()}
    libraries = {"patched": entry}
    if mode == "diagnostics":
        libraries["baseline"] = copy.deepcopy(entry)
    return dict(schema=2, mode=mode, version=version, source_archive_sha256=checksum,
                patch_id=build.PATCH_ID, patch_sha256=build.PATCH_SHA256, libraries=libraries)


class ProductionMoltenVKTests(unittest.TestCase):
    def test_exact_patch_and_scope(self):
        source = "before\n" + build.ORIGINAL + "\nafter"
        self.assertEqual(build.patch_source(source), "before\n" + build.PATCHED + "\nafter")
        self.assertIn("mvkAreAllFlagsEnabled", build.PATCHED)
        self.assertIn("VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT", build.PATCHED)

    def test_unknown_or_repeated_source_rejected(self):
        for source in ["", build.PATCHED, build.ORIGINAL * 2]:
            with self.assertRaises(ValueError):
                build.patch_source(source)

    def test_production_and_diagnostic_versions_are_separate(self):
        self.assertEqual(build.PINS["production"][0], "v1.4.2")
        self.assertEqual(build.PINS["diagnostics"][0], "v1.4.1")
        build.validate_manifest(manifest(), "production")
        build.validate_manifest(manifest("diagnostics"), "diagnostics")
        with self.assertRaises(ValueError):
            build.validate_manifest(manifest("diagnostics"), "production")

    def test_stale_cache_is_rejected(self):
        for key in ["schema", "mode", "version", "source_archive_sha256", "patch_id", "patch_sha256"]:
            invalid = manifest()
            invalid[key] = "wrong"
            with self.subTest(key=key), self.assertRaises(ValueError):
                build.validate_manifest(invalid, "production")

    def test_incomplete_libraries_are_rejected(self):
        for libraries in [{}, {"baseline": manifest()["libraries"]["patched"]}]:
            invalid = manifest()
            invalid["libraries"] = libraries
            with self.assertRaises(ValueError):
                build.validate_manifest(invalid, "production")

    def test_missing_architecture_is_rejected(self):
        invalid = manifest()
        del invalid["libraries"]["patched"]["uuids"]["arm64"]
        with self.assertRaises(ValueError):
            build.validate_manifest(invalid, "production")

    def test_mutated_binary_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "libMoltenVK.dylib"
            library.write_bytes(b"protected")
            description = manifest()
            description["libraries"]["patched"]["sha256_unsigned"] = build.file_sha256(library)
            (root / "manifest.json").write_text(json.dumps(description))
            with patch.object(build, "library_uuids", return_value=UUIDS):
                build.verify_build(root, "production")
                library.write_bytes(b"unprotected")
                with self.assertRaises(ValueError):
                    build.verify_build(root, "production")

    def test_unsafe_archive_path_is_rejected(self):
        for name in ["root/../escape", "root/a/../../escape"]:
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w:gz") as archive:
                entry = tarfile.TarInfo(name)
                entry.size = 1
                archive.addfile(entry, io.BytesIO(b"x"))
            with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                build.extract_archive(data.getvalue(), Path(temporary))

    def test_escaping_symlink_is_rejected(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            entry = tarfile.TarInfo("root/link")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "../../escape"
            archive.addfile(entry)
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            build.extract_archive(data.getvalue(), Path(temporary))

    def test_all_scene_hosts_and_icds_are_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "Mirage.app"
            payload = root / "payload"
            payload.mkdir()
            (payload / "Licenses").mkdir()
            (payload / "Licenses/LICENSE").write_text("fixture")
            (payload / "libMoltenVK.dylib").write_bytes(b"protected")
            description = manifest()
            description["libraries"]["patched"]["sha256_unsigned"] = build.file_sha256(payload / "libMoltenVK.dylib")
            (payload / "manifest.json").write_text(json.dumps(description))
            (app / "Contents/MacOS").mkdir(parents=True)
            (app / "Contents/MacOS/Mirage").write_bytes(b"executable")
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps(dict(CFBundleExecutable="Mirage")))
            components = ["", "Contents/Resources/Screen Savers/MirageScreenSaver.saver",
                          "Contents/Resources/Screen Savers/MirageDynamicLockScreen.saver",
                          "Contents/Extensions/MirageWallpaperExtension.appex"]
            for component in components:
                library = app / component / "Contents/Frameworks/libMoltenVK.dylib"
                library.parent.mkdir(parents=True)
                library.write_bytes(b"protected")
                icd = app / component / ("Contents/Resources/vulkan/icd.d/MoltenVK_icd.json" if component
                                         else "Contents/Resources/Renderers/vulkan/icd.d/MoltenVK_icd.json")
                icd.parent.mkdir(parents=True)
                relative = "../../../Frameworks/libMoltenVK.dylib" if component else "../../../../Frameworks/libMoltenVK.dylib"
                icd.write_text(json.dumps(dict(ICD=dict(library_path=relative))))
            with patch.object(build, "library_uuids", return_value=UUIDS):
                build.verify_bundle(app, payload)
                build.verify_bundle(app)
                document = json.loads((app / "Contents/Resources/MoltenVK/manifest.json").read_text())
                self.assertEqual(len(document["bundled_libraries"]), 4)
                bad = app / components[-1] / "Contents/Frameworks/libMoltenVK.dylib"
                bad.write_bytes(b"changed after packaging")
                with self.assertRaises(ValueError):
                    build.verify_bundle(app)
                bad.write_bytes(b"protected")
                with patch.object(build, "library_uuids", side_effect=lambda path: {"arm64": "wrong", "x86_64": "wrong"} if path.resolve() == bad.resolve() else UUIDS):
                    with self.assertRaises(ValueError):
                        build.verify_bundle(app)
                icd.write_text(json.dumps(dict(ICD=dict(library_path="/opt/homebrew/lib/libMoltenVK.dylib"))))
                with self.assertRaises(ValueError):
                    build.verify_bundle(app)

    def test_invalid_manifest_checksum_is_rejected(self):
        wrong = manifest()
        wrong["libraries"]["patched"]["sha256_unsigned"] = "not a checksum"
        with self.assertRaises(ValueError):
            build.validate_manifest(wrong, "production")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path)
    args, remaining = parser.parse_known_args()
    result = unittest.main(argv=[sys.argv[0]] + remaining, exit=False)
    if not result.result.wasSuccessful():
        sys.exit(1)
    if args.runtime:
        with tempfile.TemporaryDirectory(prefix="mirage-mvk-probe-") as temporary:
            executable = Path(temporary) / "MoltenVKTextureRegression"
            prefix = subprocess.check_output(["brew", "--prefix", "vulkan-headers"], text=True).strip()
            subprocess.run(["xcrun", "clang++", "-std=c++20", "-framework", "Foundation", "-framework", "Metal",
                            "-I", str(Path(prefix) / "include"), str(PROJECT / "Tests/MoltenVKTextureRegression.mm"),
                            "-o", str(executable)], check=True, timeout=180)
            subprocess.run([str(executable), str(args.runtime.resolve())], check=True, timeout=60)
