# Flujo de arquitectura — fuente única de verdad

> **Documento de referencia.** Cualquier cambio sobre quién hace qué o el orden de pasos requiere tu autorización explícita y se modifica aquí.

---

## Flujo completo

```
1. [OPENCLAW — CRON]
   ↓
   Revisa las campañas disponibles
   ↓
2. [OPENCLAW — MINI­MAX]
   ↓
   Analiza las campañas y decide cuáles interesan
   ↓
3. [OPENCLAW — MINI­MAX]
   ↓
   Analiza las reglas de la campaña seleccionada
   ↓
   Genera / actualiza CampaignSpec
   ↓
4. [VPS — BACKEND]
   ↓
   Guarda campaña + CampaignSpec en PostgreSQL
   ↓
5. [VPS — BACKEND]
   ↓
   Asset Resolver busca los vídeos/assets utilizables
   ↓
6. [VPS — BACKEND]
   ↓
   Registra los vídeos encontrados en PostgreSQL
   ↓
7. [VPS — BACKEND]
   ↓
   Crea DOWNLOAD JOBS para los vídeos
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
12. [OPENCLAW — CRON]
    ↓
    Revisa si hay transcripciones nuevas
    ↓
13. [OPENCLAW — MINI­MAX]
    ↓
    Lee:
      • CampaignSpec
      • transcripción
      • timestamps
      • información del vídeo
    ↓
    Analiza el contenido
    ↓
    Decide qué partes son buenos clips
    ↓
    Genera CANDIDATOS
    ↓
14. [OPENCLAW — CRON / AGENTE]
    ↓
    Guarda los candidatos en el VPS
    ↓
    Comprueba que cumplen las reglas de la campaña
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
20. [OPENCLAW — CRON / AGENTE]
    ↓
    Revisa estado de campañas
    ↓
    Cuando hay suficientes clips aprobados:
    ↓
21. [OPENCLAW]
    ↓
    Inicia / coordina publicación y submission
```

---

## La división fundamental

```
OPENCLAW
├── Decide qué campañas interesan
├── Entiende las reglas con MiniMax
├── Decide qué partes de los vídeos son buenos clips
├── Supervisa
├── Decide qué hacer ante problemas
└── Coordina publicación/submission


VPS BACKEND
├── PostgreSQL
├── Job Queue
├── Asset Resolver
├── Orquestación de estados
├── Recibe resultados del Worker
├── Crea los siguientes jobs
└── Es la fuente de verdad del sistema


WINDOWS WORKER
├── Descarga
├── WhisperX
├── FFmpeg
├── FFprobe
└── Ejecución pesada
```

---

## Estados de los assets / vídeos (modelo mental)

Estos nombres aparecen en los pasos 9 y 11 del flujo:

| Estado | Significado | Trigger que lo establece |
|---|---|---|
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
