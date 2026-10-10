import asyncio
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
from threading import Barrier
import time
from zipfile import ZipFile, ZipInfo
from argparse import Namespace
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from urllib.error import URLError

import pytest
from mega.errors import RequestError

import main
from services import alive, browser, extract, file_tracker, rclone_config, upload
from utilities import etc, fs
from utilities.types import Config, Credentials


@pytest.fixture
def isolated(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)
	return tmp_path


def test_config_lifecycle(isolated):
	assert fs.read_config() is None
	assert fs.write_default_config() == Config()
	assert fs.write_default_config() is None
	assert fs.concrete_read_config() == Config()
	config = fs.concrete_read_config()
	fs.write_config("executablePath", "browser.exe", config)
	assert fs.concrete_read_config().executablePath == "browser.exe"
	with pytest.raises(KeyError):
		fs.write_config("unknown", "value", config)
	Path("config.json").write_text('{"executablePath": "browser.exe"}', encoding="utf-8")
	assert fs.read_config() == Config(executablePath="browser.exe")
	assert "accountFormat" in Path("config.json").read_text(encoding="utf-8")
	Path("config.json").write_text('{"unknown": 1}', encoding="utf-8")
	assert fs.read_config() == Config()
	Path("config.json").write_text("[1]", encoding="utf-8")
	with pytest.raises(ValueError):
		fs.read_config()
	Path("config.json").write_text("{", encoding="utf-8")
	with pytest.raises(ValueError):
		fs.read_config()
	Path("config.json").write_text('{"accountFormat": 1}', encoding="utf-8")
	with pytest.raises(ValueError):
		fs.read_config()
	Path("config.json").write_text("", encoding="utf-8")
	assert fs.read_config() is None
	assert fs.concrete_read_config() == Config()


def test_credentials_and_extraction(isolated, monkeypatch, capsys):
	create_config = Mock()
	monkeypatch.setattr(fs, "create_rclone_config", create_config)
	extract.extract_credentials()
	assert not Path("credentials.txt").exists()
	first = Credentials(email="same@example.com", emailPassword="mail1", password="mega1")
	second = Credentials(email="same@example.net", emailPassword="mail2", password="mega2")
	fs.save_credentials(first, "")
	fs.save_credentials(second, "")
	assert create_config.call_count == 2
	assert [call.args[0].email for call in create_config.call_args_list] == [first.email, second.email]
	files = list(Path("credentials").glob("*.json"))
	assert len(files) == 2
	assert all("id" not in json.loads(path.read_text(encoding="utf-8")) for path in files)
	Path("credentials/broken.json").write_text("{", encoding="utf-8")
	extract.extract_credentials("{email}#{password}#{emailPassword}")
	lines = Path("credentials.txt").read_text(encoding="utf-8").splitlines()
	assert sorted(lines) == [
		"same@example.com#mega1#mail1",
		"same@example.net#mega2#mail2",
	]
	assert "Failed to parse JSON" in capsys.readouterr().out
	fs.save_credentials(first, "{email}#{password}")
	assert Path("credentials/accounts.txt").read_text(encoding="utf-8") == "same@example.com#mega1\n"


def test_custom_format_retains_json(isolated, monkeypatch):
	monkeypatch.setattr(fs, "create_rclone_config", Mock())
	credentials = Credentials(email="custom@example.com", emailPassword="mail", password="mega")
	fs.save_credentials(credentials, "{email}#{password}")
	assert len(list(Path("credentials").glob("*.json"))) == 1
	assert Path("credentials/accounts.txt").read_text(encoding="utf-8") == "custom@example.com#mega\n"


def test_rclone_config_creation(isolated, monkeypatch):
	monkeypatch.setattr(rclone_config, "CONFIG_FILE", isolated / "rclone.conf")
	monkeypatch.setattr(rclone_config, "ensure_rclone", lambda: isolated / "tools/rclone.exe")
	run = Mock(return_value=Mock(returncode=0, stdout="obscured-password\n"))
	monkeypatch.setattr(rclone_config.subprocess, "run", run)
	credentials = Credentials(email="account@example.com", password="plain-password")
	name = rclone_config.create_rclone_config(credentials)
	assert name == rclone_config.remote_name(credentials.email)
	assert run.call_args.args == ([str(isolated / "tools/rclone.exe"), "obscure", "-"],)
	assert run.call_args.kwargs["input"] == "plain-password\n"
	contents = (isolated / "rclone.conf").read_text(encoding="utf-8")
	assert "user = account@example.com" in contents
	assert "pass = obscured-password" in contents
	assert "plain-password" not in contents
	assert not (isolated / "rclone.conf.tmp").exists()
	second = Credentials(email="second@example.com", password="another-password")
	rclone_config.create_rclone_config(second)
	assert (isolated / "rclone.conf").read_text(encoding="utf-8").count("type = mega") == 2


