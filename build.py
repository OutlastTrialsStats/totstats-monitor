import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION_FILE = ROOT / "src" / "totstats" / "__init__.py"
MANIFEST_FILE = ROOT / ".release-please-manifest.json"


def get_version() -> str:
    match = re.search(r'^__version__ = "([^"]+)"', VERSION_FILE.read_text(encoding="utf-8"), re.M)
    if match is None:
        raise SystemExit(f"no __version__ found in {VERSION_FILE}")
    version = match.group(1)
    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))["."]
    if version != manifest:
        raise SystemExit(f"version drift: {VERSION_FILE.name} says {version}, manifest says {manifest}")
    return version


def write_version_info(version: str) -> None:
    major, minor, patch = version.split(".")
    Path("version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'OutlastTrialsStats'),
         StringStruct(u'FileDescription', u'TOTStatsMonitor'),
         StringStruct(u'FileVersion', u'{version}'),
         StringStruct(u'InternalName', u'TOTStatsMonitor'),
         StringStruct(u'LegalCopyright', u'GPL-3.0'),
         StringStruct(u'OriginalFilename', u'TOTStatsMonitor.exe'),
         StringStruct(u'ProductName', u'TOTStatsMonitor'),
         StringStruct(u'ProductVersion', u'{version}')])]),
    VarFileInfo([VarStruct(u'Translation', [0x0409, 1200])])
  ])""")


def build(version: str) -> None:
    print(f"Building TOTStatsMonitor v{version}...")
    result = subprocess.run([
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconsole",
        "--icon", "assets/icon.ico",
        "--add-data", "assets;assets",
        "--name", "TOTStatsMonitor",
        "--version-file", "version_info.txt",
        "--paths", "src",
        "--hidden-import", "pystray._win32",
        "src/totstats/__main__.py",
    ])
    if result.returncode != 0:
        print(f"\nBuild failed with exit code {result.returncode}")
        raise SystemExit(result.returncode)
    print("\nBuild successful! Output: dist/TOTStatsMonitor.exe")


def main() -> None:
    version = get_version()
    write_version_info(version)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as handle:
            handle.write(f"version={version}\n")
    build(version)


if __name__ == "__main__":
    main()
