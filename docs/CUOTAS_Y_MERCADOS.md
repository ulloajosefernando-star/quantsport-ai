# Módulo universal de cuotas y mercados

El módulo convierte, normaliza y evalúa cuotas de cualquier fuente autorizada. Sirven las cuotas capturadas a mano, los CSV históricos y los proveedores con API. No depende de ninguna casa de apuestas concreta y no hace scraping. Es solo lectura y cálculo: nunca apuesta, deposita ni inicia sesión en ninguna parte.

Todos los ejemplos numéricos de este documento están verificados en `backend/tests/test_markets.py`.

## 1. Formatos de cuota

Internamente la plataforma guarda siempre la **cuota decimal**. El formato y el valor con que llegó cada cuota se guardan aparte (`original_format`, `original_value`).

| Formato | Ejemplo | A decimal | Probabilidad implícita |
|---|---|---|---|
| Decimal | 2.50 | 2.50 | 1 / 2.50 = 40 % |
| Americano positivo | +150 | 1 + 150/100 = 2.50 | 100 / (150 + 100) = 40 % |
| Americano negativo | −200 | 1 + 100/200 = 1.50 | 200 / (200 + 100) = 66.67 % |
| Fraccionario | 3/2 | 3/2 + 1 = 2.50 | 40 % |
| Fraccionario | 1/2 | 1/2 + 1 = 1.50 | 66.67 % |
| Porcentaje | 50 % | 100 / 50 = 2.00 | 50 % |
| Porcentaje | 33.33 % | 100 / 33.33 = 3.0003 | 33.33 % |

Más fórmulas:
- Cuota justa: `1 / probabilidad_estimada`. Con p = 0.40, la cuota justa es 2.50.
- Detección automática del formato (opción "Detectar"): con `/` es fraccionario, con `%` es porcentaje, con `+`/`−` o con valor de 100 o más es americano, y en cualquier otro caso es decimal.

Entradas que se rechazan con un mensaje claro:
- Decimal ≤ 1, NaN o infinito.
- Americano entre −100 y +100.
- Fracción con denominador 0, o con valor cero o negativo.
- Porcentaje fuera de (0, 100).
- Texto no numérico.
- Una probabilidad igual a 0 o a 1 al calcular la cuota justa.

La pantalla **Cuotas y mercados** deja elegir el formato de visualización. La elección se recuerda en el navegador.

Código: `backend/app/probability/odds.py` (`to_decimal`, `detect_format`, `format_odds`, `convert_all`).

## 2. Tipos de mercado

`backend/app/markets/registry.py` registra 70 mercados en 5 deportes. Para cada uno declara:
- Sus selecciones válidas.
- Si necesita línea y con qué paso (0.25 en hándicap asiático, 0.5 en totales y spreads, 1 en hándicap europeo).
- El periodo.
- Si necesita un jugador.
- Si es solo en vivo.
- Su regla de liquidación.
- Si sus selecciones son excluyentes y exhaustivas. Solo en ese caso se puede calcular el margen.

| Deporte | Mercados |
|---|---|
| Fútbol | 1X2, doble oportunidad, Draw No Bet, hándicap europeo, hándicap asiático, más/menos goles, ambos anotan, resultado exacto, resultado al descanso, descanso/final, goles por equipo, córners, tarjetas, primer equipo en marcar, próximo gol (en vivo) |
| NBA | Moneyline, spread, total, totales por equipo, resultado por cuarto o mitad, ganador al descanso, props de puntos, rebotes, asistencias, robos, bloqueos y triples, combinaciones (P+R+A, P+R, P+A, R+A), doble-doble, triple-doble |
| NFL | Moneyline, spread, total, totales por equipo, ganador al descanso, hándicap y total por periodo, total de touchdowns, props de yardas de pase, carrera y recepción, touchdowns, intercepciones, anota touchdown |
| MLB | Moneyline, run line, total de carreras, totales por equipo, ganador y total de las primeras 5 entradas, props de lanzador (ponches, outs, carreras permitidas) y de bateador (hits, bases totales, carreras impulsadas, home runs) |
| Tenis | Ganador del partido, ganador del set, ganador del primer set, hándicap de sets, hándicap de juegos, total de juegos, total de sets, marcador exacto en sets, aces, dobles faltas |

Estos mercados son categorías que la plataforma sabe representar y liquidar. Que un mercado esté registrado no significa que alguna fuente lo ofrezca, ni que haya un modelo para él (ver sección 6).

