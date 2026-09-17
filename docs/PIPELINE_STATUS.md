# Pipeline Status — fuente de verdad del estado del proyecto

> **Última verificación:** 2026-09-17 14:35 UTC (refactor pipeline v2).
> **Cambio relevante:** el paso 3 monolítico se ha partido en 3a (brief-reader),
> 3b (drive-resolver) y 3c (campaign-scorer), encadenados por estado en BD.
> El paso 2 (campaign-prioritizer) está jubilado. Ver `architecture_flow.md`.
> **Relación con otros docs:**
> - **`architecture_flow.md`** sigue siendo la fuente de verdad del **flujo** (qué paso hace qué, en qué orden).
> - Este doc (`PIPELINE_STATUS.md`) es la fuente de verdad del **estado** (qué está hecho hoy, qué no, quién lo ejecuta, IDs de los crons).
> - **`MEMORY.md`** (mi memoria principal) apunta a este doc cuando Molina pregunta por "estado del pipeline".
> - **`PROJECT_STATUS.md`** (en `/opt/clipping-system/docs/`) sigue cubriendo el estado técnico interno del backend (tests, endpoints, migraciones, smoke). NO lo sustituyo: la granularidad es distinta.

> **Regla de mantenimiento:** cada vez que se cree, deshabilite o cambie de ownership un cron/script del pipeline, este doc se actualiza en el mismo commit/PR. Sin excepciones.

---

## TL;DR

| # | Paso | Quién lo ejecuta | Estado |
|---|---|---|---|
| 1 | Descubrir campañas (UPSERT mínimo) | **OpenClaw / CRON** `whop-discovery-cron` | ✅ pipeline v2 |
| 2 | ~~Priorizar campañas~~ | ~~`campaign-prioritizer-tick`~~ | ❌ jubilado 2026-09-17, absorbed by 3c |
| 3a | Leer brief → rules + asset_links | **OpenClaw / CRON** `brief-reader-tick` | ✅ pipeline v2 |
| 3b | Resolver Drive folders → assets reales | **OpenClaw / CRON** `drive-resolver-tick` | ✅ pipeline v2 |
| 3c | Puntuar campaña (rules + assets reales) | **OpenClaw / CRON** `campaign-scorer-tick` | ✅ pipeline v2 |
| 4 | Guardar campaign + spec/rules/score | VPS Backend (`PATCH /campaigns/{id}`) | ✅ |
| 5 | Asset Resolver (legacy) | VPS Backend (`resolve_assets_for_campaign`) | ✅ ya no se llama desde paso 1 |
| 6 | Registrar vídeos en BD | VPS Backend (alta en `assets`) | ✅ |
| 7 | Crear DOWNLOAD JOBS | **OpenClaw / CRON** `download-enqueue-tick` (10m) | ✅ pipeline v2 |
| 8 | Worker descarga | **Worker Windows** (Molina) | ✅ |
| 9 | Marcar DOWNLOADED + crear TRANSCRIBE JOB | VPS Backend (auto, callback Worker) | ✅ |
| 10 | Worker transcribe (WhisperX) | **Worker Windows** (Molina) | ✅ |
| 11 | Guardar transcripción + marcar TRANSCRIBED | VPS Backend (auto) | ✅ |
| 12 | Detectar transcripciones nuevas | **OpenClaw / CRON** `clip-decider-tick` | ✅ |
| 13 | Decidir buenos clips → Candidatos | **OpenClaw / Mini­Max** (`clip-decider-tick`) | ✅ |
| 14 | Guardar candidatos + validar reglas | OpenClaw (LLM) + VPS Backend (storage) | ⚠️ parcial — sin gate duro |
| 15 | Crear RENDER JOBS | VPS Backend (auto al aprobar candidato) | ✅ |
| 16 | Worker render (FFmpeg) | **Worker Windows** (Molina) | ✅ |
| 17 | Registrar clip + crear QA JOB | VPS Backend (auto) | ✅ |
| 18 | Worker QA (FFprobe) | **Worker Windows** (Molina) | ✅ |
| 19 | Guardar QA + PASS/FAIL/REVIEW | VPS Backend (auto) | ✅ |
| 20 | Revisar estado de campañas | **OpenClaw** (vía `campaign-publish-tick`) | ⚠️ sin cron dedicado |
| 21 | Publicación / submission | **OpenClaw / CRON** `campaign-publish-tick` | ✅ |

