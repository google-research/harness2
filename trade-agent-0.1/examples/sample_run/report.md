# Informe de simulación — Trade Agent 0.1

> **MODO SIMULACIÓN ÚNICAMENTE.** Resultados de una SIMULACIÓN con órdenes ficticias sobre datos SINTÉTICOS. No son cotizaciones actuales, no constituyen asesoría financiera y no anticipan resultados futuros.

- Corrida: `f72861f005ef` — estado **COMPLETED**

## Datos

| Instrumento | Procedencia | Archivo | Barras | Desde | Hasta | SHA-256 |
|---|---|---|---|---|---|---|
| EURUSD | **SINTÉTICOS** | synthetic_eurusd_h1.csv | 600 | 2020-01-06T00:00:00+00:00 | 2020-01-30T23:00:00+00:00 | `0087acfdf52b5a31…` |

> Datos SINTÉTICOS generados por computadora: no son cotizaciones de mercado reales ni actuales.

## Límites de riesgo aplicados

- Capital simulado: 100000.00 MXN
- Presupuesto (margen) máximo por operación: 25000.00 MXN
- Pérdida máxima por operación: 600.00 MXN
- Objetivo de beneficio neto por operación: 1200.00 MXN
- Límite de pérdida diaria: 1500.00 MXN
- Máximo de posiciones simultáneas: 2

## Actividad

- Señales: BUY=4, SELL=3, NO_TRADE=593
- Órdenes pendientes creadas: 7
- Órdenes ejecutadas (simuladas): 7
- Órdenes rechazadas: 0 — por motivo: ninguno
- Órdenes canceladas: 0

## Resultado económico (neto de costes)

| Concepto | Importe |
|---|---|
| P/L bruto realizado | 5003.61 MXN |
| Comisiones | 156.00 MXN |
| **P/L neto realizado** | **4847.61 MXN** |
| Spread (ya incluido en precios) | 265.20 MXN |
| P/L no realizado (abiertas) | 0.00 MXN |
| Mayor pérdida / mayor ganancia | -588.05 / 1202.88 MXN |
| Drawdown máximo (equity) | 941.64 MXN |
| Balance final / equity final | 104847.61 / 104847.61 MXN |

## Posiciones

- Cerradas: 7 (ganadoras 5, perdedoras 2); motivos de salida: STOP_LOSS=2, TAKE_PROFIT=5
- Abiertas al final: 0
- Pérdidas que excedieron el plan por huecos de precio: 0

La bitácora completa y auditable está en `journal.jsonl` (un evento por línea).
