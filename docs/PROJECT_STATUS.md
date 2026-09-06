# PROJECT_STATUS.md — Estado del proyecto Clipping

> **Documento hermano** de `docs/ARCHITECTURE.md` (en `/opt/clipping-system/docs/`) y de `docs/architecture_flow.md` (el doc de Molina con los 21 pasos, fuente única de verdad para flujo y responsabilidades).
> Refleja el estado real **punto por punto**: qué está hecho, qué no, qué está a medias, riesgos y próximos pasos.
> Actualizado en cada cambio relevante por **Clipper** (agente OpenClaw).
>
> **Última actualización:** 2026-09-06 20:18 UTC
> **Verificación de estado:** backend operativo y revisado por Clipper ahora mismo.
> **Fuente de verdad técnica:** el código en `/opt/clipping-system/` y este propio doc.
> **Fuente de verdad funcional:** el servicio corriendo en `100.109.27.21:8080` (Tailscale).

---

## TL;DR

- 🟢 **Backend MVP arrancado y respondiendo** — FastAPI en `100.109.27.21:8080`, Postgres nativo en `localhost:5432`, DB `clipping` con tablas `jobs`, `workers`, `campaigns`, `assets`, `candidates`, `clips`.
- ✅ **Tests pasan** — **150/150 pytest verde** en ~5s (47 originales + 8 workers + 14 campaigns + 17 assets + 11 candidates + 11 clips + 16 campaign_engine + 15 clip_selection + 11 candidate_lifecycle), 2 warnings deprecation menores.
- 🟢 **Servicio systemd robusto** — `clipping-api.service` enabled, Restart=always, MemoryMax=512M, hardening completo (ProtectSystem=strict, ProtectHome, ReadWritePaths). Sobrevive reboots sin problema.
- 🟢 **Fases A, B, C, D HECHAS** (steps 3, 4, 5-6, 7, 9, 11, 13, 15, 17, 19 del architecture_flow.md)
- ✅ **Push al día** — commit `5e9aa44` (16:18 UTC) en `jarvismolinabot/clipping-system-vps` main. Estado verificado.
- ✅ **Tailscale OK** — sigue como root (correcto), mesh con `molina` (PC Windows) y `vps-5764d01a` (este VPS) online.

---

## Estado por fase (mapeo a `architecture_flow.md`)

### ✅ Fase 1 — Auditoría — **HECHA** (2026-09-06 11:02 UTC, por Clipper)

| Recurso | Valor |
|---|---|
| RAM | 3.7 Gi total · 1.2 Gi usado · **2.6 Gi disponible** |
| CPU | 2 cores · load 0.20 |
| Disco | 38 Gi · 11 Gi usado (28%) |
| Swap | 2.0 Gi (9.8 Mi usado) |
| Docker | **NO instalado** — decisión correcta para 4 Gi RAM |
| PostgreSQL | 16 nativo, instalado, en `127.0.0.1:5432` |
| OpenClaw gateway | systemd user `openclaw-gateway.service`, puerto `18789`, PID 108263 |
| Tailscale | systemd system `tailscaled.service`, IP `100.109.27.21` |

### ✅ Fase 2 — Preparación — **HECHA**

- ✅ `/opt/clipping-system/` existe (owner `clipping:clipping`, creado 2026-09-04)
- ✅ `.env` existe (con `CLIPPING_DB_*`, `API_*`, `API_TOKEN`)
- ✅ `alembic.ini` y `alembic/` configurados
- ✅ `.env.example` creado en commit `80ca085`
- ✅ Git repo inicializado con 10 commits en local
- ❌ NO hay `docker-compose.yml` ni `Dockerfile` — **aceptable**, Postgres nativo consume menos RAM
- ❌ NO hay `README.md` en raíz del proyecto

### ✅ Fase 3 — Base de datos — **HECHA**