Una combinación inválida se rechaza con el motivo. Ejemplos:
- Hándicap asiático −0.3: no es múltiplo de 0.25.
- Total sin línea.
- "over" en un 1X2.
- Prop de jugador sin jugador.
- Set 6 en tenis.
- Mercado no registrado.

## 3. Estructura normalizada

Cada cuota se presenta en cuatro niveles separados. Cada nivel tiene un identificador estable (hash de sus campos naturales), de modo que la misma cuota llegada por dos caminos no se duplica.

| Nivel | Campos | Identificador estable |
|---|---|---|
| Evento | deporte, competición, participantes, fecha y hora (UTC), estado | deporte, competición, inicio al minuto, claves canónicas de los equipos |
| Mercado | tipo, línea, periodo, jugador, estado (abierto, suspendido, cerrado), prepartido o en vivo, condiciones de liquidación | evento, mercado, periodo, jugador, línea |
| Selección | selección y etiqueta | mercado, selección |
| Cuota | operador o fuente, cuota decimal, formato y valor originales, probabilidad implícita, fuente de origen, fecha de actualización | selección, operador, fuente, momento de captura |

En la base, cada cuota es una fila inmutable de `odds_snapshots`. La migración `31c300ec276a` añade periodo, jugador, en vivo, estado del mercado y formato original, y amplía la clave única con periodo y jugador.

Código: `backend/app/markets/quotes.py`.

## 4. Reglas de liquidación y valor esperado

`backend/app/markets/settlement.py` define una regla por tipo de mercado. Cada regla devuelve el estado (gana, medio gana, push, medio pierde, pierde) y el retorno neto por unidad apostada:
- `winner_3way`: 1X2, descanso y hándicap europeo; con línea, se suma al local.
- `winner_2way`: moneyline; el empate es push.
- `double_chance`.
- `handicap`: hándicap asiático, DNB, spread y hándicaps de tenis. Admite cuartos de línea.
- `total`: admite totales asiáticos.
- `team_total`.
- `btts`.
- `correct_score`.
- `ht_ft`.
- `first_to_score`.
- `player_total`: si el jugador no participa, se anula.
- `yes_no`.

Si falta un dato para liquidar (marcador, descanso, primer gol), la regla lo dice en lugar de inventarlo.

Fórmulas de EV:
- **Mercado binario sin devolución:** `EV = p · d − 1`. Con p = 0.45 y d = 2.50, EV = 0.45 · 2.50 − 1 = **+0.125 (+12.5 %)**.
- **Con devolución o liquidación parcial:** `EV = Σ P(estado) · retorno(estado)`.

Ejemplo con probabilidades del modelo de local 45 %, empate 28 % y visitante 27 %:

| Apuesta | Local gana | Empate | Visitante gana | EV |
|---|---|---|---|---|
| Draw No Bet local a 2.00 | +1.00 | 0 (push) | −1 | 0.45 − 0.27 = **+0.18** |
| Hándicap asiático local −0.25 a 1.90 | +0.90 | −0.50 (medio pierde) | −1 | 0.405 − 0.14 − 0.27 = **−0.005** |

- **Cuota justa con push:** es la que deja EV = 0, `1 + P(pierde) / P(gana)`. Para el DNB anterior: 1 + 0.27 / 0.45 = **1.60**.
- **Totales asiáticos:** over 2.25 con 2 goles es medio pierde; under 2.25 con 2 goles es medio gana; under 2.75 con 3 goles es medio pierde.

## 5. Margen, sin margen, edge e incertidumbre

- **Margen (overround):** `Σ 1/d_i − 1`. Solo se calcula cuando están todas las selecciones del mismo operador, mercado, línea y periodo, y el mercado es excluyente y exhaustivo. Ejemplo 1X2 a 2.10 / 3.40 / 3.60: 0.4762 + 0.2941 + 0.2778 − 1 = **4.81 %**.
  - Doble oportunidad, resultado exacto y goles por equipo no son excluyentes y exhaustivos, así que el margen no se calcula y la pantalla lo explica.
- **Sin margen:** método configurable.
  - **Proporcional:** `p_i = (1/d_i) / Σ 1/d_j`.
  - **Potencia:** `p_i = (1/d_i)^k`, con k tal que la suma da 1.
  - **Shin:** modelo con apostadores informados.

  Los tres son estimaciones del consenso del mercado, no la probabilidad verdadera. La pantalla muestra cuánto difieren.
