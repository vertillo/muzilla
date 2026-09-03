"""Serialized duplicate evidence model.

One evidence record per DuplicateGroup, stored as JSON in the DB and
exposed via the API. It carries calibrated confidence and explained
quality facts so the UI can describe why files were grouped without
implying verified identity or offering an automatic keep/delete.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class QualityFact:
    track_id: int
    format: str | None
    bitrate: int | None
    duration_ms: int | None
    has_embedded_art: bool | None = None


@dataclass(frozen=True, slots=True)
class DurationComparison:
    min_ms: int | None
    max_ms: int | None
    delta_ms: int | None
    delta_percent: float | None


@dataclass(frozen=True, slots=True)
class DuplicateEvidence:
    recording_id: str
    basis: str
    confidence: float
    confidence_label: str  # alta | media | bassa
    confidence_explanation: str
    duration: DurationComparison
    quality: tuple[QualityFact, ...]
    is_uncertain: bool
    is_false_positive_candidate: bool  # low confidence or large duration gap suggests false positive
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "recording_id": self.recording_id,
            "basis": self.basis,
            "confidence": self.confidence,
            "confidence_label": self.confidence_label,
            "confidence_explanation": self.confidence_explanation,
            "duration": {
                "min_ms": self.duration.min_ms,
                "max_ms": self.duration.max_ms,
                "delta_ms": self.duration.delta_ms,
                "delta_percent": self.duration.delta_percent,
            },
            "quality": [
                {
                    "track_id": q.track_id,
                    "format": q.format,
                    "bitrate": q.bitrate,
                    "duration_ms": q.duration_ms,
                    "has_embedded_art": q.has_embedded_art,
                }
                for q in self.quality
            ],
            "is_uncertain": self.is_uncertain,
            "is_false_positive_candidate": self.is_false_positive_candidate,
            "reason": self.reason,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> DuplicateEvidence:
        dur_raw: Any = data.get("duration", {})
        dur: dict[str, Any] = dur_raw if isinstance(dur_raw, dict) else {}
        qual_raw: Any = data.get("quality", [])
        qual: list[QualityFact] = []
        if isinstance(qual_raw, list):
            for item in qual_raw:
                if isinstance(item, dict):
                    qd: dict[str, Any] = item
                    qual.append(
                        QualityFact(
                            track_id=int(qd.get("track_id", 0)),
                            format=qd.get("format") if isinstance(qd.get("format"), str) else None,
                            bitrate=qd.get("bitrate") if isinstance(qd.get("bitrate"), int) else None,
                            duration_ms=qd.get("duration_ms") if isinstance(qd.get("duration_ms"), int) else None,
                            has_embedded_art=qd.get("has_embedded_art") if isinstance(qd.get("has_embedded_art"), bool) else None,
                        )
                    )
        def _int_or_none(v: Any) -> int | None:
            return v if isinstance(v, int) else None

        def _float_or_none(v: Any) -> float | None:
            return float(v) if isinstance(v, (int, float)) else None

        duration = DurationComparison(
            min_ms=_int_or_none(dur.get("min_ms")),
            max_ms=_int_or_none(dur.get("max_ms")),
            delta_ms=_int_or_none(dur.get("delta_ms")),
            delta_percent=_float_or_none(dur.get("delta_percent")),
        )
        raw_conf: Any = data.get("confidence", 0.0)
        conf_val: float = float(raw_conf) if isinstance(raw_conf, (int, float, str)) else 0.0
        return DuplicateEvidence(
            recording_id=str(data.get("recording_id", "")),
            basis=str(data.get("basis", "acoustid")),
            confidence=conf_val,
            confidence_label=str(data.get("confidence_label", "bassa")),
            confidence_explanation=str(data.get("confidence_explanation", "")),
            duration=duration,
            quality=tuple(qual),
            is_uncertain=bool(data.get("is_uncertain", False)),
            is_false_positive_candidate=bool(data.get("is_false_positive_candidate", False)),
            reason=str(data.get("reason", "")),
        )


def calibrate_confidence(avg_score: float, delta_percent: float | None) -> tuple[float, str, str]:
    """Calibrate fingerprint confidence with duration similarity.

    Returns (confidence, label, explanation).
    """
    if delta_percent is None:
        factor = 0.8
        explanation = "durata non confrontabile; confidenza basata solo sul punteggio AcoustID"
    elif delta_percent <= 5.0:
        factor = 1.0
        explanation = "durate molto simili e punteggio AcoustID alto"
    elif delta_percent <= 10.0:
        factor = 0.8
        explanation = "durate simili ma con lieve differenza"
    elif delta_percent <= 20.0:
        factor = 0.5
        explanation = "differenza di durata rilevante; possibile falso positivo"
    else:
        factor = 0.3
        explanation = "forte differenza di durata; probabile falso positivo"

    confidence = max(0.0, min(1.0, avg_score * factor))
    if confidence >= 0.75:
        label = "alta"
    elif confidence >= 0.5:
        label = "media"
    else:
        label = "bassa"
    return confidence, label, explanation
