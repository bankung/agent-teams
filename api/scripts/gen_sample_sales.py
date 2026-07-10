"""Generate the synthetic starter dataset for data-analytics projects (Kanban #1308).

Usage:
    python -m scripts.gen_sample_sales

Writes `src/templates/data-analytics/data/raw/sample_sales.csv`. The output IS
committed to the repo (this script documents/reproduces it; it is not run at
install time). Re-running regenerates a BYTE-IDENTICAL file: every random
choice is drawn from a single `random.Random(SEED)` instance seeded with a
fixed constant, rows are generated in a deterministic order, and `csv.writer`
is given an explicit fieldname order — no dict/set iteration leaks in.

Shape (locked by the #1308 spec):
  - header: order_id,customer_id,sku,category,price,qty,order_date,region
  - exactly 1000 data rows
  - ~200 unique customers (C0001..C0200), ~50 SKUs (SKU001..SKU050 — each
    pinned to one of the 5 categories, so category is consistent per SKU)
  - order_date spans a fixed ~6-month window (2026-01-01..2026-06-30)
  - price in [10, 2000] THB, qty normally in [1, 10]
  - 10-20 rows (1-2% of 1000) are intentional anomalies: qty > 50 OR
    price > 5000 — fodder for a starter "find anomalies" analysis task
  - no PII: every id is a synthetic sequential/random code, never a real name/
    email/phone/address
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 1308  # Kanban task id — arbitrary but fixed, documents provenance
N_ROWS = 1000
N_CUSTOMERS = 200
N_SKUS = 50
CATEGORIES = ("electronics", "clothing", "books", "home", "food")
REGIONS = ("bangkok", "chiang_mai", "phuket", "khon_kaen")
START_DATE = date(2026, 1, 1)
END_DATE = date(2026, 6, 30)  # ~6-month span
PRICE_MIN, PRICE_MAX = 10, 2000
ANOMALY_MIN, ANOMALY_MAX = 10, 20  # 1-2% of N_ROWS

FIELDNAMES = (
    "order_id",
    "customer_id",
    "sku",
    "category",
    "price",
    "qty",
    "order_date",
    "region",
)

OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "templates"
    / "data-analytics"
    / "data"
    / "raw"
    / "sample_sales.csv"
)


def _build_skus(rng: random.Random) -> list[tuple[str, str, int]]:
    """Return `[(sku, category, base_price)]` — each SKU pinned to one category
    (10 SKUs/category x 5 categories = 50), so a SKU's category never varies
    across rows the way a truly random per-row category would."""
    skus: list[tuple[str, str, int]] = []
    per_category = N_SKUS // len(CATEGORIES)
    idx = 1
    for category in CATEGORIES:
        for _ in range(per_category):
            base_price = rng.randint(PRICE_MIN, PRICE_MAX)
            skus.append((f"SKU{idx:03d}", category, base_price))
            idx += 1
    return skus


def _random_order_date(rng: random.Random) -> date:
    span_days = (END_DATE - START_DATE).days
    return START_DATE + timedelta(days=rng.randint(0, span_days))


def generate_rows() -> list[dict[str, object]]:
    """Deterministically build the 1000 data rows (base rows + anomaly pass)."""
    rng = random.Random(SEED)
    skus = _build_skus(rng)
    customers = [f"C{n:04d}" for n in range(1, N_CUSTOMERS + 1)]

    rows: list[dict[str, object]] = []
    for i in range(1, N_ROWS + 1):
        sku, category, base_price = rng.choice(skus)
        # +/-15% jitter around the SKU's base price, clamped back into contract range.
        price = round(base_price * rng.uniform(0.85, 1.15))
        price = max(PRICE_MIN, min(PRICE_MAX, price))
        rows.append(
            {
                "order_id": f"O{i:05d}",
                "customer_id": rng.choice(customers),
                "sku": sku,
                "category": category,
                "price": price,
                "qty": rng.randint(1, 10),
                "order_date": _random_order_date(rng).isoformat(),
                "region": rng.choice(REGIONS),
            }
        )

    # Intentional anomalies — qty > 50 OR price > 5000. Applied AFTER the base
    # rows (in a second deterministic pass off the same rng stream) so the
    # anomaly count/placement stays reproducible under the fixed seed. Normal
    # rows can never accidentally satisfy this (qty capped at 10, price capped
    # at PRICE_MAX=2000 above), so the anomaly count below is exact.
    n_anomalies = rng.randint(ANOMALY_MIN, ANOMALY_MAX)
    anomaly_indices = rng.sample(range(N_ROWS), n_anomalies)
    for idx in anomaly_indices:
        if rng.random() < 0.5:
            rows[idx]["qty"] = rng.randint(51, 120)
        else:
            rows[idx]["price"] = rng.randint(5001, 15000)

    return rows


def write_csv(rows: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(FIELDNAMES))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = generate_rows()
    write_csv(rows, OUTPUT_PATH)
    n_anomalies = sum(1 for r in rows if r["qty"] > 50 or r["price"] > 5000)
    dates = [date.fromisoformat(str(r["order_date"])) for r in rows]
    print(
        f"wrote {len(rows)} rows to {OUTPUT_PATH}\n"
        f"  anomalies: {n_anomalies}\n"
        f"  date span: {min(dates)} .. {max(dates)} ({(max(dates) - min(dates)).days} days)\n"
        f"  unique customers: {len({r['customer_id'] for r in rows})}\n"
        f"  unique skus: {len({r['sku'] for r in rows})}"
    )


if __name__ == "__main__":
    main()
