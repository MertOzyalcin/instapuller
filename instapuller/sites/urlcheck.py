"""Site bağdaştırıcılarının ortak URL yetki (authority) denetimi."""

from __future__ import annotations

from urllib.parse import SplitResult

_ALLOWED_PORTS = (None, 80, 443)


def authority_is_clean(parts: SplitResult) -> bool:
    """Kullanıcı bilgisi (@) ve 80/443 dışındaki port içeren linkleri reddeder."""
    if "@" in parts.netloc:
        return False
    try:
        port = parts.port
    except ValueError:  # 99999 gibi geçersiz port
        return False
    return port in _ALLOWED_PORTS