- **Edge:** `p_modelo − p_sin_margen`. Solo se calcula para la misma selección, mercado, línea y periodo, y solo en mercados sin devolución. En un mercado con push el edge no es comparable y se usa el EV.
- **Incertidumbre:** el modelo de fútbol se reestima 300 veces con sus parámetros muestreados. Con eso se obtienen:
  - Intervalo del 90 % de la probabilidad y del EV.
  - P(EV > 0).
  - Nivel de incertidumbre: baja si el intervalo de p mide menos de 6 puntos, media si mide menos de 12 y alta en otro caso. Sin intervalo de p se usa el del EV, con cortes de 10 y 20 puntos.
- **Calidad de datos:** cada selección indica:
  - Antigüedad de la cuota (marca "desactualizada" si supera el máximo configurado).
  - Si es sintética.
  - Si el margen se pudo calcular.
  - Qué modelo se usó.
  - Avisos del modelo, como datos viejos.

## 6. Conexión con los modelos

`backend/app/markets/models.py` declara qué mercados puede evaluar cada modelo. Si un modelo no puede evaluar un mercado, devuelve el motivo y la selección queda como "Datos insuficientes", sin EV.

| Deporte | Modelo | Mercados evaluados | Sin evaluar (motivo) |
|---|---|---|---|
| Fútbol | Dixon-Coles (matriz de marcadores) | 1X2, doble oportunidad, DNB, hándicap europeo, hándicap asiático, totales, goles por equipo, ambos anotan, resultado exacto, primer equipo en marcar (experimental: supone goles como procesos de Poisson) | Descanso y descanso/final (el modelo usa marcadores finales), córners y tarjetas (no hay datos), próximo gol (no hay modelo en vivo) |
| NBA | Ratings de ataque y defensa (normales de puntos), experimental hasta tener cuotas propias guardadas | Moneyline, spread, total, totales por equipo | Cuartos y mitades, props de jugadores (no hay datos ni modelo) |
| NFL, MLB, tenis | — | Ninguno: se muestran implícita, margen y sin margen | Sin modelo todavía (Fase 2) |

El 1X2, Over/Under 2.5 y ambos anotan pasan por el control de calidad (walk-forward). El resto de mercados de fútbol queda como **experimental**. Por eso nunca llega a "Candidato de valor", aunque muestre EV positivo.

## 7. Ranking

El ranking ordena primero por categoría y luego por una puntuación de calidad. La categoría ya exige modelo validado, EV y edge mínimos, P(EV > 0) suficiente y cuota fresca.

La puntuación de calidad suma estos componentes:

| Componente | Peso | Detalle |
|---|---|---|
| Frescura de la cuota | 0.20 | |
| Calibración histórica | 0.15 | |
| EV | 0.20 | Saturado en 10 % |
| P(EV > 0) | 0.20 | |
| Robustez | 0.05 | |
| Tamaño de la muestra de validación | 0.10 | |
| Disponibilidad de la cuota | 0.10 | Número de operadores, saturado en 3 |

Una cuota alta solo influye a través del EV, que depende de la probabilidad del modelo. Un resultado exacto 4-3 a 60.00 sale "Sin valor detectable".

Los mercados adicionales de fútbol también entran en el detalle del partido y en el ranking cuando tienen cuota: doble oportunidad, DNB, hándicap europeo, goles por equipo, resultado exacto y primer equipo en marcar.

## 8. API

| Endpoint | Qué hace |
|---|---|
| `GET /api/markets/registry?sport=` | Mercados por deporte, con su cobertura de modelo, formatos y métodos de margen |
| `GET /api/markets/events?sport=&competition=` | Eventos próximos y competiciones para los selectores |
| `GET /api/markets/board` | Mercados evaluados. Filtros: deporte, competición, evento, mercado, operador, método de margen, rango de cuota, rango de probabilidad implícita, EV mínimo y edge mínimo |
| `GET /api/tools/convert?value=&fmt=auto&p=&p_push=` | Convierte una cuota a los cuatro formatos. Con tu probabilidad, da EV, cuota justa, cuota mínima y stake |
| `POST /api/events/{id}/manual-odds` | Guarda una cuota de cualquier fuente (cualquier mercado del registro y cualquier formato) y la evalúa. Requiere token de administración fuera del modo local |

## 9. Cómo añadir un mercado o un deporte

1. Añade la definición en `registry.py`. Si la liquidación es nueva, añade una regla en `settlement.py`.
2. Si un modelo puede evaluarlo, decláralo en su adaptador de `models.py`. Si no, el mercado funciona igual con implícita y margen, y explica que no hay modelo.
3. Añade una prueba con un ejemplo numérico verificable.

El núcleo (evaluador, API y pantalla) no cambia.
