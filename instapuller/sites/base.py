"""Site bağdaştırıcı sözleşmesi: yeni platformlar (örn. YouTube) bu arayüzle eklenir."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

from instapuller.models import ParsedLink, PullError, SourceMeta


class SiteAdapter(Protocol):
    site_id: str
    hosts: frozenset[str]

    def parse_url(self, url: str) -> ParsedLink:
        """Siteye ait URL'yi doğrular ve kanonikleştirir; geçersizse PullError('LINK_GECERSIZ')."""
        ...

    def map_error(
        self,
        exc: Exception,
        *,
        allowed_paths: Iterable[str | Path] = (),
    ) -> PullError:
        """Altyapı/yt-dlp hatasını kullanıcı dostu, temizlenmiş PullError'a eşler."""
        ...

    def extract_meta(
        self,
        info: dict[str, Any],
        parsed: ParsedLink,
        *,
        duration_s: float | None,
    ) -> SourceMeta:
        """yt-dlp bilgi sözlüğünü autocript uyumlu SourceMeta'ya dönüştürür."""
        ...
