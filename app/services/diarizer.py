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

            self._pipeline = Pipeline.from_pretrained(self.MODEL_ID, token=self.hf_token)
        return self._pipeline

    def diarize(self, path: str):
        """Return speaker turns as a list of ``(start, end, speaker_label)``.

        pyannote 4 wraps the classic ``Annotation`` in a ``DiarizeOutput``
        result object: use ``speaker_diarization`` to keep the same shape
        across versions.
        """
        pipeline = self.get_pipeline()
        file, temp_path = self._safe_input(path)
        try:
            result = pipeline(file)
            annotation = getattr(result, "speaker_diarization", None)
            if annotation is None:
                annotation = result  # fallback for pre-4 output (plain Annotation)
            return [
                (turn.start, turn.end, speaker)
                for turn, _, speaker in annotation.itertracks(yield_label=True)
            ]
        finally:
            if temp_path is not None:
                os.remove(temp_path)

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