"""Export transcripts to plain text and SRT subtitle files.

Both writers are pure functions over ``TimedSegment`` lists and raise on
write failures: the caller (the UI) decides how to surface the error.
"""

from typing import Optional

from app.core.models import TimedSegment


def format_ts(seconds: float) -> str:
    """Format seconds as an SRT timestamp ``HH:MM:SS,mmm``."""
    millis = round(max(0.0, seconds) * 1000.0)
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, msecs = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{msecs:03d}"


def format_ts_txt(seconds: float) -> str:
    """Format seconds as a ``HH:MM:SS`` timestamp for TXT lines."""
    total = int(max(0.0, seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def export(
    segments: list[TimedSegment],
    base_path: str,
    language: Optional[str] = None,
) -> tuple[str, str]:
    """Write ``<base_path>.txt`` and ``<base_path>.srt`` in UTF-8.

    - TXT: one line per segment, ``[HH:MM:SS] <text>``.
    - SRT: numbered cues; a zero/negative duration falls back to 1 ms so the
      cue stays valid for subtitle players.

    Returns ``(txt_path, srt_path)`` and propagates any write exception.
    """
    txt_path = f"{base_path}.txt"
    srt_path = f"{base_path}.srt"

    with open(txt_path, "w", encoding="utf-8") as handle:
        for segment in segments:
            handle.write(f"[{format_ts_txt(segment.start)}] {segment.text}\n")

    with open(srt_path, "w", encoding="utf-8") as handle:
        for index, segment in enumerate(segments, start=1):
            start = max(0.0, segment.start)
            end = max(0.0, segment.end)
            if end <= start:
                end = start + 0.001
            handle.write(f"{index}\n")
            handle.write(f"{format_ts(start)} --> {format_ts(end)}\n")
            handle.write(f"{segment.text}\n\n")

    return txt_path, srt_path