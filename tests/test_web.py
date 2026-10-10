import asyncio
import string
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyppeteer.errors import TimeoutError as BrowserTimeout
from pymailtm.pymailtm import CouldNotGetAccountException, CouldNotGetMessagesException

from utilities import web
from utilities.types import Config, Credentials


def test_types_and_password_generation():
	config = Config()
	config["accountFormat"] = "{email}"
	assert config["accountFormat"] == "{email}"
	password = web.get_random_string(20)
	assert len(password) == 20
	assert any(char in string.ascii_lowercase for char in password)
	assert any(char in string.ascii_uppercase for char in password)
	assert any(char in string.digits for char in password)
	assert any(char in "!@#$%&*" for char in password)
	with pytest.raises(ValueError):
		web.get_random_string(3)


def test_generate_mail_retries(monkeypatch):
	account = SimpleNamespace(address="a@example.com", password="mailpass", id_="id")
	mail = Mock()
	mail.get_account.side_effect = [CouldNotGetAccountException(), account]
	monkeypatch.setattr(web.pymailtm, "MailTm", lambda: mail)
	sleep = AsyncMock()
	monkeypatch.setattr(web.asyncio, "sleep", sleep)
	credentials = asyncio.run(web.generate_mail())
	assert credentials.email == account.address
	assert credentials.emailPassword == account.password
	assert credentials.id == "id"
	assert len(credentials.password) == 20
	sleep.assert_awaited_once()
	mail.get_account.side_effect = CouldNotGetAccountException()
	with pytest.raises(CouldNotGetAccountException):
		asyncio.run(web.generate_mail())
	assert mail.get_account.call_count == 12


def test_mail_login_retries(monkeypatch):
	mail = object()
	constructor = Mock(side_effect=[CouldNotGetAccountException(), mail])
	monkeypatch.setattr(web.pymailtm, "Account", constructor)
	sleep = AsyncMock()
	monkeypatch.setattr(web.asyncio, "sleep", sleep)
	credentials = Credentials(email="a@example.com", emailPassword="secret")
	credentials.id = "id"
	assert asyncio.run(web.mail_login(credentials)) is mail
	sleep.assert_awaited_once()
	constructor.side_effect = CouldNotGetAccountException()
	with pytest.raises(CouldNotGetAccountException):
		asyncio.run(web.mail_login(credentials))
	assert constructor.call_count == 12


def test_get_mail_retries_and_times_out(monkeypatch):
	message = object()
	mail = Mock()
	mail.get_messages.side_effect = [[], CouldNotGetMessagesException(), [message]]
	sleep = AsyncMock()
	monkeypatch.setattr(web.asyncio, "sleep", sleep)
	assert asyncio.run(web.get_mail(mail)) is message
	assert sleep.await_count == 2
	mail.get_messages.side_effect = None
	mail.get_messages.return_value = []
	with pytest.raises(TimeoutError):
		asyncio.run(web.get_mail(mail))
	assert mail.get_messages.call_count == 43


def test_registration_form_steps(monkeypatch):
	page = Mock()
	page.goto = AsyncMock()
	page.waitForSelector = AsyncMock()
	page.type = AsyncMock()
	page.click = AsyncMock()
	page.evaluate = AsyncMock()
	page.waitForFunction = AsyncMock()
	monkeypatch.setattr(web.fake, "first_name", lambda: "Test")
	credentials = Credentials(email="a@example.com", password="Aa1234567890!X")
	asyncio.run(web.type_name(page, credentials))
	page.goto.assert_awaited_once_with("https://mega.nz/register")
	assert page.type.await_args_list[-1].args == ("#register-email", credentials.email)
	asyncio.run(web.type_password(page, credentials))
	assert page.evaluate.await_args.args[-1] == credentials.password
	asyncio.run(web.finish_form(page, credentials))
	page.click.assert_any_await(".register-button")
	page.waitForFunction.side_effect = BrowserTimeout("failed")
	page.evaluate.return_value = "Email invalid"
	with pytest.raises(RuntimeError, match="Email invalid"):
		asyncio.run(web.finish_form(page, credentials))


def test_confirmation_step_and_missing_link():
	page = Mock()
	page.goto = AsyncMock()
	page.waitForSelector = AsyncMock()
	page.evaluate = AsyncMock()
	page.waitForFunction = AsyncMock()
	context = Mock()
	context.newPage = AsyncMock(return_value=page)
	credentials = Credentials(password="secret")
	message = '<a href="https://mega.nz/#confirmTOKEN">Confirm</a>'
	assert asyncio.run(web.initial_setup(context, message, credentials)) is page
	page.goto.assert_awaited_once_with("https://mega.nz/#confirmTOKEN")
	page.waitForSelector.assert_awaited_once_with("#login-password2")
	assert page.evaluate.await_args_list[0].args[-1] == credentials.password
	with pytest.raises(ValueError, match="did not contain"):
		asyncio.run(web.initial_setup(context, "unrelated message", credentials))


def test_share_consent_only_when_warning_visible():
	page = Mock()
	page.goto = AsyncMock()
	page.waitForFunction = AsyncMock()
	page.evaluate = AsyncMock(side_effect=[None, True, None, True])
	assert asyncio.run(web.accept_share_consent(page, "folder/upload.txt"))
	assert page.evaluate.await_args_list[1].args[-1] == "upload.txt"
	assert "Copyright warning to all users" in page.evaluate.await_args_list[3].args[0]
	page.waitForFunction.side_effect = BrowserTimeout("warning absent")
	assert asyncio.run(web.accept_share_consent(page, "upload.txt")) is False
