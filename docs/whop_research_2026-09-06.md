# Whop Campaign Extraction - Final Report
**Date:** 2026-09-06 20:50 UTC
**Target URL:** https://whop.com/codiant/exp_XdOopaairb4g5w/app/
**Business:** Codiant (biz_F6pWuwRIJXpn5f)

## TL;DR

No pude extraer las campañas de "Content Rewards" de la URL objetivo. El iframe app renderiza contenido dinámicamente con JavaScript que requiere un browser real para ejecutarse. Solo tengo acceso a una API key de Account-level que NO puede listar experiences (Whop da "This Bot was not found" o 401). 

El Codiant storefront en `whop.com/codiant/` dice literalmente: **"Looks like there aren't any posts yet. Be the first one to make a post!"** — no hay posts públicos. No hay productos listables en la storefront.

## Hallazgos

### 1. Confirmado via `/accounts/me` (HTTP 200)
```
{
  "id": "biz_F6pWuwRIJXpn5f",
  "title": "Codiant",
  "country": "es",
  "business_type": "services",
  "industry_group": "tech_and_development"
}
```

### 2. Codiant storefront (`whop.com/codiant/`, 995KB HTML):
- Title: "Codiant | Whop"
- Texto visible: "Created by jesus Molina | 1 joined"
- Estado: **"Looks like there aren't any posts yet. Be the first one to make a post!"**
- Products page (`/codiant/products`): 200 OK pero sin contenido visible

### 3. La iframe app (`/codiant/exp_XdOopaairb4g5w/app/`):
- Es una Whop App que Molina instaló en Codiant
- 984KB HTML — solo el shell de Next.js con JS bundles
- Rutas probadas (todas devuelven 200 con el mismo SPA shell):
  - `/app/` — entry principal
  - `/app/feed` — chat/mensajes (no campaigns)
  - `/app/about` — about info
  - `/app/posts` — posts feed (probablemente vacío)
  - `/app/courses` — courses feed
- El contenido se renderiza via JavaScript que carga datos de una API interna de la app
- No hay datos embebidos en el HTML inicial (ni JSON-LD ni scripts con datos)

### 4. JS bundle encontrado con keywords relevantes:
`/_web/assets/gha-34057658267-1/index-CFFhZ9DQ.js` contiene menciones a:
- `content-rewards`
- `content_rewards`
- `campaign`
- Estos son nombres de features de Whop, no datos de las campañas específicas de Molina

### 5. Marketplace search para encontrar products de Codiant:
| query | total | de Codiant |
|-------|-------|-----------|
| codiant | 20 | 0 |
| clipper | 20 | 0 |
| claude | 20 | 0 |
| content-rewards | 20 | 0 |
| claude-ai | 2 | 0 |

→ **Cero productos de Codiant visibles via marketplace search.**

### 6. Whop API endpoints probados:

| Endpoint | HTTP | Resultado |
|----------|------|-----------|
| `GET /accounts/me` | 200 | ✅ Codiant confirmado |
| `GET /experiences?account_id=biz_XXX` | 404 | "This Bot was not found" |
| `GET /experiences?company_id=biz_XXX` | 404 | "This Bot was not found" |
| `GET /experiences/exp_XdOopaairb4g5w` | 401 | Auth failed |
| `GET /experiences?first=1` | 401 | Auth failed |
| `GET /products?company_id=biz_XXX` | 200 | Solo 1 product random (HR Career Hub, no Codiant) |
| `GET /products?account_id=biz_XXX` | 200 | Mismo product random |
| `GET /products?experience_id=exp_XXX` | 200 | 20 products globales, ninguno de Codiant |
| `GET /apps?company_id=biz_XXX` | 401 | Auth failed |
| `GET /apps/me` | 400 | "This app route is already taken" |
| `GET /experiences/exp_XdOopaairb4g5w?company_id=biz_XXX` | 401 | Auth failed |

## Diagnóstico

La Account API key de Codiant tiene permisos de Account-level pero NO tiene permisos de bot/app para acceder a experiences creadas por apps instaladas. El error "This Bot was not found" en /experiences indica que Whop busca un bot context (probablemente el app bot que creó la experience) que este key NO es.

## Lo que haría falta para extraer las campañas

Una de estas:
1. **Crear una App específica con bot identity** y darme su API key (eso me daría acceso a /experiences para apps de esa Whop App)
2. **OAuth flow** donde Molina autoriza mi app en Codiant (requiere aprobación de scopes)
3. **Headless browser** (chromium/firefox/playwright) para renderizar el JS del iframe app y extraer el DOM con las campañas — NO tengo esas tools en mi VPS

## Recomendaciones para Molina (cuando despierte)

- **Verificar el dashboard de Whop**: ¿qué app está asociada a `exp_XdOopaairb4g5w`? Si Molina instaló una app de "Content Rewards", esa app tiene su propio bot/API key
- **Generar API key de la app** (no de la cuenta): Whop Dashboard → Apps → tu app → API Keys
- O **darme acceso OAuth** a la app instalada
- O **pegarme manualmente** las URLs/IDs de las campañas que ves en el iframe

## Limitaciones del entorno
- Sin browser/headless rendering (chromium, playwright) → no puedo ejecutar JS
- Sin API key con scope "experiences:read" para apps instaladas → no puedo listar via API
- Account API key da 404 "This Bot was not found" → Whop exige bot context para experiences de apps
