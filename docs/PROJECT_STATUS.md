# PROJECT_STATUS.md — Estado del proyecto Clipping

> **Documento hermano** de `docs/ARCHITECTURE.md` (en `/opt/clipping-system/docs/`) y de `docs/architecture_flow.md` (el doc de Molina con los 21 pasos, fuente única de verdad para flujo y responsabilidades).
> Refleja el estado real **punto por punto**: qué está hecho, qué no, qué está a medias, riesgos y próximos pasos.
> Actualizado en cada cambio relevante por **Clipper** (agente OpenClaw).
>
> **Última actualización:** 2026-09-06 13:18 UTC
> **Fuente de verdad técnica:** el código en `/opt/clipping-system/` y este propio doc.
> **Fuente de verdad funcional:** el servicio corriendo en `100.109.27.21:8080` (Tailscale).

---

## TL;DR

- 🟢 **Backend MVP arrancado y respondiendo** — FastAPI en `100.109.27.21:8080`, Postgres nativo en `localhost:5432`, DB `clipping` con tablas `jobs`, `workers`, `campaigns`.
- ✅ **Tests pasan** — **69/69 pytest verde** en 2.08s (47 originales + 8 workers + 14 campaigns), 2 warnings deprecation menores.
- 🟢 **Servicio systemd robusto** — `clipping-api.service` enabled, Restart=always, MemoryMax=512M, hardening completo (ProtectSystem=strict, ProtectHome, ReadWritePaths). Sobrevive reboots sin problema.
- 🟢 **Step 4 hecho** (architecture_flow.md) — Campaign + CampaignSpec + multi-source. Endpoints `POST/GET /campaigns`, `GET/PATCH /campaigns/{id}`. Soporte para twitter/youtube/instagram/tiktok/reddit/twitch/manual/other. Commit `ce5a90a`.
- 🟡 **Steps 5-19 pendientes** — Asset + Asset Resolver + transiciones de estado + Render/QA routing + Campaign Engine skeleton.
- 🔴 **Push bloqueado** — el remote actual apunta a `Molina8/clipping-windows-worker` (repo del Worker de Molina, NO pusheo ahí). Necesito un repo del VPS para subir los commits.
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

### 🟡 Fase 2 — Preparación — **PARCIAL**

- ✅ `/opt/clipping-system/` existe (owner `clipping:clipping`, creado 2026-09-04)
- ✅ `.env` existe (con `CLIPPING_DB_*`, `API_*`, `API_TOKEN`)
- ✅ `alembic.ini` y `alembic/` configurados
- ✅ `.env.example` creado en commit `80ca085`
- ✅ Git repo inicializado con 7 commits en local
- ❌ NO hay `docker-compose.yml` ni `Dockerfile` — **aceptable**, Postgres nativo consume menos RAM
- ❌ NO hay `README.md` en raíz del proyecto
- ❌ NO hay `.gitignore` formal

### ✅ Fase 3 — Base de datos — **HECHA**

- ✅ PostgreSQL 16 nativo instalado y corriendo
- ✅ DB `clipping` creada (owner `postgres`)
- ✅ Rol `clipping_api` con permisos sobre `clipping`
- ✅ Alembic configurado (`alembic.ini`, `alembic/env.py`)
- ✅ **3 migraciones aplicadas**:
  - `0001_create_jobs_table.py` → tabla `jobs` con 16 columnas, CHECK de estados, índices de claim
  - `0002_create_workers_table.py` → tabla `workers` con status enum, gpu_name, gpu_available, capabilities JSONB, last_heartbeat_at, etc.
  - `0003_create_campaigns_table.py` → tabla `campaigns` con source_provider enum, source_id, source_url, source_metadata JSONB, spec JSONB, contadores denormalizados

### ✅ Fase 4 — API FastAPI — **HECHA**

- ✅ FastAPI + Uvicorn funcionando (user `clipping`)
- ✅ Auth Bearer token implementado (`app/auth.py` + `app/config.py`)
- ✅ **15 endpoints implementados**:
  - **Health/system**: `GET /health`, `GET /system/info`
  - **Jobs**: `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`
  - **Worker integration**: `POST /worker/register`, `POST /worker/heartbeat`, `GET /worker`, `GET /worker/{id}`, `GET /worker/jobs/next`, `POST /worker/jobs/{id}/start`, `/heartbeat`, `/result`, `/fail`
  - **Campaigns**: `POST /campaigns`, `GET /campaigns` (con `?status` y `?source_provider`), `GET /campaigns/{id}`, `PATCH /campaigns/{id}`
- ✅ Pydantic schemas separados (`app/schemas/{worker,campaign}.py`)
- 🟡 **Bind problemático**: API escucha SOLO en `100.109.27.21:8080` (Tailscale), NO en `127.0.0.1:8080`. Decisión pendiente (añadir localhost o `0.0.0.0` con firewall restrictivo)

### ✅ Fase 5 — Job Queue — **HECHA**

