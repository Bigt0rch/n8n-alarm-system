# Diseño del sistema de informes

## Arquitectura general

```
InfluxDB
   │
   ├─ alarm_notifications  ──► cálculo de uptime y conteo de alarmas
   ├─ servicio_gen          ──► historial de estado de servicios
   ├─ cpu / mem / disk      ──► métricas de servidores (Telegraf)
   └─ docker_gen            ──► métricas de contenedores
          │
          ▼
  reportMonthly.py          reportAnnual.py
  (cron: día 1 de mes)      (cron: 1 de enero)
          │                         │
          └──────────┬──────────────┘
                     ▼
              Generación PDF
              (reportlab + matplotlib)
                     │
                     ▼
              Envío por SMTP
```

## Scripts

| Script              | Período cubierto            | Cron              |
|---------------------|-----------------------------|-------------------|
| `reportMonthly.py`  | Mes natural anterior        | `0 8 1 * *`       |
| `reportAnnual.py`   | Año natural anterior        | `0 8 1 1 *`       |

Ambos scripts comparten la misma lógica, parámetros y estructura de PDF.
Solo difieren en el rango temporal de las consultas.

## Parámetros CLI (igual en ambos scripts)

```
--influx-url      URL de InfluxDB              (obligatorio)
--influx-token    Token de InfluxDB            (obligatorio)
--influx-org      Organización de InfluxDB     (obligatorio)
--influx-bucket   Bucket de InfluxDB           (obligatorio)
--smtp-host       Servidor SMTP                (obligatorio)
--smtp-port       Puerto SMTP                  (obligatorio)
--smtp-user       Usuario SMTP                 (obligatorio)
--smtp-password   Contraseña SMTP              (obligatorio)
--email-from      Remitente                    (obligatorio)
--email-to        Destinatario(s) del informe  (obligatorio)
--output-dir      Directorio donde guardar el PDF generado (opcional)
--log-dir         Directorio de logs           (opcional)
```

## Estructura del PDF

### Página 1 — Portada
- Título: "Informe mensual / anual de monitorización"
- Período cubierto
- Fecha de generación

### Sección 1 — Uptime de servicios
- Tabla con una fila por servicio:
  | Servicio | Uptime (%) | Tiempo caído | Nº incidencias |
- Cálculo: reconstruir intervalos desde `alarm_notifications`
  ordenando los cambios de estado por timestamp y sumando
  el tiempo en estado OK sobre el total del período.

### Sección 2 — Métricas de servidores y VMs
- Una subsección por host con dos gráficas de línea:
  - Media de CPU (`usage_user`) por día
  - Media de RAM (`used_percent`) por día
- Consulta Flux con `aggregateWindow(every: 1d, fn: mean)`

### Sección 3 — Histograma de alarmas
- Un gráfico de barras horizontales por alarma mostrando
  cuántas veces ha pasado a estado CRITICAL en el período.
- Fuente: `alarm_notifications` filtrando `estado == "CRITICAL"`,
  agrupando por `alarm_id` y contando registros.

## Dependencias Python

```
influxdb-client
reportlab
matplotlib
smtplib (stdlib)
```

## Instalación

```bash
pip install influxdb-client reportlab matplotlib
```

## Crontab

```cron
# Informe mensual: día 1 de cada mes a las 8:00
0 8 1 * * python3 /home/victor/.n8n-files/reportMonthly.py \
    --influx-url http://localhost:8086 \
    --influx-token TU_TOKEN \
    --influx-org TU_ORG \
    --influx-bucket TU_BUCKET \
    --smtp-host smtp.serviciodecorreo.es \
    --smtp-port 465 \
    --smtp-user uptime@actionproject.eu \
    --smtp-password TU_PASSWORD \
    --email-from "Informes <uptime@actionproject.eu>" \
    --email-to admin@actionproject.eu \
    --output-dir ~/.n8n-files/reports \
    --log-dir ~/.n8n-files/logs

# Informe anual: 1 de enero a las 8:00
0 8 1 1 * python3 /home/victor/.n8n-files/reportAnnual.py \
    --influx-url http://localhost:8086 \
    --influx-token TU_TOKEN \
    --influx-org TU_ORG \
    --influx-bucket TU_BUCKET \
    --smtp-host smtp.serviciodecorreo.es \
    --smtp-port 465 \
    --smtp-user uptime@actionproject.eu \
    --smtp-password TU_PASSWORD \
    --email-from "Informes <uptime@actionproject.eu>" \
    --email-to admin@actionproject.eu \
    --output-dir ~/.n8n-files/reports \
    --log-dir ~/.n8n-files/logs
```
