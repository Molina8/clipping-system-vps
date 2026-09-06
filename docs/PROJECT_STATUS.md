# PROJECT_STATUS.md — Estado del proyecto Clipping

> **Documento hermano** de `docs/ARCHITECTURE.md` (en `/opt/clipping-system/docs/`).
> Refleja el estado real **punto por punto**: qué está hecho, qué no, qué está a medias, riesgos y próximos pasos.
> Actualizado en cada cambio relevante por **Clipper** (agente OpenClaw).
>
> **Última actualización:** 2026-09-06 11:05 UTC
> **Fuente de verdad técnica:** el código en `/opt/clipping-system/` y este propio doc.
> **Fuente de verdad funcional:** el servicio corriendo en `100.109.27.21:8080` (Tailscale).

---

## TL;DR

- 🟢 **Backend MVP arrancado y respondiendo** — FastAPI en `100.109.27.21:8080`, Postgres nativo en `localhost:5432`, DB `clipping` con tabla `jobs`.
- ✅ **Tests pasan** — 47/47 pytest en 1.36s (2 warnings deprecation menores, sin fallos).
- 🟢 **Servicio systemd robusto** — `clipping-api.service` enabled, Restart=always, MemoryMax=512M, hardening completo (ProtectSystem=strict, ProtectHome, ReadWritePaths). Sobrevive reboots sin problema.
- 🟡 **3 fases hechas** (DB básica, API parcial, Job Queue básica), **2 a medias** (preparación), **1 hecha nueva** (Worker Integration), **1 pendiente** (testing E2E real VPS↔Worker).
- 🔴 **2 bloqueantes urgentes:** (1) NO hay git repo en `/opt/clipping-system/`; (2) API bind solo a Tailscale, no localhost (decisión pendiente).
- ✅ **Tailscale OK** — sigue como root (correcto), mesh con `molina` (PC Windows) y `vps-5764d01a` (este VPS) online.

---

## Estado por fase (mapeo al doc de arquitectura)

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
- ❌ **NO hay git repo** — bloqueante, no se puede versionar
- ❌ NO hay `.gitignore`
- ❌ NO hay `.env.example` (sí hay `.env.bak-phase4-pre-api-vars`, sugiere flujo de fases previo)
- ❌ NO hay `docker-compose.yml` ni `Dockerfile` — **aceptable**, Postgres nativo consume menos RAM
- ❌ NO hay `README.md` en raíz del proyecto

### 🟡 Fase 3 — Base de datos — **PARCIAL**

- ✅ PostgreSQL 16 nativo instalado y corriendo
- ✅ DB `clipping` creada (owner `postgres`)
- ✅ Rol `clipping_api` con permisos sobre `clipping`
- ✅ Alembic configurado (`alembic.ini`, `alembic/env.py`)
- ✅ Migración `0001_create_jobs_table.py` aplicada → tabla `jobs` existe
- ❌ **Faltan modelos**: `campaign.py`, `worker.py`, `asset.py` (solo `job.py` existe)
- ❌ **Faltan migraciones** para `campaigns`, `workers`, `assets`
- ❌ **Faltan tablas**: `campaigns`, `workers`, `assets`, `results`

### 🟡 Fase 4 — API FastAPI — **PARCIAL**

- ✅ FastAPI + Uvicorn funcionando (PID 81509, user `clipping`)
- ✅ Auth Bearer token implementado (`app/auth.py` + `app/config.py`)
- ✅ Endpoints jobs: `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`
- ✅ Endpoints worker: `GET /worker/jobs/next`, `POST /worker/jobs/{id}/{start|result|fail|heartbeat}`
- ✅ `GET /health`, `GET /system/info`
- ❌ **Faltan endpoints `/workers/*`**: `POST /workers/register`, `POST /workers/heartbeat`, `GET /workers`, `GET /workers/{id}`
- ❌ **Faltan endpoints `/campaigns/*`**: `POST /campaigns`, `GET /campaigns`, `GET /campaigns/{id}`
- ❌ **Falta estructura**: `app/api/{campaigns,workers,health}.py` (solo `jobs.py` y `system.py`)
- ❌ **Falta `app/schemas/`** (Pydantic schemas separados — el doc lo pide)
- 🟡 **Bind problemático**: API escucha SOLO en `100.109.27.21:8080` (Tailscale), NO en `127.0.0.1:8080` → health checks locales y curl desde el propio VPS fallan. Hay que añadir bind a localhost o `0.0.0.0` con firewall restrictivo (iptables/nftables limitando a `100.64.0.0/10`).

