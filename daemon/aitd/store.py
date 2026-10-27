"""Small persistent LRU cache so repeated explanations/suggestions are instant."""
import json
import os
from collections import OrderedDict

CACHE_DIR = os.path.expanduser("~/.cache/ai-terminal")


class DiskCache:
    def __init__(self, name: str, max_items: int = 500):
        self.path = os.path.join(CACHE_DIR, f"{name}.json")
        self.max_items = max_items
        self._data: OrderedDict[str, object] = OrderedDict()
        try:
            with open(self.path) as f:
                self._data.update(json.load(f))
        except (OSError, ValueError):
            pass

    def get(self, key: str):
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def put(self, key: str, value) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.max_items:
            self._data.popitem(last=False)
        try:
            os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._data, f)
            os.replace(tmp, self.path)
        except OSError:
            pass  # caching is an optimisation; never fail a request over it
