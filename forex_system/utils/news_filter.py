"""
News Filter
───────────
Loads high-impact Forex news events so the engine can enforce
the 30-minute blackout window (Step 11).

Two modes:
  1. CSV file  — news_events.csv with columns: datetime_utc, currency, impact, event
  2. Stub list — hardcoded recurring high-impact event keywords for fallback

To integrate a live feed, implement `fetch_forexfactory()` below.
"""

from __future__ import annotations
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

NEWS_CSV = Path("config/news_events.csv")

HIGH_IMPACT_KEYWORDS = [
    "nfp", "non-farm", "cpi", "fomc", "interest rate",
    "rate decision", "ecb", "boe", "rba", "rbnz", "boc",
    "gdp", "unemployment", "payroll", "inflation",
]


def load_news_events(csv_path: Path = NEWS_CSV) -> List[datetime]:
    """
    Load news datetimes from CSV.
    Expected CSV columns: datetime_utc (ISO format), currency, impact, event
    Only HIGH impact rows are loaded.
    Returns list of naive UTC datetimes.
    """
    events: List[datetime] = []

    if not csv_path.exists():
        logger.info("No news CSV found at %s — using empty news list.", csv_path)
        return events

    try:
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("impact", "").strip().upper() != "HIGH":
                    continue
                try:
                    dt = datetime.fromisoformat(row["datetime_utc"].strip())
                    events.append(dt.replace(tzinfo=None))  # store as naive UTC
                except Exception:
                    continue
    except Exception as e:
        logger.warning("Could not load news CSV: %s", e)

    logger.info("Loaded %d high-impact news events.", len(events))
    return events


def is_high_impact_keyword(event_name: str) -> bool:
    name_lower = event_name.lower()
    return any(kw in name_lower for kw in HIGH_IMPACT_KEYWORDS)


# ─── Example CSV creator (for testing) ────────────────────────────

def create_sample_news_csv(path: Path = NEWS_CSV):
    """Write a sample news_events.csv for testing the blackout filter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"datetime_utc": "2024-12-06 13:30:00", "currency": "USD", "impact": "HIGH", "event": "Non-Farm Payrolls"},
        {"datetime_utc": "2024-12-11 13:30:00", "currency": "USD", "impact": "HIGH", "event": "CPI m/m"},
        {"datetime_utc": "2024-12-18 19:00:00", "currency": "USD", "impact": "HIGH", "event": "FOMC Rate Decision"},
        {"datetime_utc": "2024-12-12 13:15:00", "currency": "EUR", "impact": "HIGH", "event": "ECB Rate Decision"},
        {"datetime_utc": "2024-12-19 12:00:00", "currency": "GBP", "impact": "HIGH", "event": "BOE Rate Decision"},
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["datetime_utc", "currency", "impact", "event"])
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Sample news CSV written to %s", path)


if __name__ == "__main__":
    create_sample_news_csv()
    events = load_news_events()
    print(f"Loaded {len(events)} events")
    for e in events:
        print(" ", e)
