# ⚡ ydlx (yt-dlp eXtended)

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
- 🍪 **Browser Cookie Extraction**: Avoid 403 Forbidden / bot-detection errors by loading cookies directly from Chrome, Firefox, Edge, Brave, Safari, or Opera.
- 🎨 **Rich UI**: Beautiful formatted metadata tables and download progress bars with native UTF-8 support.

---

## 📦 Installation

### Prerequisites
Make sure **[FFmpeg](https://ffmpeg.org/)** is installed and accessible on your system `PATH`.

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

### 2. Download Video / Playlist
```bash
# Download best quality to ~/Downloads/video
ydlx download "https://www.youtube.com/watch?v=..."

# Download and embed subtitles directly into the video
ydlx download "https://www.youtube.com/watch?v=..." --embed-subs --sub-langs en,ar

# Download subtitles as separate .srt file alongside the video
ydlx download "https://www.youtube.com/watch?v=..." --subs --auto-subs

# Force Universal MP4 (H.264 + AAC)
ydlx download "https://www.youtube.com/watch?v=..." --custom-selector

# Skip sponsor segments
ydlx download "https://www.youtube.com/watch?v=..." -s

# Custom output destination
ydlx download "https://www.youtube.com/watch?v=..." -o "./custom_folder"
```

### 3. Download Subtitles Only
```bash
# Download English subtitles (.srt) to ~/Downloads/video without downloading the video
ydlx subs "https://www.youtube.com/watch?v=..."

# Download specific languages with auto-caption fallback
ydlx subs "https://www.youtube.com/watch?v=..." --langs en,es,ar
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

# Save metadata to .info.json
ydlx info "https://www.youtube.com/watch?v=..." --save
```

---

## 📄 License
GPL-3.0 — see [LICENSE](LICENSE)