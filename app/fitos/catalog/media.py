"""GIF → MP4 (H.264 baseline, 360×360, 3 vueltas) y miniaturas del catálogo (docs/design.md §6.2, paso 2)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .importer import load_records

log = logging.getLogger(__name__)

LOOPS = 3  # -stream_loop 2 → la animación se ve tres veces
FALLBACK_SECONDS = 7
VIDEO_ARGS = [
    "-vf",
    "scale=360:360:flags=lanczos,format=yuv420p",
    "-c:v",
    "libx264",
    "-profile:v",
    "baseline",
    "-r",
    "15",
    "-threads",
    "1",
    "-movflags",
    "+faststart",
]
# WhatsApp puede no reproducir un video sin pista de audio: se agrega una pista AAC muda.
AUDIO_INPUT = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"]
AUDIO_ARGS = ["-map", "0:v:0", "-map", "1:a:0", "-shortest", "-c:a", "aac", "-b:a", "32k"]


def probe_duration(path: Path) -> float | None:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return None


def _ffmpeg(gif: Path, out: Path, with_audio: bool, fallback: bool) -> None:
    src = (
        ["-ignore_loop", "0", "-i", str(gif)]
        if fallback
        else ["-stream_loop", str(LOOPS - 1), "-i", str(gif)]
    )
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *src]
    cmd += [*AUDIO_INPUT, *AUDIO_ARGS] if with_audio else ["-an"]
    if fallback:
        cmd += ["-t", str(FALLBACK_SECONDS)]
    cmd += [*VIDEO_ARGS, "-f", "mp4", str(out)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def convert_gif(gif: Path, out: Path, with_audio: bool = True) -> float | None:
    """Convierte un GIF a MP4 escribiendo a un temporal (un corte no deja un MP4 "existente" a medias).
    Si el resultado es sospechosamente corto (el demuxer no repitió), rehace con `-ignore_loop 0 -t 7`."""
    tmp = out.with_suffix(".part.mp4")
    _ffmpeg(gif, tmp, with_audio, fallback=False)
    gif_dur, dur = probe_duration(gif), probe_duration(tmp)
    expected = LOOPS * gif_dur * 0.8 if gif_dur else 1.5
    if dur is None or dur < expected:
        _ffmpeg(gif, tmp, with_audio, fallback=True)
        dur = probe_duration(tmp)
    tmp.replace(out)
    return dur


def build_media(
    src_dir: Path, out_dir: Path, limit: int | None = None, with_audio: bool = True, workers: int = 2
) -> dict[str, Any]:
    """Genera `out_dir/mp4/<id>.mp4` y `out_dir/thumbs/<id>.jpg` para cada registro; salta los existentes."""
    src_dir, out_dir = Path(src_dir), Path(out_dir)
    mp4_dir, thumb_dir = out_dir / "mp4", out_dir / "thumbs"
    mp4_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    records = load_records(src_dir)[:limit] if limit else load_records(src_dir)
    counts: dict[str, Any] = {"built": 0, "skipped": 0, "thumbs": 0, "missing": 0, "failed": []}

    def work(rec: dict[str, Any]) -> tuple[str, bool, tuple[str, str] | None]:
        ex_id = str(rec["id"])
        image = rec.get("image")
        thumb = thumb_dir / f"{ex_id}.jpg"
        copied = bool(image) and not thumb.exists() and (src_dir / image).is_file()
        if copied:
            shutil.copyfile(src_dir / image, thumb)
        out = mp4_dir / f"{ex_id}.mp4"
        if out.exists():
            return "skipped", copied, None
        gif = src_dir / (rec.get("gif_url") or "")
        if not rec.get("gif_url") or not gif.is_file():
            return "missing", copied, None
        try:
            convert_gif(gif, out, with_audio)
        except subprocess.CalledProcessError as e:
            out.with_suffix(".part.mp4").unlink(missing_ok=True)
            log.warning("build-media: falló %s", ex_id)
            return "failed", copied, (ex_id, (e.stderr or str(e)).strip()[-300:])
        return "built", copied, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for status, copied, failure in pool.map(work, records):
            counts["thumbs"] += copied
            if failure:
                counts["failed"].append(failure)
            else:
                counts[status] += 1
    return counts
