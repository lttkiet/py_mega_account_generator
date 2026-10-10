"""Install a project-local Chrome for Testing browser."""

from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import tempfile
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zipfile import ZipFile


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
BROWSER_DIR = TOOLS_DIR / "browser"
VERSIONS_URL = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"


def _platform() -> str:
	machine = platform.machine().lower()
	system = platform.system()
	if system == "Windows":
		if machine in {"amd64", "x86_64", "arm64"}:
			return "win64"
		if machine in {"x86", "i386", "i686"}:
			return "win32"
	if system == "Linux" and machine in {"amd64", "x86_64", "aarch64", "arm64"}:
		return "linux-arm64" if machine in {"aarch64", "arm64"} else "linux64"
	if system == "Darwin" and machine in {"arm64", "aarch64", "amd64", "x86_64"}:
		return "mac-arm64" if machine in {"arm64", "aarch64"} else "mac-x64"
	raise RuntimeError("This platform is not supported by the managed browser installer.")


def _executable(folder: Path, browser_platform: str) -> Path:
	if browser_platform.startswith("win"):
		return folder / "chrome.exe"
	if browser_platform.startswith("mac"):
		return folder / "Google Chrome for Testing.app" / "Contents" / "MacOS" / "Google Chrome for Testing"
	return folder / "chrome"


def _open(url: str, timeout: int):
	return urlopen(Request(url, headers={"User-Agent": "py-mega-account-generator/1.0"}), timeout=timeout)


@contextmanager
def _install_lock():
	TOOLS_DIR.mkdir(parents=True, exist_ok=True)
	with (TOOLS_DIR / "browser-install.lock").open("a+b") as lock:
		if lock.tell() == 0:
			lock.write(b"\0")
			lock.flush()
		lock.seek(0)
		if os.name == "nt":
			import msvcrt

			while True:
				try:
					msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
					break
				except OSError as exc:
					if exc.errno not in {errno.EACCES, errno.EDEADLK} and getattr(exc, "winerror", None) != 33:
						raise
					time.sleep(0.1)
		else:
			import fcntl

			fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
		try:
			yield
		finally:
			if os.name == "nt":
				msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
			else:
				fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _restore_executable_modes(archive: ZipFile, stage: Path):
	for member in archive.infolist():
		if not member.is_dir() and (member.external_attr >> 16) & 0o111:
			(stage / member.filename).chmod(0o755)


def ensure_browser() -> Path:
	browser_platform = _platform()
	executable = _executable(BROWSER_DIR, browser_platform)
	if executable.is_file():
		return executable
	with _install_lock():
		if executable.is_file():
			return executable
		return _install_browser(browser_platform, executable)


def _install_browser(browser_platform: str, executable: Path) -> Path:

	with _open(VERSIONS_URL, 30) as response:
		stable = json.load(response)["channels"]["Stable"]
	version = stable["version"]
	if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version) is None:
		raise RuntimeError("Chrome for Testing returned an invalid version.")
	url = next((item["url"] for item in stable["downloads"]["chrome"] if item["platform"] == browser_platform), None)
	expected_path = f"/{version}/{browser_platform}/chrome-{browser_platform}.zip"
	parsed = urlparse(url or "")
	if parsed.scheme != "https" or parsed.netloc != "storage.googleapis.com" or parsed.path != "/chrome-for-testing-public" + expected_path:
		raise RuntimeError("Chrome for Testing returned an unexpected download URL.")

	with tempfile.TemporaryDirectory(dir=TOOLS_DIR, prefix="browser-install-") as temporary:
		stage = Path(temporary)
		archive_path = stage / "browser.zip"
		with _open(url, 120) as response, archive_path.open("wb") as output:
			shutil.copyfileobj(response, output)
		with ZipFile(archive_path) as archive:
			for member in archive.infolist():
				parts = PurePosixPath(member.filename).parts
				if not parts or parts[0] != f"chrome-{browser_platform}" or any(part in {".", ".."} for part in parts):
					raise RuntimeError("Chrome for Testing archive has an unsafe path.")
				if (member.external_attr >> 16) & 0o170000 == 0o120000:
					raise RuntimeError("Chrome for Testing archive contains a symbolic link.")
			archive.extractall(stage)
			if os.name != "nt":
				_restore_executable_modes(archive, stage)
		staged_executable = _executable(stage / f"chrome-{browser_platform}", browser_platform)
		if not staged_executable.is_file():
			raise RuntimeError("Chrome for Testing archive did not contain the browser executable.")
		if os.name != "nt":
			staged_executable.chmod(0o755)
		if BROWSER_DIR.exists():
			if BROWSER_DIR.resolve().parent != TOOLS_DIR.resolve():
				raise RuntimeError("The browser cache path is outside the tools folder.")
			shutil.rmtree(BROWSER_DIR)
		os.replace(stage / f"chrome-{browser_platform}", BROWSER_DIR)
	return executable
