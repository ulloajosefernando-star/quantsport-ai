"""Módulo universal de cuotas y mercados.

Capas separadas (ninguna depende de una casa de apuestas concreta):
    app.probability.odds        formatos de cuota y conversión a decimal
    app.markets.registry        tipos de mercado por deporte (qué selecciones y líneas son válidas)
    app.markets.settlement      reglas de liquidación (gana / medio gana / push / medio pierde / pierde)
    app.markets.quotes          estructura normalizada evento → mercado → selección → cuota
    app.markets.models          adaptadores de modelos probabilísticos por deporte
    app.markets.evaluate        implícita, margen, sin margen, modelo, cuota justa, edge, EV e incertidumbre
"""