def test_rclone_download_and_checksum(isolated, monkeypatch):
	monkeypatch.setattr(rclone_config, "RCLONE_DIR", isolated / "tools")
	monkeypatch.setattr(rclone_config.platform, "system", lambda: "Windows")
	monkeypatch.setattr(rclone_config.platform, "machine", lambda: "AMD64")
	archive_buffer = io.BytesIO()
	with ZipFile(archive_buffer, "w") as archive:
		archive.writestr("rclone-v1.75.2-windows-amd64/rclone.exe", b"test binary")
	archive_bytes = archive_buffer.getvalue()
	archive_name = "rclone-v1.75.2-windows-amd64.zip"
	responses = {
		"SHA256SUMS": f"{hashlib.sha256(archive_bytes).hexdigest()}  {archive_name}\n".encode(),
		"rclone-current-windows-amd64.zip": archive_bytes,
	}
	requested = []

	def open_download(url, timeout):
		requested.append(url)
		return io.BytesIO(responses[url.rsplit("/", 1)[-1]])

	monkeypatch.setattr(rclone_config, "_open_download", open_download)
	binary = rclone_config.ensure_rclone()
	assert binary.read_bytes() == b"test binary"
	assert requested[0] == "https://downloads.rclone.org/rclone-current-windows-amd64.zip"
	assert rclone_config.ensure_rclone() == binary
	binary.unlink()
	responses["SHA256SUMS"] = f"{'0' * 64}  {archive_name}\n".encode()
	with pytest.raises(RuntimeError, match="checksum did not match"):
		rclone_config.ensure_rclone()
	assert not binary.exists()


def test_keepalive_missing_and_no_json(isolated, capsys):
	alive.keepalive(False)
	assert "No credentials found" in capsys.readouterr().out
	Path("credentials").mkdir()
	Path("credentials/accounts.txt").write_text("example", encoding="utf-8")
	alive.keepalive(False)
	assert "No JSON credentials" in capsys.readouterr().out


def test_keepalive_accounts(isolated, monkeypatch, capsys):
	path = Path("credentials")
	path.mkdir()
	(path / "a.json").write_text(json.dumps({"email": "a@example.com", "password": "ok"}), encoding="utf-8")
	(path / "b.json").write_text(json.dumps({"email": "b@example.com", "password": "bad"}), encoding="utf-8")
	(path / "c.json").write_text("{", encoding="utf-8")
	clients = []

	def factory():
		client = Mock()
		if len(clients) == 1:
			client.login.side_effect = RequestError(-11)
		client.get_quota.return_value = 2048
		clients.append(client)
		return client

	monkeypatch.setattr(alive, "Mega", factory)
	alive.keepalive(False)
	output = capsys.readouterr().out
	assert "1/3 Successfully" in output
	assert "2/3 Failed" in output
	assert "3/3 Invalid" in output
	clients[0].get_quota.assert_not_called()
	assert len(clients) == 2
	clients.clear()
	alive.keepalive(True)
	assert "2.0GB" in capsys.readouterr().out
	clients.clear()
	original_factory = alive.Mega

	def quota_failure():
		client = original_factory()
		client.get_quota.side_effect = RequestError(-11)
		return client

	monkeypatch.setattr(alive, "Mega", quota_failure)
	alive.keepalive(True)
	assert "Could not retrieve storage quota" in capsys.readouterr().out


def test_upload_success_and_access_error(isolated, monkeypatch, capsys):
	client = Mock()
	client.upload.return_value = {"f": [{"h": "handle"}]}
	client.get_upload_link.return_value = "https://mega.nz/file/test"
	monkeypatch.setattr(upload, "Mega", lambda: client)
	credentials = Credentials(email="a@example.com", password="secret")
	assert asyncio.run(upload.upload_file(False, "file.txt", credentials))
	client.get_upload_link.assert_not_called()
	assert asyncio.run(upload.upload_file(True, "file.txt", credentials))
	assert "Shareable link" in capsys.readouterr().out
	client.get_upload_link.side_effect = RequestError(-11)
	assert asyncio.run(upload.upload_file(True, "file.txt", credentials)) is False
	assert "MEGA denied" in capsys.readouterr().out
	client.get_upload_link.side_effect = RequestError(-15)
	with pytest.raises(RequestError):
		asyncio.run(upload.upload_file(True, "file.txt", credentials))
	client.get_upload_link.side_effect = [RequestError(-11), "https://mega.nz/file/test"]
	consent = AsyncMock(return_value=True)
	monkeypatch.setattr(upload, "accept_share_consent", consent)
	assert asyncio.run(upload.upload_file(True, "file.txt", credentials, object()))
	consent.assert_awaited_once()
	client.get_upload_link.side_effect = [RequestError(-11), RequestError(-11)]
	assert asyncio.run(upload.upload_file(True, "file.txt", credentials, object())) is False
	with (isolated / "files.csv").open(encoding="utf-8", newline="") as source:
		rows = list(csv.DictReader(source))
	assert len(rows) == 6
	assert all(row["account_email"] == credentials.email for row in rows)
	assert all(row["file_name"] == "file.txt" for row in rows)


