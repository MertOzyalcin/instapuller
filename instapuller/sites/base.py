"""Site adapter contract: new platforms (e.g. YouTube) are added through this interface."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

from instapuller.models import ParsedLink, PullError, SourceMeta


class SiteAdapter(Protocol):
    site_id: str
    hosts: frozenset[str]

    def parse_url(self, url: str) -> ParsedLink:
        """Validate and canonicalize a site URL; raise PullError('LINK_GECERSIZ') if invalid."""
        ...

    def map_error(
        self,
        exc: Exception,
        *,
        allowed_paths: Iterable[str | Path] = (),
    ) -> PullError:
        """Map an infrastructure/yt-dlp error to a user-friendly, sanitized PullError."""
        ...

    def extract_meta(
        self,
        info: dict[str, Any],
        parsed: ParsedLink,
        *,
        duration_s: float | None,
    ) -> SourceMeta:
        """Convert a yt-dlp info dictionary into an autocript-compatible SourceMeta."""
        ...
