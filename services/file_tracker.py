"""Local index of uploaded files and their MEGA accounts."""

import csv
from datetime import datetime, timezone
from pathlib import Path


TRACKER_FILE = Path("files.csv")
FIELDS = ("uploaded_at_utc", "file_name", "source_path", "account_email")


def track_file(file: str, account_email: str) -> None:
	path = Path(file).resolve()
	write_header = not TRACKER_FILE.exists() or TRACKER_FILE.stat().st_size == 0
	with TRACKER_FILE.open("a", encoding="utf-8", newline="") as output:
		writer = csv.DictWriter(output, fieldnames=FIELDS)
		if write_header:
			writer.writeheader()
		writer.writerow(
			{
				"uploaded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
				"file_name": path.name,
				"source_path": str(path),
				"account_email": account_email,
			}
		)
