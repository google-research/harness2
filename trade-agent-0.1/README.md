# Trade Agent 0.1

Prototipo de asistente de trading algorítmico que **recibe datos OHLCV, genera señales con
reglas explícitas, valida cada operación contra límites de riesgo, la simula con costes y
deja una bitácora auditable y reproducible**.

> **MODO SIMULACIÓN ÚNICAMENTE.** No se conecta a cuentas reales, no solicita ni acepta
> credenciales (la configuración las rechaza), no automatiza interfaces web de bróker y no
> envía órdenes con dinero. El modo real no existe: `mode` solo admite `"simulation"` y el
> adaptador de MetaTrader 5 lanza `LiveTradingDisabledError` al instanciarse. El paquete no
> importa ninguna librería de red ni de bróker; hay una prueba automática que lo verifica.

Python ≥ 3.11, **sin dependencias de runtime** (solo biblioteca estándar). `pytest` solo para
las pruebas.

---

## Instalación

```powershell
cd trade-agent-0.1
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"           # instala el comando `trade-agent` y pytest
```

Sin instalar nada (el código no tiene dependencias):

```powershell
$env:PYTHONPATH = "src"           # bash: export PYTHONPATH=src
python -m trade_agent --help
```

Pruebas:

```powershell
python -m pytest                  # 96 pruebas, ~6 s
```

## Inicio rápido: demostración del ciclo completo

```powershell
trade-agent demo --out demo_out
```

Genera datos SINTÉTICOS, muestra el resumen de mercado, comprueba el rechazo de un CSV
inválido y recorre una operación completa: **datos → señal → validación de riesgo → orden
pendiente → ejecución simulada → cierre → informe**. La transcripción de una ejecución está
en [`examples/demo_output.txt`](examples/demo_output.txt).

## Uso de la CLI

| Necesidad | Comando |
|---|---|
| Cargar y validar datos | `trade-agent load datos.csv [--instrument EURUSD] [--provenance historical\|synthetic]` |
| Ejecutar el análisis y revisar señales | `trade-agent analyze datos.csv [--all]` |
| Simular operaciones | `trade-agent simulate a.csv [b.csv ...] [--config cfg.json] [--out runs] [--close-at-end] [--stop-after-bars N]` |
| Consultar posiciones | `trade-agent positions runs/<run_id> [--status open\|closed\|all]` |
| Examinar el historial | `trade-agent history runs/<run_id> [--type ORDER_REJECTED POSITION_CLOSED] [--limit N]` |
| Ver el informe | `trade-agent report runs/<run_id> [--markdown]` |
| Generar datos sintéticos | `trade-agent generate-synthetic salida.csv [--bars 600] [--seed 7]` |
| Shell interactivo | `trade-agent shell` → `load`, `analyze`, `signals`, `simulate`, `positions`, `history`, `report`, **`stop`** |

**Detener el sistema:** `stop` en el shell; `Ctrl+C` durante `simulate` (se registra
`SYSTEM_STOPPED`, se cancelan las órdenes pendientes y las posiciones quedan como abiertas);
`--stop-after-bars N` reproduce una parada de forma determinista. No hay procesos en segundo
plano.

Códigos de salida: `0` correcto, `2` datos o configuración inválidos (mensaje en `stderr`,
sin traza), `3` intento de modo real.

## Entrada de datos

CSV con cabecera (columnas extra se ignoran). `timestamp` en ISO 8601 = hora de **apertura**
de la barra; sin zona horaria se interpreta como UTC.

```csv
timestamp,open,high,low,close,volume
2020-01-06T00:00:00Z,1.10000,1.10003,1.09970,1.09996,858
2020-01-06T01:00:00Z,1.09996,1.10020,1.09895,1.09897,980
```

Validaciones: columnas obligatorias, números finitos, precios > 0, volumen ≥ 0,
`low ≤ open,close ≤ high`, timestamps estrictamente crecientes (sin duplicados). Se informan
**todos** los problemas con fila y columna:

