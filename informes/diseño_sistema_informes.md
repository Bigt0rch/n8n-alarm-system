# Diseño del sistema de informes

## Arquitectura general

```
InfluxDB
   │
   ├─ alarm_notifications  ──► uptime de servicios + disparos de alarmas
   │                           (tag "categoria" para filtrar solo servicios)
   ├─ servicio_gen          ──► historial de estado HTTP de servicios
   ├─ cpu / mem             ──► métricas de servidores/VMs (Telegraf)
   └─ docker_gen            ──► métricas de contenedores
          │
          ▼
  reportMonthly.py          reportAnnual.py
  (orquestado por n8n)      (orquestado por n8n)
          │                         │
          └──────────┬──────────────┘
                     ▼
            reportGenerator.py
            (módulo compartido)
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
       Consultas   Gráficas   Generación
       InfluxDB   matplotlib    PDF
                             (reportlab)
                     │
                     ▼
              Envío por SMTP
```

## Scripts

| Script                | Descripción                                      |
|-----------------------|--------------------------------------------------|
| `reportGenerator.py`  | Módulo compartido. No se ejecuta directamente.   |
| `reportMonthly.py`    | Genera el informe del mes natural anterior.      |
| `reportAnnual.py`     | Genera el informe del año natural anterior.      |
| `insert_test_data.py` | Inserta datos de prueba para validar el informe. |

## Orquestación con n8n

Los informes se lanzan desde nodos **Execute Command** de n8n en lugar de cron del sistema, siguiendo el mismo patrón que el resto del sistema de alarmas.

```
[Schedule: día 1 de cada mes, 8:00]
        │
        ▼
[Execute Command: reportMonthly.py]
        │
        ▼
[Schedule: 1 de enero, 8:00]
        │
        ▼
[Execute Command: reportAnnual.py]
```

## Parámetros CLI (igual en ambos scripts ejecutables)

| Parámetro         | Obligatorio | Descripción                                      |
|-------------------|-------------|--------------------------------------------------|
| `--influx-url`    | Sí          | URL del servidor InfluxDB                        |
| `--influx-token`  | Sí          | Token de autenticación de InfluxDB               |
| `--influx-org`    | Sí          | Organización de InfluxDB                         |
| `--influx-bucket` | Sí          | Bucket de InfluxDB                               |
| `--smtp-host`     | Sí          | Servidor SMTP                                    |
| `--smtp-port`     | Sí          | Puerto SMTP                                      |
| `--smtp-user`     | Sí          | Usuario de autenticación SMTP                    |
| `--smtp-password` | Sí          | Contraseña SMTP                                  |
| `--email-from`    | Sí          | Remitente (entre comillas si contiene espacios)  |
| `--email-to`      | Sí          | Destinatario del informe                         |
| `--output-dir`    | No          | Directorio donde guardar el PDF generado         |
| `--log-dir`       | No          | Directorio de logs (si se omite, solo consola)   |

## Estructura del PDF

### Portada
- Título del informe y período cubierto.
- Fecha y hora de generación.

---

### Sección 1 — Uptime de servicios

Tabla con una fila por servicio, calculada reconstruyendo los intervalos
de estado desde `alarm_notifications` (solo alarmas con `categoria == "servicio"`).

| Columna            | Descripción                                          |
|--------------------|------------------------------------------------------|
| Alarma / Servicio  | Nombre de la alarma                                  |
| Uptime (%)         | Verde ≥ media, naranja entre Q1 y media, rojo < Q1          |
| Tiempo caído (min) | Minutos acumulados en estado CRITICAL                |
| Incidencias        | Verde ≤ media, naranja entre media y Q3, rojo > Q3  |

Encima de la tabla aparecen dos frases de resumen:
- Cuántos servicios han tenido uptime inferior al 99%.
- Cuál es el servicio con peor disponibilidad y su uptime.

---

### Sección 2 — Métricas de servidores y VMs

**Una página por host.** Cada página contiene:

**Gráfica de métricas reales**
- Línea de CPU (`usage_user`) por día.
- Línea de RAM (`used_percent`) por día.
- Fuente: mediciones `cpu` y `mem` con `aggregateWindow(every: 1d, fn: mean)`.

**Frases informativas** (una por recurso, CPU y RAM):
- Consumo medio del período y máximo puntual con su fecha.
- Día de la semana y día del mes con mayor consumo.
- Tendencia (creciente / estable / decreciente) y media estimada para el próximo mes.

**Gráfica de proyección**
- Datos históricos atenuados para dar contexto.
- Línea de regresión lineal sobre el período histórico.
- Proyección de los próximos 30 días en línea sólida.
- Banda de confianza (±desviación estándar de residuos).
- Línea vertical que separa histórico de proyección.

---

### Sección 3 — Disparos de alarmas

**Una página por alarma.** Cada página contiene:

**Gráfica de barras**
- Informe mensual: número de disparos por día del mes (eje X = días 01–31).
- Informe anual: número de disparos por mes (eje X = Ene–Dic).
- Todos los días/meses del período aparecen aunque no haya disparos (valor 0).
- Colores: azul ≤ 2, naranja 3–5, rojo > 5 disparos.

**Frases informativas** (solo en informe mensual):
- Total de disparos durante el período.
- Día de la semana con más disparos.
- Día del mes con más disparos.

---

## Dependencias Python

```bash
pip3 install influxdb-client reportlab matplotlib
```

## Requisito de datos para predicciones

La regresión lineal de CPU y RAM requiere al menos **4 semanas de histórico**
para producir estimaciones significativas. Con menos datos la proyección
se calcula igualmente pero la banda de confianza será más amplia.