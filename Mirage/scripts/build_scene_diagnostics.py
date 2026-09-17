__copyright__ = "Copyright © 2026 王孝慈. All rights reserved."

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

VERSION = "v1.4.1"
SOURCE_SHA256 = "9985f141902a17de818e264d17c1ce334b748e499ee02fcb4703e4dc0038f89c"
SOURCE = f"https://codeload.github.com/KhronosGroup/MoltenVK/tar.gz/refs/tags/{VERSION}"
ORIGINAL = "mtlTexDesc.allowGPUOptimizedContents = !_image->_is2DViewOn3DImageCompatible && !_image->_isBlockTexelViewCompatible;"
PATCHED = "mtlTexDesc.allowGPUOptimizedContents = !_image->_is2DViewOn3DImageCompatible && !_image->_isBlockTexelViewCompatible && !mvkAreAllFlagsEnabled(_image->_usage, VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT);"


def fetch_archive(url, destination):
    data = subprocess.run(["/usr/bin/curl", "--fail", "--location", "--retry", "3",
                           "--max-time", "180", "--silent", "--show-error", url],
                          check=True, stdout=subprocess.PIPE).stdout
    if len(data) > 128 * 1024 * 1024:
        raise ValueError("Archive exceeds size limit")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        total = 0
        links = []
        for member in members:
            parts = Path(member.name).parts[1:]
            if not parts:
                continue
            if member.islnk() or ".." in parts or Path(*parts).is_absolute():
                raise ValueError("Unsafe archive entry")
            target = destination.joinpath(*parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("Archive path escapes its destination")
            if member.issym():
                if Path(member.linkname).is_absolute() or not (target.parent / member.linkname).resolve().is_relative_to(destination.resolve()):
                    raise ValueError("Unsafe archive symlink")
                links.append((target, member.linkname))
            elif member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                total += member.size
                if total > 1024 * 1024 * 1024:
                    raise ValueError("Expanded archive exceeds size limit")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o755)
            else:
                raise ValueError("Unsupported archive entry")
        for target, link in links:
            if not (target.parent / link).resolve().is_relative_to(destination.resolve()):
                raise ValueError("Archive symlink chain escapes its destination")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(link)
    return hashlib.sha256(data).hexdigest()


def patch_source(text):
    if text.count(ORIGINAL) != 1:
        raise ValueError("MoltenVK source does not match the expected unpatched version")
    return text.replace(ORIGINAL, PATCHED)


def run(command, cwd):
    print("Running:", " ".join(map(str, command)), flush=True)
    subprocess.run(command, cwd=cwd, check=True, timeout=5400)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Output already exists; choose a new directory")
    work = args.work.resolve() if args.work else Path(tempfile.mkdtemp(prefix="mirage-mvk-diagnostics-"))
    source = work / "MoltenVK"
    if source.exists():
        raise ValueError("Source directory already exists")
    print(f"Build directory: {work}", flush=True)
    source.mkdir(parents=True)
    source_hash = fetch_archive(SOURCE, source)
    if source_hash != SOURCE_SHA256:
        raise ValueError("MoltenVK source archive checksum mismatch")
    revisions = {}
    for name, repository, destination in [
        ("cereal", "USCiLab/cereal", "cereal"),
        ("Vulkan-Headers", "KhronosGroup/Vulkan-Headers", "Vulkan-Headers"),
        ("SPIRV-Cross", "KhronosGroup/SPIRV-Cross", "SPIRV-Cross"),
        ("SPIRV-Tools", "KhronosGroup/SPIRV-Tools", "SPIRV-Tools"),
        ("SPIRV-Headers", "KhronosGroup/SPIRV-Headers", "SPIRV-Tools/external/spirv-headers"),
        ("Vulkan-Tools", "KhronosGroup/Vulkan-Tools", "Vulkan-Tools"),
        ("Volk", "zeux/volk", "Volk"),
    ]:
        revision = (source / "ExternalRevisions" / f"{name}_repo_revision").read_text().strip()
        if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
            raise ValueError(f"Invalid revision for {name}")
        destination = source / "External" / destination
        destination.mkdir(parents=True, exist_ok=True)
        digest = fetch_archive(f"https://codeload.github.com/{repository}/tar.gz/{revision}", destination)
        revisions[name] = {"revision": revision, "archive_sha256": digest}
    with zipfile.ZipFile(source / "Templates/spirv-tools/build.zip") as archive:
        for name in archive.namelist():
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("Unsafe pre-generated header path")
        archive.extractall(source / "External/SPIRV-Tools")
    env = os.environ.copy()
    env.update(SKIP_PACKAGING="Y")
    subprocess.run([
        "xcodebuild", "build", "-project", "ExternalDependencies.xcodeproj",
        "-scheme", "ExternalDependencies-macOS", "-configuration", "Release",
        "-destination", "generic/platform=macOS", "-derivedDataPath", "External/build/Intermediates/macOS",
        "CODE_SIGNING_ALLOWED=NO", "-quiet",
    ], cwd=source, env=env, check=True, timeout=5400)
    run(["bash", "-c", 'export PROJECT_DIR=. CONFIGURATION=Release; source Scripts/create_ext_lib_xcframeworks.sh; source Scripts/package_ext_libs_finish.sh'], source)
    image = source / "MoltenVK/MoltenVK/GPUObjects/MVKImage.mm"
    original = image.read_text()
    patched = patch_source(original)
    header = (source / "MoltenVK/MoltenVK/GPUObjects/MVKImage.h").read_text()
    if "_usage" not in header:
        raise ValueError("Image usage member is unavailable")
    output.mkdir(parents=True)
    manifest = {"schema": 1, "version": VERSION, "source": SOURCE,
                "source_archive_sha256": source_hash, "dependencies": revisions,
                "upstream_fix": "https://github.com/KhronosGroup/MoltenVK/pull/2724", "libraries": {}}
    for variant, text in [("baseline", original), ("patched", patched)]:
        image.write_text(text)
        run(["xcodebuild", "build", "-project", "MoltenVKPackaging.xcodeproj",
             "-scheme", "MoltenVK Package (macOS only)", "-configuration", "Release",
             "-destination", "generic/platform=macOS", "CODE_SIGNING_ALLOWED=NO", "-quiet"], source)
        built = source / "Package/Latest/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib"
        target = output / variant / "libMoltenVK.dylib"
        target.parent.mkdir()
        shutil.copy2(built, target)
        run(["install_name_tool", "-id", "@rpath/libMoltenVK.dylib", str(target)], source)
        manifest["libraries"][variant] = {"sha256_unsigned": hashlib.sha256(target.read_bytes()).hexdigest(),
                                          "image_source_sha256": hashlib.sha256(text.encode()).hexdigest()}
    license_dir = output / "Licenses"
    license_dir.mkdir()
    for name in ("LICENSE", "NOTICE"):
        if (source / name).is_file():
            shutil.copy2(source / name, license_dir / name)
    for path in (source / "External").glob("*/LICENSE*"):
        if path.is_file():
            shutil.copy2(path, license_dir / f"{path.parent.name}-{path.name}")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Diagnostic libraries: {output}", flush=True)


if __name__ == "__main__":
    main()
