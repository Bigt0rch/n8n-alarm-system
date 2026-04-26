# n8n-alarm-system
A docker container/service/server alarm monitoring system automated using n8n.

## config.json
Este fichero te debe ser proporcionado y ahí es donde debes modificar llos valores de las distintas credenciales, tanto de base de datos como del correo.

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
 
> ⚠️ n8n requiere **Node.js 18 o superior**.
 
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

 - Para importar el workflow crea un nuevo workflow y haz click en los 3 puntos de arriba a la derecha. Luego haz click en `Import from file...` y selecciona el archivo [Procesar y enviar alarmas.json](Procesar%20y%20enviar%20alarmas.json).
 - Coloca el fichero [alarms.json](alarms.json) en el directorio `~/.n8n-files`.
  - En el nodo `Abrir alarms.json` modifica el path del archivo que se abre por donde este ubicado tu [alarms.json](alarms.json).
 - Coloca el fichero [config.json](config.json) en el directorio `~/.n8n-files`.
 - En el nodo `Abrir config.json` modifica el path del archivo que se abre por donde este ubicado tu [config.json](alarms.json).
  - Modifica tambien los paths de los scripts Python que se ejecutan en todos los nodos `AlarmMonitor` y `AlarmNotifier` para que se ejecuten tus ficheros [alarmMonitorDBn8n.py](alarmMonitorDBn8n.py) y [alarmNotifiern8n.py](alarmNotifiern8n.py).
