# Feature: scraping-resilience (A+B+D) — español

Header: mejorar la resiliencia de la recolección semanal de precios para que una
corrida fallida no deje el proyecto sin datos, y agregar una tercera fuente.

## Specs

- S1: "Que no se quede quieto" ante falla: reintento con backoff dentro de la
  corrida para errores de red / HTTP 5xx; nunca se reintenta ante bloqueos
  401/403/429 (esos frenan la corrida como hoy, sin tocar el historial).
- S2: "se busque por otra manera": ejecución diaria en vez de semanal, para
  reparar dentro de la misma semana si una corrida fracasa (crontab + README).
- S3: "otra manera o alternativa": una tercera fuente de ofertas con
  products.json público robots-permitido, investigación primero, fixtures
  offline + tests sin red.

## Log

- L1 (verbatim): "Puedes ayudarme a buscar una manera de mejorar el scrapping,
  ya que al ser 1 x semana. Como no se consigan los datos se queda quieto.
  La idea esque se busque por otra manera o alternativa para tener los datos
  visibles."
- L2: Usuario eligió alcance A (reintento backoff), B (cron diario),
  D (nueva fuente). C (aviso de frescura) queda fuera.
- L3: S23Precios no es repo propio: el git root es ../Proyectos (rama
  feat/s23-refurbished-price-tracking, cero commits, S23Precios sin trackear).
  Decisión de commit pendiente de consulta al usuario.

## Tasks

| ID | Spec | Route | Commit |
|----|------|-------|--------|
| T1 | S1 | inline, test-first | pendiente (repo a decidir) |
| T2 | S2 | inline | pendiente (repo a decidir) |
| T3 | S3 | inline | (investigación: sin commit propio) |
| T4 | S3 | inline, test-first | pendiente (repo a decidir) |

## Log (ejecución)

- T1: RetryPolicyTests RED (6 faltantes) → GREEN con `_http_request_once` +
  wrapper de reintentos (NETWORK_RETRIES=1, RETRY_BACKOFF_SECONDS=60). 57→58 tests OK.
- T2: crontab inexistente en la máquina → timer systemd de usuario diario
  (systemd/s23-prices.{service,timer} en repo, instalado y activo, primer trigger
  09:45). README reescrito (sección timer + política de reintentos); bus tests de
  docs actualizados (crontab -e → s23-prices.timer). Servicio verificado con una
  corrida real: exit 0, snapshot guardado.
- T3/T4: candidatas dlphone.cl, comprala.cl, gsmpro.cl. dlphone: UCP profile genuino
  (endpoint d8f7f8-76.myshopify.com/api/ucp/mcp), robots Allow / (products.json y
  /.well-known/ucp permitidos), muchas ofertas S23/S23 FE. comprala: UCP válido,
  quedará documentado como candidato futuro. gsmpro: sin robots/ucp respondibles.
  Falabella/Paris/Knasta descartadas por diseño (no feed público permitido).
- T4: RED.SourceTrustTests (mapeo dlphone) → GREEN (DEFAULT_SOURCES +
  TRUSTED_PRODUCT_HOSTS + script.js sourceLabel/safeOfferUrl). 58 tests Python OK,
  7 Node OK.
- Descubrimiento ejecución real: fallback pedía /products.json sin limit
  (Shopify devuelve ~30 nuevos) → RED (assert ?limit=250) → GREEN. Corrida real:
  3 fuentes ok, 3 ofertas para 2026-W45 (incl. dlphone S23 256Gb $349.990).
- dlphone mostró 0 ofertas en corrida previa: variantes S23 snapshot-available=False
  ese día (sin stock); comportamiento correcto del filtro, no bug.
- Pendiente: decisión de commits (S23Precios no es repo propio; git root es el
  Proyectos padre).
- L4: Decisión de commits: repo git propio en S23Precios (elección del usuario),
  rama feat/scraping-resilience renombrada a main por el usuario, push explícito
  a github.com/Lagger-craft/OfertasTelefonos (9e3ec77). El README/tests se
  actualizaron para reflejar la publicación (sin GitHub Actions). Commit hecho
  por el usuario con su propia identidad (repo-local).
- L5: Review nativo RDD: inassessible para el commit inicial (sin base de diff
  raíz, autoridad de review desfasada por el git init tras criar el repo, sin
  binario gentle-ai CLI en la máquina). El usuario dejó el candidato EXPLICITAMENTE
  sin review: disposición registrada; verificación fuera vía tests (58/7) + corridas reales.
- Estado final: todo commit 9e3ec77 sincronizado con origin/main; suites verdes;
  timer systemd activo con corrida real exitosa.

## Log (adenda: 4a fuente)
- L6 (verbatim): "Podemos intentar agregar la fuente comprala.cl porfa ya que
  testamos. No se me habia ocurrido". Path small, inline: RED
  (test_comprala_product_url_is_trusted_across_its_hosts) → GREEN
  (DEFAULT_SOURCES + TRUSTED_PRODUCT_HOSTS comprala-cl.myshopify.com +
  script.js sourceLabel/safeOfferUrl). README: Comprala deja de ser candidata
  futura y pasa a fuente residente. 59 tests Python OK, 7 Node OK; corrida
  real: 4 fuentes OK, comprala sin ofertas calificadas esta semana.