**Leyenda**: ✅ verificado hoy · ⚠️ parcial o pendiente · ❌ no implementado.

---

## Owner del proyecto

| Territorio | Quién | Repos |
|---|---|---|
| VPS Backend (FastAPI + Postgres + Job Queue + Mission Control) | **Clipper** (yo, OpenClaw) | `jarvismolinabot/clipping-system-vps` |
| Worker Windows (descarga + WhisperX + FFmpeg + FFprobe + QA) | **Molina** (PC Windows) | repo del Worker |
| Orquestación LLM (decider, scorer, brief_reader, drive_resolver, publish) | **Clipper** (OpenClaw crons + Mini­Max) | workspace OpenClaw |

**Regla de oro (AGENTS.md):** Worker = Molina. VPS = mío. No tocar el repo ajeno. No force-push.

---

## Inventario de crons activos (OpenClaw)

| Cron ID | Nombre | Cada | Target | Comando / Mensaje | Propietario |
|---|---|---|---|---|---|
| `7c3800ef-...` | `whop-discovery-cron` | 6h | isolated | `venv/bin/python scripts/whop_discovery.py --limit 50` (UPSERT mínimo, status='discovered') | Clipper |
| `335f304e-...` | `brief-reader-tick` ⭐ nuevo | 1h30m | isolated | agentTurn — paso 3a, status='discovered' → 'briefed' | Clipper |
| `d1f2e08e-...` | `drive-resolver-tick` ⭐ nuevo | 1h30m | isolated | agentTurn — paso 3b, status='briefed' → 'assets_resolved' | Clipper |
| `9ec4dbe3-...` | `campaign-scorer-tick` ⭐ nuevo | 1h30m | isolated | agentTurn — paso 3c, status='assets_resolved' → 'scored' / 'blocked_no_assets' | Clipper |
| `<new>` | `download-enqueue-tick` ⭐ nuevo | 10m | isolated | `venv/bin/python scripts/download_enqueue_tick.py --limit 50` — paso 7, status IN ('scored','ready') | Clipper |
| `0f0d1264-...` | `campaign-analyze-tick` ⛔ deshabilitado | 1h30m | isolated | legacy — sustituido por 3a+3b+3c. No eliminar. | Clipper |
| `239ee9a8-...` | `campaign-prioritizer-tick` ⛔ deshabilitado | 2h | isolated | legacy — absorbed by 3c. No eliminar. | Clipper |
| `84f2395a-...` | `clip-decider-tick` | 1h15m | isolated | agentTurn — pasa 12-13, usa `/clip_selection/queue?priority_only=true` | Clipper |
| `818e85d0-...` | `campaign-publish-tick` | 20h | isolated | agentTurn — paso 21 (publicación/submission) | Clipper |
| `18a1d89f-...` | Heartbeat main | 2h | main | — | OpenClaw |
| `4da427e4-...` | OpenClaw media cleanup | 1d 04:00 UTC | isolated | — | OpenClaw |
| `81bb0a7c-...` | Memory Dreaming | 1d 03:00 UTC | isolated | — | OpenClaw |

> Lista viva: `openclaw cron list`. Cada ID es estable entre reinicios del Gateway.

---

## Scripts / archivos relevantes del VPS

| Ruta | Qué hace |
|---|---|
| `/opt/clipping-system/scripts/whop_discovery.py` | Paso 1 — discover + upsert + analyze de drafts |
| `/opt/clipping-system/scripts/campaign_prioritizer.py` | Paso 2 — scoring 50/30/10/10, marca `priority_tier` en `source_metadata` |
| `/opt/clipping-system/scripts/vps_pipeline_tick.py` | Drenaje de jobs pendientes (auxiliar) |
| `/opt/clipping-system/scripts/clip_scanner.py` | Utilidad offline (no en cron) |
| `/opt/clipping-system/app/api/mission_control.py` | Dashboard read-only (7 GET endpoints) |
| `/opt/clipping-system/app/api/clip_selection.py` | `GET /clip_selection/queue[?priority_only=true&priority_tier=...]` |
| `/opt/clipping-system/app/services/campaign_analyzer.py` | Parser determinista + enriquecimiento LLM (paso 3 soporte) |
| `/opt/clipping-system/app/static/mission-control/` | Frontend del dashboard (HTML/CSS/JS vanilla) |

---

## Detalle por paso

