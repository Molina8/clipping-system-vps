# Flujo de arquitectura — fuente única de verdad

> **Documento de referencia.** Cualquier cambio sobre quién hace qué o el orden de pasos requiere tu autorización explícita y se modifica aquí.

---

## Flujo completo

```
1. [OPENCLAW — CRON: whop-discovery-cron]
   ↓
   Descubre campañas del Whop tenant (UPSERT mínimo)
   ↓
   Solo escribe: name, cpm_usd_per_1k, prize_pool_usd,
   source_url, source_instructions, source_provider='whop'
   ↓
   status='discovered' (pipeline v2, 2026-09-17)
   ↓
   NO crea assets. NO analiza. NO spec. NO rules.
   ↓ Llama a upsert_campaign(db, d, status='discovered')
   ↓
   ~~2. [DEPRECATED] campaign-prioritizer-tick~~
   ~~   Scoring 50/30/10/10 — jubilado 2026-09-17.~~
   ~~   Absorbido por 3c (campaign-scorer). Cron deshabilitado, no eliminado.~~
   ↓
3a. [OPENCLAW — CRON: brief-reader-tick]
   ↓
   Busca campaigns WHERE status='discovered'
   ↓
   Lee source_instructions + source_url y, con la skill 'brief-reader',
   extrae rules + asset_links (links crudos: Drive folders, YouTube, files).
   ↓
   Escribe:
     • campaigns.status = 'briefed' (o 'failed_brief')
     • campaigns.source_metadata.rules (jsonb)
     • campaigns.source_metadata.asset_links (jsonb)
     • Por cada Drive folder: 1 asset row con kind='drive_folder'
   ↓
   Una sola skill. Sin LLM extra. Sin Drive listing.
   ↓
3b. [OPENCLAW — CRON: drive-resolver-tick]
   ↓
   Busca campaigns WHERE status='briefed'
   ↓
   Para cada asset.kind='drive_folder', con la skill 'drive-resolver'
   (gog CLI autenticado), lista el contenido recursivamente (depth ≤ 4),
   filtra por extensiones .mp4/.mov/.mkv/.webm/.zip/.tar/.gz,
   deduplica por source_id (Drive file ID) y crea assets rows.
   ↓
   Escribe:
     • 1 asset row por archivo real (source_url = uc?export=download&id=…)
     • campaigns.status = 'assets_resolved' (o 'failed_resolve')
   ↓
   Una sola skill. Mecánica pura.
   ↓
3c. [OPENCLAW — CRON: campaign-scorer-tick]
   ↓
   Busca campaigns WHERE status='assets_resolved'
   ↓
   Con la skill 'campaign-scorer' (cálculo determinista puro):
     • Lee source_metadata.rules (puesto por 3a)
     • Cuenta assets reales (mp4/mov/mkv/webm)
     • Si count == 0 → status='blocked_no_assets', score=null. Stop.
     • Si rules falta → status='failed_scorer'. Stop.
     • Calcula: base_revenue + asset_availability + rule_completeness
                 − difficulty_penalty, clamp 0..100
     • priority = clamp(round(total/10), 1, 10)
     • tie_break = base_revenue
   ↓
   Escribe:
     • campaigns.status = 'scored'
     • campaigns.source_metadata.score = {total, breakdown, priority,
                                            tie_break, rank_reason}
   ↓
   Una sola skill. Sin LLM, sin red.
   ↓
4. [VPS — BACKEND]
   ↓
   Guarda campaña + CampaignSpec en PostgreSQL
   ↓
   ↑ Endpoint PATCH /campaigns/{id} (llamado por 3a/3b/3c)
   ↓
5. [VPS — BACKEND]
   ↓
   Asset Resolver (legacy — el nuevo pipeline no lo usa; 3b ya dejó los assets)
   ↓
   ↑ Nota pipeline v2 (2026-09-17): paso 5 se considera cubierto por 3b.
   ↑ El script whop_discovery.py ya NO llama a resolve_assets_for_campaign.
   ↓
6. [VPS — BACKEND]
   ↓
   Registra los vídeos encontrados en PostgreSQL (tabla assets)
   ↓
7. [VPS — BACKEND]
   ↓
   Crea DOWNLOAD JOBS para los vídeos (auto post-alta de assets)
   ↓
8. [WORKER WINDOWS]
   ↓
   Recoge DOWNLOAD JOB
   ↓
   Descarga el vídeo
   ↓
   Guarda vídeo en almacenamiento local del Worker
   ↓
   Devuelve resultado al VPS
   ↓
9. [VPS — BACKEND]
   ↓
   Marca el vídeo como DOWNLOADED
   ↓
   Crea TRANSCRIBE JOB
   ↓
10. [WORKER WINDOWS]
    ↓
    Recoge TRANSCRIBE JOB
    ↓
    Ejecuta WhisperX
    ↓
    Genera transcripción + timestamps
    ↓
    Devuelve resultado al VPS
    ↓
11. [VPS — BACKEND]
    ↓
    Guarda transcripción en PostgreSQL
    ↓
    Marca el vídeo como TRANSCRIBED
    ↓
12. [OPENCLAW — CRON: clip-decider-tick]
    ↓
    Revisa si hay transcripciones nuevas
    ↓
    ↑ GET /clip_selection/queue?priority_only=true&limit=10
    ↓ Fallback a la cola completa si priority_only está vacía
    ↓
13. [OPENCLAW — MINIMAX: clip-decider-tick]
    ↓
    Lee:
      • CampaignSpec (campaign.spec)
      • transcripción (con timestamps)
      • información del vídeo
    ↓
    Analiza el contenido
    ↓
    Decide qué partes son buenos clips
    ↓
    Genera CANDIDATOS
    ↓
14. [OPENCLAW — AGENTE (clip-decider-tick) + VPS BACKEND]
    ↓
    Guarda los candidatos en el VPS (POST /candidates)
    ↓
    Valida contra CampaignSpec vía LLM (gate duro: ⚠️ PENDIENTE en VPS)
    ↓
    Marca asset como 'clip_proposed'
    ↓
15. [VPS — BACKEND]
    ↓
    Crea RENDER JOBS para los candidatos aprobados
    ↓
16. [WORKER WINDOWS]
    ↓
    Recoge RENDER JOB
    ↓
    FFmpeg corta el fragmento indicado
    ↓
    Aplica:
      • formato 9:16
      • subtítulos
      • watermark
      • resolución
      • etc.
    ↓
    Genera CLIP FINAL
    ↓
    Devuelve resultado al VPS
    ↓
17. [VPS — BACKEND]
    ↓
    Registra clip generado
    ↓
    Crea QA JOB
    ↓
18. [WORKER WINDOWS]
    ↓
    Recoge QA JOB
    ↓
    FFprobe / validaciones técnicas
    ↓
    Comprueba:
      • duración
      • resolución
      • FPS
      • codec
      • audio
      • integridad
      • etc.
    ↓
    Devuelve resultado QA
    ↓
19. [VPS — BACKEND]
    ↓
    Guarda resultado QA
    ↓
    ├── PASS
    │    ↓
    │   Clip aprobado
    │
    ├── FAIL
    │    ↓
    │   Clip rechazado / reintento
    │
    └── REVIEW
         ↓
        Revisión humana / OpenClaw
    ↓
20. [OPENCLAW — ⚠️ implícito en campaign-publish-tick (cada 20h)]
    ↓
    Revisa estado de campañas (ready_to_submit=true)
    ↓
    Cuando hay suficientes clips aprobados:
    ↓
    ↑ Sin cron dedicado propio; depende del tick de paso 21
    ↓
21. [OPENCLAW — CRON: campaign-publish-tick]
    ↓
    Inicia / coordina publicación y submission (cada 20h, isolated)
```