- ✅ PostgreSQL 16 nativo instalado y corriendo
- ✅ DB `clipping` creada (owner `postgres`)
- ✅ Rol `clipping_api` con permisos sobre `clipping`
- ✅ Alembic configurado (`alembic.ini`, `alembic/env.py`)
- ✅ **4 migraciones aplicadas**:
  - `0001_create_jobs_table.py` → tabla `jobs` con 16 columnas, CHECK de estados, índices de claim
  - `0002_create_workers_table.py` → tabla `workers` con status enum, gpu_name, gpu_available, capabilities JSONB, last_heartbeat_at, etc.
  - `0003_create_campaigns_table.py` → tabla `campaigns` con source_provider enum, source_id, source_url, source_metadata JSONB, spec JSONB, contadores denormalizados
  - `0004_create_assets_table.py` → tabla `assets` con FK CASCADE, source_url, status enum, local_path, file_size, duration_seconds, sha256, mime_type, extra_metadata JSONB, downloaded_at/transcribed_at
  - `0005_candidates.py` → tabla `candidates` con start_time/end_time (CHECK end > start), score, reasoning, extra_metadata JSONB, status enum (pending/approved/rejected/rendered/superseded)
  - `0006_clips.py` → tabla `clips` con FKs a candidates/assets/jobs (SET NULL), file_path, duration_seconds, file_size, qa_status enum (pending/pass/fail/review), status enum (created/approved/rejected/review/published), qa_at/published_at
  - `0007_job_state.py` → GIN index en `jobs.payload` para lookups rápidos de asset_id

### ✅ Fase 4 — API FastAPI — **HECHA**

- ✅ FastAPI + Uvicorn funcionando (user `clipping`)
- ✅ Auth Bearer token implementado (`app/auth.py` + `app/config.py`)
- ✅ **27 endpoints implementados**:
  - **Health/system**: `GET /health`, `GET /system/info`
  - **Jobs**: `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`
  - **Worker integration**: `POST /worker/register`, `POST /worker/heartbeat`, `GET /worker`, `GET /worker/{id}`, `GET /worker/jobs/next`, `POST /worker/jobs/{id}/start`, `/heartbeat`, `/result`, `/fail`
  - **Campaigns**: `POST /campaigns`, `GET /campaigns` (con `?status` y `?source_provider`), `GET /campaigns/{id}`, `PATCH /campaigns/{id}`
  - **Assets**: `POST /assets`, `POST /assets/bulk`, `POST /assets/resolve/{campaign_id}`, `GET /assets` (filters), `GET /assets/{uuid}`, `PATCH /assets/{uuid}`
  - **Candidates**: `POST /candidates`, `POST /candidates/bulk`, `GET /candidates` (filters), `GET /candidates/{uuid}`, `PATCH /candidates/{uuid}`
  - **Clips**: `POST /clips`, `GET /clips` (filters), `GET /clips/{uuid}`, `PATCH /clips/{uuid}`
- ✅ Pydantic schemas separados (`app/schemas/{worker,campaign,asset,candidate,clip}.py`)
- 🟡 **Bind problemático**: API escucha SOLO en `100.109.27.21:8080` (Tailscale), NO en `127.0.0.1:8080`. Decisión pendiente (añadir localhost o `0.0.0.0` con firewall restrictivo)

### ✅ Fase 5 — Job Queue — **HECHA**

- ✅ Tabla `jobs` con esquema completo, CHECK de estados (`pending/assigned/processing/completed/failed/retry/cancelled`)
- ✅ Locking con `SELECT FOR UPDATE` para evitar que 2 Workers pillen el mismo job (`test_concurrency.py`)
- ✅ Tipos: `health`, `download`, `transcribe`, `render`, `qa`
- ✅ Atomic claim en `get_next_job_endpoint`
- ✅ GIN index en `jobs.payload` (migración 0007) para lookups de `asset_id` rápidos

### ✅ Fase 6 — Integración Worker — **HECHA** (commit `a88bbe0`)

