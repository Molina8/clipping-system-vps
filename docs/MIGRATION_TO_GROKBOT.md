# Migración: CRON + agente IA (Clipper) → Grokbot

**Fecha:** 2026-09-19
**Autor:** Clipper (OpenClaw) para Molina
**Estado:** borrador — Molina decide si migrar.

---

## 1. Por qué migrar

Molina está considerando migrar el lado "inteligente" del pipeline (decisiones que requieren LLM) desde Clipper (OpenClaw + MiniMax-M3) hacia Grokbot (xAI / Grok). Razones declaradas:

- Clipper falla a menudo en bucles de razonamiento sobre bugs del propio pipeline.
- Grokbot puede tener acceso a herramientas / contextos más frescos sobre bugs nuevos.
- Clipper tiene context bloat (4 sesiones recientes, memoria persistente grande, skills custom) que afecta la calidad de las decisiones.

---

## 2. Estado actual — quién hace qué

El pipeline v2 (21 pasos, `docs/architecture_flow.md`) tiene **dos tipos de componentes**:

| Tipo | Componente | Hoy (Clipper) | Mañana (Grokbot) |
|---|---|---|---|
| **CRON determinístico** | `whop-discovery-cron` (paso 1) | script Python `scripts/whop_discovery.py` | **se queda igual** — no depende de LLM |
| **CRON determinístico** | `download-enqueue-tick` (paso 7) | script Python `scripts/download_enqueue_tick.py` | **se queda igual** |
| **CRON inteligente** | `brief-reader-tick` (paso 3a) | skill OpenClaw + agente MiniMax-M3 | **migra a Grokbot** |
| **CRON inteligente** | `drive-resolver-tick` (paso 3b) | skill OpenClaw + agente MiniMax-M3 | **migra a Grokbot** |
| **CRON inteligente** | `dropbox-resolver-tick` (paso 3b') | skill OpenClaw + agente MiniMax-M3 | **migra a Grokbot** |
| **CRON inteligente** | `campaign-scorer-tick` (paso 3c) | skill OpenClaw + agente MiniMax-M3 | **migra a Grokbot** |
| **CRON inteligente** | `clip-decider-tick` (paso 12) | skill OpenClaw + agente MiniMax-M3 | **migra a Grokbot** |
| **CRON inteligente** | `campaign-publish-tick` (paso 21) | skill OpenClaw + agente MiniMax-M3 | **migra a Grokbot** |
| **CRON wake-up dummy** | `clip-decider-tick` wake | `kind: command` (print) | **se queda igual** |
| **VPS backend** | FastAPI + Postgres + Alembic | código del VPS | **se queda igual** (no migra) |
| **Worker Windows** | GPU + WhisperX + FFmpeg | código de Molina (no mío) | **fuera de scope** (no migra) |

**Resumen:** 7 cron "inteligentes" migran a Grokbot. El resto se queda en Clipper/scripts.

---

## 3. Cómo migrar cada cron

Cada cron "inteligente" hoy es un **job OpenClaw** con:
- `payload.kind: "agentTurn"`
- `payload.message: <instrucciones largas del skill>`
- `payload.model: "MiniMax-M3"` (MiniMax-M3)
- `delivery.to: "-5370574765"` (Telegram)

Para migrar a Grokbot:

1. **Crear el equivalente en Grokbot** (API de Grokbot). Cada cron se traduce a un "task" Grokbot con:
   - Mismo `message` (las instrucciones)
   - Mismo `delivery.to` (Telegram) — Grokbot tiene que poder entregar a Telegram igual
   - `model: "grok-2"` (o el que recomiende xAI)
   - Acceso a las mismas tools (exec para curl a la API, read para MEMORY.md si decide)
2. **Mantener el job OpenClaw como `--disabled`** (por si rollback), no eliminar.
3. **Verificar** que el primer run de Grokbot produce el mismo resultado que Clipper (e.g. campaign 6 con 93 assets, mismo score).
4. **Iterar** sobre el prompt si Grokbot falla (xAI tiene system prompts distintos a OpenClaw).

---

## 4. Cosas que NO migran

- **`MEMORY.md`** — es personal (preferencias de Molina, hechos del proyecto, modo "1 campaña a la vez"). Clipper la mantiene. Grokbot no debería leerla directamente (puede contener datos sensibles / contexto privado).
- **El VPS backend (FastAPI + Postgres)** — es código del VPS, no LLM. Grokbot solo lo invoca via API (`http://100.109.27.21:8080`).
- **El Worker Windows** — es código de Molina en su PC. No migra.
- **Los secrets** (`/etc/openclaw/cron-secrets.env`, `.env`) — siguen siendo del VPS. Grokbot los lee de su propio config si los necesita.
- **Los crons determinísticos** (`whop_discovery.py`, `download_enqueue_tick.py`) — siguen siendo scripts Python locales.

---

## 5. Cosas que SÍ migran

- Las **instrucciones del skill** (`message` de cada cron) — el texto exacto que le dice al LLM qué hacer.
- La **lógica de orquestación** entre crons (pipeline v2 encadenado por estado en BD).
- El **conocimiento operacional** que Clipper tiene sobre el pipeline (bug history, decisiones tomadas). Esto se traduce a un **system prompt** para Grokbot que documente el pipeline v2.

---

## 6. Riesgos y cosas a vigilar

| Riesgo | Mitigación |
|---|---|
| Grokbot no respeta `--limit 1` | Verificar primer run. Si falla, hardcodear `LIMIT=1` en el prompt. |
| Grokbot no sabe qué es `_is_skippable` (filtro folder/brand_asset) | Documentar el filtro en el system prompt de Grokbot. |
| Grokbot entrega a un Telegram chat equivocado | Forzar `delivery.to: "-5370574765"` siempre. |
| Grokbot no tiene acceso a `MEMORY.md` | Crear un **system prompt** Grokbot-side con los hechos relevantes (e.g. "modo 1 campaña activo", "blacklist por nombre Coinpoker"). |
| xAI cambia la API / modelo | Migración es 1 prompt + 1 cron. Rollback = re-habilitar OpenClaw cron. |
| Token costs de Grokbot son distintos | Medir 1 semana antes de decidir producción. |

---

## 7. Estimación de tiempo

| Fase | Tiempo | Notas |
|---|---|---|
| Escribir 7 system prompts Grokbot | 2-3h | Plantilla + adaptar cada skill |
| Crear 7 tasks Grokbot | 30min | API de xAI + auth |
| Validar con 1 campaña end-to-end | 1-2 días | Igual que Clipper |
| Rollback a Clipper si falla | 15min | `--enable` los cron OpenClaw disabled |
| **Total** | **~2 días** | Sin contar iteraciones |

---

## 8. Plan de ejecución (si Molina aprueba)

1. **Hoy:** Molina lee este doc y decide.
2. **Día 1:** Crear system prompts Grokbot para los 7 skills.
3. **Día 1:** Crear 7 tasks Grokbot con `model: grok-2`, `delivery.to: -5370574765`.
4. **Día 1:** Deshabilitar cron OpenClaw equivalentes (`--disable`, no eliminar).
5. **Día 1-2:** Validar end-to-end sobre 1 campaña (id=6 ForgeGUI actual, 93 assets).
6. **Día 2:** Si OK → dejar Grokbot. Si KO → rollback (re-habilitar OpenClaw cron).

---

## 9. Decisión recomendada

**Mi recomendación honesta (Clipper):** migrar 3-4 cron primero (los que más fallos me dan: `clip-decider-tick`, `campaign-scorer-tick`, `brief-reader-tick`) y dejar los otros 3-4 con Clipper hasta validar. Rollback granular si algo va mal.

**Pero Molina decide.** No tengo ego en esto. Si decide migrar todo, lo hacemos. Si decide quedarse con Clipper y arreglar mis bugs, también.