### Paso 1 — Descubrir campañas (UPSERT mínimo) ✅ pipeline v2
- **Quién**: OpenClaw cron `whop-discovery-cron`.
- **Cómo**: llama a la API pública de Whop (tenant vía `WHOP_TENANT_URL`), upserta **solo datos básicos** en `campaigns`: `name`, `cpm_usd_per_1k`, `prize_pool_usd`, `source_url`, `source_instructions`, `source_provider='whop'`. NO crea assets. NO analiza.
- **Output**: campañas con `status='discovered'`. Sin `spec.rules`, sin `score`, sin `asset_links`. El LLM (paso 3a) decidirá qué es asset real y qué es basura.

### Paso 2 — ~~Priorizar~~ JUBILADO ❌ 2026-09-17
- **Quién (antes)**: OpenClaw cron `campaign-prioritizer-tick` (id `239ee9a8-…`).
- **Razón**: ahora el paso 1 no trae assets, así que el peso del 10% de "assets_available" del score 50/30/10/10 se queda sin datos o pasa a 0.
- **Absorción**: el scoring completo vive ahora en `campaign-scorer-tick` (paso 3c), que usa la fórmula de `skills/campaign-scorer` (no la 50/30/10/10).
- **Estado**: cron **deshabilitado**, no eliminado. Script `scripts/campaign_prioritizer.py` queda en repo por trazabilidad. Si quieres reactivarlo en algún momento, hay que rehacer la fórmula contra el nuevo modelo.

### Paso 3a — Brief reader ✅ pipeline v2
- **Quién**: OpenClaw cron `brief-reader-tick` (id `335f304e-…`, cada 1h30m).
- **Cómo**: agentTurn aislado. `GET /campaigns?status=discovered`, lee `source_instructions` + `source_url` de cada una y, usando SOLO la skill `brief-reader`, extrae `rules` (jsonb) + `asset_links` (jsonb). Por cada Drive folder crea un asset row con `kind='drive_folder'`. Cambia `status` a `briefed` (o `failed_brief`).
- **Output**: `campaigns.source_metadata.rules`, `campaigns.source_metadata.asset_links`, posibles assets `drive_folder`. `status='briefed'`.

### Paso 3b — Drive resolver ✅ pipeline v2
- **Quién**: OpenClaw cron `drive-resolver-tick` (id `d1f2e08e-…`, cada 1h30m).
- **Cómo**: agentTurn aislado. `GET /campaigns?status=briefed`, busca assets con `kind='drive_folder'`, y por cada uno usa SOLO la skill `drive-resolver` (gog CLI autenticado): lista recursivamente (depth ≤ 4), filtra por extensiones `.mp4/.mov/.mkv/.webm/.zip/.tar/.gz`, deduplica por `source_id` y crea 1 asset row por archivo real (`source_url=https://drive.google.com/uc?export=download&id=…`). Cambia `status` a `assets_resolved` (o `failed_resolve`).
- **Output**: assets reales en `assets`. `status='assets_resolved'`.

### Paso 3c — Campaign scorer ✅ pipeline v2
- **Quién**: OpenClaw cron `campaign-scorer-tick` (id `9ec4dbe3-…`, cada 1h30m).
- **Cómo**: agentTurn aislado. `GET /campaigns?status=assets_resolved`, usa SOLO la skill `campaign-scorer` (cálculo determinista puro, sin red, sin LLM): aplica `base_revenue + asset_availability + rule_completeness − difficulty_penalty`, clamp 0..100, `priority = clamp(round(total/10), 1, 10)`, `tie_break = base_revenue`. Si 0 assets reales → `status='blocked_no_assets'`, `score=null`. Cambia `status` a `scored` (o `blocked_no_assets`).
- **Output**: `campaigns.source_metadata.score = {total, breakdown, priority, tie_break, rank_reason}`. `status='scored'`.

### Paso 4 — Guardar CampaignSpec ✅
- **Quién**: VPS Backend.
- **Endpoint**: `PATCH /campaigns/{id}` (antes era `POST /campaigns/{id}/spec`; ahora los crons 3a/3b/3c usan el PATCH genérico, que ya acepta `status`, `spec` y `source_metadata`).
- **Output**: `status` y/o `spec` y/o `source_metadata` actualizados según el paso.