---

## La división fundamental

```
OPENCLAW
├── Descubre campañas (paso 1 — whop-discovery-cron, cada 6h, minimal upsert)
├── ~~Prioriza campañas (paso 2 — JUBILADO 2026-09-17, absorbed by 3c)~~
├── Analiza reglas con MiniMax (paso 3a — brief-reader-tick, cada 1h30m)
├── Resuelve assets reales de Drive (paso 3b — drive-resolver-tick, cada 1h30m)
├── Puntúa campañas listas (paso 3c — campaign-scorer-tick, cada 1h30m)
├── Decide qué partes de los vídeos son buenos clips (pasos 12-13 — clip-decider-tick, cada 1h15m)
├── Supervisa
├── Decide qué hacer ante problemas
└── Coordina publicación/submission (paso 21 — campaign-publish-tick, cada 20h)


VPS BACKEND
├── PostgreSQL
├── Job Queue
├── Asset Resolver (invocado desde whop_discovery.py, paso 5)
├── Orquestación de estados (pasos 4, 6, 7, 9, 11, 15, 17, 19)
├── Recibe resultados del Worker
├── Crea los siguientes jobs
└── Es la fuente de verdad del sistema


WINDOWS WORKER
├── Descarga (paso 8)
├── WhisperX (paso 10)
├── FFmpeg (paso 16)
├── FFprobe / QA (paso 18)
└── Ejecución pesada
```

