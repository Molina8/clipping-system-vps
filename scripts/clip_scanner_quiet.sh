#!/bin/sh
# Wrapper silencioso para clip_scanner.py — salida de 1 línea para el announce de Telegram.
OUT=$(mktemp); ERR=$(mktemp)
/opt/clipping-system/venv/bin/python /opt/clipping-system/scripts/clip_scanner.py --limit 10 --auto-approve 0.7 >"$OUT" 2>"$ERR"
CODE=$?
if [ $CODE -ne 0 ]; then
  echo "Clip Scanner FALLÓ (exit $CODE):"
  tail -5 "$ERR"
  rm -f "$OUT" "$ERR"
  exit $CODE
fi
/opt/clipping-system/venv/bin/python - "$OUT" <<'PYEOF'
import json, sys, re
txt = open(sys.argv[1]).read()
blocks = re.findall(r'\{[^{}]*\}', txt, re.S)
assets = gen = valid = pers = 0
persisted = []
for b in blocks:
    try: d = json.loads(b)
    except Exception: continue
    if 'generated' in d:
        assets += 1
        gen += d.get('generated', 0)
        valid += d.get('valid', 0)
        pers += d.get('persisted', 0)
        if d.get('persisted'): persisted.append(str(d.get('asset_id','?'))[:8])
line = f"🎬 Scan: {assets} assets → {gen} candidatos, {valid} válidos, {pers} aprobados"
if persisted: line += " | IDs: " + ", ".join(persisted)
print(line)
PYEOF
rm -f "$OUT" "$ERR"
