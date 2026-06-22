"""Postgres-anslutning + idempotenta upsert-hjälpare (ON CONFLICT)."""
import json

import psycopg2
from psycopg2.extras import execute_values

import config


def get_conn():
    if not config.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL saknas — kopiera .env.example till .env och fyll i den."
        )
    return psycopg2.connect(config.DATABASE_URL)


def load_coin_ids(conn) -> dict[str, int]:
    """{symbol: id} för alla coins i databasen."""
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, id FROM coins")
        return {sym: cid for sym, cid in cur.fetchall()}


def upsert_coin(conn, coin) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO coins (symbol, name, sector, coingecko_id, renamed_from, active)
            VALUES (%s, %s, %s, %s, %s, true)
            ON CONFLICT (symbol) DO UPDATE SET
                name = EXCLUDED.name,
                sector = EXCLUDED.sector,
                coingecko_id = EXCLUDED.coingecko_id,
                renamed_from = EXCLUDED.renamed_from,
                updated_at = now()
            """,
            (coin.symbol, coin.name, coin.sector, coin.coingecko_id, coin.renamed_from),
        )
    conn.commit()


def get_last_ohlcv_ts(conn, coin_id: int, timeframe: str):
    """Senaste lagrade bar-tid för inkrementell hämtning (None om tom)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(ts) FROM ohlcv WHERE coin_id = %s AND timeframe = %s",
            (coin_id, timeframe),
        )
        return cur.fetchone()[0]


def upsert_ohlcv(conn, rows) -> int:
    """rows: (coin_id, timeframe, ts, open, high, low, close, volume, gap_flag, source)."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO ohlcv
                (coin_id, timeframe, ts, open, high, low, close, volume, gap_flag, source)
            VALUES %s
            ON CONFLICT (coin_id, timeframe, ts) DO UPDATE SET
                open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                close = EXCLUDED.close, volume = EXCLUDED.volume,
                gap_flag = EXCLUDED.gap_flag, source = EXCLUDED.source
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def upsert_derivatives(conn, rows) -> int:
    """rows: (coin_id, ts, open_interest, funding_rate, long_short_ratio, breakdown_dict)."""
    if not rows:
        return 0
    payload = [
        (cid, ts, oi, fr, lsr, json.dumps(breakdown))
        for (cid, ts, oi, fr, lsr, breakdown) in rows
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO derivatives
                (coin_id, ts, open_interest, funding_rate, long_short_ratio, oi_breakdown)
            VALUES %s
            ON CONFLICT (coin_id, ts) DO UPDATE SET
                open_interest = EXCLUDED.open_interest,
                funding_rate = EXCLUDED.funding_rate,
                long_short_ratio = EXCLUDED.long_short_ratio,
                oi_breakdown = EXCLUDED.oi_breakdown
            """,
            payload,
        )
    conn.commit()
    return len(rows)


def insert_universe_snapshot(conn, rows) -> int:
    """rows: (coin_id, as_of_date, qualified, market_cap_usd, volume_24h_usd, reason_dict)."""
    if not rows:
        return 0
    payload = [
        (cid, d, q, mc, vol, json.dumps(reason))
        for (cid, d, q, mc, vol, reason) in rows
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO universe_membership
                (coin_id, as_of_date, qualified, market_cap_usd, volume_24h_usd, reason)
            VALUES %s
            ON CONFLICT (coin_id, as_of_date) DO UPDATE SET
                qualified = EXCLUDED.qualified,
                market_cap_usd = EXCLUDED.market_cap_usd,
                volume_24h_usd = EXCLUDED.volume_24h_usd,
                reason = EXCLUDED.reason
            """,
            payload,
        )
    conn.commit()
    return len(rows)


def load_ohlcv_df(conn, coin_id: int, timeframe: str):
    """OHLCV som pandas-DataFrame indexerad på ts (UTC), float-kolumner."""
    import pandas as pd

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ts, open, high, low, close, volume
            FROM ohlcv WHERE coin_id = %s AND timeframe = %s ORDER BY ts
            """,
            (coin_id, timeframe),
        )
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.set_index("ts")
    return df.astype(float)


def upsert_features(conn, rows) -> int:
    """rows: (coin_id, timeframe, ts, values_dict, regime)."""
    if not rows:
        return 0
    payload = [(cid, tf, ts, json.dumps(vals), regime) for (cid, tf, ts, vals, regime) in rows]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO features (coin_id, timeframe, ts, values, regime)
            VALUES %s
            ON CONFLICT (coin_id, timeframe, ts) DO UPDATE SET
                values = EXCLUDED.values, regime = EXCLUDED.regime
            """,
            payload,
        )
    conn.commit()
    return len(rows)


def insert_signals(conn, rows) -> int:
    """rows: (coin_id, ts, direction, composite_score, entry, stop, tp_list, rr,
    confidence, triggers_dict, regime)."""
    if not rows:
        return 0
    payload = [
        (cid, ts, d, cs, e, s, tp, rr, cf, json.dumps(tr), rg)
        for (cid, ts, d, cs, e, s, tp, rr, cf, tr, rg) in rows
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO signals
                (coin_id, ts, direction, composite_score, entry, stop, tp, rr,
                 confidence, triggers, regime)
            VALUES %s
            """,
            payload,
        )
    conn.commit()
    return len(rows)


def load_open_recommendations(conn) -> list:
    """Öppna long-rekommendationer = signals utan stängande signal_outcome."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.coin_id, c.symbol, s.entry, s.stop, s.tp, s.ts, s.confidence
            FROM signals s
            JOIN coins c ON c.id = s.coin_id
            LEFT JOIN signal_outcomes o ON o.signal_id = s.id
            WHERE s.direction = 'long' AND o.signal_id IS NULL
            ORDER BY s.ts
            """
        )
        out = []
        for sid, cid, sym, entry, stop, tp, ts, conf in cur.fetchall():
            out.append({
                "id": sid, "coin_id": cid, "symbol": sym,
                "entry": float(entry), "stop": float(stop),
                "tp": float(tp[0]) if tp else None, "ts": ts,
                "conf": float(conf) if conf is not None else None,
            })
        return out


def load_bars_since(conn, coin_id: int, timeframe: str, ts) -> list:
    """(ts, high, low) för alla bars EFTER given tidpunkt — för exit-koll."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ts, high, low FROM ohlcv
            WHERE coin_id = %s AND timeframe = %s AND ts > %s ORDER BY ts
            """,
            (coin_id, timeframe, ts),
        )
        return [(t, float(h), float(l)) for (t, h, l) in cur.fetchall()]


def insert_outcome(conn, signal_id: int, outcome: str, realized_rr, closed_at) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO signal_outcomes (signal_id, outcome, realized_rr, closed_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (signal_id) DO NOTHING
            """,
            (signal_id, outcome, realized_rr, closed_at),
        )
    conn.commit()


def _json_safe(obj):
    """Ersätt icke-finita floats (inf/nan) med None — Postgres jsonb tål dem inte."""
    import math

    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def save_backtest_run(conn, params, period_start, period_end, metrics, baseline_metrics) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO backtest_runs
                (params, period_start, period_end, metrics, baseline_metrics)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                json.dumps(_json_safe(params)),
                period_start,
                period_end,
                json.dumps(_json_safe(metrics)),
                json.dumps(_json_safe(baseline_metrics)),
            ),
        )
    conn.commit()