### Paso 7 — Download enqueue (puente 3c → 8) ✅ pipeline v2
- **Quién**: OpenClaw cron `download-enqueue-tick` (10m, isolated, command payload).
- **Cómo**: `venv/bin/python scripts/download_enqueue_tick.py --limit 50`. Para cada campaña con `status IN ('scored','ready')` busca assets `status='pending'` con URL processable, crea 1 `download` job por campaña (idempotente: si ya hay un job `download` abierto, skip). Piggy-back: `process_pending_clip_selections` para drenar transcripciones cuyo decider nunca corrió.
- **Por qué nuevo**: antes era `vps_pipeline_tick.py` que solo buscaba `status='ready'`. Pipeline v2 deja `scored`. Necesita su propio cron (opción 2 aprobada por Molina 2026-09-17) para no mezclar la lógica legacy con la nueva.
- **Output**: jobs `download` encolados en `jobs` (Worker los coge via `GET /worker/jobs/next`).

### Pasos 5-7 — Asset Resolver (legacy, ya no se invoca desde paso 1) ✅
- **Quién**: VPS Backend, función `resolve_assets_for_campaign`.
- **Estado pipeline v2**: el script `whop_discovery.py` ya NO la llama. Los assets reales los crea paso 3b desde Drive folders. La función sigue existiendo por si en el futuro hay un asset_resolver desde otra fuente (YouTube, etc.).

### Pasos 8-11 — Download + Transcribe ✅
- **Quién**: Worker Windows hace el trabajo pesado (descarga + WhisperX); VPS Backend gestiona el ciclo de vida del job y persiste resultados.
- **Ya validado E2E** (commit `a88bbe0` de Molina, mencionado en MEMORY.md).

### Pasos 12-13 — Clip Decider ✅
- **Quién**: OpenClaw cron `clip-decider-tick`.
- **Cómo**: agentTurn aislado, lee `GET /clip_selection/queue?priority_only=true&limit=10`. Si la cola priority está vacía, fallback a la cola completa (`max 5`). Para cada asset, lee `CampaignSpec`, transcripción y propone `Candidate`.
- **Output**: `POST /candidates`, asset marcado como `clip_proposed`.

### Paso 14 — Validación de reglas ⚠️
- **Quién actual**: OpenClaw (LLM valida internamente) + VPS Backend (storage).
- **Gap**: no hay un **gate duro** en el VPS que rechace `Candidate` si viola `CampaignSpec` (duración, formato). La validación es solo del LLM.
- **Riesgo**: clips que se renderizan y luego fallan QA por reglas de negocio (no técnicas).
- **Mitigación futura**: endpoint `POST /candidates` que valide contra `spec` antes de aceptar.

### Pasos 15-19 — Render + QA ✅
- **Quién**: VPS Backend crea RENDER/QA jobs al aprobar candidato; Worker Windows ejecuta FFmpeg + FFprobe.
- **Estado del clip**: `pending → pass/fail/review` según resultado.

### Paso 20 — Revisar estado de campañas ⚠️
- **Quién actual**: implícito en `campaign-publish-tick` (cada 20h mira `ready_to_submit=true`).
- **Gap**: si una `ready` no genera clips durante días, nadie avisa.
- **Mitigación futura**: cron ligero cada 2-4h que reporte campañas `ready` con 0 clips approved en N días.

### Paso 21 — Publicación / submission ✅
- **Quién**: OpenClaw cron `campaign-publish-tick` (cada 20h).
- **Cómo**: agentTurn aislado, mira campañas con `ready_to_submit=true`, usa `brief_reader` si necesita re-leer reglas, marca `clips` como `published` / `submitted`.
- **Pendiente**: conectores reales de TikTok/YouTube/Instagram (ver goal `clip-publish-pipeline` en MEMORY.md).

---

## Reglas duras (no romper)

1. **No reiniciar el Gateway durante autoconfig** (incidente 2026-09-08). Hot-reload verificado en skills/heartbeat/cron/model.
2. **Worker = Molina**, VPS = Clipper. No mezclar repos. No force-push.
3. **Read-only en Mission Control**: ningún endpoint escribe en BD. Tests verifican que solo hay GET.
4. **Bearer token**: misma `API_TOKEN` del `.env` para API y Mission Control.
5. **Pesos del priorizador**: `W_CPM=0.50, W_PRIZE=0.30, W_SPEC=0.10, W_ASSETS=0.10`. Cambios requieren OK explícito de Molina y actualización de este doc.