```text
ERROR: Datos rechazados (invalid_example.csv): 4 problema(s).
  - Fila 3, columna 'high': high (1.10000) es menor que open, close o low; la barra es incoherente.
  - Fila 4, columna 'close': 'abc' no es un número válido.
  - Fila 5, columna 'volume': el volumen -5 no puede ser negativo.
  - Fila 5, columna 'timestamp': 2020-01-01T02:00:00+00:00 no respeta el orden cronológico ascendente (la fila anterior es 2020-01-01T03:00:00+00:00).
```

**Procedencia obligatoria (histórico vs. sintético).** Se declara con `--provenance` o con
`<nombre>.meta.json` junto al CSV (el generador lo crea con `"provenance": "SYNTHETIC"`). Sin
procedencia, los datos se rechazan; si el argumento contradice al `.meta.json` (p. ej.
presentar sintéticos como históricos), también. Cada salida rotula los datos como
**HISTÓRICOS** o **SINTÉTICOS** y todo valor se refiere a "la última barra del archivo", nunca
a una cotización actual. Los datos sintéticos empiezan en 2020 a propósito.

Ejemplos: [`examples/data/synthetic_eurusd_h1.csv`](examples/data/synthetic_eurusd_h1.csv)
(+ `.meta.json`) y [`examples/data/invalid_example.csv`](examples/data/invalid_example.csv).

## Configuración

[`examples/config.json`](examples/config.json) (idéntica a la configuración integrada que se
usa si no se pasa `--config`). Se rechazan claves desconocidas, valores no positivos,
`mode` distinto de `simulation` y cualquier campo con aspecto de credencial.

| Sección | Campo | Significado |
|---|---|---|
| `account` | `currency`, `initial_capital` | Divisa de la cuenta y capital simulado |
| `risk` | `max_budget_per_trade` | Margen máximo comprometido por operación (divisa de cuenta) |
| | `max_loss_per_trade` | Pérdida máxima por operación, **en divisa de cuenta, costes incluidos** |
| | `profit_target_per_trade` | Beneficio **neto** objetivo; fija el take-profit |
| | `daily_loss_limit` | Pérdida neta diaria máxima (día UTC), contando el riesgo abierto |
| | `max_open_positions` | Posiciones simultáneas (además: máximo una por instrumento) |
| `instruments.<SÍMBOLO>` | `contract_size`, `tick_size`, `spread`, `commission_per_lot_per_side`, `min_lot`, `lot_step`, `max_lot`, `margin_rate`, `quote_currency`, `quote_to_account_rate`, `slippage` (opcional, 0) | Datos de coste, contrato y exposición. Si falta alguno, **las órdenes se rechazan** con `MISSING_COST_INFO` indicando cuál |
| `strategy` | periodos SMA/RSI/ATR, umbrales RSI, múltiplo ATR del stop, `lookback_bars` | Parámetros de la estrategia |

## Estrategia (determinista)

`sma_cross_rsi`, evaluada al **cierre** de cada barra con solo el historial hasta esa barra:

* **BUY** si la SMA10 cruza por encima de la SMA30 y RSI14 < 70.
* **SELL** si la SMA10 cruza por debajo de la SMA30 y RSI14 > 30.
* **NO_TRADE** en cualquier otro caso (sin cruce, cruce filtrado por RSI o calentamiento
  insuficiente), siempre con su motivo.
* Stop propuesto: 2 × ATR14.

Cada señal registra instrumento, barra, precio de referencia, condiciones (valores de los
indicadores y booleanos), justificación textual, ventana de datos usada y procedencia. Las
justificaciones describen condiciones observadas y terminan con *"no es un pronóstico"*.

## Motor de riesgo

```
pérdida_máx (divisa cuenta) = (distancia_stop + deslizamiento) × contrato × lotes × fx
                              + 2 × comisión_por_lado × lotes
lotes = floor_a_lot_step( min( pérdida_máx_op / pérdida_por_lote,
                               presupuesto_op / margen_por_lote,
                               (límite_diario − pérdida_del_día − riesgo_abierto) / pérdida_por_lote,
                               margen_libre / margen_por_lote,
                               max_lot ) )
```