- ✅ Código del Worker Windows leído (`/tmp/worker-review/clipping-windows-worker/`)
- ✅ Compatibilidad verificada endpoint-a-endpoint
- ✅ Endpoints `POST /worker/register` y `POST /worker/heartbeat` añadidos (los que el Worker esperaba)
- ✅ Tabla `workers` + Alembic 0002
- ✅ Schemas Pydantic espejo exacto del Worker (`WorkerRegistration`, `Heartbeat`)
- ✅ E2E real verificado: register/heartbeat/list/get → 200, ghost → 404, sin auth → 401
- ✅ 8 tests unitarios nuevos en `tests/test_workers.py` — **8/8 passing**

### ✅ Step 4 — Campaign storage (architecture_flow.md) — **HECHO** (commit `ce5a90a`)

- ✅ Modelo `Campaign` con soporte multi-source:
  - `source_provider` (twitter/youtube/instagram/tiktok/reddit/twitch/manual/other) con CHECK constraint
  - `source_id` (ID en la plataforma), `source_url` (URL al post/vídeo original)
  - `source_metadata` (JSONB para datos específicos del proveedor)
- ✅ `CampaignSpec` (reglas agnósticas del proveedor): duration_min/max, captions_required, watermark_url, format, language, keywords, exclude_keywords, extra
- ✅ `CampaignStatus` enum: draft, analyzing, ready, active, paused, completed, archived
- ✅ Endpoints: `POST /campaigns`, `GET /campaigns` (con filtros `?status` y `?source_provider`), `GET /campaigns/{id}`, `PATCH /campaigns/{id}`
- ✅ Pydantic `field_validator` en `source_provider` y validación de transiciones de status
- ✅ Alembic migración 0003 aplicada con índices en `name`, `status`, `source_provider`
- ✅ 14 tests nuevos en `tests/test_campaigns.py`
- ✅ **E2E real verificado**: creación multi-source (youtube/twitter/manual), filtrado por `source_provider`, PATCH status transitions, validación Pydantic → 422, sin auth → 401

### ✅ Steps 5-6 — Asset + Asset Resolver stub (architecture_flow.md) — **HECHO** (commit `86ece4e`)

- ✅ Modelo `Asset` con UUID pk, FK CASCADE a campaigns, source_url/source_id/source_provider, status enum (`pending/downloaded/transcribed/failed`), local_path/file_size/duration_seconds/sha256/mime_type, extra_metadata JSONB, downloaded_at/transcribed_at timestamps
- ✅ Endpoints: `POST /assets`, `POST /assets/bulk`, `POST /assets/resolve/{campaign_id}`, `GET /assets` (filters), `GET /assets/{uuid}`, `PATCH /assets/{uuid}`
- ✅ Asset Resolver stub: returns empty list (real impl will hit platform APIs)
- ✅ Pydantic field_validator on source_url and status
- ✅ 17 tests nuevos en `tests/test_assets.py`
- ✅ **85/85 pytest passing**

### ✅ Steps 7, 9, 11, 15, 17, 19 — state transitions + Render/QA routing (architecture_flow.md) — **HECHO** (commit `eed020a`)

- ✅ Modelos `Candidate` (Step 13-14) + `Clip` (Step 16-19) con FKs, CHECK constraints, índices
- ✅ Migraciones 0005 (candidates) + 0006 (clips) + 0007 (GIN index)
- ✅ Endpoints: `POST /candidates` (+ /candidates/bulk), `GET /candidates` (filters), `GET/PATCH /candidates/{id}`, `POST /clips`, `GET /clips` (filters), `GET/PATCH /clips/{id}`
- ✅ `app/services/job_state_transitions.py`:
  - `on_download_completed` → asset='downloaded' + auto-create transcribe job
  - `on_transcribe_completed` → asset='transcribed' + store transcription
  - `on_render_completed` → create Clip + auto-create QA job
  - `on_qa_completed` → update Clip qa_status (pass→approved, fail→rejected, review→review)
  - `on_job_failed` → mark related asset as 'failed'
- ✅ 22 tests nuevos (test_candidates + test_clips) + 6 tests de state_transitions
- ✅ **105/105 pytest passing**

