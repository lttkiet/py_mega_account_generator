"""All functions NOT related to the browser"""

import os
import json
from urllib.request import urlopen
from urllib.error import URLError
from mega import Mega

from utilities.types import Colours, Credentials

VERSION = "v1.5.2"
mega = Mega()


def check_for_updates():
	"""Checks for updates via latest release tag."""
	try:
		with urlopen(
			"https://api.github.com/repos/qtchaos/py_mega_account_generator/tags",
			timeout=5,
		) as request:
			latest_version = json.loads(request.read().decode())[0]["name"]
	except (URLError, TimeoutError, ValueError, IndexError, KeyError):
		return False
	if latest_version == VERSION:
		return False
	p_print(
		f"New version available! Please download it from https://github.com/qtchaos/py_mega_account_generator/releases/tag/{latest_version}",
		Colours.WARNING,
	)
	return True


def delete_default(credentials: Credentials):
	"""Deletes the default welcome file."""
	mega.login(credentials.email, credentials.password)
	for handle, file in mega.get_files_in_node(2).items():
		name = file.get("a", {}).get("n", "").lower()
		if "welcome" in name and name.endswith(".pdf"):
			mega.destroy(handle)


def p_print(
	text,
	colour,
):
	"""Prints text in colour."""
	print(colour + text + Colours.ENDC)


def clear_console():
	"""Clears console."""
	os.system("cls" if os.name == "nt" else "clear")
