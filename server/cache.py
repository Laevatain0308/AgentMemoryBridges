"""LRU 缓存封装 —— 基于 cachetools.TTLCache"""
from typing import Any, Optional

from cachetools import TTLCache


class MemoryCache:
    """TTL + LRU 缓存，用于热点查询"""
    def __init__(self, maxsize: int = 256, ttl: int = 30):
        self._cache: TTLCache[str, Any] = TTLCache(maxsize=maxsize, ttl=ttl)

    def get(self, key: str) -> Optional[Any]:
        return self._cache.get(key)

    def set(self, key: str, value: Any) -> None:
        self._cache[key] = value

    def invalidate(self, pattern: str) -> None:
        """按前缀失效缓存键"""
        keys = [k for k in self._cache if k.startswith(pattern.rstrip("*"))]
        for k in keys:
            del self._cache[k]


# 全局单例
cache = MemoryCache()
