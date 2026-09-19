__copyright__ = "Copyright © 2026 王孝慈. All rights reserved."

import hashlib
import argparse
import uuid
import importlib.util
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
import sys
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
TRIM = ROOT / 'Mirage/scripts/trim_assets.sh'
APP_PATH = None
SPEC = importlib.util.spec_from_file_location('runtime_manifest', ROOT / 'Mirage/scripts/scene_runtime_manifest.py')
RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='mirage-packaging-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.app = self.root / 'Fixture.app'
        self.assets = self.app / 'Contents/Resources/assets'
        self.assets.mkdir(parents=True)
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='cn.laobamac.Mirage')))

    def trim(self, path=None):
        return subprocess.run(['bash', str(TRIM), str(path or self.assets)], capture_output=True, text=True, timeout=30)

    def put(self, path, data=b'payload'):
        target = self.assets / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def test_explicit_editor_previews_only(self):
        self.put('effects/test/effect.json', json.dumps(dict(preview='preview/project.json')).encode())
        self.put('effects/test/preview/project.json', b'{}')
        self.put('effects/test/preview/materials/texture.tex')
        self.put('materials/preview_runtime/texture.tex')
        self.put('materials/editor/needed.tex')
        self.put('materials/duplicate.tga')
        self.put('materials/duplicate.tex')
        self.put('materials/unique.tga')
        self.put('.DS_Store')
        before = self.put('materials/runtime.tex').read_bytes()
        result = self.trim()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.assets / 'effects/test/preview').exists())
        self.assertFalse((self.assets / 'materials/duplicate.tga').exists())
        for path in ['materials/editor/needed.tex', 'materials/preview_runtime/texture.tex', 'materials/unique.tga', 'materials/duplicate.tex']:
            self.assertTrue((self.assets / path).exists(), path)
        self.assertEqual((self.assets / 'materials/runtime.tex').read_bytes(), before)
        self.assertEqual(self.trim().returncode, 0)

    def test_non_preview_reference_preserves_candidate(self):
        self.put('effects/test/effect.json', b'{"preview":"preview/project.json","material":"preview/runtime.json"}')
        self.put('effects/test/preview/project.json', b'{}')
        self.put('effects/test/preview/runtime.json', b'{}')
        self.assertEqual(self.trim().returncode, 0)
        self.assertTrue((self.assets / 'effects/test/preview/runtime.json').is_file())

    def test_reject_source_or_arbitrary_target(self):
        self.assertNotEqual(self.trim(ROOT / 'assets').returncode, 0)
        self.assertNotEqual(self.trim(self.root).returncode, 0)

    def test_reject_symlink_payload_without_writing(self):
        target = self.root / 'protected.tga'
        target.write_bytes(b'protected')
        (self.assets / 'escape.tga').symlink_to(target)
        self.assertNotEqual(self.trim().returncode, 0)
        self.assertEqual(target.read_bytes(), b'protected')

    def test_shared_runtime_fingerprint(self):
        shared = self.app / 'Contents/Extensions/MirageWallpaperExtension.appex/Contents'
        frameworks = shared / 'Frameworks'
        frameworks.mkdir(parents=True)
        (shared / 'Resources/assets').mkdir(parents=True)
        (shared / 'Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='cn.laobamac.Mirage.Extension')))
        shared_icd = shared / 'Resources/vulkan/icd.d/MoltenVK_icd.json'
        shared_icd.parent.mkdir(parents=True)
        shared_icd.write_text('{}')
        texture = shared / 'Resources/assets/test.tex'
        texture.write_bytes(b'old')
        for name in ['libMirageSceneSaver.dylib', 'libMoltenVK.dylib']:
            (frameworks / name).write_bytes(name.encode())
        self.put('materials/texture.tex')
        icd = self.app / 'Contents/Resources/Renderers/vulkan/icd.d/MoltenVK_icd.json'
        icd.parent.mkdir(parents=True)
        icd.write_text('{}')
        RUNTIME.record(self.app)
        RUNTIME.record(self.app, True)
        texture.write_bytes(b'updated')
        with self.assertRaises(ValueError): RUNTIME.record(self.app, True)
        RUNTIME.record(self.app)
        (frameworks / 'libMirageSceneSaver.dylib').unlink()
        with self.assertRaises(ValueError): RUNTIME.record(self.app, True)

    def test_sandboxed_shared_runtime(self):
        if APP_PATH is None:
            self.skipTest('Pass --app to test the signed App Sandbox runtime')
        host = self.root / 'SandboxHost.app'
        extension = host / 'Contents/Extensions/RuntimeProbe.appex'
        executable = extension / 'Contents/MacOS/RuntimeProbe'
        executable.parent.mkdir(parents=True)
        source = APP_PATH.resolve() / 'Contents/Extensions/MirageWallpaperExtension.appex/Contents'
        for name in ['Frameworks', 'Resources']:
            shutil.copytree(source / name, extension / 'Contents' / name, symlinks=True)
        identifier = 'cn.laobamac.Mirage.RuntimeProbe.' + uuid.uuid4().hex
        (extension / 'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier=identifier, CFBundleExecutable='RuntimeProbe', CFBundlePackageType='XPC!', CFBundleVersion='1')))
        (host / 'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier=identifier + '.Host', CFBundlePackageType='APPL', CFBundleVersion='1')))
        entitlements = self.root / 'entitlements.plist'
        entitlements.write_bytes(plistlib.dumps({'com.apple.security.app-sandbox': True}))
        subprocess.run(['xcrun', 'swiftc', ROOT / 'Mirage/Tests/SharedRuntimeSandboxProbe.swift', '-parse-as-library', '-o', executable], check=True, timeout=90)
        subprocess.run(['codesign', '--force', '--sign', '-', '--entitlements', entitlements, extension], check=True, timeout=30)
        subprocess.run([executable], check=True, timeout=30)

    def test_component_lookup(self):
        binary = self.root / 'lookup'
        subprocess.run(['xcrun', 'swiftc', ROOT / 'Mirage/Mirage Screen Saver/MirageHostApplication.swift',
                        ROOT / 'Mirage/Tests/SharedRuntimeRegression.swift', '-o', binary], check=True, timeout=90)
        subprocess.run([binary, self.root / 'lookup-fixtures'], check=True, timeout=20)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path)
    args, rest = parser.parse_known_args()
    APP_PATH = args.app
    unittest.main(argv=[sys.argv[0], *rest])
