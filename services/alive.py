"""Functions related to the keepalive functionality."""

import os
import json

from mega import Mega
from mega.errors import RequestError

from utilities.etc import p_print, Colours


def keepalive(verbose: bool):
	"""Keep the generated accounts alive by logging in."""

	if not os.path.isdir("./credentials"):
		p_print(
			"No credentials found, please remove all arguments and try again.",
			Colours.FAIL,
		)
		return
	files = sorted(file for file in os.listdir("./credentials") if file.endswith(".json"))
	if not files:
		p_print("No JSON credentials found.", Colours.FAIL)
		return

	for i, file in enumerate(files, start=1):
		with open(os.path.join("credentials", file), "r", encoding="utf-8") as f:
			try:
				credentials = json.load(f)
				email = credentials["email"]
				password = credentials["password"]
			except (json.JSONDecodeError, KeyError, TypeError):
				p_print(f"{i}/{len(files)} Invalid credentials file: {file}", Colours.FAIL)
				continue
		mega = Mega()
		try:
			mega.login(email, password)
		except RequestError:
			p_print(f"{i}/{len(files)} Failed to login to {email}", Colours.FAIL)
			continue
		p_print(f"{i}/{len(files)} Successfully logged into {email}", Colours.OKGREEN)
		if verbose:
			try:
				storage_left = mega.get_quota() / 1024
			except RequestError:
				p_print("    Could not retrieve storage quota", Colours.WARNING)
			else:
				p_print(f"    {storage_left}GB of storage left", Colours.OKGREEN)