### ✅ Steps 3+13 — Campaign Engine skeleton (architecture_flow.md) — **HECHO** (commit `bf456ed`)

- ✅ `app/campaign_engine/` package con:
  - `models.py`: Pydantic `CampaignHints` (parser output) + `NormalizedSpec` (final spec)
  - `parser.py`: rule-based keyword extraction (regex) para duration (single + ranges "20-45 seconds"), format, language, captions, watermark, keywords, exclude_keywords
  - `normalizer.py`: merge hints con per-provider defaults (twitter/youtube/instagram/tiktok/reddit/twitch/manual/other)
  - `__init__.py`: exports `parse_instructions` + `normalize`
- ✅ Real implementation delegará a Mini­Max LLM para richer extraction; este stub provides fast, deterministic fallback para tests
- ✅ 19 tests en `tests/test_campaign_engine.py` (parser, normalizer, end-to-end)
- ✅ **124/124 pytest passing**

### ✅ Fase 7 — Testing — **HECHA** (tests unitarios)

- ✅ **124/124 tests passing en 3.86s** (47 originales + 8 workers + 14 campaigns + 17 assets + 11 candidates + 11 clips + 16 campaign_engine), 2 warnings deprecation sin impacto
- ❌ NO hay tests de integración reales VPS ↔ Worker
- ❌ NO hay CI/CD configurado

---

## Estado de infraestructura NO-documentada

### Tailscale

- ✅ Daemon `tailscaled` corriendo como **root** (correcto)
- ✅ State en `/var/lib/tailscale/tailscaled.state`
- ✅ Servicio systemd `tailscaled.service` activo desde 2026-09-03
- ✅ IP Tailscale VPS: `100.109.27.21` (IPv4)
- ✅ Mesh: `vps-5764d01a` + `molina` online
- ⚠️ **No hay ACL local** — todo en policy del Tailnet
- ✅ **Migración root → ubuntu no afecta a Tailscale**

### OpenClaw + skills

- ✅ Gateway `openclaw-gateway.service` user-level, puerto `18789`
- ✅ Skills operativas: `blogwatcher`, `xurl`, `instagram-content-studio`, `youtube-api-skill`
- ✅ Telegram bot operativo
- 🟡 `nano-pdf` y `summarize` deshabilitados (sin binarios Linux)

### Postgres

- ✅ PostgreSQL 16 nativo, `127.0.0.1:5432`
- ✅ DB `clipping` con owner `clipping_api`
- ✅ Conexión desde API verificada (`{"status":"ok","database":"ok"}`)

### Backups

- 🟡 Solo `.env.bak-phase4-pre-api-vars` (1 backup manual)
- ❌ NO hay backups automatizados

---

## 🔴 Bloqueantes / Riesgos urgentes

| # | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| 1 | ~~**Git remote equivocado**~~ ✅ **RESUELTO 16:18 UTC** | (resuelto) | `5e9aa44` ya en `jarvismolinabot/clipping-system-vps` main. |
| 2 | **API bind solo a Tailscale** | curl desde localhost falla, herramientas internas no pueden hablarle | Decidir bind: localhost + Tailscale, o `0.0.0.0` con firewall restrictivo |

## 🟡 Decisiones pendientes

- [ ] **Bind de la API**: ¿añadir localhost, dejar solo Tailscale, o `0.0.0.0` con firewall?
- [ ] **A/B/C del tool executor** — problema estructural desde 10:52, sigue sin resolver
- [x] ~~**Device Flow E8A8-95D4**~~ ✅ Resuelto (`5e9aa44` pusheado 16:18 UTC)

## 🟢 Logros verificados hoy (2026-09-06)

- ✅ **124/124 pytest verde** (47 + 8 + 14 + 17 + 11 + 11 + 16)
- ✅ **27 endpoints API** en OpenAPI
- ✅ **6 tablas en DB**: jobs, workers, campaigns, assets, candidates, clips
- ✅ **7 migraciones Alembic** aplicadas (0001-0007)
- ✅ **E2E real verificado** con curl + Python contra el servicio: jobs CRUD, worker integration, campaigns (multi-source), assets, state transitions
- ✅ **10 commits en local**: `04649ae`, `80ca085`, `a88bbe0`, `e01db81`, `9c14867`, `ce5a90a`, `ecebd24`, `86ece4e`, `eed020a`, `bf456ed`
- ✅ `clipping-api.service` activo y robusto