def test_file_tracker_handles_repeated_and_special_filenames(isolated):
	file = isolated / "sample,report.txt"
	file_tracker.track_file(str(file), "first@example.com")
	file_tracker.track_file(str(file), "second@example.com")
	with (isolated / "files.csv").open(encoding="utf-8", newline="") as source:
		rows = list(csv.DictReader(source))
	assert len(rows) == 2
	assert [row["account_email"] for row in rows] == ["first@example.com", "second@example.com"]
	assert all(row["file_name"] == "sample,report.txt" for row in rows)
	assert all(row["source_path"] == str(file) for row in rows)
	assert all(row["uploaded_at_utc"].endswith("+00:00") for row in rows)


def test_updates_and_console(monkeypatch, capsys):
	monkeypatch.setattr(etc, "urlopen", lambda *args, **kwargs: io.BytesIO(b'[{"name":"v1.5.2"}]'))
	assert etc.check_for_updates() is False
	monkeypatch.setattr(etc, "urlopen", lambda *args, **kwargs: io.BytesIO(b'[{"name":"v2"}]'))
	assert etc.check_for_updates() is True
	assert "New version" in capsys.readouterr().out
	monkeypatch.setattr(etc, "urlopen", Mock(side_effect=URLError("offline")))
	assert etc.check_for_updates() is False
	monkeypatch.setattr(etc, "urlopen", lambda *args, **kwargs: io.BytesIO(b"[]"))
	assert etc.check_for_updates() is False
	system = Mock()
	monkeypatch.setattr(etc.os, "system", system)
	etc.clear_console()
	system.assert_called_once()
	etc.p_print("message", etc.Colours.OKGREEN)
	assert "message" in capsys.readouterr().out


def test_welcome_cleanup_only_deletes_welcome_pdf(monkeypatch):
	client = Mock()
	client.get_files_in_node.return_value = {
		"other": {"a": {"n": "notes.pdf"}},
		"welcome": {"a": {"n": "Welcome to MEGA.pdf"}},
	}
	monkeypatch.setattr(etc, "mega", client)
	etc.delete_default(Credentials(email="a@example.com", password="secret"))
	client.destroy.assert_called_once_with("welcome")
	client.get_files_in_node.return_value = {"other": {"a": {"n": "notes.pdf"}}}
	client.destroy.reset_mock()
	etc.delete_default(Credentials(email="a@example.com", password="secret"))
	client.destroy.assert_not_called()


def test_setup_and_upload_size(isolated, monkeypatch):
	browser = isolated / "browser.exe"
	browser.write_bytes(b"browser")
	monkeypatch.setattr(main, "ensure_browser", lambda: browser)
	path, config = main.setup()
	assert path == str(browser)
	assert main.setup(require_browser=False)[0] == ""
	file = isolated / "sample.txt"
	with pytest.raises(ValueError):
		main.upload_size(str(file))
	config.executablePath = "C:/Program Files/Google/Chrome/Application/chrome.exe"
	fs.write_config("executablePath", config.executablePath, config)
	assert main.setup()[0] == str(browser)
	file.write_bytes(b"")
	with pytest.raises(ValueError):
		main.upload_size(str(file))
	file.write_bytes(b"sample")
	assert main.upload_size(str(file)) == 6
	monkeypatch.setattr(main.os.path, "getsize", lambda path: 20_000_000_000)
	with pytest.raises(ValueError):
		main.upload_size(str(file))


