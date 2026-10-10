"""All functions related to working with the config file."""

from dataclasses import asdict
from hashlib import sha256
import os
import json
import sys

from utilities.etc import p_print
from utilities.types import Colours, Credentials, Config
from services.rclone_config import create_rclone_config

CONFIG_FILE = "config.json"


def read_config() -> Config | None:
	"""
	Reads the config file and returns the contents as a dictionary.
	"""
	json_data: dict[str, str]

	if not os.path.exists(CONFIG_FILE):
		return None
	with open(CONFIG_FILE, "r", encoding="utf-8") as f:
		if f.read() == "":
			write_default_config()
			return None
		f.seek(0)
		try:
			json_data = json.loads(f.read())
		except json.JSONDecodeError as exc:
			raise ValueError("config.json contains invalid JSON.") from exc
	if not isinstance(json_data, dict):
		raise ValueError("config.json must contain a JSON object.")

	defaults = asdict(Config())
	for key in defaults:
		if key in json_data and not isinstance(json_data[key], str):
			raise ValueError(f"config.json field {key} must be a string.")
	config = Config(**{key: json_data.get(key, value) for key, value in defaults.items()})
	if any(key not in json_data for key in defaults):
		with open(CONFIG_FILE, "w", encoding="utf-8") as f:
			f.write(json.dumps(asdict(config), indent=4, sort_keys=True))

	return config


def concrete_read_config() -> Config:
	"""
	Reads the config file and returns the contents as a dictionary.
	"""

	config = read_config()
	if config is None:
		p_print("Fatal error while reading config file!", Colours.FAIL)
		sys.exit(1)
	return config


def write_config(k: str, v: str, config: Config) -> None:
	"""
	Writes the config file.
	"""

	if config is None:
		return
	if k not in asdict(config):
		raise KeyError(k)

	with open(CONFIG_FILE, "w", encoding="utf-8") as f:
		config[k] = v
		f.write(json.dumps(asdict(config), indent=4, sort_keys=True))


def write_default_config() -> Config | None:
	"""
	Writes the default config file.
	"""
	if os.path.exists(CONFIG_FILE) and os.stat(CONFIG_FILE).st_size != 0:
		return None

	with open(CONFIG_FILE, "w", encoding="utf-8") as f:
		f.write(json.dumps(asdict(Config()), indent=4, sort_keys=True))
		return Config()


def save_credentials(credentials: Credentials, account_format: str) -> None:
	"""Pass credentials into a file."""
	if not os.path.exists("credentials"):
		os.mkdir("credentials")
	filename = sha256(credentials.email.encode("utf-8")).hexdigest() + ".json"
	with open(os.path.join("credentials", filename), "w", encoding="utf-8") as file:
		file.write(json.dumps(asdict(credentials)))

	if account_format != "":
		account_format = (
			account_format.replace("{email}", credentials.email)
			.replace("{password}", credentials.password)
			.replace("{emailPassword}", credentials.emailPassword)
			+ "\n"
		)

		with open("credentials/accounts.txt", "a", encoding="utf-8") as file:
			file.write(account_format)
	create_rclone_config(credentials)
