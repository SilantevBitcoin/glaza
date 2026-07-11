---
name: glaza
description: Watch a training/lecture video locally and turn it into a self-contained HTML конспект — text-first (from the ru transcript), with screenshots added ONLY where you must look at the screen. Dense fps sampling + pHash-dedup + vision fan-out over subagents, then build the digest. Local, no cloud.
argument-hint: "<video-url-or-path> [why you're watching / what to focus on]"
allowed-tools: Bash, Read, Write, Agent
user-invocable: true
---

# /glaza — Claude turns a video into a конспект

Output is a **self-contained HTML конспект**, not a chat dump. Text is the core (from the ru transcript); screenshots are optional illustrations attached to already-formulated theses. **Никаких скринов ради скринов.**

## Правила (соблюдать весь прогон — подробно и «почему» в `RULES.md`)
1. **Текст — ядро.** Конспект из транскрипта; кадры — иллюстрации к тезисам. `exit 4` (транскрипт упал) → стоп, чинить бэкенд, **не** собирать из одних кадров.
2. **Не читай все кадры сам** — раздавай субагентам пачками ~40, назад только текстовый журнал.
3. **Второй проход — по бюджету:** `dense_zones` сам считает смету и отказывает при `>600` кадров; `--force` — только осознанно.
4. **Скрины только к тезисам,** никаких ради скринов; **открой (Read) каждый выбранный скрин перед вставкой.**
5. **Финальные скрины — родное разрешение** (`extract-one`, секунды `span_end`), не ужатые vision-кадры.
6. **Cleanup в конце** (Step 7): остаётся один `digest.html`.
7. **Всё локально** — ничего в облако; только аудио идёт в локальный Whisper.
8. **Windows:** зови `python`, не `python3`; у нового CLI-скрипта первым делом `sys.stdout.reconfigure(encoding="utf-8")`.

## Step 0 — preflight (silent on success)
```bash
python "${CLAUDE_SKILL_DIR}/scripts/setup.py" --check
```
Exit 0 → proceed. Exit 2 → missing `ffmpeg`/`ffprobe` or `Pillow`. Exit 3 → Whisper backend not configured (`.env` from `.env.example`); the message names the exact problem. A `yt-dlp` warning is not fatal — it is only needed for URL sources.

