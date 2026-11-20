# Arquitectura de Trade Agent 0.1

## Principios

1. **Simulación por construcción.** El motor solo acepta brókers con `mode == "SIMULATION"`;
   la configuración rechaza `mode != "simulation"` y cualquier credencial; el adaptador MT5
   lanza una excepción al instanciarse; el paquete no importa librerías de red ni de bróker.
2. **Sin anticipación por construcción.** Las funciones de indicadores calculan el valor en el
   último elemento de lo que reciben; la estrategia recibe `bars[:i+1]` (vía `window_for`) y el
   motor ejecuta en la barra siguiente.
3. **Determinismo.** `Decimal` en todo cálculo, sin aleatoriedad (salvo el generador
   sintético con semilla), sin horas de reloj en la bitácora, serialización canónica.
4. **Capas con dependencias en un solo sentido** y sin infraestructura extra: biblioteca
   estándar, archivos JSON/JSONL.

## Módulos

| Capa | Módulo | Responsabilidad |
|---|---|---|
| Dominio | `domain.py` | `Bar`, `Signal`, `OrderRequest`, `Position`, `Side`, `Action`, redondeos a paso |
| Configuración | `config.py` | Carga y validación; `RiskLimits`, `InstrumentSpec`, `StrategyParams`; bloqueo de modo real y credenciales |
| Ingesta | `data/ingest.py` | CSV → `Dataset` validado (OHLC, cronología, procedencia, SHA-256) |
| | `data/synthetic.py` | Generador determinista de datos SINTÉTICOS + `.meta.json` |
| Estrategia | `indicators.py` | SMA, RSI y ATR de Wilder (funciones puras) |
| | `strategy.py` | Interfaz `Strategy` y `SmaCrossRsiStrategy`; `generate_signals` |
| | `observer.py` | Resumen de mercado y tendencia "a la última barra" |
| Riesgo | `risk.py` | `RiskEngine.evaluate` → `RiskDecision` (aprobación, lotes, desglose, chequeos) |
| Simulación | `costs.py` | Precios de ejecución y valoración (spread, deslizamiento), P/L, comisiones |
| | `broker.py` | Interfaz `Broker`; `SimulatedBroker` (idempotente); `MetaTrader5Broker` deshabilitado |
| | `portfolio.py` | Posiciones abiertas/cerradas, P/L realizado/no realizado, margen, riesgo abierto |
| | `engine.py` | Bucle de simulación, bitácora (`Journal`), `run_id`, manifiesto |
| Persistencia | `serialization.py` | JSON canónico (Decimal → texto exacto) |
| | `reporting.py` | Informe estructurado |
| | `persistence.py` | Escritura/lectura de corridas |
| Presentación | `presentation.py` | Tablas de texto e informe Markdown |
| | `cli.py` | Subcomandos `argparse` y shell `cmd.Cmd` |

Dependencias (→ = "usa"): `cli → engine, persistence, presentation, observer, strategy,
data` · `engine → strategy, risk, broker, portfolio, reporting, serialization` ·
`risk → portfolio, config, domain` · `broker, portfolio → costs → config, domain` ·
`strategy → indicators, data, domain`. Ninguna capa inferior importa la CLI ni la
presentación (salvo `persistence`, que reutiliza el renderizador Markdown del informe).

## Flujo por barra

```
                 apertura de la barra t                              cierre de la barra t
 ┌──────────────────────────────────────────────┐   ┌─────────────────────────────────────────────────┐
 │ orden PENDIENTE (de t-1)                     │   │ strategy.evaluate(bars[:t+1]) → SIGNAL          │
 │   → RiskEngine (AT_FILL, precio = open_t)    │   │ si BUY/SELL:                                    │
 │   → SimulatedBroker.execute (spread, comis.) │   │   → RiskEngine (PRE_TRADE, precio = close_t)    │
 │   → ORDER_FILLED + POSITION_OPENED           │   │   → ORDER_PENDING (se ejecutará en open_{t+1})  │
 │ durante la barra: stop / objetivo            │   │   o ORDER_REJECTED con códigos y motivos        │
 │   → POSITION_CLOSED                          │   │                                                 │
 └──────────────────────────────────────────────┘   └─────────────────────────────────────────────────┘
```

Con varios instrumentos, las barras se intercalan por `(timestamp, símbolo)`; el límite de
posiciones simultáneas y el límite diario se evalúan sobre la cartera completa.

## Interfaz de bróker y la futura integración con MetaTrader 5

```python
class Broker(ABC):
    mode: str
    def execute(self, order: OrderRequest, market: Bar, spec: InstrumentSpec) -> ExecutionReport
    def check_protective_exit(self, position: Position, market: Bar, spec) -> ExitFill | None
    def settle(self, position, exit_price, spec) -> CloseReport
    def close_at_market(self, position, mid, spec) -> CloseReport
```

Una integración futura debería implementar esta interfaz contra el terminal, empezando por
cuenta **demo**, con confirmación humana explícita por orden, credenciales fuera del repositorio
y los mismos controles del `RiskEngine` antes de cada envío. Para habilitarla habría que
cambiar deliberadamente tres puntos que hoy la bloquean (validación de `mode`, `create_broker`
y el chequeo de `mode` en `SimulationEngine`) y la prueba
`test_package_imports_no_network_or_broker_libraries`, que fallaría a propósito.

## Decisiones y alternativas descartadas

* **JSONL en lugar de SQLite:** append-only, legible, fácil de comparar byte a byte; el volumen
  del prototipo no justifica una base de datos.
* **Decimal en lugar de float/pandas:** exactitud monetaria y reproducibilidad entre
  plataformas; sin dependencias.
* **Doble validación de riesgo (señal y ejecución):** la segunda usa el único precio
  disponible en ese momento (apertura) y la cartera actualizada (otro instrumento pudo abrir
  posición en la misma marca de tiempo).
* **Take-profit por beneficio neto objetivo** (no por múltiplo de R): el objetivo de la
  configuración se expresa en dinero de la cuenta, igual que la pérdida máxima.
