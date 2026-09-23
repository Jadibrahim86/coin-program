"""Volym-radar (bevakning, EJ råd) — hittar möjliga KÖPLÄGEN.

  🟢 Vänder UPP + volym   → coinet har börjat röra sig upp och volym bekräftar (AVAX-mönstret)

Två mönster klassas fortfarande och LOGGAS för utvärdering men skickas inte längre
(se SEND_SECTIONS):

  🟡 Faller + volym       → volym medan det fortfarande faller (fallande kniv)
  🔴 Säljvolym efter uppgång → coinet steg, vänder ner med volym

Varför de tystades 2026-08-17: användaren går bara long och shortar aldrig, så ett
coin som faller är bara intressant om han redan äger det — och då är det
`exit_watch.py` som ska larma, inte radarn. 🔴 på ett innehav dubblerade dessutom
exit-vaktens eget säljvolym-larm. Kvar i radarn: köplägen, inget annat.

Innehav filtreras bort ur 🟢 — ett coin du redan äger är inget nytt köpläge
(ZEC flaggades 4/4 medan användaren låg +4.6% i den och nästan köpte igen).

Ärligt: även 🟢 missar mer än den träffar, och är MÄTT NEGATIV mot BTC
(−0.5 till −1.5% på 48h, n=26 i veckorapporten 2026-08-16). Strålkastare att
GRANSKA SJÄLV, inte autoköp. Dedup hindrar upprepning inom DEDUP_HOURS.
"""
import math
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np

import alerts
import config
import db
import features
from live_signals import MIN_BARS, _is_stale, _last_closed_idx

# ---- Trösklar (känslighet — skruva här) ----
VOL_SPIKE = 6.0          # × snittvolym (höjd 5→6 vid 28-coin-universumet, håller larmvolymen nere)
TURN_UP = 0.015          # kort-momentum (6h) ≥ +1.5% = "vänder upp" (6h-fönster = tidigare upptäckt)
TURN_DN = -0.015         # kort-momentum (6h) ≤ -1.5% = "vänder ner"
LATE_24H = 0.05          # 24h-rörelse > +5% ⇒ "⚠️ sent i rörelsen" på 🟢-flaggor
TREND_MOVE = 0.04        # ±4% över 5 dygn = "har fallit / har stigit"
OVEREXTENDED = 0.20      # hoppa 🟢 om redan upp >20% på 5d (för sent)
FUNDING_EXTREME = 0.0003
FUNDING_MAX_AGE_H = 6
DEDUP_HOURS = 8

# Vilka mönster som SKICKAS. Alla tre klassas och loggas fortfarande till
# radar_alerts så veckorapporten kan fortsätta mäta dem — men bara dessa går ut
# till telefonen. Lägg tillbaka "falling"/"distribution" här för att få dem igen.
SEND_SECTIONS = ("turning_up",)

# --- Marknadsregim (filtrerar 🟢) --------------------------------------------
# Mätning 2026-07-25: 🟢-flaggor gav -1.4%/24h i en platt vecka. Uppdelat på
# marknadens efficiency ratio (trend vs chop) blev det -0.03% i trendande läge
# mot -2.5% i chop. Utbrottssignaler i hackig marknad = köpa toppen av en wiggle.
# OBS: kalibrerat på FÅ observationer — tröskeln hålls trubbig med flit och
# regimen skrivs alltid ut i meddelandet så vi kan fortsätta mäta.
REGIME_REF = "BTC"          # marknadens taktpinne
REGIME_WINDOW = 24          # timmar bakåt för efficiency/förändring
CHOP_MAX = 0.20             # efficiency under detta = chop → inga 🟢
MARKET_DOWN = -0.02         # BTC 24h under detta = risk-off → inga 🟢

# --- Open interest (konfluens) -----------------------------------------------
# OI läst TILLSAMMANS med priset skiljer äkta nya pengar från kulisser:
#   pris upp + OI upp  = nya positioner öppnas → rörelsen har bränsle
#   pris upp + OI ner  = shorts som täcker → ihålig rusning, rinner ofta ut
#   pris ner + OI upp  = nya shorts pressar → nedtrycket har kraft
#   pris ner + OI ner  = longs likvideras → kan närma sig utbottning
# Tröskeln ±2% är satt från fördelningen av 12060 mätta 24h-förändringar
# (kvartiler ±3%) → ~30% "stiger", ~34% "faller", ~36% neutralt.
# EJ VALIDERAD som edge än — visas som markering, mäts på kommande trades.
OI_WINDOW_H = 24
OI_THRESHOLD = 0.02
# OI_STRONG (✅✅ vid ≥7%) ÄR BORTTAGET 2026-08-30. Det infördes på n=4 med
# antagandet att storleken betyder mer än tecknet. Med n=35 gick det åt andra
# hållet: flaggor med OI ≥ +7% gav +0.4% mot +2.2% för övriga — alltså 1.8
# procentenheter SÄMRE. En markering som pekar fel är värre än ingen markering.
VOL_STRONG = 9.0            # höjd 8→9 2026-08-30: ≥9× gav +2.0% mot +0.9% under (n=93)

