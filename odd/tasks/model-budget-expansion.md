# Feature: model-budget-expansion (español)

Header: agregar S23+ y S24 FE reacondicionados a la búsqueda y permitir al
usuario fijar su propio límite de precio y su banda de "estiramiento".

## Specs

- S1: verbatim — "Tambien busca el S23+ y el S24 Fe reacondicionado."
- S2: verbatim — "solo agregalo a la busqueda": incluir ambos modelos en la
  clasificación del collector y las consultas por defecto; sin commit ni push.
- S3: verbatim — "No hagas un commit ni push".
- S4: verbatim — "agrega la opcion de que uno mismo agrega el limite de precio
  y cuanto esta dispuesto a estirar el bolsillo": límite `within` y banda
  `near` (estiramiento) configurables por el usuario; los valores anteriores
  (340.000 / 370.000 CLP) quedan como default cuando no se configuran.
- S5: estiramiento tiene sentido solo si es mayor que el límite base;
  configuración inválida (near <= within o valores no positivos) falla antes
  de tocar la red o el historial.

## Log

- L1 (verbatim): "Tambien busca el S23+ y el S24 Fe reacondicionado. No hagas
  un commit ni push, solo agregalo a la busqueda y agrega la opcion de que
  uno mismo agrega el limite de precio y cuanto esta dispuesto a estirar el
  bolsillo."
- Contexto previo relevante: los límites 340k/370k y la exclusión de S23+ eran
  reglas del proyecto original (README, tests S1/S2). Este feature las
  reemplaza a solicitud expresa del usuario.

## Tasks

| ID | Spec | Route | Commit |
|----|------|-------|--------|
| T1 | S5, S4 | inline test-first | sin commit (S3) |
| T2 | S1, S2 | inline test-first | sin commit (S3) |
| T3 | S2, S3 | inline | sin commit |

## Log (ejecución)

- T1: BudgetConfigTests (4) RED→GREEN: run_collection(budget=(w,n)) valida near>within
  y >0 antes de red/historial; escribe history["budget"] y bandas por caps;
  budget_from_options (CLI > env > default) con S23_WITHIN_MAX/S23_NEAR_MAX;
  flags --within-max/--near-max.
- T2: clasificador RED→GREEN: detect_model s23_plus y s24_fe; S24 base/+, S23 Ultra
  siguen excluidos; processor s23_plus→Snapdragon 8 Gen 2 for Galaxy, s24_fe→Exynos
  2400e; DEFAULT_QUERIES ahora 4 consultas.
- T3 (dashboard, cambió a inline por backstop): subtagentes y review nativo indisponibles
  ("Writer session Git authority changed before admission"). RED→GREEN en Node:
  dashboard-core.js (MODELS 4 + budgetFor + priceBand con caps opcionales + currentOffers
  por historial + histogram con bins generados según caps, defaults intactos + trend/compare
  4 modelos); script.js (labels/tablas/strip/advice/trend); index.html (2 tabs, 2 paneles,
  tiles nuevos, strip dinámico TOPE DE COMPRA / TOPE ESTIRADO); styles.css (2 series nuevas).
- Verificación: 67 Python OK, 13 Node OK, node --check ok; corrida real: 4 fuentes OK,
  4 ofertas y S24 FE presente (antes invisible); budget persistido en history.
- Estado final: sin commit ni push (S3); cambios en working tree; T3 línea de commit
  en la tabla debe leerse "inline (dashboard)".

## Log (adenda 2: soporte restante + selección de modelos)

- L7 (verbatim): "Seria bueno que indique cuanto tiempo le queda de
  actualizaciones y Si podemos dejar esas opciones de telefono por defecto
  para mostrar y si el usuario puede escoger que telefono desea buscar seria
  fantastico solo si es viable, en el caso que no sea viable agregar no se
  cambia eso ultimo".
- T1: RED→GREEN en Node (15 tests): MODEL_UPDATE_SUPPORT (S23/S23+/S23 FE:
  seguridad hasta 2028-02; S24 FE: 7 años, seguridad hasta 2031-09) y
  updateMonthsLeft(model, now?) con contador de meses enteros clamp>=0.
- T2: UI — cada fila muestra "Actualizaciones de seguridad: hasta <mes> (<n>
  meses)" y los pies de panel documentan la ventana; sigue español es-CL.
- T3: el usuario puede elegir qué teléfonos buscar: --models KEYS (env
  S23_MODELS), default los 4; validación de claves antes de red/historial y
  filtro post-colección (las búsquedas siguen cubriendo todos los modelos).
  CORRIGE: header de test loop del autor corregido por defecto del test, no
  del product code.
- T4: README + suites completas: 69 Python OK, 15 Node OK; corrida real exitosa
  (4 fuentes OK, s23 + s24_fe presentes, budget por default 340k/370k).
- Estado: los 4 tabs siguen siendo la vista por defecto (S2 del pedido).
