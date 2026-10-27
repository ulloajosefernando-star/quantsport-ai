# QuantSport AI · Arquitectura, fuentes, presupuesto y plan

Fecha de verificación: 23-oct-2026. Los precios se consultaron en las páginas oficiales de cada proveedor en esa fecha. Tipos de cambio manuales: 1 USD = 18.42 MXN y 1 EUR ≈ 21.4 MXN (el de EUR es aproximado). Antes de contratar cualquier servicio, vuelve a verificar precio y tipo de cambio. La plataforma nunca contrata nada automáticamente.

## 1. Inspección del entorno

| Elemento | Resultado |
|---|---|
| Python | 3.13 (venv en `backend/.venv`) |
| Node | 22 |
| Base de datos | SQLite (archivo en `data/`), migraciones Alembic. PostgreSQL es posible cambiando `DATABASE_URL` |
| Docker | CLI disponible; el daemon no corre en el entorno de construcción, así que las imágenes no se construyeron aquí. `docker compose config` valida |
| Red | El contenedor de desarrollo no puede llamar a las APIs de los proveedores (proxy). Por eso todo lo real se probó con respuestas simuladas (`httpx.MockTransport`) y no se consumió ninguna cuota |
| Repositorio GitHub | Pendiente de que Fernando indique cuál usar |

## 2. Fuentes de datos y cobertura

| Fuente | Deportes / ligas | Qué aporta | Gratis | De pago | Estado en Fase 1 |
|---|---|---|---|---|---|
| Demo sintético interno | Liga ficticia de 16 equipos, 3 temporadas | Resultados y cuotas SINTÉTICOS, etiquetados en toda la interfaz | Sí | — | Completo |
| football-data.co.uk (CSV) | Ligas europeas, Liga MX (`new/MEX.csv`), MLS | Resultados históricos, cuotas pre-cierre y de cierre | Sí | — | Importador completo (subida desde la web o API) |
| The Odds API | Fútbol (Liga MX por verificar en `/v4/sports`), NBA, NFL, MLB, tenis | Cuotas actuales y calendario (`/events`, sin coste) | Starter: 500 créditos/mes, sin históricos | 20K créditos 30 USD; 100K 59 USD | Adaptador completo, con control de cuota y caché |
| football-data.org | 12 competiciones (sin Liga MX) | Calendario y resultados | Free: 10 llamadas/min | Standard 49 EUR; cuotas y estadísticas son extras de 15 EUR | Adaptador completo |
| API-Football | Liga MX (id 262) y 1,000+ ligas | Calendario, resultados, cuotas | Free: 100 solicitudes/día, **solo temporadas pasadas** (ver nota) | Pro 19 USD, Ultra 29 USD, Mega 39 USD | Adaptador completo (Fase 3) |
| openfootball (football.json) | Inglaterra 1.ª/2.ª, España, Alemania, Italia, Francia (sin Liga MX) | Resultados y calendario; se usa para cruzar resultados con otras fuentes | Sí, dominio público | — | Adaptador completo |
| Club Elo | Clubes europeos (sin Liga MX) | Ratings Elo diarios; referencia externa en el detalle de cada partido, no entra en el modelo | Sí | — | Importación manual de CSV (su robots.txt no permite descargas automáticas) |
| TheSportsDB | Incluye Liga MX | Calendario y resultados, sin cuotas | Clave de prueba con límites muy bajos (15 partidos por consulta de temporada) | 9 USD/mes (≈ 166 MXN) | No integrado; opcional si se decide pagar |
| nflverse, MLB Stats API, NBA, tenis | NFL, MLB, NBA, tenis | Datos de cada deporte | Sí, en general | — | Registrados como pendientes (Fase 2) |

Coste de The Odds API: cada consulta de cuotas cuesta mercados × regiones créditos. Consultar 1X2 y totales en una región cuesta 2 créditos por liga. Con 2 ligas y 2 consultas al día son unos 240 créditos al mes, dentro del plan gratis. Los endpoints `/sports` y `/events` no consumen créditos. La plataforma lee las cabeceras `x-requests-remaining/used/last` y se detiene antes de agotar la cuota.

Nota sobre API-Football: la página de precios confirma 100 solicitudes/día gratis y "todas las competiciones y endpoints", pero no dice qué temporadas incluye. Dos reportes independientes de usuarios (oct-2026) indican que el plan gratuito rechaza la temporada actual con un error `errors.plan` y solo permite temporadas pasadas (2022–2024). El adaptador detecta ese error, lo explica y no lo reintenta. Por eso la ruta gratuita para Liga MX es: histórico de football-data.co.uk + calendario y cuotas de The Odds API.

No se usa scraping ni se elude ningún control de acceso. No hay integración con ninguna casa de apuestas concreta: las cuotas de fuentes sin API se capturan a mano en el detalle del partido. Se descartaron ESPN, SofaScore, FBref, Understat, FotMob y Transfermarkt: son APIs no oficiales o requieren scraping.