---

## 📋 Próximos pasos (orden propuesto)

1. ✅ ~~Device Flow `E8A8-95D4`~~ Resuelto (`5e9aa44` en main).
2. ✅ ~~Step 12-13 — agente LLM Clipper~~ Resuelto (`9218bba` en main, `app/clip_selection/` con agent+validator+MockLLMClient).
3. ✅ ~~Step 14 — candidate lifecycle (approve→RENDER + reject)~~ Resuelto (`ee7baf1` en main, `app/services/candidate_lifecycle.py`).
4. **Step 1+2** — CRON que escanea fuentes + LLM que decide cuáles campañas interesan. **No existe.**
5. **Asset Resolver real** (multi-source collector: twitter/youtube/instagram/tiktok/reddit/twitch). Stub actual devuelve `[]`.
6. **HttpLLMClient con credenciales reales** (openai/anthropic/minimax-portal en `.env`) para reemplazar `MockLLMClient`.
7. **Bind API**: localhost + Tailscale, o `0.0.0.0` con firewall. Decisión Molina.
8. **Test E2E real VPS↔Worker** (cliente Python que dispara el flujo completo: register → heartbeat → claim → execute → upload).
9. **CI/CD** (opcional, futuro).

## 🔌 Subsistema de campañas — estado al 2026-09-06 16:54 UTC

### Worker Windows — ✅ YA ESTÁ HECHO
- **No es ❌ falta.** El Worker Windows YA está integrado vía API (commit `a88bbe0`, Fase 6 ✅ cerrado). Endpoints `/worker/register` + `/worker/heartbeat` añadidos. Tabla `workers` + Alembic 0002. Schemas Pydantic espejo del Worker. E2E real verificado.
- **Territorio:** PC Windows de Molina, no se toca desde aquí. Errores previos: en reportes anteriores se marcó como ❌ falta; corregido.

### Cómo obtener campañas activas HOY
- `GET /campaigns?status=active` — lista todas con `status='active'`. Filtros extra: `?source_provider=youtube`, `?limit=&offset=`.
- `GET /campaigns/{id}` — una campaña concreta por id.
- `POST /campaigns` — crear (auth Bearer, status inicial `draft` por defecto).
- **Hoy solo se crean campañas manuales** vía API. NO hay collector de Twitter/YouTube/etc.

### Requisitos para crear una campaña (`POST /campaigns`)
- **Obligatorio:** `name` (str, 1-256, único).
- **Opcionales:** `source_provider` (default `'manual'`), `source_instructions` (texto libre con las reglas), `spec` (CampaignSpec JSONB precomputado), `source_id`, `source_url`, `source_metadata`.
- **Transición de status:** `draft` → `analyzing` → `ready` → `active` → `paused` → `completed`/`archived`. Se hace con `PATCH /campaigns/{id}` con `{"status": "..."}`.

### Reglas (CampaignSpec)
- **Pipeline:** `source_instructions` (texto) → `parser.parse_instructions()` → `CampaignHints` (regex) → `normalizer.normalize()` → `NormalizedSpec` → `Campaign.spec` JSONB.
- **Campos del spec:** `duration_min`, `duration_max`, `captions_required`, `watermark_url`, `format`, `language`, `keywords`, `exclude_keywords`, `extra`.
- **Defaults por provider:** twitter→16:9/en/15-140, youtube→9:16/en/30-60, tiktok→9:16/en/15-180, instagram→1:1/en/3-90, manual→9:16/es/20-60. Hints ganan sobre defaults; `max <= min` se fuerza a `max = min + 10`.
- **Parser actual:** stub regex (no LLM real). Fix reciente: `_EXCLUDE_RE` acepta `:` y `=` como separador.
- **Usado en:** step 13 (clip_selection agent, re-deriva spec en cada propuesta) + step 14 (candidate_lifecycle, valida antes de auto-RENDER).
- **Lo que falta:** real LLM call (skeleton `HttpLLMClient` listo, sin credenciales en `.env`).

