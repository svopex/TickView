"""Posluchači otevřených stránek pro služby na pozadí (alerty, scanner)."""

import logging
from typing import Callable

logger = logging.getLogger(__name__)


class Listeners:
    """Seznam callbacků stránek; chyba jedné stránky (zavřený klient) nezastaví ostatní ani službu."""

    def __init__(self) -> None:
        self._listeners: set[Callable[[dict], None]] = set()

    def add_listener(self, callback: Callable[[dict], None]) -> None:
        """Přihlásí stránku k událostem služby."""
        self._listeners.add(callback)

    def remove_listener(self, callback: Callable[[dict], None]) -> None:
        """Odhlásí stránku (odpojení klienta)."""
        self._listeners.discard(callback)

    def _emit(self, event: dict) -> None:
        for callback in list(self._listeners):
            try:
                callback(event)
            except Exception:
                logger.exception("Posluchač %s selhal", type(self).__name__)