El stop se coloca a `distancia_stop` del precio de **ejecución** (que ya incluye medio spread),
así que la pérdida planificada es exacta mientras no haya huecos de precio. El take-profit se
coloca donde el beneficio neto de comisiones alcanza `profit_target_per_trade`.

Rechazos (código en la bitácora): `MISSING_INSTRUMENT_SPEC`, `MISSING_COST_INFO`,
`MISSING_STOP`, `STOP_INSIDE_SPREAD`, `DUPLICATE_ORDER`, `ADD_TO_LOSING_POSITION`,
`POSITION_ALREADY_OPEN`, `MAX_OPEN_POSITIONS`, `DAILY_LOSS_LIMIT_REACHED`,
`SIZE_BELOW_MIN_LOT` (indica qué límite lo impide). La validación se hace dos veces: al
cierre de la barra de la señal (`PRE_TRADE`) y al ejecutar con el precio de apertura
(`AT_FILL`).

## Simulador

* Señal al cierre de la barra *t* → orden **PENDIENTE** → ejecución a la **apertura de *t+1***.
  Ninguna decisión usa precios posteriores al momento en que se toma.
* Compra a `medio + spread/2 + deslizamiento`, venta a `medio − spread/2 − deslizamiento`;
  comisión por lado. Las posiciones se valoran por el lado de cierre (bid/ask).
* Stop/objetivo durante la barra; si ambos caben en la misma barra se asume el stop
  (conservador). Si la barra abre más allá del stop se cierra a la apertura y se marca
  `loss_beyond_plan`.
* Estados de orden: `PENDING`, `REJECTED`, `FILLED_SIMULATED`, `CANCELLED`. Posiciones:
  `OPEN` / `CLOSED`. Idempotencia por `client_order_id` determinista
  (`EURUSD-20200111T020000Z-BUY`): un duplicado nunca produce una segunda ejecución.

## Bitácora y reproducibilidad

Cada corrida se guarda en `runs/<run_id>/`:

| Archivo | Contenido |
|---|---|
| `journal.jsonl` | Un evento por línea: `RUN_STARTED`, `DATA_LOADED`, `SIGNAL`, `RISK_CHECK`, `ORDER_PENDING`, `ORDER_REJECTED`, `ORDER_FILLED`, `POSITION_OPENED`, `POSITION_CLOSED`, `ORDER_CANCELLED`, `SYSTEM_STOPPED`, `RUN_COMPLETED` |
| `report.md` / `report.json` | Informe legible / estructurado |
| `positions.json` | Posiciones abiertas y cerradas |
| `manifest.json` | `run_id`, versión, configuración, SHA-256 de los datos y de la bitácora |
| `run_meta.json` | Hora de reloj, duración y entorno (único archivo no determinista) |

`run_id` = hash de (versión, configuración, huellas de datos, opciones). Todo el cálculo usa
`Decimal`; la bitácora no contiene horas de reloj ni rutas absolutas. Mismos datos + misma
configuración ⇒ **mismos bytes**. Ejemplo versionado: [`examples/sample_run/`](examples/sample_run/)
(una prueba lo regenera y compara byte a byte).

## Criterios de aceptación → evidencia

