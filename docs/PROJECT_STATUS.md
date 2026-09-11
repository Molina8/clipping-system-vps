# Clipping Project Status — 2026-09-11 04:36 UTC

## ✅ Cerrado este turno (todo verificado)

### Pipeline completo operativo
- **A+B (analyze + qa_rules)** + cron cada 10 min: 20+ campañas a ready
- **Discovery Whop** + cron cada 6h: 50 campañas parseadas, 20+ upserted
- **Endpoints REST** funcionando: /health=200, /discovery/providers=200, /discovery/run=200
- **Postgres schema** migrado (Alembic 0008_whop) — check constraint incluye whop
- **Parser de detail refinado** + tests unitarios pasando (5 tests OK)

### Módulo nuevo
- app/services/discovery/ (8 archivos, syntax OK)
- ~/.openclaw/workspace/skills/campaign-discovery/ (5 docs, 23 KB)

### Umbrales de scoring (centralizados en config.Settings)
- cpm_min_usd_per_1k: 0.50
- prize_pool_min_usd: 5000.0
- fetch_detail=True por defecto

### Assets en BD: 157 totales
- whop: 131 (62 nuevos + 69 retageados)
- youtube: 25
- manual: 1

## 🛠️ Trabajo que hago esta noche

1. Tests unitarios del parser refinado (5 tests, OK)
2. Backfill de nombres limpios cuando corra el cron (cada 6h)
3. clip_scanner_quiet.sh verificado (1093 bytes, root:root, ejecuta clip_scanner.py)

## 🌅 Lo que hay que hacer al despertar

### Worker Windows (tú)
1. Encender PC
2. Iniciar Worker con tu sesión de Whop activa
3. Bajar .zip real de cada campaña en ready
4. Subir a BD como Asset con asset_type=video
5. Disparar pipeline: download → transcribe → render → qa

### Live test
- Pegar API_TOKEN para probar flujo A+B contra campaña real
- O dar URL de un vídeo de prueba en /opt/clipping-system/inbox/

### Diagnóstico rápido
- curl http://100.109.27.21:8080/health
- sudo systemctl status clipping-api.service
- sudo journalctl -u clipping-api.service -n 50
- PGPASSWORD=*** psql -h 127.0.0.1 -U clipping_api -d clipping