# --- Mätt trendstyrka (den starkaste enskilda faktorn vi hittat) --------------
# eff ≥ 0.55 gav +3.9% mot BTC och 74% positiva; under det -1.1% och 34%.
# Skillnaden (5.1 procentenheter) är större än alla andra kriterier tillsammans.
STRONG_TREND = 0.55

# --- Halva stjärnor (2026-09-23) ---------------------------------------------
# Användaren: "9× ger grön bock, 8.9× ger kryss och en hel stjärna försvinner".
# En hård gräns säger att 8.9 och 9.0 är olika saker. Halva stjärnor ger en
# mellanzon — och den är lagd där mätningen (172 flaggor) visar en trappa:
#
#   TREND   eff 0.40–0.55 → ½.  Uppmätt gradient: sidled -1.5%, mellan +0.8%,
#           stark +3.9% (2026-08-30). Den tydligaste trappan vi har.
#   OI      +2–4% → ½, ≥ +4% → hel. Slog BTC 36–40% av gångerna under +4%,
#           58–61% över. Steget ligger vid 4%, inte vid 2% där hela stjärnan
#           satt förut. OBS: OI har bytt tecken mellan mätningar tidigare.
#   VOLYM   7–9× → ½, ≥ 9× → hel. INGEN uppmätt trappa: 6–7× slog BTC 55%,
#           9–12× 53%. Halvstjärnan här tar bara bort klippkanten. Den börjar
#           vid 7× och inte 6× eftersom 6× är kravet för att flaggan ska gå
#           alls — en halvstjärna från 6× hade getts till varenda flagga.
#   SLÄPAR  ingen halv. Mätt som ren uppdelning vid noll (+4.4% mot -0.4/-1.0%).
TREND_HALF = 0.40
OI_STAR_HALF, OI_STAR_FULL = 0.02, 0.04
VOL_HALF = 7.0

# --- Flaggans egen träffhistorik (visas i utskicket) -------------------------
TRACK_DAYS = 45             # hur långt bak vi räknar
TRACK_HORIZON_H = 48        # samma horisont som veckorapporten
TRACK_MIN_N = 8             # under detta skriver vi inget — för tunt att uttala sig om

