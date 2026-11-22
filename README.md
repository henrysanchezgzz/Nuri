# NURI · 누리

Los mejores picks de valor para casas que operan en México, con autoevaluación semanal.
Corre solo en la nube: no necesita ninguna computadora prendida.

**App:** https://henrysanchezgzz.github.io/Nuri/ (se instala en el iPhone desde Safari → Compartir → *Agregar a inicio*).

## Cómo funciona

Cada 15 minutos GitHub Actions (`.github/workflows/nuri.yml`) corre `cloud/run.py`:

1. Baja de ESPN marcadores, líneas de referencia (DraftKings), estadísticas reales y momios reales de Bet365.
2. Analiza todos los mercados: probabilidad justa de la línea sin comisión (Shin), simulación de 25 mil partidos por
   deporte (Dixon-Coles / Poisson / normal), y la compara contra los momios reales.
3. Sólo emite un pick si la ventaja se sostiene en dos revisiones seguidas y pasa todos los filtros:
   - **Momio real:** la casa paga más de lo justo (5 % o más; más en empates, BTTS y momios altos). Totales y hándicaps
     sólo cerca de una línea con precio de referencia. Se descarta si la casa alargó el momio sin que el mercado se moviera.
   - **Línea moviéndose:** el mercado se movió 4–12 puntos en las últimas horas (medido por NURI) y las estadísticas
     coinciden; el pick dice desde qué momio conviene.
   - Máximo 5 al día, 2 por liga, 1 por partido y 10 % del banco por día (¼ de Kelly, tope 3 % por pick).
4. Califica los picks terminados, mide si le ganó a la línea de cierre (CLV) y se vuelve más exigente si no.
5. Manda cada pick, su resultado y el resumen de los lunes al celular con [ntfy](https://ntfy.sh).
6. Publica la app y los datos **cifrados** (PBKDF2-SHA256 200 mil vueltas + AES-256-GCM) en la rama `site`
   (GitHub Pages). El repositorio es público; los picks sólo se abren con el código.

## Secretos del repositorio

| Nombre | Para qué |
| --- | --- |
| `NURI_CODE` | Código que cifra los datos; la app lo pide una vez en cada celular |
| `NTFY_TOPIC` | Tema de ntfy donde llegan los avisos |
| `ODDSIO_KEY` | (opcional) clave de odds-api.io para sumar momios de Caliente y Codere |

## Carpetas

- `backend/` motor (feeds, modelo, registro de picks). `main.py` es el modo de escritorio viejo.
- `cloud/` la corrida en la nube y el cifrado.
- `app/` la app (una sola página, estilo Namufin: Aero, Oscuro y Claro).
- `frontend/` la interfaz de escritorio anterior.

En la laptop, el ícono de NURI ya sólo abre la app de la nube (con `NURI_LOCAL=1` en `.env` vuelve el modo local).

Apostar implica riesgo. NURI busca ventaja matemática, no garantiza resultados.