### 🟡 Fase 5 — Job Queue — **PARCIAL**

- ✅ Tabla `jobs` con esquema completo (inferido de migración)
- ✅ Estados: `pending`, `assigned`, `processing`, `completed`, `failed`, `retry`, `cancelled` (a confirmar con `app/models/job.py`)
- ✅ Locking con `SELECT FOR UPDATE` (`tests/test_concurrency.py` sugiere implementación correcta)
- ✅ Tipos: `health`, `download`, `transcribe`, `render`, `qa` (a confirmar en código)
- 🟡 **Pendiente verificar**: que los tests pasen (`pytest` no ejecutado aún desde esta sesión)

### ✅ Fase 6 — Integración Worker — **HECHA** (2026-09-06)

- ✅ Código del Worker Windows leído y comparado endpoint-a-endpoint
- ✅ Endpoints `POST /worker/register` y `POST /worker/heartbeat` añadidos al backend (eran los que el Worker esperaba)
- ✅ Tabla `workers` creada en Postgres (Alembic 0002)
- ✅ Schemas Pydantic espejo exacto del Worker (`WorkerRegistration`, `Heartbeat`)
- ✅ E2E verificado con curl + python contra el servicio real: register/heartbeat/list/get → 200, ghost → 404, sin auth → 401
- ✅ 8 tests unitarios nuevos en `tests/test_workers.py` — **8/8 passing**
- ✅ Commit `a88bbe0` (workers) + `e01db81` (docs) en local
- 🟡 Pendiente: push a GitHub (bloqueado por decisión de Molina sobre repo/Write access)
### ✅ Fase 7 — Testing — **HECHA** (tests unitarios)

- ✅ Tests escritos y pasando:
  - `tests/test_health.py` (básico)
  - `tests/test_auth.py` (auth Bearer)
  - `tests/test_jobs.py` (CRUD jobs)
  - `tests/test_concurrency.py` (locking)
  - `tests/test_state_transitions.py` (transiciones de estado)
- ✅ **47/47 tests passing en 1.36s** (verificado 2026-09-06 11:10 UTC, 2 warnings deprecation sin impacto)
- ❌ **NO hay tests de integración** reales VPS ↔ Worker
- ❌ **NO hay CI/CD** configurado

---

## Estado de infraestructura NO-documentada

### Tailscale

- ✅ Daemon `tailscaled` corriendo como **root** (correcto, NO migrar a user — tailscaled necesita NET_ADMIN)
- ✅ State en `/var/lib/tailscale/tailscaled.state` (root, correcto)
- ✅ Servicio systemd de sistema `tailscaled.service` activo desde 2026-09-03
- ✅ IP Tailscale VPS: `100.109.27.21` (IPv4) + `fd7a:115c:a1e0::262e:1b16` (IPv6)
- ✅ Mesh:
  - `vps-5764d01a` (linux, online)
  - `molina` (windows, online) ← **el PC Worker de Molina, listo**
  - `port477` (windows, **offline desde hace 19 días** — revisar si era otro PC tuyo o un nodo olvidado)
- ✅ MagicDNS funcionando
- ✅ DERP más cercano: London (3.7ms), Madrid 19.9ms
- ✅ Conectividad UDP + IPv4 + IPv6 OK
- ⚠️ **No hay ACL local** — todo se gestiona en la policy del Tailnet (cloud). OK para nuestro caso (solo 2 nodos).
- ✅ **Decisión sobre la migración root → ubuntu: NO afecta a Tailscale**. tailscaled DEBE ser root (gestiona interfaz `tailscale0`). La migración a `ubuntu` afecta solo a OpenClaw y a servicios user-level. No tocar.

### OpenClaw + skills

- ✅ Gateway `openclaw-gateway.service` user-level, puerto `18789`, PID 108263
- ✅ Skills operativas: `blogwatcher`, `xurl`, `instagram-content-studio`, `youtube-api-skill`
- ✅ Plugins provider: `agntdata-youtube@1.0.15`, `agntdata-instagram@1.0.15`
- ✅ Telegram bot operativo
- 🟡 `nano-pdf` y `summarize` deshabilitados (sin binarios Linux)

### Postgres

- ✅ PostgreSQL 16 nativo, en `127.0.0.1:5432` (solo localhost, correcto)
- ✅ DB `clipping` con owner `clipping_api`
- ✅ Conexión desde API verificada (`{"status":"ok","database":"ok"}`)

### Backups

