"""PyAnnote wrapper: local speaker diarization (who speaks when)."""

import os
from typing import Optional

# pyannote is imported lazily (heavy import) so app startup stays fast.


class Diarizer:
    """Runs PyAnnote speaker diarization with a lazy-loaded pipeline.

    The model is gated on Hugging Face: it requires a free account, accepting
    the model license, and a token (``HF_TOKEN``). Everything stays local once
    the model is downloaded.
    """

    MODEL_ID = "pyannote/speaker-diarization-3.1"

    def __init__(self, hf_token: str = ""):
        self.hf_token = hf_token
        self._pipeline = None

    def get_pipeline(self):
        if self._pipeline is None:
            self._check_token()
            from pyannote.audio import Pipeline
            import torch

            self._pipeline = Pipeline.from_pretrained(self.MODEL_ID, token=self.hf_token)
            if torch.cuda.is_available():
                self._pipeline.to(torch.device("cuda"))
        return self._pipeline

    def diarize(self, path: str, ranges: Optional[list[tuple[float, float]]] = None):
        """Return speaker turns as a list of ``(start, end, speaker_label)``.

        pyannote 4 wraps the classic ``Annotation`` in a ``DiarizeOutput``
        result object: use ``speaker_diarization`` to keep the same shape
        across versions.

        ``ranges`` restricts the pass to the given ``(start, end)`` time ranges
        in seconds. Turns come back already expressed in media time, so no
        offset is added to them. Each range is diarized on its own: the silence
        between two disjoint ranges is never fed to the models.
        """
        pipeline = self.get_pipeline()
        if ranges:
            return self._diarize_ranges(pipeline, path, ranges)
        file, temp_path = self._safe_input(path)
        try:
            return self._tracks(pipeline(file))
        finally:
            if temp_path is not None:
                os.remove(temp_path)

    def _diarize_ranges(self, pipeline, path: str, ranges):
        """Diarize each range on its own and return the turns it contains.

        The ``SpeakerDiarization`` pipeline of pyannote 4 does not expose
        ``crop``: only the raw ``Inference`` behind it has that method, and
        calling it would return features instead of clustered turns. So the
        range is decoded with the clipper and handed over as a waveform, which
        the pipeline accepts directly and which keeps the reader that
        ``_safe_input`` works around (torchcodec) out of the picture. The models
        want 16 kHz mono, exactly what ``decode_range`` produces.
        """
        import torch

        from app.services.clipper import RANGE_WARMUP, SAMPLE_RATE, decode_range, duration

        total = duration(path)
        turns: list[tuple[float, float, str]] = []
        for start, end in ranges:
            # The clustering needs neighbouring speech to stay stable: a bare
            # 12 s range of the reference media came back with different speaker
            # labels and 2 turns instead of 5, while this margin reproduces a
            # whole-file run to within 30 ms. The transcription path decodes
            # with the same margin for the same class of reason, so the value
            # is canonical in clipper.RANGE_WARMUP.
            low = max(0.0, start - RANGE_WARMUP)
            high = end + RANGE_WARMUP
            if total:
                high = min(high, total)
            audio = decode_range(path, low, high)
            if audio.size == 0:
                continue
            result = pipeline(
                {
                    "waveform": torch.from_numpy(audio).unsqueeze(0),
                    "sample_rate": SAMPLE_RATE,
                }
            )
            # The pipeline sees a waveform with no idea of where it came from,
            # so its turns start at zero. Shift them back into media time and
            # trim to the range the caller asked for, dropping the margin.
            for turn_start, turn_end, speaker in self._tracks(result):
                begin = max(start, low + turn_start)
                finish = min(end, low + turn_end)
                if finish > begin:
                    turns.append((begin, finish, speaker))
        return turns

    @staticmethod
    def _tracks(result):
        """``(start, end, speaker)`` triples of a pyannote result object."""
        annotation = getattr(result, "speaker_diarization", None)
        if annotation is None:
            annotation = result  # fallback for pre-4 output (plain Annotation)
        return [
            (turn.start, turn.end, speaker)
            for turn, _, speaker in annotation.itertracks(yield_label=True)
        ]

    @staticmethod
    def _safe_input(path: str):
        """Return ``(pyannote_input, temp_path_or_None)``, decoding via PyAV when needed.

        torchcodec (pyannote 4's audio reader) reports ``None`` duration from
        the container header for some MKV files, which crashes its crop() with
        ``NoneType * int``. WAV files always carry a valid header, so we decode
        to a temporary 16 kHz mono WAV with PyAV (which reads those MKVs fine)
        and return that path instead. The caller removes the temp file.
        """
        import tempfile

        import av

        try:
            from torchcodec.decoders import AudioDecoder

            decoder = AudioDecoder(path)
            has_header_duration = decoder.metadata.duration_seconds_from_header is not None
        except Exception:
            has_header_duration = True  # let pyannote surface non-media errors

        if has_header_duration:
            return path, None

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        with av.open(path) as container:
            out = av.open(tmp.name, "w")
            try:
                stream = out.add_stream("pcm_s16le", rate=16000)
                stream.layout = "mono"
                resampler = av.AudioResampler(
                    format="s16",
                    layout="mono",
                    rate=16000,
                )
                for frame in container.decode(audio=0):
                    for resampled in resampler.resample(frame):
                        for packet in stream.encode(resampled):
                            out.mux(packet)
                for resampled in resampler.resample(None):
                    for packet in stream.encode(resampled):
                        out.mux(packet)
                for packet in stream.encode():
                    out.mux(packet)
            finally:
                out.close()
        return tmp.name, tmp.name

    @staticmethod
    def _check_token() -> None:
        # The token is checked on demand so the app stays usable without it.
        from app.core.config import HF_TOKEN

        if not HF_TOKEN:
            raise ValueError(
                "Falta HF_TOKEN para la diarización de hablantes.\n\n"
                "1. Crea una cuenta gratuita en https://huggingface.co\n"
                "2. Acepta la licencia del modelo:\n   "
                "   https://huggingface.co/pyannote/speaker-diarization-3.1\n"
                "3. Genera un token en:\n   "
                "   https://huggingface.co/settings/tokens\n"
                "4. Añádelo en el archivo .env:  HF_TOKEN=hf_..."
            )


def assign_speaker(start: float, end: float, turns) -> Optional[str]:
    """Label of the turn with maximum overlap for ``(start, end)``, or None."""
    best_label: Optional[str] = None
    best_overlap = 0.0
    for t_start, t_end, label in turns:
        overlap = max(0.0, min(end, t_end) - max(start, t_start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_label = label
    return best_label


def speaker_index(turns) -> dict:
    """Map pyannote labels (SPEAKER_00, …) to 1-based friendly numbers."""
    mapping: dict = {}
    for _, _, label in turns:
        if label not in mapping:
            mapping[label] = len(mapping) + 1
    return mapping