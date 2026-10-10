"""All functions related to the browser"""

import asyncio
import os
import re
import string
import secrets
import pymailtm
from faker import Faker

from pymailtm.pymailtm import CouldNotGetAccountException
import pyppeteer
import pyppeteer.page
from requests.exceptions import RequestException

from utilities.etc import Credentials, p_print, Colours

fake = Faker()


def get_random_string(length):
	"""Generate a random string with a given length."""
	groups = [string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%&*"]
	if length < len(groups):
		raise ValueError("Password length must be at least four characters.")
	characters = [secrets.choice(group) for group in groups]
	characters.extend(secrets.choice("".join(groups)) for _ in range(length - 4))
	secrets.SystemRandom().shuffle(characters)
	return "".join(characters)


async def initial_setup(context, message, credentials):
	"""Initial setup for the account."""
	confirm_links = re.findall(
		r'href="(https:\/\/mega\.nz\/#confirm[^ ][^"]*)', str(message)
	)
	if not confirm_links:
		raise ValueError("Confirmation email did not contain a MEGA link.")

	confirm_page = await context.newPage()
	await confirm_page.goto(confirm_links[0])
	await confirm_page.waitForSelector("#login-password2")
	await confirm_page.evaluate(
		"""(password) => {
			const input = document.querySelector('#login-password2');
			input.value = password;
			input.dispatchEvent(new Event('input', {bubbles: true}));
			input.dispatchEvent(new Event('change', {bubbles: true}));
		}""",
		credentials.password,
	)
	await confirm_page.evaluate(
		"() => Array.from(document.querySelectorAll('.login-button')).find(button => button.offsetParent).click()"
	)
	await confirm_page.waitForFunction(
		"location.pathname.includes('/fm') || location.pathname.includes('/key')",
		timeout=30000,
	)
	return confirm_page


async def accept_share_consent(page, file):
	"""Accept MEGA's copyright warning for a newly uploaded file."""
	name = os.path.basename(file)
	try:
		if not (isinstance(page.url, str) and page.url.startswith("https://mega.nz/fm")):
			await page.goto("https://mega.nz/fm")
		await page.waitForFunction(
			"document.querySelector('tr.file') || document.body.innerText.includes('Later')",
			timeout=60000,
		)
		await page.evaluate(
			"""() => Array.from(document.querySelectorAll('*'))
				.find(el => el.childElementCount === 0 && el.textContent.trim() === 'Later' && el.getClientRects().length)?.click()"""
		)
		await page.waitForFunction(
			"(name) => Array.from(document.querySelectorAll('tr.file')).some(row => row.innerText.includes(name))",
			{"timeout": 60000},
			name,
		)
		opened = await page.evaluate(
			"""(name) => {
				const row = Array.from(document.querySelectorAll('tr.file')).find(row => row.innerText.includes(name));
				const button = row?.querySelector('.grid-url-arrow');
				button?.click();
				return !!button;
			}""",
			name,
		)
		if not opened:
			return False
		await page.waitForFunction(
			"() => Array.from(document.querySelectorAll('*')).some(el => el.childElementCount === 0 && el.textContent.trim() === 'Share link' && el.getClientRects().length)",
			timeout=10000,
		)
		await page.evaluate(
			"""() => Array.from(document.querySelectorAll('*'))
				.find(el => el.childElementCount === 0 && el.textContent.trim() === 'Share link' && el.getClientRects().length).click()"""
		)
		await page.waitForFunction(
			"document.body.innerText.includes('Copyright warning to all users')",
			timeout=10000,
		)
		accepted = await page.evaluate(
			"""() => {
				if (!document.body.innerText.includes('Copyright warning to all users')) return false;
				const button = Array.from(document.querySelectorAll('*'))
					.find(el => el.childElementCount === 0 && el.textContent.trim() === 'I agree' && el.getClientRects().length);
				button?.click();
				return !!button;
			}"""
		)
		if not accepted:
			return False
		await page.waitForFunction(
			"!document.body.innerText.includes('Copyright warning to all users')",
			timeout=15000,
		)
		return True
	except pyppeteer.errors.TimeoutError:
		return False


async def mail_login(credentials: Credentials):
	"""Logs into the mail.tm account with the generated credentials"""
	for attempt in range(10):
		try:
			mail = pymailtm.Account(
				credentials.id, credentials.email, credentials.emailPassword
			)
			p_print("Retrieved mail successfully!", Colours.OKGREEN)
			return mail
		except CouldNotGetAccountException:
			if attempt == 9:
				raise
			await asyncio.sleep(1)


async def get_mail(mail):
	"""Get the latest email from the mail.tm account"""
	for attempt in range(40):
		try:
			message = mail.get_messages()[0]
			p_print("Found mail!", Colours.OKGREEN)
			return message
		except (IndexError, RequestException):
			if attempt == 0:
				p_print("Waiting for confirmation email...", Colours.WARNING)
			await asyncio.sleep(1.5)
	raise TimeoutError("No confirmation email arrived after 60 seconds.")


async def type_name(page: pyppeteer.page.Page, credentials: Credentials):
	"""Types name into the name fields."""
	firstname = str(fake.first_name())
	await page.goto("https://mega.nz/register")
	await page.waitForSelector("#register-firstname")
	await page.type("#register-firstname", firstname)
	await page.type("#register-email", credentials.email)


async def finish_form(page: pyppeteer.page.Page, credentials: Credentials):
	"""Checks the boxes for the register page."""
	await page.click(".register-button")
	try:
		await page.waitForFunction(
			"document.body.innerText.includes('Check your email inbox')", timeout=30000
		)
	except pyppeteer.errors.TimeoutError as exc:
		message = await page.evaluate("() => document.body.innerText.slice(-500)")
		raise RuntimeError(f"Registration was not accepted: {message}") from exc


async def type_password(page: pyppeteer.page.Page, credentials: Credentials):
	"""Types passwords into the password fields."""
	await page.click("#register-password")
	await page.evaluate(
		"""(password) => {
			const input = document.querySelector('#register-password');
			input.value = password;
			input.dispatchEvent(new Event('input', {bubbles: true}));
			input.dispatchEvent(new Event('change', {bubbles: true}));
		}""",
		credentials.password,
	)
	await page.waitForFunction(
		"document.body.innerText.includes('This is a strong password')", timeout=10000
	)


async def generate_mail() -> Credentials:
	"""Generate mail.tm account and return account credentials."""
	mail = pymailtm.MailTm()

	for attempt in range(10):
		try:
			account = mail.get_account()
			break
		except CouldNotGetAccountException:
			if attempt == 9:
				raise
			p_print("Retrying mail.tm account generation...", Colours.WARNING)
			await asyncio.sleep(min(attempt + 1, 5))

	credentials = Credentials()
	credentials.email = account.address
	credentials.emailPassword = account.password
	credentials.password = get_random_string(20)
	credentials.id = account.id_
	return credentials
