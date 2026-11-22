# NURI · 누리 — Picks de valor  (v3)

Una sola pantalla: **los mejores picks de la semana** y **la autoevaluación de esos mismos picks**.
Si ningún partido tiene ventaja real, NURI no te da nada. Toda la matemática corre en tu laptop.

## Cómo elige un pick

NURI revisa todos los partidos de las próximas 48 h (fútbol, NBA, NFL, NHL, MLB) cada 5 segundos y sólo
deja pasar dos tipos de pick:

- **Momio real con valor**: una casa que opera en México (Bet365 vía ESPN, gratis; Caliente / Codere si
  conectas odds-api.io) paga más que la probabilidad real (línea de mercado sin margen, modelo de Shin, o
  el modelo calibrado a ella). Ventaja mínima 5 % (7 % si la línea la calcula el modelo).
- **Línea movida**: la línea de referencia se movió ≥ 4 puntos de probabilidad a favor desde que abrió y
  las estadísticas reales lo respaldan. El pick dice el momio mínimo: si tu casa paga menos, no se toma.

Por dentro usa todo: forma de los últimos 5, diferencia de goles/puntos de la temporada, bajas de los
líderes, predictor de ESPN, cara a cara y local/visita. El candidato debe sostenerse 3 minutos;
máximo 5 por día y uno por partido.

## Autoevaluación (por semana, lunes a domingo)

Cada pick queda guardado con su momio y su probabilidad. Al terminar el partido NURI lo califica solo con
el marcador oficial de ESPN (aunque la app haya estado cerrada) y muestra: récord, unidades (1 u por pick),
ROI, aciertos esperados contra aciertos reales y **CLV** (si la línea se movió a favor después del pick,
la mejor señal de que la ventaja es real). Si los picks dejan de ganarle al cierre, NURI sube su exigencia solo.

## Cómo se abre

Doble clic en el ícono **NURI** del escritorio (o búscalo en el menú Inicio).
Se abre como ventana de app (Brave, sin barra de direcciones ni Terminal).
Para apagarlo: botón **Apagar NURI** abajo a la derecha, o simplemente cierra la ventana:
el servidor se apaga solo a los 20 minutos sin uso.

## De dónde salen los datos

| Qué | Fuente | Costo |
|---|---|---|
| Partidos, marcador en vivo, minuto, logos oficiales | ESPN (API pública) | gratis |
| **Estadísticas reales**: récord, tabla, últimos 5, promedios de temporada, bajas, líderes, predictor ESPN, cara a cara, alineaciones | Resumen de cada partido en ESPN | gratis |
| **Momios reales de Bet365** (fútbol: 1X2, total de goles, goleador) en Premier, Serie A, Bundesliga, Ligue 1, MLS, Champions, Europa League, Eredivisie, Portugal… | Los que ESPN publica de Bet365 | gratis |
| Línea de mercado y su movimiento (apertura → actual) | DraftKings vía ESPN, **sólo como referencia** de probabilidad real (Shin le quita el margen) | gratis |
| **Momios reales de Caliente y Codere MX** (pre-partido y en vivo) | odds-api.io con tu clave (plan Solo ≈ US$65/mes, 2 casas) | de pago |
| Probabilidades | Motor NURI: Dixon-Coles / Poisson / Gaussiano + Monte Carlo 25,000 + Bayes con marcador, reloj y tarjetas | — |

Ligas: Liga MX, Premier League, LaLiga, Serie A, Bundesliga, Ligue 1, Champions League, MLS, NBA, NFL, NHL y MLB.

### Caliente y Codere

No tienen API pública y NURI no se mete a sus sistemas. El único servicio autorizado que hoy los publica
es **odds-api.io** (Caliente y Codere MX aparecen activos en su catálogo). Para conectarlos:

1. Compra el plan **Solo** en https://odds-api.io/pricing (tú pagas; NURI nunca toca tu tarjeta).
2. Copia la clave que te llega por correo.
3. En NURI → Radar → **Conectar Caliente y Codere** → pega la clave → Guardar → elige Caliente + Codere MX.

La clave se guarda sólo en tu laptop (SQLite en `%LOCALAPPDATA%\NURI`). También puedes ponerla en `.env`
como `ODDSIO_KEY=...`. Sin clave, NURI sigue funcionando con Bet365 + líneas públicas + estadísticas ESPN.

## Honestidad del modelo

Una ventaja real en apuestas es de 2–8 %. Nadie tiene ventajas de 50–90 %: si una app te las muestra,
está mal calculada o te miente. Si NURI te marca una ventaja enorme al escribir un momio, casi siempre es
un error de captura o una línea distinta (otro hándicap/total).

## Archivos

```
backend/nuri_app.py     lanzador de escritorio (pythonw, sin consola)
backend/main.py         FastAPI + WebSocket /ws/live (cada 5 s)
backend/quant_engine.py las 7 capas matemáticas + tablero de probabilidades
backend/pickbook.py     elige los picks, los califica y arma la autoevaluación semanal
backend/mx_feeds.py     ESPN (real), simulador demo, The Odds API opcional
backend/espn_facts.py   estadísticas reales + momios Bet365 del resumen de ESPN
backend/oddsio.py       Caliente / Codere MX vía odds-api.io (con tu clave)
backend/database.py     SQLite local (nuri.sqlite3)
backend/nuri.log        registro (si algo falla, aquí está el motivo)
backend/estado.json     resumen del estado de las fuentes
frontend/               React + Vite + Tailwind (dist/ ya compilado)
```
