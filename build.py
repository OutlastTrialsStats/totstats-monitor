import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION_FILE = ROOT / "src" / "totstats" / "__init__.py"
MANIFEST_FILE = ROOT / ".release-please-manifest.json"
WINDOWS = sys.platform == "win32"
INSTALLER_SCRIPT = ROOT / "installer" / "TOTStatsMonitor.iss"
ISCC_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe",
)


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


def pyinstaller_args() -> list[str]:
    common = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--noupx",
        "--add-data", f"assets{os.pathsep}assets",
        "--paths", "src",
    ]
    if WINDOWS:
        # A folder, not --onefile: a onefile exe unpacks itself into %TEMP% on every start,
        # which is exactly the behaviour antivirus heuristics treat as a dropper.
        return [
            *common,
            "--onedir",
            "--noconsole",
            "--icon", "assets/icon.ico",
            "--name", "TOTStatsMonitor",
            "--version-file", "version_info.txt",
            "--hidden-import", "pystray._win32",
            "src/totstats/__main__.py",
        ]
    return [
        *common,
        "--onefile",
        "--name", "TOTStatsMonitor-linux-x86_64",
        "--hidden-import", "pystray._xorg",
        "src/totstats/__main__.py",
    ]


def find_iscc() -> str | None:
    found = shutil.which("iscc")
    if found:
        return found
    for candidate in ISCC_CANDIDATES:
        path = os.path.expandvars(candidate)
        if os.path.isfile(path):
            return path
    return None


def build_installer(version: str) -> None:
    iscc = find_iscc()
    if iscc is None:
        if os.environ.get("CI"):
            raise SystemExit("Inno Setup (ISCC.exe) not found")
        print("\nInno Setup not found, skipping the installer. The program folder is dist/TOTStatsMonitor.")
        return
    result = subprocess.run([iscc, f"/DAppVersion={version}", str(INSTALLER_SCRIPT)])
    if result.returncode != 0:
        print(f"\nInstaller build failed with exit code {result.returncode}")
        raise SystemExit(result.returncode)
    print("\nInstaller built: dist/TOTStatsMonitor-Setup.exe")


def build(version: str) -> None:
    print(f"Building TOTStatsMonitor v{version}...")
    result = subprocess.run(pyinstaller_args())
    if result.returncode != 0:
        print(f"\nBuild failed with exit code {result.returncode}")
        raise SystemExit(result.returncode)
    if WINDOWS:
        build_installer(version)
    else:
        print("\nBuild successful! Output: dist/TOTStatsMonitor-linux-x86_64")


def main() -> None:
    version = get_version()
    if WINDOWS:
        write_version_info(version)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as handle:
            handle.write(f"version={version}\n")
    build(version)


if __name__ == "__main__":
    main()