- 🟡 Solo `.env.bak-phase4-pre-api-vars` (1 backup manual)
- ❌ NO hay backups automatizados del código
- ❌ NO hay backups de la DB (`pg_dump` no programado)

---

## 🔴 Bloqueantes / Riesgos urgentes

| # | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| 1 | **NO hay git repo** en `/opt/clipping-system/` | Imposible versionar, rollback, auditar cambios | `git init` + primer commit con estado actual |
| 2 | **API bind solo a Tailscale** — no responde en localhost | Health checks locales, curl desde VPS, herramientas internas fallan | Cambiar bind a `0.0.0.0:8080` con firewall iptables que limite a `100.64.0.0/10` (Tailscale CGNAT) |

## 🟡 Decisiones pendientes

- [ ] **A/B/C del tool executor** — problema estructural de OpenClaw que afecta a TODO trabajo futuro (instalaciones largas pueden matar mi sesión). Sin B, trabajos >2 min son arriesgados. **(Sigue abierto desde el 6 sept 10:52)**
- [ ] **Bind de la API**: ¿añadir localhost, dejar solo Tailscale, o `0.0.0.0` con firewall?
- [ ] **`.env.example`**: ¿generar uno con todas las variables actuales? (sin valores reales)
- [ ] **Worker Windows**: ¿Molina me pasa el código del Worker o me da acceso a su repo?

## 🟢 Logros verificados hoy (2026-09-06)

- ✅ Backend localizado y auditado (`/opt/clipping-system/`)
- ✅ API health respondiendo `{"status":"ok","database":"ok"}` desde `100.109.27.21:8080/health`
- ✅ Tailscale auditado y verificado post-migración (sin acción necesaria)
- ✅ Estructura de OpenAPI inspeccionada (10 endpoints)
- ✅ DB schema inspeccionado (tabla `jobs` con 16 columnas, CHECK constraint de estados, índices de claim)
- ✅ `pytest` ejecutado: **47/47 passing en 1.36s** (luego 55/55 con los 8 nuevos de workers en 1.89s)
- ✅ `clipping-api.service` inspeccionado: enabled, Restart=always, MemoryMax=512M, hardening completo
- ✅ `PROJECT_STATUS.md` creado y corregido (este doc)

---

## 📋 Próximos pasos (orden propuesto)

1. **`git init` + commit inicial** en `/opt/clipping-system/` con todo el estado actual
2. **Crear `.env.example`** a partir del `.env` actual (sin secretos)
3. **Decidir bind API** (recomendación: `0.0.0.0` + iptables restrictivo a `100.64.0.0/10`)
4. **Modelos faltantes**: `campaign.py`, `asset.py` + migraciones (worker.py ya hecho)
5. **Endpoints faltantes**: `/campaigns/*` (los `/worker/*` están todos)
6. **Tests E2E** reales VPS ↔ Worker (cliente Python que dispara el flujo completo)
7. **CI/CD** (opcional, futuro)
8. **Push a GitHub** del commit `a88bbe0` (pendiente decisión de Molina sobre Molina8/clipping-windows-worker)

---

## Changelog

- **2026-09-06 11:05 UTC** — Versión inicial creada por Clipper tras auditoría completa del backend y Tailscale. Detecta estado real: backend parcialmente montado (no 0/7 como se dijo antes), Tailscale OK post-migración root→ubuntu, 3 bloqueantes urgentes identificados.
- **2026-09-06 11:55 UTC** — **Fase 6 ✅ (Worker Integration)**. Endpoints `/worker/register` y `/worker/heartbeat` añadidos. Tabla `workers` + Alembic 0002. 8/8 tests passing. E2E real verificado. Commits `a88bbe0` + `e01db81`.
- **2026-09-06 11:10 UTC** — **Corrección**: `clipping-api.service` SÍ existe, está enabled y con hardening robusto (PID 81509, Restart=always, MemoryMax=512M, ProtectSystem=strict, ProtectHome). Pytest ejecutado: 47/47 passing en 1.36s. Bloqueante #1 (systemd) eliminado; quedan 2 (git repo + API bind).
- **2026-09-06 12:10 UTC** — **Fase 6 (Worker Integration) marcada ✅**. Commit `a88bbe0` con +608 líneas: modelo Worker, migración 0002, schemas Pydantic espejo del Worker Windows, service, router, 8 tests unitarios, main.py patched. E2E real verificado: register/heartbeat/list/get → 200, ghost heartbeat → 404, sin auth → 401. Pytest suite global: 55/55 passing en 1.89s. Commit en local; push pendiente de decisión de Molina.
