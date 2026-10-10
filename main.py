"""Main file for the project, handles the arguments and calls the other files."""

import asyncio
import argparse
import os
import sys
from typing import Tuple
import pyppeteer

from services.alive import keepalive
from services.upload import upload_file
from services.extract import extract_credentials
from utilities.fs import (
	Config,
	concrete_read_config,
	read_config,
	write_config,
	write_default_config,
	save_credentials,
)
from utilities.web import (
	finish_form,
	generate_mail,
	type_name,
	type_password,
	initial_setup,
	mail_login,
	get_mail,
)
from utilities.etc import (
	Credentials,
	p_print,
	clear_console,
	Colours,
	check_for_updates,
	delete_default,
)

default_installs = [
	"C:/Program Files/Google/Chrome/Application/chrome.exe",
	"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
	"C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe",
	"C:/Program Files (x86)/BraveSoftware/Brave-Browser/Application/brave.exe",
	"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
	"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
]
args = [
	"--no-sandbox",
	"--disable-setuid-sandbox",
	"--disable-infobars",
	"--window-position=0,0",
	"--ignore-certificate-errors",
	"--ignore-certificate-errors-spki-list",
	'--user-agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0"',
]

parser = argparse.ArgumentParser()
parser.add_argument(
	"-ka",
	"--keepalive",
	required=False,
	action="store_true",
	help="Logs into the accounts to keep them alive.",
)
parser.add_argument(
	"-e",
	"--extract",
	required=False,
	action="store_true",
	help="Extracts the credentials to a file.",
)
parser.add_argument(
	"-v",
	"--verbose",
	required=False,
	action="store_true",
	help="Shows storage left while using keepalive function.",
)
parser.add_argument(
	"-f", "--file", required=False, help="Uploads a file to the account."
)
parser.add_argument(
	"-p",
	"--public",
	required=False,
	action="store_true",
	help="Generates a public link to the uploaded file, use with -f",
)
parser.add_argument(
	"-l",
	"--loop",
	required=False,
	help="Loops the program for a specified amount of times.",
	type=int,
)

console_args = parser.parse_args([])


def setup(require_browser: bool = True) -> Tuple[str, Config]:
	"""Sets up the configs so everything runs smoothly."""

	executable_path = ""
	config = read_config()

	if config is None:
		write_default_config()
		config = concrete_read_config()
	if not require_browser:
		return "", config
	executable_path = config.executablePath
	if not os.path.isfile(executable_path):
		executable_path = next(
			(path for path in default_installs if os.path.isfile(path)), ""
		)
		if executable_path:
			write_config("executablePath", executable_path, config)

	# If no Chromium based browser is found, ask the user for the path to one.
	if not executable_path:
		p_print(
			"Failed to find a Chromium based browser. Please make sure you have one installed.",
			Colours.FAIL,
		)
		executable_path = input(
			"Please enter the path to a Chromium based browser's executable: "
		)
		if os.path.exists(executable_path):
			p_print("Found executable!", Colours.OKGREEN)
			write_config("executablePath", executable_path, config)
		else:
			p_print("Failed to find executable!", Colours.FAIL)
			sys.exit(1)

	return executable_path, config


def upload_size(file: str) -> int:
	if not os.path.isfile(file):
		raise ValueError(f"File not found: {file}")

	size = os.path.getsize(file)
	if not 0 < size < 20_000_000_000:
		raise ValueError("File must be larger than 0 bytes and smaller than 20GB.")
	return size


def loop_registrations(loop_count: int, executable_path: str, config: Config):
	"""Registers accounts in a loop."""
	for _ in range(loop_count):
		p_print(f"Loop {_ + 1}/{loop_count}", Colours.OKGREEN)

		credentials = asyncio.run(generate_mail())
		asyncio.run(register(credentials, executable_path, config))


async def register(credentials: Credentials, executable_path: str, config: Config):
	"""Registers and verifies mega.nz account."""
	browser = await pyppeteer.launch(
		{
			"headless": True,
			"ignoreHTTPSErrors": True,
			"args": args,
			"executablePath": executable_path,
			"autoClose": False,
			"ignoreDefaultArgs": ["--enable-automation", "--disable-extensions"],
		}
	)

	try:
		context = await browser.createIncognitoBrowserContext()
		page = await context.newPage()

		await type_name(page, credentials)
		await type_password(page, credentials)
		await finish_form(page, credentials)
		mail = await mail_login(credentials)

		await asyncio.sleep(1.5)
		message = await get_mail(mail)

		confirm_page = await initial_setup(context, message, credentials)
		await asyncio.sleep(0.5)

		p_print("Verified account.", Colours.OKGREEN)
		p_print(
			f"Email: {credentials.email}\nPassword: {credentials.password}",
			Colours.OKCYAN,
		)

		save_credentials(credentials, config.accountFormat)
		delete_default(credentials)

		if console_args.file is not None:
			try:
				file_size = upload_size(console_args.file)
			except ValueError as exc:
				p_print(str(exc), Colours.FAIL)
				sys.exit(1)
			else:
				if file_size >= 5e9:
					p_print(
						"File is larger than 5GB, mega.nz limits traffic to 5GB per IP.",
						Colours.WARNING,
					)
				if not await upload_file(console_args.public, console_args.file, credentials, confirm_page):
					sys.exit(1)
	finally:
		await browser.close()
	if console_args.loop is None or console_args.loop <= 1:
		sys.exit(0)


def run_cli(argv=None):
	global console_args
	console_args = parser.parse_args(argv)
	if console_args.extract and console_args.keepalive:
		parser.error("Choose either --extract or --keepalive.")
	if (console_args.extract or console_args.keepalive) and (
		console_args.file or console_args.public or console_args.loop is not None
	):
		parser.error("Services cannot be combined with registration or upload options.")
	if console_args.verbose and not console_args.keepalive:
		parser.error("--verbose requires --keepalive.")
	if console_args.public and console_args.file is None:
		parser.error("--public requires --file.")
	if console_args.loop is not None and console_args.loop < 1:
		parser.error("--loop must be at least 1.")
	if console_args.file is not None:
		try:
			upload_size(console_args.file)
		except ValueError as exc:
			parser.error(str(exc))

	clear_console()
	check_for_updates()

	executable_path, config = setup(
		require_browser=not (console_args.extract or console_args.keepalive)
	)
	if not executable_path and not (console_args.extract or console_args.keepalive):
		p_print("Failed while setting up!", Colours.FAIL)
		sys.exit(1)

	if console_args.extract:
		extract_credentials(config.accountFormat or "{email}#{password}")
	elif console_args.keepalive:
		keepalive(console_args.verbose)
	elif console_args.loop is not None and console_args.loop > 1:
		loop_registrations(console_args.loop, executable_path, config)
	else:
		credentials = asyncio.run(generate_mail())
		asyncio.run(register(credentials, executable_path, config))


if __name__ == "__main__":
	run_cli()
