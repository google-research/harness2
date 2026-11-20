"""Trade Agent 0.1 — asistente de trading en MODO SIMULACIÓN ÚNICAMENTE.

Este paquete no contiene ningún mecanismo para conectarse a cuentas reales, solicitar
credenciales ni enviar órdenes con dinero. Ver ``trade_agent.broker`` para la interfaz
abstracta de bróker y el adaptador de MetaTrader 5, deshabilitado técnicamente.
"""

__version__ = "0.1.0"

#: Constante de seguridad: esta versión solo puede ejecutar órdenes ficticias.
SIMULATION_ONLY = True