---

## Estados de los assets / vídeos (modelo mental)

Estos nombres aparecen en los pasos 9 y 11 del flujo:

| Estado | Significado | Trigger que lo establece |
|---|---|---|
| `discovered` | Detectado por paso 1 (whop-discovery-cron), sin analizar | Paso 1 |
| `briefed` | brief-reader (3a) extrajo rules + asset_links | Paso 3a |
| `assets_resolved` | drive-resolver (3b) convirtió folders en assets reales | Paso 3b |
| `scored` | campaign-scorer (3c) escribió score + priority | Paso 3c |
| `blocked_no_assets` | 3c no encontró assets reales | Paso 3c |
| `failed_brief` / `failed_resolve` | paso 3a/3b no pudo procesar | Pasos 3a / 3b |
| `pending` | Detectado por Asset Resolver, todavía no descargado | Paso 6 (registro en PostgreSQL) |
| `downloaded` | Vídeo en almacenamiento local del Worker | Paso 9 (post-DOWNLOAD JOB `completed`) |
| `transcribed` | Transcripción disponible en PostgreSQL | Paso 11 (post-TRANSCRIBE JOB `completed`) |
| `failed` | Cualquier job intermedio terminó en `failed` | Worker `POST /fail` |
| `rejected` | QA devolvió `FAIL` y se descarta el clip | Paso 19 rama `FAIL` |
| `approved` | QA devolvió `PASS` | Paso 19 rama `PASS` |
| `review` | QA o LLM no deciden; necesita humano o nuevo pase LLM | Paso 19 rama `REVIEW` |
| `published` | OpenClaw confirmó la publicación | Paso 21 |

Estos nombres son **orientativos**: cuando el VPS los implemente oficialmente pueden ajustarse, pero cualquier desviación debe documentarse aquí.

---

## Tipos de jobs del Worker (resumen)

| Job | Creador | Lo ejecuta | Resultado |
|---|---|---|---|
| `download` | VPS (paso 7) | Worker (paso 8) | vídeo en disco local + path devuelto |
| `transcribe` | VPS (paso 9) | Worker (paso 10) | transcripción + timestamps |
| `render` | VPS (paso 15) | Worker (paso 16) | clip final con formato, captions, watermark |
| `qa` | VPS (paso 17) | Worker (paso 18) | PASS / FAIL / REVIEW con checks |

**Otros jobs internos del Worker** (sanity / debug, no parte del flujo de producción):

- `health`: reporte de GPU/CUDA/RAM/herramientas.

---

## Cambio sobre `AGENTS.md`

Este documento **sustituye** al diagrama ASCII y a la tabla de responsabilidades dentro de `AGENTS.md` como referencia de flujo. `AGENTS.md` mantiene:

- Estado confirmado (qué está implementado y validado).
- Contratos de jobs del Worker (payloads y `result.data`).
- API y comandos del VPS.
- Decisiones técnicas tomadas.

Cualquier conflicto entre `AGENTS.md` y `architecture_flow.md` se resuelve a favor de `architecture_flow.md` salvo que el documento indique lo contrario.

---

## Estado del pipeline

Este documento describe el **flujo**. El **estado actual** (qué está hecho hoy, qué no, IDs de crons, owner) vive en `PIPELINE_STATUS.md` (workspace y `/opt/clipping-system/docs/`). Cualquier cambio sobre cron/script activa debe reflejarse allí en el mismo commit.
