"""Run under OV_PYTHON — the interpreter that has openvino-genai + librosa installed.
Writes JSON [{start,end,text}] to <out_json>.
usage: whisper_ov_worker.py <model_dir> <audio> <lang> <device> <out_json>"""
import sys, json
import librosa
import openvino_genai as ov_genai

model_dir, audio_path, lang, device, out_json = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
speech, _ = librosa.load(audio_path, sr=16000)
pipe = ov_genai.WhisperPipeline(model_dir, device)
kw = {"task": "transcribe", "return_timestamps": True}
if lang and lang != "auto":
    kw["language"] = f"<|{lang}|>"
result = pipe.generate(speech, **kw)

# Без чанков нет таймкодов, а без таймкодов конспект не привязать к кадрам.
# Молча схлопывать всё в один сегмент [0,0] — это тихая потеря данных: падаем.
chunks = getattr(result, "chunks", None)
if not chunks:
    raise SystemExit("ov worker: pipeline returned no timestamped chunks "
                     "(return_timestamps не поддержан этой моделью?)")
segs = [{"start": float(ch.start_ts), "end": float(ch.end_ts), "text": ch.text} for ch in chunks]
with open(out_json, "w", encoding="utf-8") as f:
    json.dump(segs, f, ensure_ascii=False)