## 3. Estimación de costes mensuales (MXN)

| Categoría | Opción | MXN/mes | Recomendación |
|---|---|---|---|
| Datos deportivos | football-data.org Free + CSV football-data.co.uk | 0 | Recomendado |
| Datos deportivos | API-Football Free | 0 | Útil solo para históricos (temporadas pasadas) |
| Datos deportivos | API-Football Pro (19 USD) | ≈ 350 | Solo si necesitas la temporada actual desde API-Football |
| Cuotas | The Odds API Starter | 0 | Recomendado |
| Cuotas | The Odds API 20K (30 USD) | ≈ 553 | Excede el presupuesto |
| Alojamiento | Tu computadora (modo local) | 0 | Recomendado para empezar |
| Alojamiento | VPS Hetzner CPX02 (1 vCPU, 1 GB, 6.49 EUR sin IVA) | ≈ 139 | Opcional, para tener el bot 24/7 |
| Base de datos | SQLite o PostgreSQL en el mismo servidor | 0 | Recomendado |
| Dominio | Ninguno (IP o túnel) | 0 | Un .com cuesta ≈ 20–35 MXN/mes si lo quieres |
| Servicios opcionales | Telegram Bot API | 0 | Recomendado |
| Servicios opcionales | Backups del VPS (+20%) | ≈ 28 | Alternativa gratis: `scripts/backup.sh` |

| Escenario | Total MXN/mes | ¿Dentro de 500 MXN? |
|---|---|---|
| Modo local | 0 | Sí |
| VPS mínimo | ≈ 139 | Sí |
| VPS + API-Football Pro | ≈ 489 | Sí, muy justo (el IVA o el tipo de cambio pueden sacarlo del presupuesto) |

Recomendación: empezar en modo local con todo gratis, y solo pasar al VPS cuando el bot de Telegram necesite estar encendido todo el día.

## 4. Arquitectura

```
frontend/ (React + Vite + Tailwind, español)  ──/api──►  backend/ (FastAPI)
                                                         ├─ providers/      adaptadores normalizados, caché, cuotas, registro de llamadas
                                                         ├─ ingestion/      deduplicación entre fuentes, cuotas inmutables, trabajos idempotentes
                                                         ├─ sports/football Dixon-Coles con decaimiento temporal e incertidumbre (Laplace)
                                                         ├─ probability/    formatos de cuota, márgenes (proporcional, potencia, Shin), EV, Monte Carlo
                                                         ├─ markets/        registro de mercados, liquidación, cuotas normalizadas, evaluador
                                                         ├─ calibration/    Brier, log loss, ECE, fiabilidad, control de calidad por mercado
                                                         ├─ backtesting/    walk-forward sin fuga de información
                                                         ├─ value_engine/   categorías de valor y NO BET
                                                         ├─ ranking/        ranking de oportunidades
                                                         ├─ bankroll/       Kelly fraccionado con límites
                                                         ├─ reporting/      registro y liquidación de pronósticos, presupuesto
                                                         └─ telegram/       resúmenes con lista blanca y límite diario
                                                         SQLite / PostgreSQL (data/)
```

Un modelo solo produce recomendaciones si supera el control de calidad del mercado correspondiente: muestra mínima fuera de muestra, ECE máximo, mejor que la frecuencia base, y no peor que el mercado sin margen. Si no lo supera, el mercado queda bloqueado y la plataforma muestra NO BET.

## 5. Plan por fases

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Núcleo matemático, fútbol (Dixon-Coles), validación, motor de valor, ranking, dashboard, demo, CSV, The Odds API, football-data.org, envío a Telegram | Hecho |
| 2 | Modelos NBA, NFL, MLB y tenis (Elo, regresión logística, Poisson por deporte) con sus fuentes gratuitas | Pendiente |
| 3 | API-Football (Liga MX), calendario gratis vía The Odds API, validación con cuotas de cierre, revisión de equipos, CLI de validación | En curso: conectores hechos y primera validación real corrida (sin ventaja frente al cierre). Faltan cuotas pre-cierre acumuladas, alineaciones, lesiones y xG |
| 4 | Mercados adicionales: córners, tarjetas, props | Registrados en el módulo de mercados (se muestran implícita y margen); faltan datos y modelos |
| 5 | Comparación de modelos y ensamblado, recalibración (isotónica / Platt) | Pendiente |
| 6 | Bot de Telegram interactivo: /start /help /today /ranking /football /nba /nfl /mlb /tennis /event /performance /status /settings | Pendiente |
| 7 | Programador de tareas (APScheduler): sincronización, snapshots y liquidación automáticos con presupuesto de créditos | Completa (PR de Fase 7) |
| 8 | Despliegue en VPS, backups y monitoreo | Preparado (Docker Compose), sin desplegar |

## 6. Qué está completo y qué falta

