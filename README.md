# py_mega_account_generator

Create a MEGA account, save its credentials, optionally upload a file, and list its cloud files with a project-local rclone binary.

## Prerequisites and installation

- Python 3.10 or newer, Git, and internet access are required for the commands below. No Go or Node.js installation is needed.
- Run commands from the repository root. The tool downloads Chrome for Testing into `tools/browser` before registration and rclone into `tools` when it saves the first account. You do not need to install either system-wide.
- Windows is the tested path. The Ubuntu instructions below are an example for Linux; Chrome sandbox restrictions may prevent the project-local browser from starting on some Linux systems. macOS is currently out of scope.

### Windows (PowerShell)

Install prerequisites with [WinGet](https://learn.microsoft.com/windows/package-manager/winget/):

```powershell
winget install --id Python.Python.3.10 --exact
winget install --id Git.Git --exact
```

Open a new PowerShell window so `git` and `py` are available, then run:

```powershell
git clone https://github.com/lttkiet/py_mega_account_generator.git
cd py_mega_account_generator
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Ubuntu 22.04 (APT)

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip ca-certificates libnss3 libgbm1 libgtk-3-0 libasound2
git clone https://github.com/lttkiet/py_mega_account_generator.git
cd py_mega_account_generator
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

Check `python3 --version` before installing: it must be 3.10 or newer. Chrome may need further system libraries; [Chrome's Linux troubleshooting guide](https://github.com/puppeteer/puppeteer/blob/main/docs/troubleshooting.md#chrome-doesnt-launch-on-linux) lists them. On Ubuntu releases with restrictive AppArmor rules, the current browser launcher does not configure a sandbox workaround.

## Quickstart

There is no separate sign-in or API key setup. From the repository root, run:

```powershell
.\.venv\Scripts\python.exe main.py
```

On Ubuntu, use `./.venv/bin/python main.py`. The tool creates a temporary email address, registers and confirms a MEGA account, prints the credentials, saves a JSON file under `credentials/`, and creates its remote in `rclone.conf`. It downloads Chrome for Testing and rclone on their first use. Keep the saved credentials and `rclone.conf` private; rclone's obscured passwords are reversible.

## Command syntax

This is a flat CLI: it has options but no commands or subcommands.

```text
python main.py [(-f | --file) <path>] [(-p | --public)] [(-l | --loop) <count>]
python main.py (-ka | --keepalive) [(-v | --verbose)]
python main.py (-e | --extract)
python main.py (-h | --help)
```

`<param>` is required when its flag is used, `[param]` is optional, `(-f | --file)` shows alternate spellings of one flag, and `...` means multiple values where a command supports them. This CLI has no multi-value argument.

| Flag | Effect |
| --- | --- |
| `-f`, `--file <path>` | Upload an existing, nonempty file smaller than 20 GB to the new account. |
| `-p`, `--public` | Print a public link for the uploaded file. Requires `--file`. |
| `-l`, `--loop <count>` | Create `<count>` accounts; count must be at least 1. |
| `-ka`, `--keepalive` | Log in to all saved accounts. |
| `-v`, `--verbose` | Show storage quota during keepalive. Requires `--keepalive`. |
| `-e`, `--extract` | Write saved credentials to `credentials.txt` using the configured format. |

`--extract` and `--keepalive` cannot be combined with each other or with registration, upload, or loop options. If MEGA denies a public link after its sharing warning, the upload and credentials stay saved for review.

## Configuration and saved files

The tool creates `config.json` in the current directory on first run. To customize credential exports, set `accountFormat`, for example:

```json
{"accountFormat": "{email}#{password}"}
```

Available placeholders are `{email}`, `{emailPassword}` (the temporary mail account password), and `{password}` (the MEGA password). The default `accountFormat` is an empty string: accounts are still saved as separate JSON files. A nonempty value also appends to `credentials/accounts.txt` when an account is created. `--extract` uses `{email}#{password}` when `accountFormat` is empty.

For this setting, precedence is **local `config.json` > built-in default**. There is no CLI flag or application environment variable for `accountFormat`. The legacy `executablePath` field may be present in `config.json`, but registration always uses the managed browser in `tools/browser`; that field is ignored. CLI flags select the operation and do not override configuration values.

| Path | Contents |
| --- | --- |
| `credentials/*.json` | One saved credential record per account. |
| `credentials/accounts.txt` | Appended custom-format records when `accountFormat` is nonempty. |
| `credentials.txt` | Output of `--extract`. |
| `rclone.conf` | A MEGA remote for each saved account. |
| `files.csv` | Successful uploads: UTC time, filename, source path, and account email. |
| `tools/` | Downloaded browser and rclone binaries; browser profiles are temporary. |

These local files are ignored by Git. Run from the repository root so `config.json`, `credentials/`, `credentials.txt`, and `files.csv` are read and written in the expected place.

## Common workflows

The following commands use PowerShell after the Windows installation above.

### Upload a file and get a link

```powershell
Set-Content -LiteralPath .\example.txt -Value 'MEGA upload test'
.\.venv\Scripts\python.exe main.py --file .\example.txt --public
```

The upload is recorded in `files.csv` with the account email even if public-link creation is denied.

### Create three accounts, then check them

```powershell
.\.venv\Scripts\python.exe main.py --loop 3
.\.venv\Scripts\python.exe main.py --keepalive --verbose
```

### Export and inspect saved account data

```powershell
.\.venv\Scripts\python.exe main.py --extract
Get-ChildItem .\credentials\*.json | ForEach-Object { Get-Content $_.FullName -Raw | ConvertFrom-Json } | Select-Object email
Import-Csv .\files.csv | Select-Object file_name, account_email | Format-Table
```

The last command needs at least one successful upload. The JSON pipeline displays account emails without printing passwords.

### Verify files in MEGA without downloading them

After saving an account, list its rclone remotes and then list filenames from one remote:

```powershell
.\tools\rclone.exe --config .\rclone.conf listremotes
$remote = .\tools\rclone.exe --config .\rclone.conf listremotes | Select-Object -First 1
.\tools\rclone.exe --config .\rclone.conf lsf $remote --files-only
```

Each remote has a `mega_` prefix and an account-specific hash. Choose a different line from `listremotes` to inspect another account. `lsf` lists cloud contents; it does not download file data. On Ubuntu, replace `.\tools\rclone.exe` with `./tools/rclone` and use the same rclone arguments.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
```
