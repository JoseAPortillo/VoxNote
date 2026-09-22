"""PyAnnote wrapper: local speaker diarization (who speaks when)."""

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
        result = self.get_pipeline()(path)
        annotation = getattr(result, "speaker_diarization", None)
        if annotation is None:
            annotation = result  # fallback for pre-4 output (plain Annotation)
        return [
            (turn.start, turn.end, speaker)
            for turn, _, speaker in annotation.itertracks(yield_label=True)
        ]

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