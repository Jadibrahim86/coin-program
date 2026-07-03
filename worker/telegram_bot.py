"""Telegram-kommandolyssnare (daemon, körs som systemd-tjänst på VPS:en).

Du berättar vad du köpt — boten bevakar det och exit_watch.py (timvis) larmar
när det är läge att sälja.

Kommandon (skriv i boten):
    /buy SOL 82        → bevaka SOL köpt på 82 (stop default -7%)
    /buy SOL 82 78     → samma, med egen stop på 78
    /sell SOL 85       → stäng bevakningen (85 = din säljkurs; kan utelämnas)
    /positions         → visa innehav med P/L
    /help              → hjälp

Säkerhet: lyssnar BARA på TELEGRAM_CHAT_ID — andra ignoreras.
"""
import time

import requests

import config
import db

API = "https://api.telegram.org/bot{token}/{method}"
DEFAULT_STOP_PCT = 0.07  # stop = entry -7% om ingen anges
POLL_TIMEOUT = 50


def _tg(method: str, **params):
    import os
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = requests.post(API.format(token=token, method=method), json=params, timeout=POLL_TIMEOUT + 10)
    r.raise_for_status()
    return r.json()


def _send(chat_id, text: str) -> None:
    _tg("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")


def _num(s: str) -> float:
    return float(s.replace(",", "."))  # tål svenskt decimalkomma


def _fmt_pl(entry: float, price: float) -> str:
    pl = price / entry - 1
    return f"{pl*100:+.1f}%"


def handle_command(conn, text: str) -> str:
    """Tolkar ett kommando → svarstext. Kastar inget; fel blir vänliga svar."""
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]  # tål /buy@botnamn
    coin_ids = db.load_coin_ids(conn)

    if cmd in ("/start", "/help"):
        return (
            "<b>Kommandon:</b>\n"
            "/buy SOL 82 — bevaka SOL köpt på 82 (stop -7%)\n"
            "/buy SOL 82 78 — med egen stop på 78\n"
            "/sell SOL 85 — stäng bevakning (säljkurs valfri)\n"
            "/positions — innehav med P/L\n\n"
            f"Coins: {' '.join(sorted(coin_ids))}\n"
            "<i>Jag kollar dina innehav varje timme och larmar vid stop, "
            "vikande topp eller säljvolym.</i>"
        )

    if cmd in ("/positions", "/pos"):
        holdings = db.load_open_holdings(conn)
        if not holdings:
            return "Inga bevakade innehav. Lägg till med t.ex. /buy SOL 82"
        lines = ["<b>Dina innehav:</b>"]
        for h in holdings:
            price = db.get_last_close(conn, h["coin_id"])
            pl = f" · nu {price:g} ({_fmt_pl(h['entry'], price)})" if price else ""
            stop = f" · stop {h['stop']:g}" if h["stop"] else ""
            lines.append(f"• <b>{h['symbol']}</b> köpt {h['entry']:g}{pl}{stop}")
        return "\n".join(lines)

    if cmd == "/buy":
        if len(parts) < 3:
            return "Skriv: /buy SYMBOL PRIS — t.ex. /buy SOL 82"
        sym = parts[1].upper()
        cid = coin_ids.get(sym)
        if cid is None:
            return f"Känner inte till {sym}. Coins: {' '.join(sorted(coin_ids))}"
        if db.get_open_holding(conn, cid):
            return f"{sym} bevakas redan — /sell {sym} först om du vill börja om."
        try:
            entry = _num(parts[2])
            stop = _num(parts[3]) if len(parts) > 3 else entry * (1 - DEFAULT_STOP_PCT)
        except ValueError:
            return "Kunde inte tolka priset. Skriv: /buy SOL 82 (eller /buy SOL 82 78)"
        if stop >= entry:
            return f"Stoppen ({stop:g}) måste ligga UNDER köpkursen ({entry:g})."
        db.insert_holding(conn, cid, entry, stop)
        return (
            f"✅ Bevakar <b>{sym}</b> från {entry:g}.\n"
            f"Stop: {stop:g} ({(stop/entry-1)*100:+.1f}%)\n"
            f"<i>Jag hör av mig när det är läge att säkra vinst eller om stoppen bryts. "
            f"Kollar varje timme.</i>"
        )

    if cmd == "/sell":
        if len(parts) < 2:
            return "Skriv: /sell SYMBOL — t.ex. /sell SOL (pris valfritt: /sell SOL 85)"
        sym = parts[1].upper()
        cid = coin_ids.get(sym)
        if cid is None:
            return f"Känner inte till {sym}."
        holdings = [h for h in db.load_open_holdings(conn) if h["coin_id"] == cid]
        if not holdings:
            return f"{sym} bevakas inte just nu."
        h = holdings[0]
        try:
            price = _num(parts[2]) if len(parts) > 2 else db.get_last_close(conn, cid)
        except ValueError:
            return "Kunde inte tolka priset."
        db.close_holding(conn, h["id"], price)
        pl = f" — resultat {_fmt_pl(h['entry'], price)} ({h['entry']:g} → {price:g})" if price else ""
        return f"🔚 Slutar bevaka <b>{sym}</b>{pl}."

    return "Okänt kommando. /help visar vad jag kan."


def main() -> None:
    import os
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id or not os.environ.get("TELEGRAM_BOT_TOKEN"):
        raise SystemExit("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID saknas i .env")

    conn = db.get_conn()
    db.ensure_exit_tables(conn)
    offset = int(db.get_bot_state(conn, "tg_offset", "0"))
    print(f"Bot igång (offset {offset}). Lyssnar på chat {chat_id}...")

    while True:
        try:
            resp = _tg("getUpdates", offset=offset, timeout=POLL_TIMEOUT)
            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                text = msg.get("text", "")
                from_chat = str(msg.get("chat", {}).get("id", ""))
                if from_chat == str(chat_id) and text.startswith("/"):
                    try:
                        reply = handle_command(conn, text)
                    except Exception as exc:
                        reply = f"Hoppsan, något gick fel: {exc}"
                    _send(chat_id, reply)
                db.set_bot_state(conn, "tg_offset", str(offset))
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print(f"fel: {exc} — återansluter om 10s")
            time.sleep(10)
            try:
                conn.close()
            except Exception:
                pass
            conn = db.get_conn()


if __name__ == "__main__":
    main()
