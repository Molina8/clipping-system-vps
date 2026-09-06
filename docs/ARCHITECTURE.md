# Arquitectura del Pipeline de Clipping — v1 (referencia vigente)

**Aprobado por:** Molina · **Fecha:** 2026-09-05 16:28 UTC · **Estado:** v1 — referencia bloqueada.

> ⚠️ **Cualquier cambio a este documento requiere autorización explícita de Molina.** No editar sin su OK.

---

## 1. División de roles

### OPENCLAW (Clipper — yo)
- Decido qué campañas interesan.
- Entiendo las reglas con MiniMax-M3.
- Decido qué partes de los vídeos son buenos clips.
- Superviso el pipeline end-to-end.
- Decido qué hacer ante problemas (con gates definidos por Molina).
- Coordino publicación y submission.

### VPS BACKEND (FastAPI + PostgreSQL)
- PostgreSQL: fuente de verdad del sistema.
- Job Queue (motor de jobs ya implementado en Fase 5).
- Asset Resolver.
- Orquestación de estados (campaign, asset, job, clip, qa).
- Recibe resultados del Worker.
- Crea los siguientes jobs según el flujo.

### WINDOWS WORKER (GPU, fuera del VPS)
- Descarga de vídeos.
- WhisperX (transcripción + alineación de palabras).
- FFmpeg (renderizado).
- FFprobe (QA técnica).
- Toda la ejecución pesada.

---

## 2. Flujo paso a paso (21 pasos)

### Fase 1 · Descubrimiento y análisis
1. **[OPENCLAW — CRON]** Revisa las campañas disponibles.
2. **[OPENCLAW — MINIMAX]** Analiza las campañas y decide cuáles interesan.
3. **[OPENCLAW — MINIMAX]** Analiza las reglas de la campaña seleccionada y genera / actualiza CampaignSpec.
4. **[VPS — BACKEND]** Guarda campaña + CampaignSpec en PostgreSQL.

### Fase 2 · Asset Resolution
5. **[VPS — BACKEND]** Asset Resolver busca los vídeos/assets utilizables.
6. **[VPS — BACKEND]** Registra los vídeos encontrados en PostgreSQL.

### Fase 3 · Descarga
7. **[VPS — BACKEND]** Crea DOWNLOAD JOBS para los vídeos.
8. **[WORKER WINDOWS]** Recoge DOWNLOAD JOB → descarga el vídeo → guarda en almacenamiento local del Worker → devuelve resultado al VPS.
9. **[VPS — BACKEND]** Marca el vídeo como DOWNLOADED → crea TRANSCRIBE JOB.

### Fase 4 · Transcripción
10. **[WORKER WINDOWS]** Recoge TRANSCRIBE JOB → ejecuta WhisperX → genera transcripción + timestamps → devuelve resultado al VPS.
11. **[VPS — BACKEND]** Guarda transcripción en PostgreSQL → marca el vídeo como TRANSCRIBED.

### Fase 5 · Selección de clips
12. **[OPENCLAW — CRON]** Revisa si hay transcripciones nuevas.
13. **[OPENCLAW — MINIMAX]** Lee CampaignSpec + transcripción + timestamps + info del vídeo → analiza el contenido → decide qué partes son buenos clips → genera CANDIDATOS.
14. **[OPENCLAW — CRON / AGENTE]** Guarda los candidatos en el VPS → comprueba que cumplen las reglas de la campaña.

### Fase 6 · Renderizado
15. **[VPS — BACKEND]** Crea RENDER JOBS para los candidatos aprobados.
16. **[WORKER WINDOWS]** Recoge RENDER JOB → FFmpeg corta el fragmento → aplica formato 9:16, subtítulos, watermark, resolución, etc. → genera CLIP FINAL → devuelve resultado al VPS.

### Fase 7 · QA
17. **[VPS — BACKEND]** Registra clip generado → crea QA JOB.
18. **[WORKER WINDOWS]** Recoge QA JOB → FFprobe / validaciones técnicas (duración, resolución, FPS, codec, audio, integridad, etc.) → devuelve resultado QA.
19. **[VPS — BACKEND]** Guarda resultado QA:
    - **PASS** → clip aprobado.
    - **FAIL** → clip rechazado / reintento.
    - **REVIEW** → revisión humana / OpenClaw.

### Fase 8 · Publicación
20. **[OPENCLAW — CRON / AGENTE]** Revisa estado de campañas. Cuando hay suficientes clips aprobados, avanza.
21. **[OPENCLAW]** Inicia / coordina publicación y submission.

---

## 3. Tipos de job del Worker (extensión de Fase 5)

| job_type   | Quién lo crea | Quién lo ejecuta | Output persistido en VPS              |
|------------|---------------|------------------|----------------------------------------|
| `download`   | VPS backend   | Worker Windows    | vídeo en disco local + metadatos        |
| `transcribe` | VPS backend   | Worker Windows    | `{language, duration, segments, words}` |
| `render`     | VPS backend   | Worker Windows    | clip final + metadatos                  |
| `qa`         | VPS backend   | Worker Windows    | `{status: pass/fail/review, validations}` |

---

## 4. Estados principales (a modelar en el VPS)

### Campaign
- `pending` → `selected` → `rules_extracted` → `processing` → `completed` / `archived`

### Asset (vídeo)
- `discovered` → `download_pending` → `downloaded` → `transcribe_pending` → `transcribed` / `failed`

### Clip candidate
- `proposed` → `validated` / `rejected` → `render_pending` → `rendered` → `qa_pending` → `approved` / `rejected`

### Job (ya existe en Fase 5)
- `pending` → `assigned` → `processing` → `completed` / `failed` (+ `cancelled`, `retry`)

---

## 5. Cambios al documento

| Fecha       | Versión | Cambio                          | Autorizado por |
|-------------|---------|----------------------------------|----------------|
| 2026-09-05  | v1      | Creación inicial del flujo v1     | Molina         |

---

## 6. Pendiente operativo (no bloquea la lectura del documento)

1. Modelo de ejecución del loop (daemon vs bajo demanda vs híbrido).
2. Decision gates: qué decide OPENCLAW solo y qué requiere OK de Molina.
3. Credenciales de fuentes externas: ubicación (`.env` del VPS vs SecretStore).
4. Modelo LLM final para Campaign Analyzer / Clip Selector.
5. Forma de almacenamiento de CampaignSpec (SQL con JSONB vs Pydantic/dict).