| Criterio | Pruebas |
|---|---|
| Resultados reproducibles | `test_identical_inputs_produce_identical_results`, `test_committed_sample_run_is_reproduced_exactly` |
| Ninguna operación supera los límites | `test_no_filled_order_exceeds_configured_limits`, `test_orders_violating_limits_are_rejected_not_executed`, `test_multiple_instruments_respect_max_simultaneous_positions`, `test_risk.py` |
| Señales sin información futura | `test_signals_do_not_depend_on_future_bars` (truncar y alterar el futuro no cambia el pasado), `test_engine_decisions_do_not_depend_on_future_bars` |
| Orden duplicada ≠ dos ejecuciones | `test_duplicate_order_is_executed_only_once`, `test_engine_never_fills_the_same_client_order_id_twice`, `test_duplicate_order_is_rejected` |
| Posiciones abiertas vs. cerradas | `test_open_and_closed_positions_are_distinguished`, `test_open_position_is_marked_or_closed_at_end_of_data` |
| Los costes afectan al resultado | `test_costs_change_the_economic_result_of_the_same_trade` (diferencia exacta = spread + comisiones), `test_reported_results_are_net_of_costs` |
| Datos inválidos → rechazos comprensibles | `test_invalid_data_is_rejected_with_clear_message` (8 casos), `test_cli_reports_invalid_data_without_traceback`, `test_all_problems_are_reported_with_row_numbers` |
| Modo real inexistente / deshabilitado | `test_live_mode_cannot_be_configured`, `test_broker_credentials_are_refused`, `test_live_broker_is_technically_disabled`, `test_package_imports_no_network_or_broker_libraries` |
| Demostración del ciclo completo | `test_demo_runs_the_full_cycle`, `test_interactive_shell_full_session` |

Las pruebas de aceptación ([`tests/test_acceptance.py`](tests/test_acceptance.py)) se
escribieron **antes** de la implementación y se observó que fallaban. Además, una verificación
por mutación (8 defectos introducidos a propósito: anticipación de una barra, sin control de
duplicados, spread ignorado, tamaño con el límite más laxo, modo real permitido, comisión de
salida omitida, límite diario sin riesgo abierto, sin control cronológico) fue detectada por
la suite en **8 de 8** casos.

## Arquitectura

Ver [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Resumen de capas:

```
ingesta (data/)  →  estrategia (strategy, indicators)  →  riesgo (risk)  →  simulación (engine, broker, portfolio, costs)
                                                                                 ↓
                                     presentación (cli, presentation)  ←  persistencia (persistence, reporting, serialization)
```

## Limitaciones conocidas

* **Los resultados de los datos de ejemplo no significan nada sobre rentabilidad.** Son datos
  sintéticos con regímenes de tendencia que favorecen a una estrategia de cruce de medias;
  con otra semilla el neto es casi nulo (+39 MXN tras 65 MXN de comisiones en 300 barras).
* Sin datos históricos reales incluidos (no se descarga nada); el formato los admite
  declarando `--provenance historical`.
* Tipo de cambio cotización→cuenta **estático** (`quote_to_account_rate`); en realidad varía.
* Spread y deslizamiento constantes; sin swaps/financiación nocturna, sin sesiones de
  mercado ni festivos, sin ejecución parcial ni rechazos del bróker.
* Ejecución en barras: dentro de una barra se desconoce el orden real de máximos y mínimos
  (se asume el stop primero). Un hueco de precio puede producir una pérdida mayor que la
  planificada; queda marcada (`loss_beyond_plan`) pero no puede evitarse en una simulación por
  barras.
* Una posición por instrumento; las señales opuestas con posición abierta se rechazan (no
  hay cierre por señal contraria ni trailing stop).
* El límite diario usa el día UTC de la barra.
* `MetaTrader5Broker` es solo un marcador de la interfaz: no hay integración, ni siquiera de
  lectura.
* El shell mantiene el estado en memoria; las corridas sí quedan persistidas en disco.

## Métricas del desarrollo

* Duración de reloj (inicio del trabajo → documentación): ~25 min (00:55–01:20 UTC,
  20/11/2026).
* Código: ~2 900 líneas en `src/`, ~1 100 en `tests/`; 96 pruebas en ~6 s; una simulación de
  600 barras tarda ~0,2 s.
* Herramientas: Claude Code (Opus 5.5) con lectura/escritura de archivos, shell y pytest; sin
  subagentes, sin servicios externos, sin descargas.
* Tokens: el agente no tiene acceso al recuento de tokens de la sesión.
