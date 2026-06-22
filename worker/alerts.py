"""Telegram-alerts — skickar topp-rankade AKTUELLA signaler till din mobil, var du än är.

Kräver TELEGRAM_BOT_TOKEN och TELEGRAM_CHAT_ID i .env (skapa bot via @BotFather).

⚠️ EJ VALIDERAD: varje alert märks tydligt tills strategin klarat walk-forward.
"""
import os

import requests

import db  # importerar config → laddar .env

API = "https://api.telegram.org/bot{token}/{method}"
MIN_CONFIDENCE = 50.0
TOP_N = 10


def _token() -> str:
    t = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not t:
        raise RuntimeError("TELEGRAM_BOT_TOKEN saknas i .env")
    return t


def get_chat_ids() -> list:
    """Skriv ett meddelande till boten, kör detta → få ditt chat_id."""
    r = requests.get(API.format(token=_token(), method="getUpdates"), timeout=30)
    r.raise_for_status()
    out = []
    for upd in r.json().get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat", {})
        if chat.get("id"):
            out.append((chat["id"], chat.get("first_name") or chat.get("title") or "?"))
    return out


def send(text: str) -> dict:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID saknas i .env")
    r = requests.post(
        API.format(token=_token(), method="sendMessage"),
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def latest_signals(conn, min_conf: float = MIN_CONFIDENCE, top_n: int = TOP_N) -> list:
    """Senaste signal-batchen (max created_at) över confidence-tröskeln."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.symbol, s.direction, s.confidence, s.entry, s.stop, s.tp, s.rr
            FROM signals s JOIN coins c ON c.id = s.coin_id
            WHERE s.created_at = (SELECT max(created_at) FROM signals)
              AND s.confidence >= %s
            ORDER BY s.confidence DESC
            LIMIT %s
            """,
            (min_conf, top_n),
        )
        return cur.fetchall()


def format_message(rows: list) -> str | None:
    if not rows:
        return None
    lines = ["⚠️ <b>EJ VALIDERAD</b> — preliminära signaler\n"]
    for sym, direction, conf, entry, stop, tp, rr in rows:
        arrow = "🟢 LONG" if direction == "long" else "🔴 SHORT"
        tp0 = float(tp[0]) if tp else 0.0
        lines.append(
            f"<b>{sym}</b> {arrow}  conf {float(conf):.0f}\n"
            f"  entry {float(entry):g} · stop {float(stop):g} · tp {tp0:g} · RR {float(rr):g}"
        )
    lines.append("\n<i>Agera inte på dessa än — strategin är inte validerad.</i>")
    return "\n".join(lines)


def run(conn, min_conf: float = MIN_CONFIDENCE, top_n: int = TOP_N) -> int:
    rows = latest_signals(conn, min_conf, top_n)
    msg = format_message(rows)
    if msg is None:
        print("Inga signaler över tröskeln — inget skickat.")
        return 0
    send(msg)
    print(f"Skickade {len(rows)} signaler till Telegram.")
    return len(rows)
