# n8n-alarm-system
A docker container/service/server alarm monitoring system automated using n8n.

## config.json
Este fichero te debe ser proporcionado y ahí es donde debes modificar llos valores de las distintas credenciales, tanto de base de datos como del correo.

## Versiones

Este sistema ha sido desarrollado usando:
 - Node 20.20.2
 - npm 10.8.2
 - n8n 2.8.4
 - Linux Mint 21.2

## Instalación de n8n
 
### Requisitos previos
 
Antes de instalar n8n, necesitas tener **Node.js** (que incluye **npm**) instalado en tu sistema.
 
#### ¿Ya tienes Node.js/npm instalado?
 
Abre una terminal y ejecuta:
 
```bash
node --version
npm --version
```
 
Si ambos comandos devuelven un número de versión (por ejemplo, `v20.11.0` y `10.2.4`), puedes saltarte directamente al apartado [Instalación de n8n](#instalación-de-n8n).
 
> ⚠️ n8n requiere **Node.js 20.22 o superior, debido a las dependencias.**.
 
---
 
### Instalación de Node.js y npm

```bash
# Actualizar los repositorios
sudo apt update
 
sudo apt install npm
 
# Verificar la instalación
node --version
npm --version
```

 
---
 
### Instalación de n8n
 
Con Node.js y npm listos, instala n8n de forma global ejecutando:
 
```bash
npm install -g n8n
```
 
Este proceso puede tardar unos minutos. Una vez terminado, verifica que n8n se instaló correctamente:
 
```bash
n8n --version
```
 
---
 
### Lanzar n8n
 
Para arrancar n8n y poder ejecutar este workflow, ejecuta simplemente:
 
```bash
# Asegurar que n8n no excluya nodos como Execute Command que son necesarios en nuestro
export NODES_EXCLUDE="[]"

n8n start
```
 
---
 
### Crear tu cuenta de administrador
 
1. Abre tu navegador y accede a:
   ```
   http://localhost:5678
   ```
 
2. La primera vez que arranques n8n, se mostrará la pantalla de **configuración del propietario**:
3. Rellena los campos del formulario:
   | Campo | Descripción |
   |---|---|
   | **First name** | Tu nombre |
   | **Last name** | Tu apellido |
   | **Email** | Correo que usarás para iniciar sesión |
   | **Password** | Contraseña |
4. Haz clic en **Next** para continuar.
 
---
 
### Notas adicionales
 
- Por defecto, n8n guarda todos los datos (flujos, credenciales, etc.) en un archivo SQLite en tu directorio de usuario (`~/.n8n`).
- n8n solo permite leer archivos desde el directorio (`~/.n8n-files`), así que debes colocar los archivos del workflow (scripts, archivos de configuración, etc) allí.
---

## Scripts

 - Coloca los archivos [alarmMonitorDBn8n.py](alarmMonitorDBn8n.py) y [alarmNotifiern8n.py](alarmNotifiern8n.py) en la carpeta `~/.n8n-files`.

## Workflow

 - Para importar el workflow crea un nuevo workflow y haz click en los 3 puntos de arriba a la derecha. Luego haz click en `Import from file...` y selecciona el archivo [Workflow principal alarmas.json](Workflow%20principal%20alarmas.json).
 - Realiza el mismo proceso con el archivo [Workflow secundario (webhook).json](Workflow%20secundario%20(webhook).json)
 - Coloca el fichero [alarms.json](alarms.json) en el directorio `~/.n8n-files`.
  - En el nodo `Abrir alarms.json` del workflow `Workflow principal alarmas.json` modifica el path del archivo que se abre por donde este ubicado tu [alarms.json](alarms.json).
 - Coloca el fichero [config.json](config.json) en el directorio `~/.n8n-files`.
 - En el nodo `Abrir config.json` del workflow `Workflow secundario (webhook).json` modifica el path del archivo que se abre por donde este ubicado tu [config.json](alarms.json).
  - Modifica tambien los paths de los scripts Python que se ejecutan en los nodos `AlarmMonitor` y `AlarmNotifier` del workflow `Workflow secundario (webhook).json` para que se ejecuten tus ficheros [alarmMonitorDBn8n.py](alarmMonitorDBn8n.py) y [alarmNotifiern8n.py](alarmNotifiern8n.py).

## Configurar n8n como un Servicio

### Descripción General

Esta guía explica cómo configurar n8n como un servicio utilizando `systemd` en sistemas Linux. Al configurarlo como servicio, n8n se iniciará automáticamente cada vez que el servidor arranque, garantizando que los flujos de automatización estén disponibles de forma continua sin necesidad de iniciarlo manualmente.

---

### Crear un archivo de servicio para systemd

Abre un terminal y crea un archivo de servicio para n8n en el directorio de configuración de `systemd`:

```bash
sudo nano /etc/systemd/system/n8n.service
```

Añade el siguiente contenido al archivo:

```ini
[Unit]
Description=n8n Automation Service
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/n8n
Restart=always
RestartSec=10
User=tu_usuario
Environment=NODE_ENV=production
Environment=N8N_PORT=5678
Environment=N8N_HOST=0.0.0.0
Environment=WEBHOOK_URL=http://TU_IP_O_DOMINIO:5678/

[Install]
WantedBy=multi-user.target
```

#### Explicación de los parámetros

* `ExecStart`: Especifica la ruta completa al binario de n8n.
  Puedes verificar la ruta ejecutando:

```bash
which n8n
```

Por ejemplo:

```ini
ExecStart=/usr/local/bin/n8n
```

* `User`: Cambia `tu_usuario` por el usuario que ejecutará el servicio (por ejemplo, `luisgarcia`).

* `N8N_PORT`: Puerto donde escuchará n8n.

* `N8N_HOST`: Dirección IP o interfaz de red donde n8n estará disponible.

* `WEBHOOK_URL`: URL pública utilizada por los webhooks de n8n.

---

### Recargar systemd

Después de guardar el archivo, recarga los demonios de `systemd` para que el nuevo servicio sea reconocido:

```bash
sudo systemctl daemon-reload
```

---

### Habilitar el servicio para inicio automático

Habilita el servicio para que se ejecute automáticamente cada vez que el servidor arranque:

```bash
sudo systemctl enable n8n
```

---

### Iniciar el servicio de n8n

Inicia el servicio manualmente para comprobar que funciona correctamente:

```bash
sudo systemctl start n8n
```

---

### Verificar el estado del servicio

Revisa el estado del servicio para confirmar que está corriendo correctamente:

```bash
sudo systemctl status n8n
```

Si el servicio se está ejecutando correctamente, deberías ver una salida similar a:

```bash
Active: active (running)
```

---

### Logs del servicio

Para depurar problemas o verificar que n8n está funcionando correctamente, puedes consultar los logs del servicio:

```bash
sudo journalctl -u n8n -f
```

Esto mostrará los eventos en tiempo real relacionados con el servicio de n8n.

---

### Notas adicionales

#### Persistencia de datos

Por defecto, n8n almacena su base de datos SQLite y configuraciones en:

```bash
~/.n8n
```

Asegúrate de que el usuario especificado en `User` tenga permisos sobre este directorio.

---

#### Acceso desde navegador

Una vez iniciado el servicio, podrás acceder a n8n desde:

```text
http://IP_DEL_SERVIDOR:5678
```

o mediante tu dominio configurado.

---

