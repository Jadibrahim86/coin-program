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
import features
import scout

API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 50
SLEEPY_VOL = 0.02        # < 2%/dag = trögt för swing

# Default-stop skalas mot coinets dagsrörelse i stället för fasta -7%. Efter en
# vecka med verklig data låg ALLA förluster på exakt -7% (stoppen låg i brusfältet)
# medan vinsterna kapades vid +2%. Ett coin som rör sig 3%/dag behöver mer luft.
STOP_VOL_MULT = 2.5
STOP_MIN, STOP_MAX = 0.05, 0.15
PRICE_DEVIATION_MAX = 0.03   # >3% från marknaden = be om bekräftelse (fångar typos)


def _daily_vol(conn, coin_id: int):
    """Dagsvolatilitet (andel, t.ex. 0.045 = 4.5%/dag) från senaste ~10 dygnens 1h-data."""
    return features.daily_vol(db.load_recent_closes(conn, coin_id, "1h", 240))


def _suggest_stop(entry: float, vol) -> tuple:
    """(stop_pris, stop_andel) — volatilitetsanpassad default-stop."""
    pct = STOP_MIN if vol is None else min(max(STOP_VOL_MULT * vol, STOP_MIN), STOP_MAX)
    return entry * (1 - pct), pct


def _vol_advice(vol, entry: float, stop: float) -> str:
    """Tydligt budskap: passar stoppen coinets dagsrörelse? (Tumregel: stop >= 2× dagsvol.)"""
    if vol is None:
        return ""
    stop_pct = 1 - stop / entry
    line = f"\n📊 Rör sig ~{vol*100:.1f}%/dag · stop ligger {stop_pct/vol:.1f}× dagsrörelsen bort."
    if stop_pct < 2 * vol:
        rec_stop, rec_pct = _suggest_stop(entry, vol)
        line += (
            f"\n⚠️ <b>Snävare än 2× dagsrörelsen</b> — risk att brus stoppar ut dig. "
            f"Överväg stop ~{rec_stop:g} (-{rec_pct*100:.0f}%) och <b>mindre position</b> "
            f"så kronorna du riskerar blir desamma."
        )
    elif vol < SLEEPY_VOL:
        line += " 😴 Trög för swing — rörelser tar ofta veckor här."
    else:
        line += " ✅ Rimligt utrymme för coinets normala rörelser."
    return line


