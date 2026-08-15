#!/usr/bin/env bash
# Stop-hook: paminner om att README.md / CLAUDE.md kan ha blivit inaktuella.
#
# Kor EN gang per session (sentinel-fil pa session_id), och bara nar riktiga
# kallfiler andrats utan att dokumentationen foljt med. Tyst i alla andra lagen —
# en hook som gnaller varje svar ar varre an ingen hook alls.
#
# "Foljt med" mats pa ANDRINGSTID, inte pa om filen ar ocommittad. Annars hade
# en README som legat ocommittad sedan forra veckan tystat hooken i alla
# sessioner darefter — just de sessioner dar ny kod behover ny dokumentation.
#
# Avaktivera: /hooks, eller ta bort Stop-blocket ur .claude/settings.json.
set -u

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir/../.." || exit 0

payload="$(cat)"
sid="$(printf '%s' "$payload" \
  | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
[ -n "$sid" ] || sid="nosession"

marker="${TMPDIR:-/tmp}/claude-docs-check-$sid"
[ -e "$marker" ] && exit 0

# -uall: annars kollapsar git nya kataloger till "worker/nymapp/" och en helt
# ny modul skulle missas. Sokvagar med blanksteg citeras av git -> quotes bort.
changed="$(git status --porcelain -uall 2>/dev/null)" || exit 0
[ -n "$changed" ] || exit 0

# git status --porcelain: 2 statustecken + blanksteg + sokvag
code="$(printf '%s\n' "$changed" | cut -c4- | sed 's/^"//; s/"$//' \
  | grep -E '^(worker/|db/)|\.(sh|service)$' || true)"
[ -n "$code" ] || exit 0

# Millisekunder som heltal (%.3Y ger "sek.mmm", punkten bort). Sekundupplosning
# racker inte: kod och dokumentation redigeras ofta inom samma sekund.
mtime() { stat -c %.3Y "$1" 2>/dev/null | tr -d . || echo 0; }

newest_code=0
while IFS= read -r f; do
    [ -n "$f" ] && [ -f "$f" ] || continue
    m="$(mtime "$f")"
    [ "$m" -gt "$newest_code" ] && newest_code="$m"
done <<< "$code"

newest_doc=0
for d in README.md CLAUDE.md; do
    [ -f "$d" ] || continue
    m="$(mtime "$d")"
    [ "$m" -gt "$newest_doc" ] && newest_doc="$m"
done

# Dokumentationen minst lika fars som senaste kodandringen => redan omhandertagen.
[ "$newest_doc" -ge "$newest_code" ] && exit 0

: > "$marker"

files="$(printf '%s\n' "$code" | tr '\n' ' ')"
cat <<EOF
{"decision":"block","reason":"Kallkod har andrats efter att dokumentationen senast rordes ($files). Ga igenom vad som faktiskt andrats och avgor om README.md eller CLAUDE.md blivit inaktuella: nya eller borttagna Telegram-kommandon, andrade trosklar och kalibreringar (skriv da vad den nya nivan bygger pa), ny fil-/modulstruktur, andrade korinstruktioner, nya jobb i run_pipeline.sh. Uppdatera bara det som verkligen blivit fel — om inget blivit inaktuellt sag det och stanna. Sammanfatta kort for anvandaren vad du andrade eller varfor inget behovdes. Det har ar en engangskoll per session."}
EOF
