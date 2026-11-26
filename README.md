<p align="center"><img src="assets/shardbox-128.png" width="96" alt="Shardbox icon"></p>

<h1 align="center">Shardbox</h1>

<p align="center"><b>Big files, split into shards, stored without limits.</b></p>

A Google Drive-style desktop app that stores your files in **your own private Telegram channel**.

Telegram lets you keep as many files as you like in a channel, but limits each file to 2 GB
(4 GB with Telegram Premium). Shardbox handles that for you: add a file of any size and
it is uploaded automatically. Anything over the limit is cut into pieces, and the pieces are joined
back together and checked when you download.

> ⚠️ **Read [Limitations and risks](#limitations-and-risks) before you trust this app with anything important.**

## Features

- Drive-style window: folder sidebar, file list with type badges, details panel showing every piece,
  search, rename and move
- Floating transfers card with progress, speed and time left for each upload and download
- Light and dark mode in Windows 11 File Explorer colours, or follow the Windows setting automatically
- Drag and drop files or whole folders to upload them
- Files over 2 GB are split into pieces automatically (4 GB with Premium)
- Parallel uploads and downloads (several connections per file)
- Every piece is checked with SHA-256 after download
- Interrupted uploads resume from the next piece
- Double-click a file to download and open it
- Your index (the folder structure) is backed up to the channel automatically
- If every index backup is lost, the index can be **rebuilt from the channel**, because each piece's
  caption carries its own metadata
- Runs on Windows, macOS and Linux

## How it works

```
your file (5.2 GB)
   │  hashed (SHA-256, whole file + each piece)
   ▼
┌──────────────┬──────────────┬────────────┐
│ piece 1      │ piece 2      │ piece 3    │   ≤ 1900 MB each (configurable)
└──────┬───────┴──────┬───────┴─────┬──────┘
       ▼              ▼             ▼
  Telegram message  message       message      ← in your private channel,
  (document +       ...           ...            caption = JSON metadata
   caption)
       │
       ▼
  index.db (SQLite on your computer): folders → files → pieces → message ids
       │
       └──► backed up to the same channel after every change
```

The app logs in as **your own Telegram account** (MTProto, through [Telethon](https://github.com/LonamiWebs/Telethon)),
not as a bot. The Bot API only allows 50 MB uploads; user accounts get 2 GB per file.

## Setup

### 1. Get your own Telegram API keys (free, one-time)

1. Go to <https://my.telegram.org/apps> and log in with your phone number.
2. Create an application. Any name works; choose platform "Desktop".
3. Keep the page open. You'll need the **api_id** and **api_hash**.

Every user needs their own keys. Never share yours, and never commit them.

### 2. Install

**Windows:** download `Shardbox-<version>-Setup.exe` from the
[latest release](../../releases/latest) and run it. No admin rights are needed; it installs
just for you unless you choose "all users". Prefer not to install? Download the `-win64.zip`,
unzip it anywhere and run `Shardbox.exe`.

> Windows may show **"Windows protected your PC"**, because the app isn't code-signed (signing
> certificates cost money). Click **More info → Run anyway**. You can check the download against
> `SHA256SUMS.txt` in the release (`Get-FileHash Shardbox-*-Setup.exe` in PowerShell).

To uninstall, use **Settings → Apps → Shardbox**. Your login, settings and index in
`%APPDATA%\Shardbox` are kept, and your files stay in your Telegram channel.

**From source (Windows, macOS, Linux):** you need **Python 3.11 or newer**.

```bash
git clone https://github.com/<you>/shardbox.git
cd shardbox
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python -m shardbox
```

On first start the app asks for:

1. your API ID and API hash,
2. your phone number and the login code Telegram sends you (plus your password if you use
   two-step verification),
3. where to store files: a **new private channel** (recommended) or one you already own.

## Using it

| To… | Do this |
|---|---|
| Upload | Drag files or folders into the window, or use **Upload files** / **Upload folder** |
| Open a file | Double-click it (it downloads to a cache folder, then opens) |
| Download | Select items → **Download** (folders keep their structure) |
| Rename / move / delete | Right-click, or use the buttons in the details panel on the right. Delete also removes the files from Telegram |
| Finish an interrupted upload | Open **Incomplete** in the sidebar, select the file → **Resume upload** |
| Use on a new computer | Log in with the same account and pick the same channel. The app offers to restore your index |
| See a file's pieces | Select it. The details panel lists every piece and whether it reached Telegram |
| Recover a lost index | **☰ menu → Index → Rebuild index from channel** |

Settings are under **☰ menu → Preferences**: appearance, piece size, parallel transfers, and the default
download folder. Switch light/dark mode quickly with the sun/moon button in the top bar.

## Where your data lives

| What | Where |
|---|---|
| Your files | Your private Telegram channel |
| Index, settings, login session | Windows: `%APPDATA%\Shardbox`<br>macOS: `~/Library/Application Support/Shardbox`<br>Linux: `~/.local/share/shardbox` |

Set the environment variable `SHARDBOX_HOME` to use a different folder (for example a portable install).

🔒 **`account.session` gives full access to your Telegram account.** Don't share it, sync it to
other clouds, or commit it. If it leaks, end the session in Telegram: *Settings → Devices*.

## Limitations and risks

- **Telegram is not a backup service.** Using it as unlimited storage is a grey area under
  Telegram's Terms of Service. Accounts that upload heavily can be rate-limited or, in rare cases,
  banned. **Use a spare account if you can, and keep another copy of anything you can't lose.**
- **Speed is set by Telegram.** Free accounts are often throttled. Premium is usually faster.
- **Don't delete messages in the channel by hand**, or the files they belong to can't be downloaded.
- Files are not encrypted by the app (yet). They're in a private channel, but Telegram can read them.
- Renaming or moving a file only changes the index. A **rebuild** brings files back under the name and
  folder they were first uploaded with.

## Development

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The tests use an in-memory fake Telegram, so they need no account.

| Module | Job |
|---|---|
| `shardbox/app.py` | Startup flow: keys → login → channel → main window |
| `shardbox/tg.py` | Telegram: login, channel, parallel upload and download |
| `shardbox/transfers.py` | Upload/download queue, hashing, resume, verification |
| `shardbox/db.py` | SQLite index: folders, files, pieces |
| `shardbox/backup.py` | Index backup, restore and rebuild |
| `shardbox/captions.py` | Metadata stored in each message caption |
| `shardbox/chunking.py` | Splitting and hashing |
| `shardbox/ui/` | Qt (PySide6) windows and dialogs; `theme.py` holds the colours and the app icon |
| `tools/make_icons.py` | Rebuilds `assets/` (the `.ico` and PNGs) from the icon in `theme.py` |

Qt and asyncio share one thread through [qasync](https://github.com/CabbageDevelopment/qasync).
Don't call blocking `dialog.exec()` from inside a coroutine. Use `run_dialog()` / `ask()` from `ui/common.py`.

### Build the Windows installer

```bash
pip install -r requirements-build.txt
winget install JRSoftware.InnoSetup      # once; needed for the installer
python tools/build_windows.py
```

This makes, in `dist/`:

| File | What it is |
|---|---|
| `Shardbox-<version>-Setup.exe` | The installer (Start menu entry, optional desktop icon, uninstaller) |
| `Shardbox-<version>-win64.zip` | The same app without an installer |
| `SHA256SUMS.txt` | Checksums for the release notes |
| `Shardbox/` | The app folder itself (`Shardbox.exe` + its files) |

The recipe is in `packaging/` (`shardbox.spec` for PyInstaller, `shardbox.iss` for Inno Setup).

### Publish a release

GitHub builds the release for you (`.github/workflows/release.yml`):

1. Set `__version__` in `shardbox/__init__.py` (for example `"0.2.0"`) and commit.
2. `git tag v0.2.0` and `git push origin v0.2.0`.
3. When the workflow finishes, open **Releases** on GitHub: a draft with the installer, the zip and
   the checksums is waiting. Check it, write a few notes, and press **Publish**.

## Roadmap

- [ ] Optional client-side encryption of pieces
- [ ] Sync a local folder automatically
- [ ] Streaming video playback without a full download
- [ ] Thumbnails and previews

## License

[MIT](LICENSE)
