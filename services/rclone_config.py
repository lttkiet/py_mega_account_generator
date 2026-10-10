"""Install rclone locally and maintain MEGA remotes in the project config."""

import configparser
from hashlib import sha256
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
from urllib.request import Request, urlopen
from zipfile import ZipFile

from utilities.types import Credentials


ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "rclone.conf"
RCLONE_DIR = ROOT / "tools"


def _open_download(url: str, timeout: int):
	return urlopen(Request(url, headers={"User-Agent": "py-mega-account-generator/1.0"}), timeout=timeout)


def remote_name(email: str) -> str:
	return "mega_" + sha256(email.lower().encode("utf-8")).hexdigest()


def ensure_rclone() -> Path:
	system = {"Windows": "windows", "Linux": "linux", "Darwin": "osx"}.get(platform.system())
	machine = platform.machine().lower()
	arch = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64", "x86": "386", "i386": "386", "i686": "386"}.get(machine)
	if system is None or arch is None:
		raise RuntimeError("This platform is not supported by the bundled rclone installer.")
	binary = RCLONE_DIR / ("rclone.exe" if system == "windows" else "rclone")
	if binary.is_file():
		return binary

	archive_name = f"rclone-current-{system}-{arch}.zip"
	RCLONE_DIR.mkdir(parents=True, exist_ok=True)
	archive_fd, archive_path = tempfile.mkstemp(suffix=".zip", dir=RCLONE_DIR)
	os.close(archive_fd)
	temporary_binary = binary.with_name(binary.name + ".tmp")
	try:
		digest = sha256()
		with _open_download("https://downloads.rclone.org/" + archive_name, timeout=120) as response, open(archive_path, "wb") as output:
			while chunk := response.read(1024 * 1024):
				digest.update(chunk)
				output.write(chunk)
		with ZipFile(archive_path) as archive:
			members = [name for name in archive.namelist() if Path(name).name == binary.name]
			if len(members) != 1:
				raise RuntimeError("The rclone archive did not contain one executable.")
			folder = members[0].split("/")[0]
			match = re.fullmatch(rf"rclone-(v\d+\.\d+\.\d+)-{system}-{arch}", folder)
			if match is None:
				raise RuntimeError("The rclone archive has an unexpected release name.")
			version = match.group(1)
		with _open_download(f"https://downloads.rclone.org/{version}/SHA256SUMS", timeout=30) as response:
			checksums = response.read().decode("ascii")
		versioned_name = f"rclone-{version}-{system}-{arch}.zip"
		checksum = next(
			(parts[0] for line in checksums.splitlines() if (parts := line.split()) and parts[-1].lstrip("*") == versioned_name),
			None,
		)
		if checksum is None or re.fullmatch(r"[0-9a-fA-F]{64}", checksum) is None:
			raise RuntimeError("Could not verify the rclone archive checksum.")
		if digest.hexdigest().lower() != checksum.lower():
			raise RuntimeError("The rclone archive checksum did not match.")
		with ZipFile(archive_path) as archive:
			with archive.open(members[0]) as source, temporary_binary.open("wb") as output:
				shutil.copyfileobj(source, output)
		if system != "windows":
			temporary_binary.chmod(0o755)
		os.replace(temporary_binary, binary)
	finally:
		Path(archive_path).unlink(missing_ok=True)
		temporary_binary.unlink(missing_ok=True)
	return binary


def create_rclone_config(credentials: Credentials) -> str:
	rclone = ensure_rclone()
	result = subprocess.run(
		[str(rclone), "obscure", "-"],
		input=credentials.password + "\n",
		text=True,
		capture_output=True,
		check=False,
	)
	password = result.stdout.strip()
	if result.returncode != 0 or not password:
		raise RuntimeError("rclone could not obscure the MEGA password.")
	config = configparser.ConfigParser(interpolation=None)
	config.read(CONFIG_FILE, encoding="utf-8")
	name = remote_name(credentials.email)
	config[name] = {"type": "mega", "user": credentials.email, "pass": password}
	temporary = CONFIG_FILE.with_suffix(".conf.tmp")
	try:
		with temporary.open("w", encoding="utf-8") as output:
			config.write(output)
		os.replace(temporary, CONFIG_FILE)
	finally:
		temporary.unlink(missing_ok=True)
	return name