BARS_PER_DAY = {"5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}

SECTIONS = {
    "turning_up": ("🟢", "<b>Vänder upp + volym</b> (start på rörelse?)",
                   # "mönstret som funkade (AVAX)" stod här från juni 2026 till
                   # 2026-08-30. Det var fel på två sätt: AVAX-traden gick -5.2%,
                   # och raden om tidigare utfall står numera direkt ovanför och
                   # kan säga -0.5% i samma andetag. Ett utskick får inte
                   # motsäga sin egen mätning.
                   "volym bekräftar att något händer i coinet — men flaggan missar oftare än den träffar. Granska chart själv."),
    "falling": ("🟡", "<b>Faller + volym</b> (botten? ofta KNIV – var försiktig)",
                "volym medan det fortfarande faller — fångar ofta fallande knivar. Vänta hellre på vändning än att fånga."),
    "distribution": ("🔴", "<b>Säljvolym efter uppgång</b> (möjlig distribution – topp?)",
                     "säljare kliver in efter en uppgång — överväg att säkra vinst."),
}


def _snapshot(conn, coin, cid: int, tf: str):
    bpd = BARS_PER_DAY[tf]
    df = db.load_ohlcv_df(conn, cid, tf)
    if len(df) < max(MIN_BARS, 5 * bpd + 5) or _is_stale(df, tf):
        return None
    i = _last_closed_idx(df, tf)
    c = float(df["close"].iloc[i])
    vol_base = df["volume"].rolling(2 * bpd).mean().iloc[i]
    return {
        "cid": cid, "sym": coin.symbol, "price": c,
        "vol_ratio": float(df["volume"].iloc[i] / vol_base) if vol_base else 0.0,
        "mom_short": float(c / df["close"].iloc[i - 6] - 1),    # ~6h: vänder upp/ner? (tidig)
        "mom24": float(c / df["close"].iloc[i - bpd] - 1),      # 24h: hur sen är du?
        "mom5": float(c / df["close"].iloc[i - 5 * bpd] - 1),   # 5d kontext
        "oi_chg": db.oi_change(conn, cid, OI_WINDOW_H),         # derivat-konfluens
    }


def sort_key(s: dict) -> tuple:
    """Ordning i utskicket: bäst först. Volym sist eftersom den är svagast.

    Sorterades tidigare bara på volym, vilket är det minst betydelsefulla av de
    mätta kriterierna (+1.1 procentenheter mot trendstyrkans +5.1). Den 3 sep
    hamnade LINK först och AVAX tredje, fastän AVAX var det enda coinet som
    uppfyllde allt. Det översta coinet är det som läses.

    Relativ styrka var tiebreaker från 3 sep och blev stjärna 13 sep: coins som
    SLÄPADE efter BTC vid flaggan har gett +4.4% mot BTC efteråt (69% positiva,
    n=39), de som ledde -0.4% till -1.0% (n=61).

    Ordningen är: (1) hela stjärnbetyget, (2) släpar efter BTC, (3) volym.
    Trend utelämnas ur (1) — den är samma för alla coins i ett utskick. Fram
    till 2026-09-23 var första nyckeln bara volymstjärnan, vilket kunde ställa
    ett coin med tre stjärnor under ett med två. Listan ska stå i den ordning
    stjärnorna säger. Släpandet kommer ändå före volym vid lika betyg, eftersom
    det är den starkare av de två (+4.9 mot +1.1 procentenheter).

    Steg 2 är BINÄRT på tecknet, inte på råvärdet. Mätningen är en uppdelning
    vid noll (släpar +4.4% mot ledande -0.4 till -1.0%) utan glidande skala
    inom grupperna. Att sortera på råtalet hade låtsats om precision vi inte har.
    """
    rs = s.get("rs_btc")
    poang = sum(p for p, _ in stjarnor_for(s, None))
    return (-poang,                                     # betyget först
            0 if (rs is not None and rs < 0) else 1,    # sedan: släpar före leder
            -s["vol_ratio"])                            # sist: starkast volym


def rs_line(s: dict) -> str:
    """24h-raden med BTC:s egen rörelse utskriven.

    Stod tidigare som "24h +6% · vs BTC +1%", vilket kräver huvudräkning för att
    ens se om BTC gått upp eller ner — och sa ingenting om att det är SLÄPANDE
    coins som historiskt gått bäst. BTC-talet härleds ur samma tal som
    differensen (mom24 - rs), så raden alltid går ihop aritmetiskt.
    """
    mom, rs = s["mom24"], s.get("rs_btc")
    if rs is None:
        return f"      <i>Rörelse senaste dygnet: {mom*100:+.0f}%</i>"
    # Domen ("släpar/leder") står numera på stjärnraden ovanför — här bara
    # råsiffrorna, annars sägs samma sak två gånger i samma block.
    return (f"      <i>Senaste dygnet: coinet {mom*100:+.0f}% · "
            f"BTC {(mom-rs)*100:+.0f}%</i>")


def bucket_of(eff, rs) -> str:
    """Vilken grupp en flagga tillhör — de TVÅ STARKASTE faktorerna.

    Grupperade tidigare på trendstyrka × volym. Bytt 2026-09-13 till
    trendstyrka × "har inte rusat i förväg", eftersom de är de två som
    separerar mest (+5.1 respektive +4.9 procentenheter, mot volymens +1.1).
    Historiken räknas om från loggen varje körning, så inget mätvärde går
    förlorat av bytet — bara grupperingen ändras.
    """
    stark = eff is not None and eff >= STRONG_TREND
    slapar = rs is not None and rs < 0
    return f"{'stark' if stark else 'svag'}/{'slapar' if slapar else 'leder'}"


def flag_track_record(conn, days: int = TRACK_DAYS) -> dict:
    """{grupp: (snitt_överavkastning, andel_positiva, n)} ur egna loggade flaggor.

    Bara flaggor äldre än horisonten tas med, så fönstret hunnit stängas —
    annars mäter vi på halva utfall och lurar oss själva.
    """
    import re
    from datetime import timedelta
    rows = db.load_flag_outcomes(conn, days, flag_types=("turning_up",))
    ids = db.load_coin_ids(conn)
    btc_id = ids.get("BTC")
    if not btc_id:
        return {}
    cache, grupper = {}, {}
    grans = datetime.now(timezone.utc) - timedelta(hours=TRACK_HORIZON_H + 2)

    def ret(cid, sym, t0):
        if sym not in cache:
            cache[sym] = db.load_ohlcv_df(conn, cid, "1h")
        df = cache[sym]
        if df.empty:
            return None
        t1 = t0 + timedelta(hours=TRACK_HORIZON_H)
        i0 = df.index.get_indexer([t0], method="nearest")[0]
        i1 = df.index.get_indexer([t1], method="nearest")[0]
        if abs((df.index[i1] - t1).total_seconds()) > 3 * 3600:
            return None
        return float(df["close"].iloc[i1] / df["close"].iloc[i0] - 1)

    def btc_mom24(t0):
        """BTC:s egen 24h-rörelse vid flaggan — behövs för att återskapa 'släpar'."""
        if "BTC" not in cache:
            cache["BTC"] = db.load_ohlcv_df(conn, btc_id, "1h")
        df = cache["BTC"]
        if df.empty:
            return None
        i = df.index.get_indexer([t0], method="nearest")[0]
        return None if i < 24 else float(df["close"].iloc[i] / df["close"].iloc[i - 24] - 1)

    for sym, cid, ft, ts, meta in rows:
        if ts > grans:
            continue
        r, m = ret(cid, sym, ts), ret(btc_id, "BTC", ts)
        if r is None or m is None:
            continue
        meta = meta or {}
        eff = re.search(r"eff (\d\.\d+)", meta.get("regime", "") or "")
        # rs loggas inte i meta — härleds ur coinets mom24 minus BTC:s vid samma tid.
        mom, bm = meta.get("mom24"), btc_mom24(ts)
        rs = (mom - bm) if (mom is not None and bm is not None) else None
        grupper.setdefault(
            bucket_of(float(eff.group(1)) if eff else None, rs), []
        ).append(r - m)

    return {k: (sum(v) / len(v), sum(1 for x in v if x > 0) / len(v), len(v))
            for k, v in grupper.items()}


def track_line(track: dict, eff, rs) -> str | None:
    """Hur just den här sortens läge faktiskt har gått — utan jargong.

    Skrev tidigare "+4.3% mot BTC på 48h, 82% positiva (n=22)". Användaren
    förstod varken "n=22" eller procenttalen i det sammanhanget, så det är
    omskrivet till antal gånger av tio och hur många fall det bygger på.
    """
    st = track.get(bucket_of(eff, rs))
    if not st or st[2] < TRACK_MIN_N:
        return None
    snitt, andel, n = st
    dom = "✅" if snitt > 0.01 else ("⚠️" if snitt < -0.005 else "➖")
    return (f"      {dom} <b>Såna här lägen förr:</b> slog marknaden {av_tio(andel)}"
            f" · i snitt {snitt*100:+.1f}% mot BTC · bygger på {n} tidigare fall")


def trend_ord(eff) -> str:
    """Trendstyrkan i klartext. "eff 0.22" säger ingenting för en människa."""
    if eff is None:
        return "Marknadsläget är okänt"
    if eff >= STRONG_TREND:
        return "Marknaden går tydligt åt ett håll"
    if eff >= TREND_HALF:        # samma gräns som halvstjärnan, annars säger
        return "Marknaden rör sig, men ryckigt"   # texten emot betyget
    return "Marknaden vandrar mest i sidled"


def av_tio(andel: float) -> str:
    """0.63 -> "6 gånger av 10". Procent och n= är jargong; det här är inte det."""
    return f"{round(andel * 10)} gånger av 10"


def slapar_hitrate(track: dict) -> float | None:
    """Andel av SLÄPANDE flaggor som slog marknaden — räknad ur loggen.

    Får inte hårdkodas i fotnoten. "Mönstret som funkade (AVAX)" stod kvar i två
    månader efter att AVAX-traden gått -5.2%; en siffra som inte räknas om
    hinner bli fel utan att någon märker det.
    """
    tot = [(a, n) for k, (_, a, n) in track.items() if k.endswith("/slapar")]
    n_tot = sum(n for _, n in tot)
    return sum(a * n for a, n in tot) / n_tot if n_tot else None


def _ned(v: float) -> float:
    """Avrunda NEDÅT till en decimal. 8.96 får inte stå som "9.0" bredvid en
    halvstjärna som säger "hel från 9×" — det var exakt den motsägelsen
    användaren reagerade på. Gränserna har en decimal, så nedrundat tal når
    gränsen om och endast om det verkliga gör det."""
    return math.floor(v * 10 + 1e-9) / 10


def _x(v: float) -> str:
    """Volymkvot med decimal nära stjärngränserna, annars motsäger texten betyget."""
    nara = min(abs(v - VOL_STRONG), abs(v - VOL_HALF)) < 1
    return f"{_ned(v):.1f}×" if nara else f"{v:.0f}×"


def stjarnor_for(s: dict, eff) -> list:
    """[(poäng 0 / 0.5 / 1, text)] för de fyra tecknen. Se halvstjärne-blocket.

    Samma funktion används av betyget (confluence) och ordningen (sort_key), så
    listan i utskicket alltid står i samma ordning som stjärnorna säger.
    """
    rs, oi, vol = s.get("rs_btc"), s.get("oi_chg"), s["vol_ratio"]
    ut = []

    if eff is None:
        ut.append((0, "Marknadsläget är okänt"))
    elif eff >= STRONG_TREND:
        ut.append((1, trend_ord(eff)))
    elif eff >= TREND_HALF:
        ut.append((0.5, f"{trend_ord(eff)} (hel stjärna när den går tydligt åt ett håll)"))
    else:
        ut.append((0, trend_ord(eff)))

    if rs is None:
        ut.append((0, "Går inte att jämföra med marknaden"))
    elif rs < 0:
        ut.append((1, f"Har inte rusat i förväg — släpar {abs(rs)*100:.1f}% efter marknaden"))
    else:
        ut.append((0, f"Har redan rusat före marknaden — ligger {rs*100:.1f}% före"))

    if vol >= VOL_STRONG:
        ut.append((1, f"Volymen ovanligt hög — {_x(vol)} det normala"))
    elif vol >= VOL_HALF:
        ut.append((0.5, f"Volymen förhöjd — {_x(vol)} det normala "
                        f"(hel stjärna från {VOL_STRONG:.0f}×)"))
    else:
        ut.append((0, f"Volymen {_x(vol)} det normala "
                      f"(halv stjärna från {VOL_HALF:.0f}×)"))

    if oi is None:
        ut.append((0, "Derivatdata saknas för det här coinet"))
    elif oi >= OI_STAR_FULL:
        ut.append((1, f"Nya pengar i derivaten — OI {_pct(oi)}"))
    elif oi >= OI_STAR_HALF:
        ut.append((0.5, f"Lite nya pengar i derivaten — OI {_pct(oi)} "
                        f"(hel stjärna från +{OI_STAR_FULL*100:.0f}%)"))
    else:
        ut.append((0, f"Inga nya pengar i derivaten — OI {_pct(oi)}"))
    return ut


def _pct(v: float) -> str:
    """En decimal under 10% — "-0%" och "+2%" för 1.7% har lurat oss förut.
    Nedrundad, så +1.97% inte står som "+2.0%" utan stjärna (se _ned)."""
    return f"{_ned(v * 100):+.1f}%" if abs(v) < 0.10 else f"{v*100:+.0f}%"


def stjarnrad(n: float, av: int) -> str:
    """2.5 av 4 -> "⭐⭐½☆". Det finns ingen halvstjärne-emoji som syns överallt."""
    hela = int(n)
    halv = (n - hela) >= 0.5
    return "⭐" * hela + ("½" if halv else "") + "☆" * (av - hela - (1 if halv else 0))


def betyg_txt(n: float, av: int) -> str:
    """2.5 -> "2½/4", 0.5 -> "½/4"."""
    hela = int(n)
    if (n - hela) < 0.5:
        return f"{hela}/{av}"
    return f"{hela or ''}½/{av}"


def confluence(s: dict, regime: dict, track: dict, visade: set | None = None) -> tuple:
    """(stjärnor, antal, rader) — fyra kriterier, alla i klartext.

    Historik: fyra stjärnor till 2026-08-30, sedan två (OI och "5d positiv" var
    dekoration), nu fyra igen 2026-09-13. Det är inte en cirkel — de två nya är
    inte de gamla två:

      Marknaden går åt ett håll   +5.1 pp   starkast av alla, mätt tre gånger
      Har inte rusat i förväg     +4.9 pp   ny; "släpar efter BTC"
      Volymen ovanligt hög        +1.1 pp   svag men stabil
      Nya pengar i derivaten (OI) +1.4 pp   SVAGAST och har bytt tecken

    Om OI: den mättes inverterad på n=93 och n=46, men med n=127 pekar den åt
    förväntat håll (slog BTC 6 ggr av 10 vid OI ≥ +2%, mot 4 av 10 under).
    Att en effekt vänder när data läggs till betyder att den är svag. Den är med
    för att användaren uttryckligen bad om den och för att riktningen nu stämmer
    — men den är märkt som svagast i fotnoten, och ska kollas om igen.

    Ingen siffra som bara en kvant förstår får stå här. "eff 0.22" och "n=30"
    var obegripliga för användaren; det är hans verktyg, så de är översatta.
    """
    eff, rs = regime.get("eff"), s.get("rs_btc")
    delar = stjarnor_for(s, eff)
    n = sum(p for p, _ in delar)
    stars = stjarnrad(n, len(delar))
    rows = [f"      {'✅' if p == 1 else '◐' if p == 0.5 else '❌'} {txt}" for p, txt in delar]

    # Historikraden är samma för alla coins i samma grupp — skriv den en gång
    # per utskick i stället för att upprepa identisk text sex gånger.
    grupp = bucket_of(eff, rs)
    if visade is None or grupp not in visade:
        tl = track_line(track, eff, rs)
        if tl:
            rows.append(tl)
            if visade is not None:
                visade.add(grupp)
    return stars, n, rows


def oi_label(kind: str, oi) -> tuple:
    """(markering, kort_text) för ett mönster givet OI-förändringen. Se OI_-kommentaren."""
    if oi is None:
        return "", "OI saknas"
    # Avrundningen fick texten att motsäga sig själv: 1.7% skrevs "+2%" och
    # underkändes i samma rad ("OI +2% — ingen bekräftelse"), och 0.001 blev "-0%".
    # En decimal under 10% och ord för det som ligger still.
    if abs(oi) < 0.005:
        pct = "oförändrad"
    elif abs(oi) < 0.10:
        pct = f"{oi*100:+.1f}%"
    else:
        pct = f"{oi*100:+.0f}%"
    if kind == "turning_up":
        if oi >= OI_THRESHOLD:
            return "✅", f"OI {pct} — nya pengar in"
        if oi <= -OI_THRESHOLD:
            return "⚠️", f"OI {pct} — mest short-covering"
        return "➖", f"OI {pct} — ingen bekräftelse"
    if kind == "falling":
        if oi >= OI_THRESHOLD:
            return "⚠️", f"OI {pct} — nya shorts pressar"
        if oi <= -OI_THRESHOLD:
            return "👀", f"OI {pct} — longs likvideras, kan bottna"
        return "➖", f"OI {pct}"
    if kind == "distribution":
        if oi >= OI_THRESHOLD:
            return "⚠️", f"OI {pct} — nya shorts kliver in"
        if oi <= -OI_THRESHOLD:
            return "➖", f"OI {pct} — longs stänger"
        return "➖", f"OI {pct}"
    return "", f"OI {pct}"


def classify(s: dict) -> str | None:
    if s["vol_ratio"] < VOL_SPIKE:
        return None
    if s["mom_short"] >= TURN_UP and s["mom5"] < OVEREXTENDED:
        return "turning_up"
    if s["mom5"] <= -TREND_MOVE and s["mom_short"] <= 0:
        return "falling"
    if s["mom5"] >= TREND_MOVE and s["mom_short"] <= TURN_DN:
        return "distribution"
    return None


def market_regime(conn, coin_ids: dict) -> dict:
    """Marknadens läge just nu: trendande, chop eller risk-off.

    Returnerar {'label', 'allow_long', 'eff', 'chg'} — allow_long=False betyder
    att 🟢-flaggor tystas (de har historiskt failat i chop/nedgång).
    """
    cid = coin_ids.get(REGIME_REF)
    closes = db.load_recent_closes(conn, cid, "1h", REGIME_WINDOW + 1) if cid else None
    if not closes or len(closes) < REGIME_WINDOW:
        return {"label": "okänd", "allow_long": True, "eff": None, "chg": None}

    eff = features.market_efficiency(closes)
    chg = closes[-1] / closes[0] - 1
    if chg <= MARKET_DOWN:
        return {"label": f"risk-off ({REGIME_REF} {chg*100:+.1f}% 24h)", "allow_long": False, "eff": eff, "chg": chg}
    if eff is not None and eff < CHOP_MAX:
        return {"label": f"hackig/chop (eff {eff:.2f})", "allow_long": False, "eff": eff, "chg": chg}
    # Etiketten skiljer nu på stark och svag trend. Rubriken sa tidigare
    # "trendande" vid eff 0.46 medan kriteriet nedanför underkände samma siffra
    # för att den är under 0.55 — två rader i samma utskick som sa emot varandra.
    # OBS: bara texten, allow_long är oförändrat (CHOP_MAX styr fortfarande).
    stark = eff >= STRONG_TREND
    ord_ = "stark trend" if stark else "svag trend"
    return {"label": f"{ord_} (eff {eff:.2f}, {REGIME_REF} {chg*100:+.1f}%)",
            "allow_long": True, "eff": eff, "chg": chg}


# --- Marknadsläge: spelar coinvalet roll just nu? ----------------------------
# Två oberoende saker, båda kalibrerade på 54 dygns egen historik:
#   korrelation  — hur mycket coinsen rör sig ihop (median 0.26, p75 0.39, p90 0.59)
#   tvärsnittsvol — hur stora skillnaderna är samma timme (median 0.56%)
# Hög korrelation = alla gör samma sak, då är det bara insatsens STORLEK som är
# ett beslut. Låg korrelation + stor spridning = coinvalet avgör faktiskt utfallet.
# EJ MÄTT mot utfall — det här beskriver läget, det förutsäger inte riktning.
MODE_CORR_HIGH = 0.50
MODE_CORR_LOW = 0.30
MODE_XVOL_HIGH = 0.0056     # medianen


def market_mode(conn) -> str | None:
    """En rad överst som säger vilken sorts marknad du fattar beslut i."""
    import stress
    panel = stress.load_panel(conn, hours=120)
    if panel.empty or len(panel) < stress.CORR_WINDOW + 6:
        return None
    r = panel.pct_change().dropna()
    c = r.tail(stress.CORR_WINDOW).corr().values
    iu = np.triu_indices_from(c, k=1)
    korr = float(np.nanmean(c[iu]))
    xvol = float(r.tail(6).std(axis=1).mean())

    # Siffrorna står kvar sist i raden för den som vill ha dem, men orden först:
    # "korrelation 0.51" betydde ingenting för användaren.
    if korr >= MODE_CORR_HIGH:
        return ("🔗 <b>Coinen rör sig nästan likadant just nu</b> — vilket du väljer "
                "spelar mindre roll än hur mycket du satsar.")
    if korr <= MODE_CORR_LOW and xvol >= MODE_XVOL_HIGH:
        return ("🎯 <b>Coinen går sina egna vägar just nu</b> — här kan valet av coin "
                "faktiskt avgöra utfallet.")
    return "<i>Coinen rör sig delvis ihop — mittemellan de vanliga lägena.</i>"


def _funding_map(conn) -> dict:
    """{symbol: funding} för FÄRSKA och EXTREMA värden.

    Det egna funding-utskicket är borta (2026-08-17): en lista på coins med
    extrem funding gav inget för någon som bara köper och säljer spot, och INJ
    låg kroniskt extrem så den pingade i praktiken varje dygn. Funding visas i
    stället på det 🟢-coin man faktiskt överväger, där den kan betyda något.
    """
    out, now = {}, datetime.now(timezone.utc)
    for sym, funding, ts in db.load_latest_funding(conn):
        if funding is None or ts is None:
            continue
        if (now - ts).total_seconds() / 3600 <= FUNDING_MAX_AGE_H and abs(funding) >= FUNDING_EXTREME:
            out[sym] = float(funding)
    return out


def funding_line(funding) -> str | None:
    """Förklarar extrem funding för en KÖPARE. EJ MÄTT som edge — ren kontext."""
    if funding is None:
        return None
    pct = f"{funding*100:+.3f}%"
    if funding < 0:
        return (f"      💰 Funding {pct} — shortarna betalar longarna, ovanligt "
                f"många ligger kort. Vänder det upp kan de tvingas köpa tillbaka. "
                f"<i>(ej mätt)</i>")
    return (f"      💰 Funding {pct} — longarna betalar shortarna, trängd "
            f"köpsida. Sen i rörelsen snarare än tidig. <i>(ej mätt)</i>")


def run(conn, timeframe: str = "1h", send: bool = True) -> None:
    coin_ids = db.load_coin_ids(conn)
    btc = _snapshot(conn, SimpleNamespace(symbol="BTC"), coin_ids["BTC"], timeframe) \
        if "BTC" in coin_ids else None
    buckets = {"turning_up": [], "falling": [], "distribution": []}
    for coin in config.UNIVERSE:
        cid = coin_ids.get(coin.symbol)
        if cid is None:
            continue
        s = _snapshot(conn, coin, cid, timeframe)
        if not s:
            continue
        # Relativ styrka: UNI:s verkliga tell var att den steg MEDAN BTC föll.
        s["rs_btc"] = (s["mom24"] - btc["mom24"]) if btc and coin.symbol != "BTC" else None
        kind = classify(s)
        if kind:
            buckets[kind].append(s)

    # Marknadsfilter: 🟢 tystas i chop/risk-off (se REGIME-kommentaren ovan).
    regime = market_regime(conn, coin_ids)
    suppressed = 0
    if not regime["allow_long"]:
        suppressed = len(buckets["turning_up"])
        buckets["turning_up"] = []

    # Innehav är inga köplägen — exit_watch sköter dem. Utan detta flaggades ZEC
    # 4/4 som "start på rörelse" medan användaren redan låg +4.6% i den.
    held = {h["coin_id"] for h in db.load_open_holdings(conn)}
    owned = [s["sym"] for s in buckets["turning_up"] if s["cid"] in held]
    buckets["turning_up"] = [s for s in buckets["turning_up"] if s["cid"] not in held]

    recent = db.recent_radar_alerts(conn, DEDUP_HOURS)
    for k in buckets:
        buckets[k] = sorted((s for s in buckets[k] if (s["cid"], k) not in recent),
                            key=sort_key)

    sent = {k: v for k, v in buckets.items() if k in SEND_SECTIONS and v}
    if not sent:
        extra = f" ({suppressed} 🟢 tystade — {regime['label']})" if suppressed else ""
        if owned:
            extra += f" ({', '.join(owned)} flaggade men ägs redan)"
        print(f"Inget nytt över trösklarna — inget skickat.{extra}")
        _log_flags(conn, buckets, regime, send)
        return

    funding = _funding_map(conn)
    track = flag_track_record(conn)

    # Rubriken i klartext. regime["label"] behåller "eff 0.60" eftersom den
    # texten LOGGAS till radar_alerts.meta och parsas tillbaka av
    # flag_track_record() — ändra aldrig formatet där, bara visningen här.
    btc_txt = f"BTC {regime['chg']*100:+.1f}% senaste dygnet" if regime.get("chg") is not None else ""
    L = [f"📡 <b>VOLYM-RADAR</b> ({timeframe}, bevakning – ej råd) — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
         f"<i>{trend_ord(regime.get('eff'))} · {btc_txt}</i>"]
    mode = market_mode(conn)
    if mode:
        L.append(mode)
    if suppressed:
        L.append(f"<i>({suppressed} köp-flagga(or) tystad — köpsignaler har historiskt "
                 f"failat i det här marknadsläget)</i>")
    visade_grupper: set = set()
    for k in SEND_SECTIONS:
        if k not in sent:
            continue
        icon, title, note = SECTIONS[k]
        L.append(f"\n{icon} {title}:")
        for s in sent[k][:6]:
            rs = s.get("rs_btc")
            rs_txt = f" · vs BTC {rs*100:+.0f}%" if rs is not None else ""
            if k == "turning_up":
                stars, n, rows = confluence(s, regime, track, visade_grupper)
                L.append(f"  • <b>{s['sym']}</b> ~{s['price']:g} — {stars} {betyg_txt(n, 4)}")
                L.extend(rows)
                L.append(rs_line(s))
                fl = funding_line(funding.get(s["sym"]))
                if fl:
                    L.append(fl)
            else:
                mark, oitxt = oi_label(k, s["oi_chg"])
                L.append(f"  • {mark} <b>{s['sym']}</b> ~{s['price']:g}: {s['vol_ratio']:.1f}× volym, "
                         f"6h {s['mom_short']*100:+.0f}%, 24h {s['mom24']*100:+.0f}%, "
                         f"5d {s['mom5']*100:+.0f}%{rs_txt}\n"
                         f"      {oitxt}")
        L.append(f"  <i>↳ {note}</i>")
        if k == "turning_up":
            # Kort med flit: förklaringen står på varje utskick, så den får inte
            # vara längre än innehållet. Historiken bakom (varför fyra stjärnor
            # blev två) hör hemma i CLAUDE.md, inte i din telefon varje timme.
            L.append("  <i>↳ ⭐ = fyra tecken, mätta mot systemets egna tidigare "
                     "flaggor; ◐ = halv stjärna, nära gränsen. Starkast är att "
                     "marknaden går åt ett håll, näst starkast att coinet inte redan "
                     "rusat. Volym och OI väger lättare — volym har ingen uppmätt "
                     "skillnad alls just nu, och OI har bytt riktning mellan "
                     "mätningar. Läs dem som ledtrådar, inte besked.</i>")
            # Tolkningen är kontraintuitiv nog att behöva stå utskriven: att
            # SLÄPA är det gynnsamma läget. Ingen gissar det av sig själv.
            hr = slapar_hitrate(track)
            if hr is not None and any(s.get("rs_btc") is not None for s in sent[k][:6]):
                L.append(f"  <i>↳ Att ett coin <b>släpar efter</b> marknaden är bra, inte "
                         f"dåligt: såna har slagit marknaden {av_tio(hr)}, medan de som "
                         f"redan rusat gått sämre. Därför ger det en hel stjärna.</i>")
    if owned:
        L.append(f"\n<i>({', '.join(owned)} flaggades också men du äger dem redan — "
                 f"de bevakas av exit-vakten.)</i>")
    L.append("\n<i>Strålkastare att granska själv — inte köp/sälj. Fler missar än träffar; din bedömning avgör.</i>")
    text = "\n".join(L)

    print(text)
    if send:
        alerts.send(text)
        print("\n[skickat]")
    _log_flags(conn, buckets, regime, send)


def _log_flags(conn, buckets: dict, regime: dict, send: bool) -> None:
    """Loggar ALLA klassade mönster till radar_alerts — även de som inte skickas.

    Dedupen och veckorapporten läser härifrån. 🟡/🔴 loggas fortfarande så att
    mätserien inte bryts den dag vi vill utvärdera dem igen, men de går inte ut.
    """
    if not send:
        return
    db.record_radar_alerts(
        conn,
        [(s["cid"], k, {"price": s["price"], "vol_ratio": round(s["vol_ratio"], 1),
                        "oi_chg": s["oi_chg"], "mom24": s["mom24"],
                        "regime": regime["label"], "sent": k in SEND_SECTIONS})
         for k, rows in buckets.items() for s in rows],
    )
