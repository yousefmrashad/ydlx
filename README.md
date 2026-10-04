# ⚡ ydlx (yt-dlp eXtended)

[![PyPI version](https://img.shields.io/pypi/v/ydlx)](https://pypi.org/project/ydlx/)
[![PyPI downloads](https://img.shields.io/pypi/dm/ydlx?label=downloads%2Fmonth)](https://pypi.org/project/ydlx/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/ydlx/)
[![License](https://img.shields.io/pypi/l/ydlx)](https://github.com/yousefmrashad/ydlx/blob/master/LICENSE)

A modern, fast, interactive CLI dashboard and downloader built on top of [yt-dlp](https://github.com/yt-dlp/yt-dlp).

**ydlx** eliminates complex parameter strings and brings an intuitive interactive menu, universal codec compatibility presets, automated SponsorBlock skipping, browser cookie integration, and smart platform-agnostic output organization.

---

## ✨ Features

- 🎮 **Interactive Dashboard**: Run `ydlx` without arguments to launch a guided terminal menu.
- 📁 **Smart Output Routing**:
  - Audio files automatically save to `~/Downloads/audio`
  - Videos automatically save to `~/Downloads/video`
- 🎬 **Universal MP4 Mode**: Automatically selects and muxes compatible H.264 + AAC streams that play everywhere.
- 🎵 **Dedicated Audio Extraction**: One-command audio downloading with format conversion (M4A, MP3, WAV, FLAC).
- 💬 **Subtitles & Closed Captions**: Download separate `.srt` files, embed subtitles into video files, or download subtitles only without fetching the video.
- 🛡️ **SponsorBlock Integration**: Seamlessly skip sponsors and self-promotions with `--sponsorblock` / `-s`.
- 🍪 **Cookie Support**: Avoid 403 Forbidden / bot-detection errors by loading cookies directly from your browser, or from a `cookies.txt` file.
- 🎨 **Rich UI**: Beautiful formatted metadata tables and download progress bars with native UTF-8 support.

---

## 📦 Installation

### Prerequisites
Make sure **[FFmpeg](https://ffmpeg.org/)** is installed and accessible on your system `PATH`.

**[Deno](https://deno.com/)** is optional but recommended: it lets yt-dlp solve YouTube's JavaScript challenges, so downloads run at full speed and all formats stay available.

### Install from PyPI (recommended)

```bash
# with uv
uv tool install ydlx

# or with pip
pip install ydlx

# or with pipx
pipx install ydlx
```

### Install from source (requires [uv](https://docs.astral.sh/uv/))

Clone this repository and install it as an isolated tool:

```bash
git clone git@github.com:yousefmrashad/ydlx.git
cd ydlx
uv tool install .
```

This installs the `ydlx` command globally while keeping dependencies isolated from your system Python.

To upgrade after pulling new changes:

```bash
git pull
uv tool install . --force
```

To uninstall:

```bash
uv tool uninstall ydlx
```

---

## 🚀 Usage

### 1. Interactive Wizard (Default)
Simply run:
```bash
ydlx
```
This opens the interactive dashboard where you can inspect metadata, choose resolutions, extract audio, download subtitles, and manage cookies.

For a single video the wizard lists every available resolution to pick from. A playlist has no shared format list — each entry carries its own format IDs — so the wizard instead caps the height (`720p`, `1080p`, …) and applies it per video, the same as `--res`.

### 2. Download Video / Playlist
```bash
# Download Universal MP4 (H.264 + AAC) to ~/Downloads/video
ydlx download "https://www.youtube.com/watch?v=..."

# Cap the resolution (picks the best available at or below it)
ydlx download "https://www.youtube.com/watch?v=..." --res 1080

# Same cap applied to every video in a playlist
ydlx download "https://www.youtube.com/playlist?list=..." --res 1080

# Highest quality instead (AV1/VP9, native container)
ydlx download "https://www.youtube.com/watch?v=..." --preset best --res 720

# Escape hatch: raw yt-dlp format code or selector (cannot be combined with --preset/--res)
ydlx download "https://www.youtube.com/watch?v=..." -f "137+140"
ydlx download "https://www.youtube.com/watch?v=..." -f "bestvideo[height<=720]+bestaudio"

# Download and embed subtitles directly into the video
ydlx download "https://www.youtube.com/watch?v=..." --embed-subs --sub-langs en,ar

# Download subtitles as separate .srt file alongside the video
ydlx download "https://www.youtube.com/watch?v=..." --subs --auto-subs

# Skip sponsor segments
ydlx download "https://www.youtube.com/watch?v=..." -s

# Custom output destination
ydlx download "https://www.youtube.com/watch?v=..." -o "./custom_folder"
```

| Preset | Result |
|---|---|
| `universal` *(default)* | H.264 video + AAC audio, merged into MP4 — plays on any device |
| `best` | yt-dlp's highest ranked quality (usually AV1/VP9), native container |

### 3. Download Subtitles Only
```bash
# Download English subtitles (.srt) to ~/Downloads/video without downloading the video
ydlx subs "https://www.youtube.com/watch?v=..."

# Download specific languages with auto-caption fallback
ydlx subs "https://www.youtube.com/watch?v=..." --sub-langs en,es,ar

# Choose the subtitle format
ydlx subs "https://www.youtube.com/watch?v=..." --sub-format vtt
```

### 4. Extract Audio
```bash
# Extract M4A audio to ~/Downloads/audio
ydlx audio "https://www.youtube.com/watch?v=..."

# Convert to MP3
ydlx audio "https://www.youtube.com/watch?v=..." --codec mp3
```

### 5. Inspect Metadata & Available Subtitles
```bash
# View metadata, formats, and available subtitle languages
ydlx info "https://www.youtube.com/watch?v=..."

# Save metadata to .info.json in the current directory
ydlx info "https://www.youtube.com/watch?v=..." --save

# Save metadata into a specific directory
ydlx info "https://www.youtube.com/watch?v=..." -o "./metadata"
```

### Shared Flags
| Flag | Meaning |
|---|---|
| `-o` | Output directory |
| `-b`, `--cookies-from-browser` | Extract cookies from browser (brave, chrome, chromium, edge, firefox, opera, safari, vivaldi, whale) |
| `--cookies` | Path to a Netscape `cookies.txt` file |
| `-l` | Comma-separated subtitle languages |
| `-S` | Download subtitles as a separate file |
| `--auto-subs` / `--no-auto-subs` | Include auto-generated captions |
| `-s` | Skip sponsor segments via SponsorBlock |
| `-v` | Verbose output |

Cookie flags take precedence over saved settings: passing `-b` or `--cookies` overrides the source stored in the interactive menu entirely, so a flag never silently merges with a previously saved one. Pass both flags together if you deliberately want a `cookies.txt` layered over browser cookies. Passing neither uses the saved source.

### Cookies

Cookie reads fail on some setups, and ydlx explains the common cases instead of printing a raw yt-dlp error:

| Symptom | Cause | Fix |
|---|---|---|
| `Failed to decrypt with DPAPI` | Chrome/Edge encrypt cookies with App-Bound Encryption on Windows, which yt-dlp cannot decrypt ([yt-dlp#10927](https://github.com/yt-dlp/yt-dlp/issues/10927)) | Use `--cookies-from-browser firefox` (or brave, vivaldi, opera, chromium), or export a `cookies.txt` and pass `--cookies` |
| `Could not copy ... cookie database` | The browser holds a lock on its cookie DB ([yt-dlp#7271](https://github.com/yt-dlp/yt-dlp/issues/7271)) | Close the browser completely, then retry — a running browser also hides freshly written cookies |
| `Cookies file must be Netscape formatted, not JSON` | A JSON cookie export was passed to `--cookies` | Export with a Netscape-format extension such as *Get cookies.txt LOCALLY* |

On macOS and Linux all supported browsers work: App-Bound Encryption is Windows-only.

To create a `cookies.txt`, install the **Get cookies.txt LOCALLY** extension, log in to the site, and export from the extension. The file is plain tab-separated text, and ydlx validates the path up front because yt-dlp silently ignores a `cookies.txt` it cannot read.

---

## 🧪 Development
```bash
# Install with dev dependencies
uv sync --group dev

# Run the test suite (no network required)
uv run pytest

# Lint, format, and type check
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
```

CI runs lint, format check, tests, and type checks on Linux and Windows.

---

## 📄 License
GPL-3.0 — see [LICENSE](LICENSE)