def test_managed_browser_install_and_cache(tmp_path, monkeypatch):
	tools = tmp_path / "tools"
	monkeypatch.setattr(browser, "TOOLS_DIR", tools)
	monkeypatch.setattr(browser, "BROWSER_DIR", tools / "browser")
	monkeypatch.setattr(browser, "_platform", lambda: "win64")
	archive_bytes = io.BytesIO()
	with ZipFile(archive_bytes, "w") as archive:
		archive.writestr("chrome-win64/chrome.exe", b"browser")
	version = "155.0.8059.39"
	url = f"https://storage.googleapis.com/chrome-for-testing-public/{version}/win64/chrome-win64.zip"
	metadata = {"channels": {"Stable": {"version": version, "downloads": {"chrome": [{"platform": "win64", "url": url}]}}}}
	calls = []

	def open_download(address, timeout):
		calls.append(address)
		return io.BytesIO(json.dumps(metadata).encode() if address == browser.VERSIONS_URL else archive_bytes.getvalue())

	monkeypatch.setattr(browser, "_open", open_download)
	executable = browser.ensure_browser()
	assert executable.read_bytes() == b"browser"
	assert browser.ensure_browser() == executable
	assert calls == [browser.VERSIONS_URL, url]


def test_managed_browser_concurrent_install(tmp_path, monkeypatch):
	tools = tmp_path / "tools"
	monkeypatch.setattr(browser, "TOOLS_DIR", tools)
	monkeypatch.setattr(browser, "BROWSER_DIR", tools / "browser")
	monkeypatch.setattr(browser, "_platform", lambda: "win64")
	archive_bytes = io.BytesIO()
	with ZipFile(archive_bytes, "w") as archive:
		archive.writestr("chrome-win64/chrome.exe", b"browser")
	version = "155.0.8059.39"
	url = f"https://storage.googleapis.com/chrome-for-testing-public/{version}/win64/chrome-win64.zip"
	metadata = {"channels": {"Stable": {"version": version, "downloads": {"chrome": [{"platform": "win64", "url": url}]}}}}
	calls = []

	def open_download(address, timeout):
		calls.append(address)
		if address == browser.VERSIONS_URL:
			time.sleep(0.1)
		return io.BytesIO(json.dumps(metadata).encode() if address == browser.VERSIONS_URL else archive_bytes.getvalue())

	monkeypatch.setattr(browser, "_open", open_download)
	start = Barrier(2)

	def run():
		start.wait()
		return browser.ensure_browser()

	with ThreadPoolExecutor(max_workers=2) as pool:
		results = list(pool.map(lambda _: run(), range(2)))
	assert results == [tools / "browser" / "chrome.exe"] * 2
	assert calls == [browser.VERSIONS_URL, url]


def test_managed_browser_restores_helper_modes(tmp_path, monkeypatch):
	archive_bytes = io.BytesIO()
	with ZipFile(archive_bytes, "w") as archive:
		for name, mode in (("chrome-linux64/chrome", 0o755), ("chrome-linux64/chrome_crashpad_handler", 0o755), ("chrome-linux64/resources.pak", 0o644)):
			member = ZipInfo(name)
			member.external_attr = (0o100000 | mode) << 16
			archive.writestr(member, b"file")
	changed = []
	monkeypatch.setattr(Path, "chmod", lambda self, mode: changed.append((self.name, mode)))
	with ZipFile(io.BytesIO(archive_bytes.getvalue())) as archive:
		browser._restore_executable_modes(archive, tmp_path)
	assert changed == [("chrome", 0o755), ("chrome_crashpad_handler", 0o755)]


def test_config_missing_paths(monkeypatch, capsys):
	monkeypatch.setattr(fs, "read_config", lambda: None)
	with pytest.raises(SystemExit) as result:
		fs.concrete_read_config()
	assert result.value.code == 1
	assert "Fatal error" in capsys.readouterr().out
	assert fs.write_config("accountFormat", "value", None) is None


def test_registration_orchestration(monkeypatch):
	credentials = Credentials(email="a@example.com", password="secret")
	context = Mock()
	context.newPage = AsyncMock(return_value=Mock())
	browser = Mock()
	browser.createIncognitoBrowserContext = AsyncMock(return_value=context)
	browser.close = AsyncMock()
	monkeypatch.setattr(main.pyppeteer, "launch", AsyncMock(return_value=browser))
	for name in ("type_name", "type_password", "finish_form", "initial_setup"):
		monkeypatch.setattr(main, name, AsyncMock())
	monkeypatch.setattr(main, "mail_login", AsyncMock(return_value=Mock()))
	monkeypatch.setattr(main, "get_mail", AsyncMock(return_value="message"))
	monkeypatch.setattr(main.asyncio, "sleep", AsyncMock())
	saved = Mock()
	monkeypatch.setattr(main, "save_credentials", saved)
	monkeypatch.setattr(main, "delete_default", Mock())
	monkeypatch.setattr(main, "console_args", Namespace(file=None, public=False, loop=None))
	with pytest.raises(SystemExit) as result:
		asyncio.run(main.register(credentials, "browser.exe", Config()))
	assert result.value.code == 0
	browser.close.assert_awaited_once()
	saved.assert_called_once()
	main.type_name.side_effect = RuntimeError("browser failed")
	with pytest.raises(RuntimeError):
		asyncio.run(main.register(credentials, "browser.exe", Config()))
	assert browser.close.await_count == 2