- ✅ Tabla `jobs` con esquema completo, CHECK de estados (`pending/assigned/processing/completed/failed/retry/cancelled`)
- ✅ Locking con `SELECT FOR UPDATE` para evitar que 2 Workers pillen el mismo job (`test_concurrency.py`)
- ✅ Tipos: `health`, `download`, `transcribe`, `render`, `qa`
- ✅ atomic claim en `get_next_job_endpoint`
- 🟡 **Pendiente**: lógica para que `POST /worker/jobs/{id}/result` con `status='completed'` cree automáticamente el siguiente job (TRANSCRIBE después de DOWNLOAD, etc.). Eso es parte de Step 9+11+15+17 de `architecture_flow.md`, va en Fase C.

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
- ✅ **69/69 pytest verde**
- ✅ **E2E real verificado**: creación multi-source (youtube/twitter/manual), filtros `?source_provider`, PATCH status transitions, validación → 422, sin auth → 401

### ❌ Steps 5-19 (architecture_flow.md) — **PENDIENTE**

- ❌ Step 5-6: Asset model + Asset Resolver (buscar vídeos/assets utilizables, registrar con estado `pending`)
- ❌ Step 7, 9, 11, 15, 17, 19: transiciones de estado de assets (`pending → downloaded → transcribed → approved/rejected/review/published`)
- ❌ Step 3, 13: Campaign Engine skeleton (`app/campaign_engine/{parser,rule_normalizer,models}.py`) para que OpenClaw/MiniMax pueda rellenar `spec`

### ✅ Fase 7 — Testing — **PARCIAL**

- ✅ **69/69 tests passing en 2.08s** (47 originales + 8 workers + 14 campaigns)
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
| 1 | **Git remote apunta a `Molina8/clipping-windows-worker`** (repo del Worker de Molina, NO pusheo ahí) | Commits locales sin backup remoto | Crear repo nuevo del VPS o que Molina dé Write access a `Molina8/<vps-repo>` o `jarvismolinabot/<vps-repo>` |
| 2 | **API bind solo a Tailscale** | curl desde localhost falla, herramientas internas no pueden hablarle | Decidir bind: localhost + Tailscale, o `0.0.0.0` con firewall restrictivo |

## 🟡 Decisiones pendientes

- [ ] **Repo destino del VPS** para push (ver bloqueante #1)
- [ ] **A/B/C del tool executor** — problema estructural desde 10:52
- [ ] **Bind de la API**: ¿añadir localhost, dejar solo Tailscale, o `0.0.0.0` con firewall?

## 🟢 Logros verificados hoy (2026-09-06)

- ✅ **69/69 pytest verde** (47 + 8 workers + 14 campaigns)
- ✅ **15 endpoints API** en OpenAPI
- ✅ **3 tablas en DB**: jobs, workers, campaigns (con índices y CHECK constraints)
- ✅ **3 migraciones Alembic** aplicadas (0001, 0002, 0003)
- ✅ **E2E real verificado** con curl + Python contra el servicio: jobs CRUD, worker integration (register/heartbeat/list/get), campaigns (multi-source, filtros, status transitions)
- ✅ **7 commits en local**: `04649ae`, `80ca085`, `a88bbe0`, `e01db81`, `9c14867`, `ce5a90a` (+ el de status de este doc)
- ✅ `clipping-api.service` activo y robusto

---

## 📋 Próximos pasos (orden propuesto)

1. **Decidir repo destino del VPS** (necesario antes de cualquier push)
2. **Fase B** (Steps 5-6): Asset model + Asset Resolver
3. **Fase C** (Steps 7, 9, 11, 15, 17, 19): transiciones de estado + Render/QA routing automático
4. **Fase D** (Steps 3+13): Campaign Engine skeleton (parser + rule_normalizer)
5. **Tests E2E** reales VPS↔Worker (cliente Python que dispara el flujo completo)
6. **CI/CD** (opcional, futuro)

---

## Changelog

- **2026-09-06 11:05 UTC** — Versión inicial creada por Clipper tras auditoría completa del backend y Tailscale.
- **2026-09-06 11:55 UTC** — **Fase 6 ✅ (Worker Integration)**. Endpoints `/worker/register` y `/worker/heartbeat` añadidos. Tabla `workers` + Alembic 0002. 8/8 tests passing. E2E real verificado. Commits `a88bbe0` + `e01db81`.
- **2026-09-06 12:10 UTC** — **Fase 6 (Worker Integration) marcada ✅**. Commit `a88bbe0` con +608 líneas.
- **2026-09-06 13:18 UTC** — **Step 4 ✅ (Campaign storage + multi-source)**. Commit `ce5a90a` con 7 archivos: modelo Campaign con source_provider/source_id/source_url/source_metadata, CampaignSpec (reglas agnósticas del proveedor), migración 0003, 4 endpoints (`POST/GET /campaigns`, `GET/PATCH /campaigns/{id}`), service, schemas Pydantic, 14 tests nuevos (69/69 verde). Soporte multi-proveedor integrado desde diseño (twitter/youtube/instagram/tiktok/reddit/twitch/manual/other). E2E verificado: creación multi-source, filtrado por source_provider, transiciones de status, validación Pydantic → 422, sin auth → 401. Commit en local; **push pendiente de repo destino del VPS** (el remote actual apunta al repo del Worker de Molina por error previo).