Completo y probado:
- Conversión de cuotas (decimal, americana, fraccionaria), probabilidad implícita y cuota justa.
- Eliminación de margen por tres métodos, con aviso cuando difieren.
- EV por estados de liquidación, incluidas líneas asiáticas de cuarto, DNB y totales asiáticos.
- Kelly binario y por estados, fraccionado, con tope por selección y por evento.
- Dixon-Coles con ponderación temporal, regularización, ρ acotado e incertidumbre de parámetros.
- Matriz de marcadores 0–10: 1X2, doble oportunidad, totales, ambos anotan, totales por equipo y hándicap.
- Validación walk-forward con control de fuga, comparación con frecuencias base y con el mercado, intervalos bootstrap.
- Control de calidad por mercado (1X2, Over/Under 2.5, ambos anotan).
- Motor de valor con seis categorías, cuota mínima aceptable, P(EV>0) y detección de cuotas desactualizadas.
- Ranking, registro de pronósticos antes del partido, liquidación y métricas de rendimiento.
- Dashboard en español con 8 pantallas y la etiqueta DEMO visible en todo dato sintético.
- Importación CSV; adaptadores de The Odds API y football-data.org con caché, límite de cuota y registro.
- Resumen a Telegram (vista previa y envío) con lista blanca, deduplicación y límite diario.
- Adaptador de API-Football (resultados, calendario, cuotas; detecta restricciones de plan), calendario gratuito desde The Odds API, revisión y fusión de equipos entre fuentes, línea de comandos (`python -m app.cli`).
- Fuentes gratuitas adicionales: openfootball (resultados de 6 ligas europeas, dominio público) y ratings de Club Elo como referencia externa.
- Módulo universal de cuotas y mercados ([CUOTAS_Y_MERCADOS.md](CUOTAS_Y_MERCADOS.md)): cuatro formatos de cuota, 70 mercados en 5 deportes con reglas de liquidación, estructura normalizada con identificadores estables, margen con método configurable, EV por estados, pantalla "Cuotas y mercados" y calculadora. El modelo de fútbol cubre 10 mercados; NBA, NFL, MLB y tenis aún no tienen modelo.
- Captura manual de cuotas de cualquier fuente, con evaluación inmediata; no entra en el consenso del mercado.
- 226 pruebas automáticas, ninguna usa APIs reales.

Pendiente o incompleto:
- Fútbol queda como parcial. Primera validación real de Liga MX (23-oct-2026, ver sección 8): 1X2 bloqueado y Over/Under y ambos anotan experimentales. Hoy el modelo no encuentra ventaja y la plataforma recomienda NO BET.
- NBA, NFL, MLB y tenis: solo están registrados; no tienen modelo ni datos (no se declaran implementados).
- Alineaciones, lesiones, xG, córners y tarjetas.
- Comandos interactivos de Telegram y programador automático de tareas.
- Limpieza de tipos con mypy (92 avisos, sin impacto en las pruebas).
- Imágenes Docker sin construir en este entorno (no había daemon de Docker).

## 7. Resultado del demo (datos SINTÉTICOS)

El demo genera una liga ficticia cuyo "mercado" ignora a propósito la ventaja de local, para que exista una ineficiencia que el modelo pueda detectar. Aun así, el control de calidad bloquea 1X2 (el modelo no supera al mercado en log loss) y totales (ECE 0.067 > 0.05). Solo "ambos anotan" pasa a producción. Esto muestra que la plataforma dice NO BET cuando la evidencia no alcanza. Nada de esto indica rentabilidad real.

## 8. Primera validación con datos reales: Liga MX (23-oct-2026)

Corrida en el equipo de Fernando con `scripts/validar_liga_mx.sh`, sin clave de The Odds API, con el CSV de football-data.co.uk:
- 4,770 partidos importados y 25 equipos reconciliados, ninguno pendiente de revisión.
- Entrenamiento: 1,304 partidos.
- Walk-forward: 861 predicciones fuera de muestra entre 2024-03 y 2026-10.
- Referencia de mercado: solo cuotas de cierre.

| Mercado | Estado | Log loss modelo | Log loss frecuencias | Log loss mercado | ECE |
|---|---|---|---|---|---|
| 1X2 | Bloqueado | 1.0052 | 1.0638 | 0.9829 | 0.012 |
| Over/Under 2.5 | Experimental | 0.6891 | 0.6891 | — | 0.043 |
| Ambos anotan | Experimental | 0.6889 | 0.6830 | — | 0.057 |

Lectura de los resultados:
- **1X2:** el modelo supera a las frecuencias, pero es peor que el cierre sin margen (Δ +0.0223, IC90 [+0.0120, +0.0327]).
- **Simulación al cierre:** 689 apuestas con ROI −7.9% (IC90 −17.4% a +2.7%).
- **Over/Under y ambos anotan:** no mejoran a las frecuencias, y no hay cuotas históricas de esos mercados.
- **Conclusión:** sin ventaja demostrada. La plataforma recomienda NO BET.

Siguiente paso: acumular cuotas pre-cierre con The Odds API para medir CLV. Después, nuevas variables (Fase 3) y recalibración y ensamblado (Fase 5).
