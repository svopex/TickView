"""Sdílené konstanty testů."""

from pathlib import Path

# Hlavní stylopis aplikace (testy kontrolují, že obsahuje potřebná pravidla)
STATIC_CSS = Path(__file__).resolve().parent.parent / "tickview" / "ui" / "static" / "style.css"
