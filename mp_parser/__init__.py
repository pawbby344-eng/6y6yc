"""Парсер поиска Wildberries и Ozon с выдачей в Telegram."""

from .models import Product
from .aggregator import SearchResult, search

__all__ = ["Product", "SearchResult", "search"]
__version__ = "0.1.0"
