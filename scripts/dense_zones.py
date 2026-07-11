"""«Вариант B» в коде: найти demo/UI-зоны по журналу субагентов и перерезать их гуще.

`fps=1` + pHash-дедуп сплющивает короткое, но смысловое изменение UI: применённый
тумблер отличается от соседнего кадра на 2-3 бита из 64, и дедуп считает его дублем.
Пиксельно «важное малое» неотличимо от шума — различает только смысл. Смысл знает
журнал: субагенты размечают кадры категориями. Отсюда и берутся зоны.

Раньше это делала модель на глаз: границы мерились приблизительно, результат от
прогона к прогону отличался. Здесь то же самое, но детерминировано.
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import frames as F
import dedup as DD

# Категории, где на экране идёт работа, а не говорящая голова.
DENSE_CATEGORIES = {"ui", "terminal", "code", "demo", "diagram"}
# Никогда не зоны, даже с has_screen_content: говорящая голова и переход.
EXCLUDE_CATEGORIES = {"talking_head", "transition"}


def _is_dense(rec: dict, categories: set[str]) -> bool:
    """Плотная ли запись журнала. Основной сигнал — `has_screen_content: true` (кроме
    головы и перехода): он не зависит от того, угадал ли субагент точную категорию
    (code vs slide), а ошибка категории теряет применённое состояние — ровно баг,
    ради которого фича существует. Fallback на allow-list — для журналов старой
    схемы без этого поля. Статику `has_screen_content` не раздувает: держащийся
    слайд даёт одну запись после дедупа, и её отбросит фильтр плотности."""
    if rec.get("has_screen_content") is True and rec.get("category") not in EXCLUDE_CATEGORIES:
        return True
    return rec.get("category") in categories

FPS_CEILING = 10.0        # выше — только дубли (частота видео) и неразличимо глазом (0.1 с)
FRAME_BUDGET = 600        # кадров на весь второй проход; выше — отказ без --force


def _clamp_fps(requested: float, video_fps: float,
               ceiling: float = FPS_CEILING) -> tuple[float, bool]:
    """Зажать запрошенный fps в `min(ceiling, video_fps)`; вернуть (fps, был_ли_clamp).

    Выше собственной частоты видео ffmpeg штампует дубли, выше 10 кадров/с состояние
    короче 0.1 с человек на экране не различит. `video_fps<=0` (ffprobe не дал частоту)
    → зажимаем только по потолку, видео из формулы выпадает."""
    limit = min(ceiling, video_fps) if video_fps > 0 else ceiling
    clamped = min(requested, limit)
    return clamped, clamped != requested


def _estimate(zones: list[tuple[float, float]], fps: float, width: int,
              height: int, duration: float) -> dict:
    """Смета второго прохода ДО нарезки. Стоимость = произведение: покрытие зон
    (сек) × fps = кадров, каждый кадр ≈ ⌈w/28⌉×⌈h/28⌉ токенов у vision-субагента.
    Ограничивать надо это произведение — не fps в одиночку (live-coding идёт при
    fps=3) и не долю видео (50 % часа при fps=3 = 5400 кадров)."""
    coverage = sum(end - start for start, end in zones)
    frames = round(coverage * fps)
    tpf = math.ceil(width / 28) * math.ceil(height / 28) if width and height else 0
    return {"coverage_sec": coverage,
            "coverage_frac": coverage / duration if duration > 0 else 0.0,
            "frames": frames, "tokens_per_frame": tpf, "tokens": frames * tpf}


def detect_dense_zones(journal: list[dict],
                       categories: set[str] = DENSE_CATEGORIES,
                       pad_sec: float = 2.0,
                       gap_merge_sec: float = 5.0,
                       min_zone_sec: float = 3.0) -> list[tuple[float, float]]:
    """Из журнала → список окон (start, end) в секундах, где идёт работа с экраном.

    Плотные записи (`has_screen_content` или нужная категория — см. `_is_dense`)
    сортируются по времени и группируются: соседи с разрывом
    **строго меньше** `gap_merge_sec` попадают в одну зону. Группа короче
    `min_zone_sec` отбрасывается — она меряется по сырым таймкодам, до расширения,
    поэтому одиночный кадр зоной не становится. Уцелевшие расширяются на `pad_sec`
    с обеих сторон (действие начинается раньше, чем экран это показал), и окна,
    которые расширение склеило, сливаются.

    Пустой журнал или журнал без нужных категорий → []. Записи без `timestamp` или с
    нечисловым `timestamp` пропускаются. Результат детерминирован: он зависит только
    от таймкодов и категорий, не от порядка записей.
    """
    stamps: list[float] = []
    for rec in journal:
        if not _is_dense(rec, categories):
            continue
        try:
            stamps.append(float(rec["timestamp"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not stamps:
        return []
    stamps.sort()

    groups: list[list[float]] = [[stamps[0], stamps[0]]]
    for t in stamps[1:]:
        if t - groups[-1][1] < gap_merge_sec:
            groups[-1][1] = t
        else:
            groups.append([t, t])

    merged: list[tuple[float, float]] = []
    for lo, hi in groups:
        if hi - lo < min_zone_sec:
            continue
        zone = (max(0.0, lo - pad_sec), hi + pad_sec)
        if merged and zone[0] <= merged[-1][1]:      # padding склеил соседей
            merged[-1] = (merged[-1][0], max(merged[-1][1], zone[1]))
        else:
            merged.append(zone)
    return [(round(a, 2), round(b, 2)) for a, b in merged]


def _journal_stats(journal: list[dict], categories: set[str] = DENSE_CATEGORIES) -> dict:
    """Разложить журнал по причинам: рабочие (demo-категория + валидный ts), вне
    категорий, битые (нет/нечисловой ts). Битый ts перевешивает категорию — такую
    запись всё равно не использовать. Диагностика, чтобы «зон не найдено» не путали
    с «журнал битый»; логику зон не меняет (detect_dense_zones фильтрует сам)."""
    dense = other = broken = 0
    for rec in journal:
        try:
            float(rec["timestamp"])
        except (KeyError, TypeError, ValueError):
            broken += 1
            continue
        if _is_dense(rec, categories):
            dense += 1
        else:
            other += 1
    return {"total": len(journal), "dense": dense, "other_category": other, "broken": broken}


def resample_zone(video: str, start: float, end: float, out_dir: Path,
                  width: int, fps: float, threshold: int, mode: str = "fps") -> list[dict]:
    """Перерезать окно гуще → `dedup_sequential` → представители окна (те же поля,
    что у dedup; таймкоды абсолютные).

    `mode="fps"` — равномерная нарезка (`extract_fps1`), предсказуемая плотность.
    `mode="scene"` — кадры на сменах сцены (`extract_scene_changes`): ловит именно
    событие-изменение, а не момент времени, с фолбэком на fps для плавных сцен."""
    out_dir = Path(out_dir)
    if mode == "scene":
        raw = F.extract_scene_changes(video, out_dir, width=width, start=start, end=end)
    else:
        raw = F.extract_fps1(video, out_dir, width=width, fps=fps, start=start, end=end)
    return DD.dedup_sequential(raw, threshold=threshold)


def _load_journal(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise SystemExit(f"{path}: ожидался JSON-массив записей журнала, получен {type(data).__name__}")
    return data


def _count_new(reps: list[dict], journal_stamps: list[float], tol: float = 0.15) -> int:
    """Сколько представителей зоны НЕ садятся на тот же момент, что представитель
    первого прохода (совпадение — ближе `tol` секунд; `tol` мал, это «та же
    секунда», не «рядом»). Диагностика ценности второго прохода — сам он
    представителей не выкидывает (потеря в demo-зоне дороже пары дублей)."""
    return sum(1 for r in reps
               if all(abs(r["timestamp"] - js) > tol for js in journal_stamps))


def _refusal_text(est: dict, fps: float) -> str:
    """Текст отказа по бюджету — идёт в STDOUT (туда же, куда результат, — грабля
    №8 в MEMORY: сообщение об ошибке должно попасть туда, куда смотрит модель)."""
    return ("\n# glaza: второй проход ОТКЛОНЁН — превышен бюджет кадров\n\n"
            f"- Покрытие зон **{est['coverage_sec']:.0f} c**, при fps={fps:g} это "
            f"**~{est['frames']} кадров** (бюджет {FRAME_BUDGET}, ~{est['tokens'] // 1000}k токенов).\n"
            "- Это почти наверняка непрерывно меняющийся экран (live-coding, "
            "скроллящийся терминал): детектор склеил всё в одну зону. Второй проход тут "
            "**бесполезен** — `fps=1` первого прохода уже дал представителя почти на каждое "
            "изменение, догущать нечего.\n"
            "- Что делать: сузить зоны (`--min-zone`, `--gap-merge`), снизить `--fps`, "
            "либо, если это действительно нужно, повторить с `--force`.")


def _run(journal_path: str, video: str, out_dir: str, *, fps: float, width: int,
         threshold: int, pad: float, gap_merge: float, min_zone: float,
         force: bool, mode: str = "fps") -> int:
    """Ядро CLI: журнал → зоны → смета → (бюджет-гард) → плотная перенарезка.
    Возвращает exit-код. Смета всегда печатается в stderr до первой нарезки;
    превышение бюджета без `--force` отказывает, не тронув диск."""
    journal = _load_journal(journal_path)
    st = _journal_stats(journal)
    print(f"[dense] журнал: {st['total']} записей → {st['dense']} рабочих, "
          f"{st['other_category']} вне demo-категорий, {st['broken']} битых "
          f"(нет/нечисловой timestamp)", file=sys.stderr)
    zones = detect_dense_zones(journal, pad_sec=pad,
                               gap_merge_sec=gap_merge, min_zone_sec=min_zone)
    if not zones:
        print("\n# glaza: второй проход по demo-зонам\n\n"
              "_Ни одной demo/UI-зоны в журнале — второй проход не нужен._")
        return 0

    meta = F.get_metadata(video)
    fps_c, clamped = _clamp_fps(fps, meta.get("fps") or 0.0)
    est = _estimate(zones, fps_c, width, meta.get("height") or 0,
                    meta.get("duration_seconds") or 0.0)
    print(f"[dense] смета: {len(zones)} зон, покрытие {est['coverage_sec']:.0f} c "
          f"({est['coverage_frac'] * 100:.0f}% видео), fps={fps_c:g} → "
          f"~{est['frames']} кадров, ~{est['tokens'] // 1000}k токенов "
          f"(режим {mode})", file=sys.stderr)
    if clamped:
        print(f"[dense] fps зажат {fps:g}→{fps_c:g} (потолок min(10, частота видео))",
              file=sys.stderr)
    if est["frames"] > FRAME_BUDGET and not force:
        print(_refusal_text(est, fps_c))
        return 3

    journal_stamps: list[float] = []
    for r in journal:
        try:
            journal_stamps.append(float(r["timestamp"]))
        except (KeyError, TypeError, ValueError):
            pass

    out_root = Path(out_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    lines = ["", "# glaza: второй проход по demo-зонам", "",
             f"- **Зон найдено:** {len(zones)}",
             f"- **Плотность:** fps={fps_c:g}, порог dedup {threshold}, режим {mode}",
             f"- **Смета:** покрытие {est['coverage_sec']:.0f} c → ~{est['frames']} кадров", "",
             "**Сам эти кадры не открывай** — раздай пачками субагентам, как в Step 3.", ""]
    for i, (start, end) in enumerate(zones):
        reps = resample_zone(video, start, end, out_root / f"zone_{i:02d}",
                             width, fps_c, threshold, mode=mode)
        new = _count_new(reps, journal_stamps)
        print(f"[dense] zone {i}: {F.format_time(start)}–{F.format_time(end)} "
              f"→ {len(reps)} представителей ({new} новых)", file=sys.stderr)
        lines += [f"## Зона {i}: {F.format_time(start)} → {F.format_time(end)} "
                  f"[{start:.2f}s … {end:.2f}s]", "",
                  f"Представителей: {len(reps)} (новых относительно первого прохода: {new})", ""]
        lines += [F.format_rep(r) for r in reps]
        lines.append("")
    print("\n".join(lines))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="dense_zones",
        description="Второй проход по demo/UI-зонам: журнал -> зоны -> плотная перенарезка.")
    ap.add_argument("journal", help="journal.json — массив записей от vision-субагентов")
    ap.add_argument("video", help="то же видео, что и в первом проходе")
    ap.add_argument("out_dir", help="куда класть кадры зон (подпапки zone_NN)")
    ap.add_argument("--fps", type=float, default=3.0)
    ap.add_argument("--width", type=int, default=1568)
    ap.add_argument("--threshold", type=int, default=10)
    ap.add_argument("--pad", type=float, default=2.0)
    ap.add_argument("--gap-merge", type=float, default=5.0)
    ap.add_argument("--min-zone", type=float, default=3.0)
    ap.add_argument("--mode", choices=("fps", "scene"), default="fps",
                    help="fps — равномерно (дефолт); scene — на сменах сцены внутри зоны")
    ap.add_argument("--force", action="store_true",
                    help="обойти бюджет кадров (sanity-guard по fps остаётся)")
    args = ap.parse_args()
    if args.fps <= 0:
        raise SystemExit(f"--fps must be > 0 (got {args.fps})")
    if args.width <= 0:
        raise SystemExit(f"--width must be > 0 (got {args.width})")
    if not 0 <= args.threshold <= 64:
        raise SystemExit(f"--threshold must be 0..64 (got {args.threshold})")

    return _run(args.journal, args.video, args.out_dir, fps=args.fps, width=args.width,
                threshold=args.threshold, pad=args.pad, gap_merge=args.gap_merge,
                min_zone=args.min_zone, force=args.force, mode=args.mode)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")   # грабля №4: cp1251-консоль Windows
    raise SystemExit(main())