## Step 1 — parse intent + ask where to save
Separate the source from the reason for watching ("understand feature X" / "full конспект"). Pass it via `--intent`.
**Ask the user for the output path** for the конспект (Егор's choice: ask each run). Default suggestion: `<cwd>/glaza-digests/<video-title>/digest.html`.

## Step 2 — prepare (one download, dense frames, ru transcript)
```bash
python "${CLAUDE_SKILL_DIR}/scripts/glaza.py" "<source>" --intent "<intent>"
```
Prints: **work dir** (URL source → `<work>/download/video.mp4`), a list of **unique representative frames** (fps=1 sampled, then pHash-deduped — a static head/slide collapses to one), and the **transcript (ru)** with timestamps. Each frame line carries both a human timecode and **raw seconds**: `` `path` (t=00:50 [50.00s] → span_end=00:55 [54.67s]) ``. This is the raw material; you do NOT read all frames yourself.

**Exit 4 = the transcript failed** (Whisper backend broke). Stop and fix it — text is the core of the конспект, and a digest built from frames alone is the failure this skill exists to prevent. A video with no audio track is different: it says so, and exits 0.

## Step 3 — vision fan-out (build the event journal)
The representatives can still be dozens–hundreds. **Do not read them all into your own context** — that is what broke earlier. Dispatch them in batches of **~40** to `general-purpose` subagents (parallel). Each subagent reads its batch and returns a TEXT journal — no images back.

Give each subagent the **timecodes together with the paths** — it cannot derive them (the file number is not the second: `frame_00166.jpg` is index 165, i.e. 55.0s at fps=3), and it needs them to line its frames up against the speech.

Subagent prompt template:
> Ты анализируешь кадры обучающего видео для конспекта. Intent зрителя: `<intent>`. Речь на этом отрезке (транскрипт с таймкодами): `<transcript slice covering this batch>`.
> Ниже — кадры, каждый с таймкодом появления. Для КАЖДОГО: открой его (Read) и опиши **факт** того, что на экране — НЕ выдумывай изменений, ты видишь один кадр. Таймкод копируй из списка, не вычисляй.
> Верни СТРОГО JSON-массив, картинки не возвращай:
> `[{"frame":"<path>","timestamp":<sec из списка>,"screen":"<приложение/экран/ключевой видимый текст, 1 фраза>","category":"slide|ui|code|terminal|demo|diagram|talking_head|transition|other","has_screen_content":true|false,"salient":"<важная деталь для конспекта, иначе null>"}]`
> Кадры:
> `<path>` — t=50.00s
> `<path>` — t=54.67s
> …

Collect all JSON into one **journal** (ordered by timestamp) and **save it** as `<work>/journal.json` — the next step reads it. Events («ошибка появилась → исправлена») you derive later from the sequence of states + transcript — a single frame never shows a change.

### Step 3b — second pass over dense UI zones (вариант B)
In demo/UI stretches a small-but-meaningful change — a toggle applied, a menu selection confirmed — gets flattened by dedup: it differs from its neighbour by 2–3 bits out of 64, pixel-wise indistinguishable from noise. Only meaning tells them apart, and the journal carries the meaning, so zones are computed from it, not eyeballed. **Always run this step** — no dense zones in the journal → it says so on stderr (`N записей → M рабочих, … битых`) and does nothing:
```bash
python "${CLAUDE_SKILL_DIR}/scripts/dense_zones.py" \
  "<work>/journal.json" "<work>/download/video.mp4" "<work>/zones" --fps 3
```
It groups the dense records into zones — any frame with `has_screen_content:true` (except `talking_head`/`transition`), or the `ui`/`terminal`/`code`/`demo`/`diagram` categories as a fallback, so a changing screen mislabeled `slide` still counts (a missed zone loses the applied state). It pads/merges/drops, re-samples each zone denser, and prints fresh representatives per zone — same line format as Step 2, absolute timecodes, with a «новых относительно первого прохода» count so you see what the second pass added. Each zone trims the decode, so this costs the zones, not the whole video.

**The pass is budgeted and prints a cost estimate to stderr before cutting — read «Cost & knobs» before raising `--fps` or reaching for `--force`.** Then send the zone representatives through the same vision fan-out as above, and merge their journal into the main one.

## Step 4 — write the конспект (text is the core)
You (main context) write the digest from **transcript (what's said) + journal (what's on screen)**. The text must stand on its own without any image.

Follow the эталон's bar (see «Стиль» below): meaningful sections, тезис-карточки with a short explanation + **verbatim quote** from speech, a glossary of terms, a final synthesis, and a footer disclaimer. Order sections **chronologically**; if an intent was given, put a short **intent-answer block at the top**. Russian text; English tool/term names kept as-is. Plot events from state-changes in the journal + transcript. Плотно, без воды.

## Step 5 — attach screenshots (optional, only where the screen matters)
For a thesis where the reader **must look at the screen** (a shown UI/slide/code/diagram/result), pick the best representative from the journal (`has_screen_content: true`). Prefer the **applied/finished state**, not an open menu or mid-animation — dedup already keeps each run's last frame, so a representative is usually the resolved state; for a UI action pick the frame where the effect is visible (toggle ON, slide fully rendered). A purely spoken point gets **no** screenshot. Talking-head frames are dropped unless the intent is literally about the person / what they hold.

**Open every chosen screenshot yourself (Read) before wiring it in** — confirm it shows what the thesis claims. (Прямая защита от прошлого провала, где вместо контента попали говорящие головы.)

## Step 6 — render self-contained HTML
For each chosen thesis-screenshot, **re-extract at native resolution** — do NOT reuse the downscaled vision frame (its small text may be unreadable). Pass the representative's **`span_end` seconds** from the Step 2 report (the applied state / fully-rendered slide), not the `t` seconds:
```bash
python "${CLAUDE_SKILL_DIR}/scripts/frames.py" extract-one \
  --video "<work>/download/video.mp4" --ts 54.67 --out "<out>/screens/01.jpg"
```
Width is omitted → native resolution, `-q:v 2`. **Keep the video until this step is done** — delete only in Step 7. Then reference `<img src="screens/01.jpg">` in the HTML and inline them:
```bash
python "${CLAUDE_SKILL_DIR}/scripts/inline_images.py" "<out>/digest.raw.html" "<out>/digest.html" "<out>"
```
Result: one portable `digest.html`. Give the user the path.

### Стиль (планка = эталонный конспект)
Self-contained single file. Dark + light theme (system default via `prefers-color-scheme` **plus** a toggle button that stamps `data-theme`). Sticky top-nav with the active section highlighted. Hero: title, one-line lede, mono meta-chips (длительность, тема, N разделов). Sections of **тезис-карточки** (заголовок + пояснение + `«дословная цитата»`). `<details>` for secondary «договорённости/цитаты». A **глоссарий** of terms. A **footer disclaimer**: составлено по локальной транскрипции; названия/термины могли быть расслышаны неточно. Screens are illustrations inside the relevant thesis, `max-width:100%`, and **clickable → lightbox**: the thumbnail stays compact in flow, a click opens the shot large over the page (overlay, closes on click/Esc). Так конспект не растягивается, а читаемость экрана доступна по клику. No horizontal page scroll (wide code/tables scroll in their own container).

## Step 7 — cleanup (last)
After the digest is saved, delete the glaza work dir(s) — the **downloaded video**, frames, audio — **and the digest's own leftovers**: `<out>/screens/` (already inlined as base64) and `<out>/digest.raw.html`. Only `digest.html` remains. `rm` may be sandbox-blocked → use Python:
```bash
python -c "import shutil,sys; [shutil.rmtree(p,ignore_errors=True) for p in sys.argv[1:]]" <work1> <work2> "<out>/screens"
python -c "import pathlib,sys; [pathlib.Path(p).unlink(missing_ok=True) for p in sys.argv[1:]]" "<out>/digest.raw.html"
```

## Cost & knobs
Vision reads every representative frame, so frame width sets the token cost per frame (Opus tier, `⌈w/28⌉×⌈h/28⌉`): **512→209 · 1024→777 · 1568→1792 · 2576→4784**.
- `WATCH_LANG` (default **`ru`**) — язык транскрипции, на языке оригинала (`task=transcribe`, never translate). `ru` распознаёт русскую речь и оставляет английские термины как есть. **Не ставь `en` на русское видео** — Whisper начнёт подбирать похожие по звуку английские слова и вывернет смысл. `auto` рискует ошибиться на интро/музыке и утащить весь транскрипт в чужой язык.
- `WATCH_RES_OVERVIEW` (default **1568**) — это **потолок ширины, а не цель**: видео уже 1568 px кадры не апскейлятся (растянутый кадр стоит те же токены и не добавляет ни одной детали). На 1080p+ потолок даёт читаемый мелкий текст слайдов и кода — ≈2.3× цены 1024, а длинное видео даёт 200+ представителей. Final digest screenshots do NOT depend on it (Step 6 re-extracts them at native resolution).
- `WATCH_FPS` (default `1`) — density scales with video length; raise it only for demo zones (Step 3, вариант B).
- `WATCH_DEDUP_THRESHOLD` (≈10/64) — lower keeps small-but-meaningful changes (a toggle switching ON) at the cost of more frames; higher collapses more and can lose them. `0` turns dedup off entirely.
### Second pass (Step 3b) knobs
- **Budget.** Cost is a product — `frames = coverage(sec) × fps` — so the guard caps the product, not one factor. A continuously-changing screen (live-coding) glues into one whole-video zone and would re-slice everything (~4500 frames); at `> 600` frames `dense_zones` **refuses** (reason on stdout, estimate on stderr) and cuts nothing. There the pass is useless anyway — `fps=1` already caught almost every change. Narrow zones (`--min-zone`, `--gap-merge`), drop `--fps`, or re-run with `--force` (bypasses the budget, not the clamp). A demo inside a lecture stays one small zone, cut silently.
- **`--fps` is clamped** to `min(10, video_fps)` — above the video's own rate ffmpeg only stamps duplicates, and a state under 0.1 s a viewer never sees. Warns on stderr, not a refusal. (`video_fps` from `avg_frame_rate`, the real average — `r_frame_rate` lies on VFR screencasts.)
- **`--mode`** — `fps` (default) or `scene` (frames on scene-changes, falls back to uniform on a smooth stretch). Default `fps` is **chosen by measurement**: `scene` drops the state present at the window's start (a scene-change frame needs a previous frame to differ from), the exact loss this feature prevents — so `scene` is opt-in for zones with very short hard-cut states away from their edges. Other knobs: `--pad`, `--gap-merge`, `--min-zone`, `--width`, `--threshold`.

## Security
Runs yt-dlp/ffmpeg/Whisper locally. Only extracted audio goes to the local Whisper backend (never leaves the machine). No cloud, no accounts.
