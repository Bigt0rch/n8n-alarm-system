# Diseño del sistema de informes

## Arquitectura general

```
InfluxDB
   │
   ├─ alarm_notifications  ──► estados de alarmas, responsables, contexto de fallos
   │                           y cálculo de uptime por categoría
   ├─ cpu / mem / disk     ──► métricas de servidores y máquinas virtuales
   └─ mediciones auxiliares ─► datos usados por el sistema de monitorización
          │
          ▼
  reportDaily.py        reportMonthly.py        reportAnnual.py
  informe por           informe del mes         informe del año
  responsable           natural anterior        natural anterior
          │                    │                     │
          └────────────────────┴──────────┬──────────┘
                                           ▼
                                  reportGenerator.py
                                  módulo compartido
                                           │
                         ┌─────────────────┼─────────────────┐
                         ▼                 ▼                 ▼
                      Consultas          Gráficas        Generación PDF
                      InfluxDB          matplotlib         reportlab
                                           │
                                           ▼
                                      Envío por SMTP
```

## Scripts

| Script               | Descripción |
|----------------------|-------------|
| `reportGenerator.py` | Módulo compartido con las consultas a InfluxDB, generación de gráficas, composición del PDF y envío por SMTP. |
| `reportDaily.py`    | Genera informes diarios por responsable. Reconstruye la relación responsable → alarmas → dockers → máquinas virtuales desde `alarmas.json` y `dockers.json`. |
| `reportMonthly.py`  | Genera el informe del mes natural anterior. Incluye uptime, activaciones, métricas, predicciones, disparos y análisis de fallos con contexto cuando existe. |
| `reportAnnual.py`   | Genera el informe del año natural anterior. Actualmente solo incluye la sección de uptime de servicios y contenedores Docker. |

## Estructura del PDF

### Portada e índice

- Portada con título del informe, período cubierto y fecha/hora de generación.
- Índice automático con las secciones y subsecciones generadas.

---

### Sección 1 — Uptime de servicios y contenedores Docker

La sección de uptime se divide en dos subsecciones independientes. No se mezclan servicios y contenedores Docker en la misma tabla.

#### 1.1 Servicios

Esta subsección contiene exclusivamente alarmas de servicios:

```text
alarm_notifications
  └─ categoria == "servicio"
```

Para cada servicio se reconstruyen los intervalos de estado dentro del período informado:

- Se consulta el último estado anterior al inicio del período.
- Si no existe estado previo, se asume `OK`.
- Se recorre la secuencia de cambios de estado durante el período.
- El uptime se calcula acumulando el tiempo en estado `OK`.
- El tiempo caído se calcula como la diferencia entre el total del período y el tiempo en `OK`.

La tabla de servicios se presenta en dos bloques:

| Bloque | Contenido |
|--------|-----------|
| Uptimes críticos de servicios | Servicios con `uptime_pct < umbral_uptime`. |
| Resto de uptimes de servicios | Servicios con `uptime_pct >= umbral_uptime`. |

Columnas de la tabla de servicios:

| Columna | Descripción |
|---------|-------------|
| Alarma / Servicio | Nombre visible de la alarma de servicio. Si empieza por `alarma:`, se elimina ese prefijo. |
| Uptime (%) | Porcentaje de tiempo en estado `OK` durante el período. |
| Tiempo caído (min) | Minutos acumulados fuera de `OK` durante el período. |

Frases informativas de la subsección de servicios:

- Número de servicios con uptime inferior al umbral configurado.
- Total de servicios analizados.
- Servicio con peor uptime.
- Porcentaje de uptime del peor servicio.

Ejemplo de significado de la frase generada:

```text
3 de los 12 servicios han tenido un uptime inferior al 95.0% durante el período.
El peor uptime corresponde a api_backend con un 88.4%.
```

#### 1.2 Contenedores Docker

Esta subsección contiene exclusivamente alarmas de contenedores Docker:

```text
alarm_notifications
  └─ categoria == "docker"
```

No se incluyen aquí servicios HTTP ni alarmas de categoría `servicio`. Aunque un servicio pueda estar asociado a un contenedor en la configuración, el uptime de Docker se calcula solo con las alarmas cuya categoría es `docker`.

La reconstrucción del estado se hace igual que en servicios:

- Se consulta el último estado anterior al inicio del período.
- Si no existe estado previo, se asume `OK`.
- Se acumula el tiempo en estado `OK`.
- Se calcula el tiempo caído como minutos fuera de `OK`.

La tabla de Docker se presenta en dos bloques:

| Bloque | Contenido |
|--------|-----------|
| Uptimes críticos de contenedores Docker | Contenedores Docker que quedan en el rango crítico del coloreado por cuartiles de uptime Docker. |
| Resto de uptimes de contenedores Docker | Contenedores Docker que no quedan en el rango crítico; su valor puede aparecer en verde o naranja según el cuartil correspondiente. |