def _kr(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def _parse_extras(parts: list) -> tuple:
    """Plockar ut '1000kr' (insats) och 'risk20'/'20%' ur kommandot.

    Returnerar (kvarvarande_parts, belopp, risk_andel). Bare tal lämnas kvar så
    tredje positionen fortfarande betyder stop-pris som förut.
    """
    rest, amount, risk = [], None, None
    for p in parts:
        low = p.lower().replace(",", ".")
        if low.endswith("kr"):
            try:
                amount = float(low[:-2]); continue
            except ValueError:
                pass
        if low.startswith("risk") or low.endswith("%"):
            try:
                risk = float(low.removeprefix("risk").rstrip("%")) / 100; continue
            except ValueError:
                pass
        rest.append(p)
    return rest, amount, risk


def _risk_line(entry: float, stop: float, amount, chosen_risk) -> str:
    """Vad den här traden faktiskt riskerar — i kronor, inte bara procent."""
    stop_dist = 1 - stop / entry
    if amount is None:
        return ("\n💡 Lägg till <code>1000kr</code> i kommandot så räknar jag ut vad du "
                "riskerar i kronor (och visar P/L i kr framöver).")
    risk_kr = amount * stop_dist
    src = " (din gräns)" if chosen_risk else " (volanpassad)"
    return (f"\n💰 Insats {_kr(amount)} kr · stoppen ligger {stop_dist*100:.1f}% bort{src}\n"
            f"   → du riskerar <b>{_kr(risk_kr)} kr</b> om den träffas")


def _correlation_warning(conn, coin_id: int, symbol: str) -> str:
    """Varnar om det nya coinet i praktiken är samma bet som det du redan äger."""
    holdings = [h for h in db.load_open_holdings(conn) if h["coin_id"] != coin_id]
    if not holdings:
        return ""
    new = db.load_recent_closes(conn, coin_id, "1h", 336)
    if len(new) < 100:
        return ""
    import numpy as np
    new_r = np.diff(np.array(new)) / np.array(new[:-1])
    corrs = []
    for h in holdings:
        c = db.load_recent_closes(conn, h["coin_id"], "1h", 336)
        n = min(len(c), len(new) )
        if n < 100:
            continue
        r = np.diff(np.array(c[-n:])) / np.array(c[-n:][:-1])
        m = min(len(r), len(new_r))
        if m >= 100:
            corrs.append((h["symbol"], float(np.corrcoef(r[-m:], new_r[-m:])[0, 1])))
    if not corrs:
        return ""
    avg = sum(c for _, c in corrs) / len(corrs)
    if avg < 0.6:
        return ""
    worst = max(corrs, key=lambda x: x[1])
    return (f"\n🔗 Rör sig nästan likadant som dina nuvarande innehav "
            f"(snittkorrelation {avg:.2f}, mest med {worst[0]} {worst[1]:.2f}).\n"
            f"   Det blir {len(holdings)+1} positioner men i praktiken ETT bet — "
            f"de faller ihop när marknaden vänder.")


def _price_check(conn, coin_id: int, price: float, verb: str, cmd_hint: str) -> str | None:
    """Varning om priset avviker kraftigt från marknaden (typo-skydd). None = ok."""
    market = db.get_last_close(conn, coin_id)
    if not market:
        return None
    dev = price / market - 1
    if abs(dev) <= PRICE_DEVIATION_MAX:
        return None
    return (
        f"⚠️ <b>Kollar en gång till:</b> du angav {price:g} för {verb}, men marknaden "
        f"står i ~{market:g} ({dev*100:+.0f}%).\n"
        f"Skrev du fel? Rätta annars siffran — eller lägg till <b>!</b> sist för att "
        f"registrera ändå:\n<code>{cmd_hint} !</code>"
    )


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
    force = "!" in parts          # "!" var som helst = hoppa över priskontrollen
    parts = [p for p in parts if p != "!"]
    cmd = parts[0].lower().split("@")[0]  # tål /buy@botnamn
    coin_ids = db.load_coin_ids(conn)

    if cmd in ("/start", "/help"):
        watched = sorted(c.symbol for c in config.UNIVERSE)
        return (
            "<b>Kommandon:</b>\n"
            "<code>/buy WLD 0.34</code> — bevaka, volanpassad stop\n"
            "<code>/buy WLD 0.34 1000kr</code> — med insats → P/L i kronor\n"
            "<code>/buy WLD 0.34 1000kr risk20</code> — stop sätts på −20%\n"
            "<code>/buy WLD 0.34 0.30</code> — egen stop-kurs\n"
            "<code>/sell WLD 0.36</code> — stäng bevakning\n"
            "<code>/positions</code> (/innehav) — innehav med P/L\n"
            "<code>/bevaka XRP</code> — följ ett coin du INTE äger\n"
            "<code>/sluta XRP</code> · <code>/bevakning</code> — sluta följa / se listan\n"
            "<i>Lägg till ! sist för att kringgå priskontrollen.</i>\n\n"
            f"Bevakar {len(watched)} coins: {' '.join(watched)}\n"
            "<i>Jag kollar dina innehav varje timme och larmar vid stop, vikande topp "
            "eller säljvolym — plus marknadslarm när allt rör sig ihop, och "
            "veckorapport på söndagar.</i>"
        )

    if cmd in ("/kapital", "/capital"):
        return ("Det kommandot finns inte längre — du anger insatsen per trade i stället:\n"
                "<code>/buy WLD 0.34 1000kr</code> (eller lägg till <code>risk20</code> "
                "för egen stop-gräns).")

    if cmd in ("/bevaka", "/watch"):
        if len(parts) < 2:
            return ("Skriv: /bevaka SYMBOL — t.ex. <code>/bevaka XRP</code>\n"
                    "Då hör jag av mig när något ändras i coinet: riktning, "
                    "handel, derivat eller läge. Du behöver inte äga den.")
        sym = parts[1].upper()
        cid = coin_ids.get(sym)
        if cid is None:
            return f"Känner inte till {sym}. Coins: {' '.join(sorted(coin_ids))}"
        if db.get_watch(conn, cid):
            return f"{sym} bevakas redan — <code>/sluta {sym}</code> för att sluta."
        pris = db.get_last_close(conn, cid)
        db.add_watch(conn, cid, pris)
        import watchlist
        return (f"👁 Bevakar <b>{sym}</b>{f' från {pris:g}' if pris else ''}.\n"
                f"Jag hör av mig när riktningen, handeln, derivaten eller läget "
                f"ändras — som mest var {watchlist.MIN_TIMMAR_MELLAN}:e timme.\n"
                f"<i>Obs: jag kan säga vad som HAR hänt, inte vart det ska. De "
                f"fyra vanliga bottentecknen är mätta på 43 000 timmar och inget "
                f"av dem förutsade något.</i>")

    if cmd in ("/sluta", "/unwatch"):
        if len(parts) < 2:
            return "Skriv: /sluta SYMBOL — t.ex. /sluta XRP"
        sym = parts[1].upper()
        cid = coin_ids.get(sym)
        wid = db.get_watch(conn, cid) if cid else None
        if not wid:
            return f"{sym} bevakas inte just nu."
        db.stop_watch(conn, wid)
        return f"👁 Slutar bevaka <b>{sym}</b>."

    if cmd in ("/bevakning", "/bevakade"):
        lista = db.load_watchlist(conn)
        if not lista:
            return "Inga bevakade coins. Lägg till med t.ex. /bevaka XRP"
        rader = ["<b>Du bevakar:</b>"]
        for w in lista:
            pris = db.get_last_close(conn, w["coin_id"])
            f = ""
            if pris and w["start_price"]:
                f = f" · {(pris/w['start_price']-1)*100:+.1f}% sedan start"
            st = w["last_state"] or {}
            lage = " · ".join(x for x in (st.get("riktning"), st.get("volym")) if x)
            rader.append(f"• <b>{w['symbol']}</b> {pris:g}{f}"
                         + (f"\n  <i>{lage}</i>" if lage else ""))
        return "\n".join(rader)

    if cmd in ("/positions", "/pos", "/innehav"):
        holdings = db.load_open_holdings(conn)
        if not holdings:
            return "Inga bevakade innehav. Lägg till med t.ex. /buy SOL 82"
        lines, total_kr, total_in = ["<b>Dina innehav:</b>"], 0.0, 0.0
        for h in holdings:
            price = db.get_last_close(conn, h["coin_id"])
            pl = f" · nu {price:g} ({_fmt_pl(h['entry'], price)})" if price else ""
            kr = ""
            if price and h["amount"]:
                gain = h["amount"] * (price / h["entry"] - 1)
                total_kr += gain; total_in += h["amount"]
                kr = f" <b>{gain:+,.0f} kr</b>".replace(",", " ")
            stop = f" · stop {h['stop']:g}" if h["stop"] else ""
            vol = _daily_vol(conn, h["coin_id"])
            vs = f" · ~{vol*100:.0f}%/d" if vol else ""
            alarm = " 🚨 UNDER STOP — överväg sälj!" if (price and h["stop"] and price <= h["stop"]) else ""
            lines.append(f"• <b>{h['symbol']}</b> köpt {h['entry']:g}{pl}{kr}{stop}{vs}{alarm}")
        if total_in:
            lines.append(f"\n<b>Totalt:</b> {_kr(total_in)} kr insatt · "
                         f"{total_kr:+,.0f} kr ({total_kr/total_in*100:+.1f}%)".replace(",", " "))
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
        vol = _daily_vol(conn, cid)
        parts, amount, chosen_risk = _parse_extras(parts)
        try:
            entry = _num(parts[2])
            if chosen_risk:                       # "risk20" → stoppen härleds ur din gräns
                stop = entry * (1 - chosen_risk)
            elif len(parts) > 3:
                stop = _num(parts[3])
            else:
                stop = _suggest_stop(entry, vol)[0]
        except (ValueError, IndexError):
            return ("Kunde inte tolka. Exempel:\n"
                    "<code>/buy WLD 0.34</code>\n"
                    "<code>/buy WLD 0.34 1000kr</code>\n"
                    "<code>/buy WLD 0.34 1000kr risk20</code>")
        if stop >= entry:
            return f"Stoppen ({stop:g}) måste ligga UNDER köpkursen ({entry:g})."
        if not force:
            warn = _price_check(conn, cid, entry, "köp", " ".join(parts))
            if warn:
                return warn
        corr_warn = _correlation_warning(conn, cid, sym)
        db.insert_holding(conn, cid, entry, stop, amount)
        advice = _vol_advice(vol, entry, stop)
        oi = db.oi_change(conn, cid, 24)
        if oi is not None:
            mark, oitxt = scout.oi_label("turning_up", oi)
            advice += f"\n{mark} {oitxt} (senaste dygnet)"
        advice += _risk_line(entry, stop, amount, chosen_risk) + corr_warn
        return (
            f"✅ Bevakar <b>{sym}</b> från {entry:g}.\n"
            f"Stop: {stop:g} ({(stop/entry-1)*100:+.1f}%)"
            f"{advice}\n"
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
        if not force and len(parts) > 2:
            warn = _price_check(conn, cid, price, "sälj", " ".join(parts))
            if warn:
                return warn
        db.close_holding(conn, h["id"], price)
        pl = f" — resultat {_fmt_pl(h['entry'], price)} ({h['entry']:g} → {price:g})" if price else ""
        if price and h["amount"]:
            gain = h["amount"] * (price / h["entry"] - 1)
            pl += f", <b>{gain:+,.0f} kr</b> på {_kr(h['amount'])} kr insats".replace(",", " ")
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