---

## Whop integration — estado 2026-09-06 20:18 UTC

**Hallazgos del día (Whop OAuth + API testing):**
- Account API keys de Whop **NO cruzan businesses** — la key de Codiant (`biz_F6pWuwRIJXpn5f`) solo lee su propia cuenta, no otras (probado: 401 al listar `biz_kVYmkVBmvnLGHa`).
- Para acceder a un business "X" se necesita: (a) Account API key generada en el dashboard de X, o (b) App API key con app instalada en X, o (c) OAuth app aprobada por X.
- Marketplace search (`/products?query=`) es público y funciona con cualquier key válida — útil para descubrir qué companies tienen productos visibles.
- Los scopes OAuth de Whop no se exponen al crear App en dashboard — Whop pide justificación formal para scopes como `read:experiences` o `access_pass:basic:read`.
- Pattern URL → business id: `whop.com/<route>/...` → buscar en marketplace con el route o con el nombre del product para obtener `biz_XXX`.

**Estado actual del proyecto (Whop):**
- Key de Codiant rotada y validada: `/accounts/me`, `/experiences`, `/products` → 200 OK.
- Codiant tiene 1 experience ("Public forum") + 1 product ("openclaw", test). No es donde están las campañas reales.
- "Content Rewards" de Molina vive en OTRO business (`biz_kVYmkVBmvnLGHa` = "ViralYa ClippingCulture"). Sin API key de ese business, no se puede leer via API.
- Skill `whop-integration` construido en `~/.openclaw/workspace/whop-integration/` (SKILL.md + scripts + whop_client + tests) pero no deployado en `/opt/clipping-system/` ni probado end-to-end.

**Para retomar:** Molina debe elegir entre (a) generar API key de ViralYa desde su dashboard, (b) OAuth flow con app aprobada en ViralYa, o (c) pegar URLs manualmente.

## Changelog

