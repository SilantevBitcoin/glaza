**English** | [Русский](README.ru.md)

# /glaza — local content-aware video watcher for Claude

Claude watches a training video **locally** and turns it into a **self-contained HTML digest (конспект)**: the text is the core (from the local Whisper transcript), and screenshots are attached only where you actually have to look at the screen. No cloud, nothing leaves your machine.

## Install

```bash
git clone https://github.com/SilantevBitcoin/glaza
cd glaza
python scripts/install.py
```

`install.py` detects your hardware, picks a Whisper backend and a model size that fits your memory, **shows the plan and asks before touching anything**, then installs the packages, downloads the model and writes `.env`. To just look: `python scripts/install.py --dry-run`.

The one thing it does **not** install is `ffmpeg` (that needs a package manager and privileges). If it's missing, the script prints the single command for your OS (`winget` / `brew` / `apt`) and stops.

### Hooking it into Claude Code

**As a plugin** — invoked as `/glaza:glaza`:
```
/plugin marketplace add SilantevBitcoin/glaza
/plugin install glaza@glaza
```
**As a personal skill** — invoked as bare `/glaza`: copy `SKILL.md` + `scripts/` into `~/.claude/skills/glaza/`.

### Tested on

| Setup | Status |
|---|---|
| Windows + Intel Arc (OpenVINO) | **verified by the author** on real videos |
| Windows / Linux + NVIDIA (CUDA) | implemented, not verified on the author's hardware |
| macOS, Apple Silicon (whisper.cpp) | should work, unverified — issues welcome |
| AMD (Vulkan) | should work, unverified — issues welcome |
| CPU only | works, but slow |

## Usage
```
/glaza:glaza "https://youtu.be/…" "what matters about the architecture?"
/glaza:glaza C:/videos/demo.mp4
```
(bare `/glaza …` if installed as a personal skill)

## Requirements

Everything except `ffmpeg` is installed by `install.py`: `Pillow` (perceptual-hash dedup of frames), `yt-dlp` (only for URL sources) and the packages of the chosen Whisper backend.

`python scripts/setup.py --check` verifies readiness **before** the video is downloaded, and names the exact problem.

## Settings that matter (`.env`)
| key | default | why |
|---|---|---|
| `WATCH_LANG` | `ru` | Transcribe in the **source language** (`task=transcribe`, never translate). `ru` transcribes Russian speech and leaves English tool names as they are. Setting `en` on Russian audio makes Whisper pick English words that merely *sound* alike — the meaning inverts. `auto` can misfire on an intro or music and drag the whole transcript into the wrong language. |
| `WATCH_RES_OVERVIEW` | `1568` | A **width ceiling, not a target.** Frames are never upscaled: a 720p source stays 1280px wide. An upscaled frame costs a vision subagent the same tokens (`⌈w/28⌉×⌈h/28⌉`) and carries no extra detail. Final screenshots ignore this — Step 6 re-extracts them at native resolution. |
| `WATCH_FPS` | `1` | Sampling density; raise only for demo zones. |
| `WATCH_DEDUP_THRESHOLD` | `10` | pHash distance (0..64). Lower keeps small-but-meaningful changes (a toggle switching ON); `0` disables dedup. |

## Whisper backends

`install.py` picks one for you; this table is for when you want to override it.

| backend | hardware | what gets installed |
|---|---|---|
| `faster` | NVIDIA (CUDA) or CPU | `faster-whisper` + `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` (for CUDA); the model downloads on first run |
| `ov` | Intel Arc / OpenVINO | `openvino-genai`, `librosa`; the ready-made `OpenVINO/whisper-large-v3-fp16-ov` (no conversion needed) |
| `whispercpp` | Apple Silicon (Metal), AMD (Vulkan) | the `whisper-cli` binary (macOS: `brew install whisper-cpp`) + a `ggml` model |

### Model per memory budget

| Hardware | Model |
|---|---|
| NVIDIA, VRAM ≥ 8 GB | `large-v3` |
| NVIDIA, VRAM 4–8 GB | `medium` |
| NVIDIA, VRAM < 4 GB | `small` |
| Apple / AMD | by RAM: ≥16 GB → `large-v3`, ≥8 → `medium`, else `small` |
| CPU only | `small` (`base` if RAM < 8 GB) |

**Do not leave `FW_DEVICE=auto` on an NVIDIA box.** `auto` detects CUDA via `import torch`, and torch is not a dependency — without it transcription silently falls back to CPU. `install.py` writes `FW_DEVICE=cuda` explicitly.

## How it works
1. `yt-dlp` downloads the video **once** (or you pass a local file).
2. `ffmpeg` samples frames uniformly (`fps=1` — density scales with video length, no frame cap; never upscaled), then a **pHash dedup** collapses near-identical runs. Each run's representative is its **last** frame — the resolved state (a toggle applied, a slide fully rendered).
3. A local Whisper backend transcribes the audio into timestamped segments, in the source language. If the backend fails, `glaza.py` says so loudly and exits non-zero — a digest without text is not a digest.
4. `glaza.py` prints a markdown report: the unique representative frames (with their on-screen spans) and the transcript.
5. Claude follows `SKILL.md`: it dispatches the frames in batches to **subagents**, which return a text journal of screen states (so the main context never fills with images); writes the digest from transcript + journal; re-extracts the chosen screenshots at **native resolution**; and renders one self-contained `digest.html` (dark/light theme, clickable screenshots). The downloaded video is deleted at the end.

## Build under yourself
The transcription backend is isolated behind one interface (`transcribe(audio, lang, backend, cfg)`). To add your own: add a `_yourbackend.py` returning `[{start,end,text}]`, wire it into `transcribe.py`'s dispatch, and add a `WATCH_WHISPER=yourbackend` branch. Personal/private paths live in `.env` (git-ignored).

## Credits
Reuses yt-dlp argv-injection hardening from claude-watch (MIT). See `NOTICE`. Everything else is original. MIT licensed — see `LICENSE`.