def test_registration_loop(monkeypatch):
	credentials = Credentials(email="a@example.com")
	monkeypatch.setattr(main, "generate_mail", AsyncMock(return_value=credentials))
	register = AsyncMock()
	monkeypatch.setattr(main, "register", register)
	main.loop_registrations(2, "browser.exe", Config())
	assert register.await_count == 2


def test_registration_upload_branches(isolated, monkeypatch, capsys):
	file = isolated / "upload.txt"
	file.write_bytes(b"file")
	credentials = Credentials(email="a@example.com", password="secret")
	context = Mock()
	context.newPage = AsyncMock(return_value=Mock())
	browser = Mock()
	browser.createIncognitoBrowserContext = AsyncMock(return_value=context)
	browser.close = AsyncMock()
	monkeypatch.setattr(main.pyppeteer, "launch", AsyncMock(return_value=browser))
	for name in ("type_name", "type_password", "finish_form", "initial_setup"):
		monkeypatch.setattr(main, name, AsyncMock())
	monkeypatch.setattr(main, "mail_login", AsyncMock(return_value=Mock()))
	monkeypatch.setattr(main, "get_mail", AsyncMock(return_value="message"))
	monkeypatch.setattr(main.asyncio, "sleep", AsyncMock())
	monkeypatch.setattr(main, "save_credentials", Mock())
	monkeypatch.setattr(main, "delete_default", Mock())
	upload_file = AsyncMock(return_value=True)
	monkeypatch.setattr(main, "upload_file", upload_file)
	monkeypatch.setattr(main, "console_args", Namespace(file=str(file), public=True, loop=2))
	asyncio.run(main.register(credentials, "browser.exe", Config()))
	upload_file.assert_awaited_once_with(True, str(file), credentials, main.initial_setup.return_value)
	upload_file.return_value = False
	with pytest.raises(SystemExit) as result:
		asyncio.run(main.register(credentials, "browser.exe", Config()))
	assert result.value.code == 1
	file.unlink()
	with pytest.raises(SystemExit) as result:
		asyncio.run(main.register(credentials, "browser.exe", Config()))
	assert result.value.code == 1
	assert "File not found" in capsys.readouterr().out
	file.write_bytes(b"file")
	monkeypatch.setattr(main, "upload_size", lambda path: 5_000_000_000)
	upload_file.return_value = True
	asyncio.run(main.register(credentials, "browser.exe", Config()))
	assert "larger than 5GB" in capsys.readouterr().out


def test_cli_routes_and_validation(isolated, monkeypatch):
	monkeypatch.setattr(main, "clear_console", Mock())
	monkeypatch.setattr(main, "check_for_updates", Mock())
	setup = Mock(return_value=("browser.exe", Config()))
	monkeypatch.setattr(main, "setup", setup)
	extract_credentials = Mock()
	keepalive = Mock()
	loop = Mock()
	monkeypatch.setattr(main, "extract_credentials", extract_credentials)
	monkeypatch.setattr(main, "keepalive", keepalive)
	monkeypatch.setattr(main, "loop_registrations", loop)
	for args in (
		["-p"], ["-v"], ["-l", "0"], ["-e", "-ka"],
		["-e", "-f", "file"], ["-ka", "-l", "2"], ["-f", "missing"],
	):
		with pytest.raises(SystemExit) as result:
			main.run_cli(args)
		assert result.value.code == 2
	main.run_cli(["-e"])
	extract_credentials.assert_called_once_with("{email}#{password}")
	setup.assert_called_with(require_browser=False)
	main.run_cli(["-ka", "-v"])
	keepalive.assert_called_once_with(True)
	main.run_cli(["-l", "2"])
	loop.assert_called_once_with(2, "browser.exe", Config())
	credentials = Credentials(email="a@example.com")
	monkeypatch.setattr(main, "generate_mail", AsyncMock(return_value=credentials))
	register = AsyncMock()
	monkeypatch.setattr(main, "register", register)
	main.run_cli([])
	register.assert_awaited_once()
	setup.return_value = ("", Config())
	with pytest.raises(SystemExit) as result:
		main.run_cli([])
	assert result.value.code == 1
