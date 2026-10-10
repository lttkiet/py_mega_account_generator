"""Functions related to the upload functionality."""

from mega import Mega
from mega.errors import RequestError

from services.file_tracker import track_file
from utilities.etc import Credentials, p_print, Colours
from utilities.web import accept_share_consent


async def upload_file(public: bool, file: str, credentials: Credentials, page=None):
	"""Uploads a file to the account."""

	mega = Mega()
	mega.login(credentials.email, credentials.password)
	p_print("Uploading file... this might take a while.", Colours.OKGREEN)
	uploaded_file = mega.upload(file)
	track_file(file, credentials.email)
	p_print("File uploaded successfully.", Colours.OKGREEN)

	if public:
		try:
			link = mega.get_upload_link(uploaded_file)
		except RequestError as exc:
			if exc.code != -11:
				raise
			if page is None or not await accept_share_consent(page, file):
				p_print("File uploaded, but MEGA denied a public link. Review its sharing warning in the saved account.", Colours.FAIL)
				return False
			try:
				link = mega.get_upload_link(uploaded_file)
			except RequestError as retry_exc:
				if retry_exc.code != -11:
					raise
				p_print("File uploaded, but MEGA still denied a public link after the sharing warning.", Colours.FAIL)
				return False
		p_print(f"Shareable link: {link}", Colours.OKGREEN)
	return True
