"""Site adapter contract: new platforms are added through this interface."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

from instapuller.models import ParsedLink, PullError, SourceMeta


class SiteAdapter(Protocol):
    site_id: str
    hosts: frozenset[str]
    allowed_extractors: list[str]  # yt-dlp extractor keys loaded for this site
    format: str  # yt-dlp format selector
    requires_js_runtime: bool  # True if the downloader must resolve a JS runtime first

    def extra_ydl_opts(self, js_runtime: Any = None) -> dict[str, Any]:
        """Site-specific yt-dlp options (e.g. js_runtimes); js_runtime is a jsruntime.JsRuntime or None."""
        ...

    def check_info(self, info: Any, max_duration_s: float, max_bytes: int) -> None:
        """Validate the extracted info before downloading; raise PullError if it must be rejected."""
        ...

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