Columnas de la tabla de Docker:

| Columna | Descripción |
|---------|-------------|
| Alarma / Servicio | Nombre visible de la alarma Docker. En esta subsección representa el contenedor o alarma Docker, aunque la cabecera del PDF sea común. |
| Uptime (%) | Porcentaje de tiempo en estado `OK` del contenedor Docker durante el período. |
| Tiempo caído (min) | Minutos acumulados fuera de `OK` para esa alarma Docker. |

Frases informativas de la subsección de Docker:

- Número de contenedores Docker que quedan en el rango crítico mostrado por la tabla.
- Total de contenedores Docker analizados.
- Contenedor Docker con peor uptime.
- Porcentaje de uptime del peor contenedor Docker.

Ejemplo de significado de la frase generada:

```text
1 de los 8 contenedores Docker aparecen como uptimes críticos durante el período.
El peor uptime corresponde a nginx_proxy con un 91.2%.
```

#### Colores de uptime

El coloreado del uptime no se debe documentar como una regla única para servicios y Docker.

##### Uptime de servicios

En los servicios, el color de la columna **Uptime (%)** usa un umbral fijo:

| Color | Criterio |
|-------|----------|
| Verde | `uptime_pct >= umbral_uptime` |
| Rojo | `uptime_pct < umbral_uptime` |

En esta tabla de servicios no se usa una escala por cuartiles. El umbral por defecto es `95.0`, salvo que se indique otro valor al ejecutar el script.

##### Uptime de contenedores Docker

En los contenedores Docker, el color del uptime se interpreta de forma relativa mediante cuartiles de la distribución de uptimes Docker del período. Como en uptime un valor más bajo es peor, el coloreado resalta los valores bajos:

| Color | Interpretación |
|-------|----------------|
| Rojo | Uptime situado en el tramo crítico o peor cuartil de la distribución. |
| Naranja | Uptime situado en el tramo intermedio entre los cuartiles de referencia. |
| Verde | Uptime situado en el tramo favorable de la distribución. |

Por tanto, el umbral simple de servicios no debe usarse para explicar el color de los uptimes Docker.

---

### Sección 2 — Incidencias / activaciones de servicios y contenedores Docker

Esta sección usa el contador `n_incidencias` calculado durante la reconstrucción del uptime. Una incidencia cuenta únicamente cuando se detecta una transición real:

```text
OK → CRITICAL
```

Igual que el uptime, las incidencias se muestran en dos subsecciones independientes.

#### 2.1 Incidencias de servicios

Esta subsección contiene solo incidencias de alarmas de servicios:

```text
alarm_notifications
  └─ categoria == "servicio"
```

No incluye contenedores Docker.

La tabla de incidencias de servicios se divide en:

| Bloque | Contenido |
|--------|-----------|
| Activaciones críticas de servicios | Servicios cuya cantidad de incidencias cae en el rango rojo. |
| Resto de activaciones de servicios | Servicios con incidencias en rango verde o naranja. |

Columnas de la tabla de servicios:

| Columna | Descripción |
|---------|-------------|
| Alarma / Servicio | Nombre visible de la alarma de servicio. |
| Incidencias | Número de transiciones `OK → CRITICAL` del servicio durante el período. |

Frase informativa de la subsección de servicios:

- Servicio con más activaciones.
- Número total de incidencias de ese servicio durante el período.

Ejemplo de significado de la frase generada:

```text
La alarma con más activaciones ha sido api_backend, con 7 incidencias en el período.
```

#### 2.2 Incidencias de contenedores Docker

Esta subsección contiene solo incidencias de alarmas Docker:

```text
alarm_notifications
  └─ categoria == "docker"
```

No incluye servicios. Si un servicio está relacionado con un contenedor, esa relación no hace que sus incidencias aparezcan en la tabla Docker: aquí solo entran las alarmas de categoría `docker`.

La tabla de incidencias Docker se divide en:

| Bloque | Contenido |
|--------|-----------|
| Activaciones críticas de contenedores Docker | Contenedores Docker cuya cantidad de incidencias cae en el rango rojo. |
| Resto de activaciones de contenedores Docker | Contenedores Docker con incidencias en rango verde o naranja. |

Columnas de la tabla Docker:

| Columna | Descripción |
|---------|-------------|
| Alarma / Servicio | Nombre visible de la alarma Docker. En esta subsección representa el contenedor o alarma Docker, aunque la cabecera del PDF sea común. |
| Incidencias | Número de transiciones `OK → CRITICAL` del contenedor Docker durante el período. |

Frase informativa de la subsección Docker:

- Contenedor Docker con más activaciones.
- Número total de incidencias de ese contenedor durante el período.

Ejemplo de significado de la frase generada:

```text
La alarma con más activaciones ha sido nginx_proxy, con 4 incidencias en el período.
```

#### Colores de incidencias

Las incidencias se colorean por cuartiles de la distribución de `n_incidencias`. Aquí un valor más alto es peor, porque representa más transiciones `OK → CRITICAL` durante el período.

| Color | Interpretación |
|-------|----------------|
| Verde | Valores situados en el tramo favorable o cuartil bajo de incidencias. |
| Naranja | Valores situados en el tramo intermedio entre los cuartiles de referencia. |
| Rojo | Valores situados en el tramo crítico o cuartil alto de incidencias. |

La separación entre **servicios** y **contenedores Docker** afecta a qué filas aparecen en cada tabla, pero no debe explicarse como una regla de colores basada en un umbral fijo. En incidencias, el color es relativo a la distribución de incidencias del período.

---

### Sección 3 — Métricas de servidores y máquinas virtuales

Esta sección muestra una página por host.

**Gráfica de métricas reales**

Incluye:

- CPU: medición `cpu`, campo `usage_user`, filtrada por `cpu == "cpu-total"`.
- RAM: medición `mem`, campo `used_percent`.
- Disco: medición `disk`, campo `used_percent`, separado por partición o `path`.

La granularidad depende del período:

| Tipo de informe | Granularidad de métricas |
|-----------------|--------------------------|
| Diario | Puntos reales del período. |
| Mensual | Media diaria mediante `aggregateWindow(every: 1d, fn: mean)`. |
| Anual | Media diaria, pero sin predicción ARIMA si el informe anual solo incluye uptime. |

**Frases informativas de CPU y RAM**

Para cada recurso se añade:

- Consumo medio del período.
- Consumo máximo y fecha del máximo.
- Día de la semana con mayor media.
- Día del mes con mayor media.
- Tendencia: `creciente`, `estable` o `decreciente`.
- En informes diarios y mensuales, indicación de que la predicción ARIMA se entrena con los últimos 365 días disponibles o con el período del informe si no hay más histórico.

**Frases informativas de disco**

Para cada partición de disco se añade:

- Uso medio.
- Uso máximo.
- Uso mínimo.
- Tendencia.
- Si la tendencia es creciente, estimación aproximada de días hasta alcanzar el 100% de uso.

**Gráfica de predicción**

En informes diarios y mensuales, si la sección de proyecciones está activa, se añade una segunda página de predicción por host.

La predicción usa:

- Histórico diario de hasta los últimos 365 días anteriores al final del período informado.
- Regresión lineal como tendencia de referencia.
- Modelo ARIMA mediante `pmdarima.auto_arima` cuando hay datos suficientes.
- Horizonte de 30 días.
- Intervalo de confianza del 95% cuando el modelo ARIMA se ajusta correctamente.
- Línea vertical para separar histórico y predicción.

---

### Sección 4 — Disparos de alarmas y análisis de fallos

Esta sección contiene una página por alarma con histograma de activaciones reales `OK → CRITICAL`.

La granularidad del histograma depende del período:

| Tipo de informe | Agrupación del histograma |
|-----------------|---------------------------|
| Diario | Por hora, cuando el período dura hasta 2 días. |
| Mensual | Por día. |
| Anual | Por mes. |

La gráfica rellena todos los intervalos del período aunque no haya disparos, usando valor `0`.

Colores de la gráfica de disparos:

| Elemento | Color real usado |
|----------|------------------|
| Barras del histograma | Azul fijo `#2e86c1` |
| Borde de las barras | Blanco |
| Texto de eventos de fallo | Rojo crítico `#c0392b` |

No hay escala azul/naranja/rojo según número de disparos en las barras. Esa descripción anterior era incorrecta.

Frases informativas de disparos:

- Total de disparos durante el período.
- En informes diarios horarios: hora con más disparos.
- En informes mensuales: día de la semana con más disparos y día del mes con más disparos.

**Contextos de disparos de alarma**

Si una alarma tiene eventos `CRITICAL` con campo `contexto` no vacío, se añade debajo del histograma un bloque **Análisis de fallos**.

El bloque muestra:

- Fecha y hora del evento `CRITICAL`.
- Diagnóstico guardado en `contexto`.
- Información de estado del contenedor y recursos de la máquina virtual asociada, cuando ese contexto fue generado por el sistema de monitorización.

Las alarmas sin contexto no muestran una lista vacía de fechas. Simplemente no reciben bloque de análisis de fallos.

---

## Requisito de datos para predicciones

Las predicciones se calculan con ARIMA sobre histórico diario. El generador intenta usar hasta **365 días** anteriores al final del período informado.

Si hay pocos puntos, el gráfico puede mostrar que no hay datos suficientes para una predicción fiable. Cuando ARIMA falla o no se puede ajustar correctamente, la gráfica conserva la tendencia lineal como referencia.