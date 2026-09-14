"""
BEAST v2 - Research Data Collector
research/data_collector.py

Background collector for historical candles, order flow metrics, and journal events.
Saves continuous market snapshots to disk for backtesting and feature analysis.
"""

import os
import json
import time
import logging
from typing import Dict, Any, List, Optional
from delta_client import delta_client

logger = logging.getLogger("beast_v2.research.data_collector")


class ResearchDataCollector:
    def __init__(self, data_dir: str = "data/historical"):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    def get_file_path(self, symbol: str, resolution: str) -> str:
        safe_symbol = symbol.replace("/", "_")
        return os.path.join(self.data_dir, f"{safe_symbol}_{resolution}.json")

    def collect_candles(self, symbol: str, resolution: str = "15m", count: int = 200) -> List[Dict[str, Any]]:
        """
        Fetch candles from Delta and append or merge with local disk storage.
        """
        candles = delta_client.get_candles(symbol, resolution=resolution, count=count)
        if not candles:
            return self.load_candles(symbol, resolution)

        file_path = self.get_file_path(symbol, resolution)
        existing = self.load_candles(symbol, resolution)

        # Merge by timestamp to maintain continuous sequence without duplicates
        candle_map = {c.get("time"): c for c in existing if c.get("time")}
        for c in candles:
            t = c.get("time")
            if t:
                candle_map[t] = c

        merged = sorted(candle_map.values(), key=lambda x: x.get("time", 0))

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving candles to {file_path}: {e}")

        return merged

    def load_candles(self, symbol: str, resolution: str = "15m") -> List[Dict[str, Any]]:
        file_path = self.get_file_path(symbol, resolution)
        if not os.path.exists(file_path):
            return []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading candles from {file_path}: {e}")
            return []


data_collector = ResearchDataCollector()