- **2026-09-06 11:05 UTC** — Versión inicial creada por Clipper tras auditoría completa del backend y Tailscale.
- **2026-09-06 11:55 UTC** — **Fase 6 ✅ (Worker Integration)**. Endpoints `/worker/register` y `/worker/heartbeat` añadidos. Tabla `workers` + Alembic 0002. 8/8 tests passing. E2E real verificado. Commits `a88bbe0` + `e01db81`.
- **2026-09-06 12:10 UTC** — **Fase 6 (Worker Integration) marcada ✅**. Commit `a88bbe0` con +608 líneas: modelo Worker, migración 0002, schemas Pydantic espejo del Worker Windows, service, router, 8 tests unitarios, main.py patched. E2E real verificado: register/heartbeat/list/get → 200, ghost heartbeat → 404, sin auth → 401. Pytest suite global: 55/55 passing en 1.89s. Commit en local; push pendiente de decisión de Molina.
- **2026-09-06 13:18 UTC** — **Step 4 ✅ (Campaign storage + multi-source)**. Commit `ce5a90a` con 7 archivos: modelo Campaign con source_provider/source_id/source_url/source_metadata, CampaignSpec (reglas agnósticas del proveedor), migración 0003, 4 endpoints (`POST/GET /campaigns`, `GET/PATCH /campaigns/{id}`), service, schemas Pydantic, 14 tests nuevos (69/69 verde). Soporte multi-proveedor integrado desde diseño (twitter/youtube/instagram/tiktok/reddit/twitch/manual/other). E2E verificado: creación multi-source, filtrado por source_provider, transiciones de status, validación Pydantic → 422, sin auth → 401. Commit en local; **push pendiente de repo destino del VPS** (el remote actual apunta al repo del Worker de Molina por error previo).
- **2026-09-06 13:40 UTC** — **Steps 5-6 ✅ (Asset + Asset Resolver stub)**. Commit `86ece4e` con 7 archivos: modelo Asset con UUID pk, campaign_id FK CASCADE, source_url/source_id/source_provider/asset_type, status enum (pending/downloaded/transcribed/failed), local_path/file_size/duration_seconds/sha256/mime_type, extra_metadata JSONB, downloaded_at/transcribed_at timestamps. Migración 0004 con índices en campaign_id, status, source_provider, (campaign_id, status), FK + CHECK constraints. Endpoints: `POST /assets`, `POST /assets/bulk`, `POST /assets/resolve/{campaign_id}`, `GET /assets` (filters), `GET /assets/{uuid}`, `PATCH /assets/{uuid}`. Asset Resolver stub: returns empty list (real impl will hit platform APIs). 17 tests nuevos. **85/85 pytest passing**.
- **2026-09-06 13:48 UTC** — **Steps 7, 9, 11, 15, 17, 19 ✅ (state transitions + Render/QA routing)**. Commit `eed020a` con 15 archivos: modelos Candidate (Step 13-14) + Clip (Step 16-19) con FKs/CHECK/indexes, migraciones 0005+0006+0007 (incluye GIN index en jobs.payload), endpoints de candidates/clips, `app/services/job_state_transitions.py` con on_download_completed/ on_transcribe_completed/ on_render_completed/ on_qa_completed/ on_job_failed (auto-crea transcribe después de download, QA después de render, etc.), 28 tests nuevos. **105/105 pytest passing**.
- **2026-09-06 13:52 UTC** — **Steps 3+13 ✅ (Campaign Engine skeleton)**. Commit `bf456ed` con 5 archivos: `app/campaign_engine/{models,parser,normalizer,__init__}.py` — stubs para que OpenClaw/Mini­Max parsee `source_instructions` a CampaignHints (regex para duration ranges "20-45s", format, language, captions, watermark, keywords) y luego normalize a NormalizedSpec con per-provider defaults. 19 tests nuevos. **124/124 pytest passing**. `5e9aa44` pusheado después (push OK 16:18 UTC).
- **2026-09-06 16:14 UTC** — **Verificación de estado por Clipper**. Sin cambios materiales en backend desde 13:52. Verificado: servicio `clipping-api.service` activo, 10 commits en local (sin push), API escuchando en `100.109.27.21:8080` (solo Tailscale, no localhost), `openclaw-gateway.service` activo. **Bloqueantes siguen iguales:**
  - ~~Git remote equivocado~~ → ✅ RESUELTO (`5e9aa44` pusheado 16:18 UTC).
  - Bind API solo Tailscale → decisión pendiente de Molina.
  - **Steps OpenClaw PENDIENTES (no son backend, son mi trabajo de Clipper):** 1, 2, 12-14 (con LLM real, no regex), 20, 21.
- **2026-09-06 16:42 UTC** — **Step 14 ✅ (Candidate lifecycle: approve → RENDER job + reject)**. `app/services/candidate_lifecycle.py` con `approve_candidate()` (valida contra NormalizedSpec re-derivada, marca como `approved`, auto-crea RENDER job con payload completo `candidate_id/asset_id/campaign_id/source_url/local_path/start_time/end_time/format/captions_required/watermark_url/language`; idempotente vía `_find_render_job_for_candidate`; rollback a `pending` si falla la creación del RENDER job) y `reject_candidate()` (rechazo manual con reason). Endpoints nuevos en `app/api/candidates.py`: `POST /candidates/{id}/approve` y `POST /candidates/{id}/reject` (con `RejectPayload` opcional `{reason}`). Side fix: `_EXCLUDE_RE` en `app/campaign_engine/parser.py` ahora acepta `:` y `=` como separador (caso real "Exclude: keyword"). 11 tests nuevos en `tests/test_candidate_lifecycle.py` (approve happy path, out-of-window reject, exclude_keyword reject, double-approve idempotency, manual reject, endpoint tests, auth). **150/150 pytest verde**. Commit `ee7baf1`.
