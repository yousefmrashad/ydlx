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

# Only videos in a duration window (playlists)
ydlx download "https://www.youtube.com/playlist?list=..." --min-duration 60 --max-duration 600

# Keep subtitles in WebVTT instead of converting to .srt
ydlx download "https://www.youtube.com/watch?v=..." --subs --sub-format vtt

# Custom output destination
ydlx download "https://www.youtube.com/watch?v=..." -o "./custom_folder"

# Reuse a saved info.json instead of re-fetching metadata
ydlx download --info-json "./video.info.json"
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

Leaving `--sub-langs` at its default lets yt-dlp choose the track, and it prefers an official track over an automatic caption. Pinning a language is an exact match, which misses official tracks YouTube names with a suffix (`en-ehkg1hFWq8A`).

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

### Flags

Every command lists the flags it accepts under `ydlx <command> --help`.

### Cookies

`-b` and `--cookies` take precedence over saved settings: passing one overrides the source stored in the interactive menu entirely, so a flag never silently merges with a previously saved one. Pass both together if you deliberately want a `cookies.txt` layered over browser cookies. Passing neither uses the saved source.

Cookie reads fail on some setups, and ydlx explains the common cases instead of printing a raw yt-dlp error:

| Symptom | Cause | Fix |
|---|---|---|
| `Failed to decrypt with DPAPI` | The browser migrated its cookies to App-Bound Encryption on Windows, which yt-dlp cannot decrypt ([yt-dlp#10927](https://github.com/yt-dlp/yt-dlp/issues/10927)) | Use `--cookies-from-browser firefox`, or export a `cookies.txt` and pass `--cookies` |
| `Could not copy ... cookie database` | The browser holds a lock on its cookie DB ([yt-dlp#7271](https://github.com/yt-dlp/yt-dlp/issues/7271)) | Close the browser completely, then retry — a running browser also hides freshly written cookies |
| `Cookies file must be Netscape formatted, not JSON` | A JSON cookie export was passed to `--cookies` | Export with a Netscape-format extension such as *Get cookies.txt LOCALLY* |

On Windows this depends on whether that browser has migrated its cookie store to the `v20` App-Bound format: a migrated store cannot be read, while a browser still on `v10` works, so Vivaldi is Chromium too and still reads fine. **Firefox is the most reliable option anywhere.** On macOS and Linux all supported browsers work, since App-Bound Encryption is Windows-only.

To create a `cookies.txt`, install the **Get cookies.txt LOCALLY** extension, log in to the site, and export from the extension. The file is plain tab-separated text, and ydlx validates the path up front because yt-dlp silently ignores a `cookies.txt` it cannot read.

**Browser profiles.** yt-dlp only searches Mozilla's own profile directory, so a Firefox-based browser that keeps profiles elsewhere (Zen, LibreWolf, Waterfox) needs an explicit path:

```bash
ydlx download "https://youtu.be/..." -b "firefox:/home/me/.zen/profiles/abc123.default"
```

ydlx checks the browser name against the list above and rejects anything else, but **it does not guess which reader matches an unknown browser**: there is no reliable name-to-format mapping and forks vary. If your browser stores cookies like one of the listed ones, name that one and supply the path; otherwise use a `cookies.txt`. In the dashboard this appears as **custom** under *Set Cookie Source*.

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
