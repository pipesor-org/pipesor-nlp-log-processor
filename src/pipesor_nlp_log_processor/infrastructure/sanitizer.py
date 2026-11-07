import re


_REDACTED = "[REDACTED]"


class PatternLogSanitizer:
    """Redacts common credential shapes before logs reach downstream adapters."""

    _patterns = (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key|client[_-]?secret)"
            r"(\s*[:=]\s*)([^\s,;]+)"
        ),
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    )

    def sanitize(self, content: str) -> str:
        sanitized = self._patterns[0].sub(
            lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}",
            content,
        )
        sanitized = self._patterns[1].sub(f"Bearer {_REDACTED}", sanitized)
        return self._patterns[2].sub(_REDACTED, sanitized)

