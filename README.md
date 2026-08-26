# CBR-to-OBS Migration Tool

Herramienta para migrar backups de discos EVS desde CBR (Cloud Backup and Recovery) de Huawei Cloud hacia buckets de OBS (Object Storage Service), utilizando FunctionGraph como motor de orquestacion.

Soporta las regiones de **Buenos Aires** (`sa-argentina-1`) y **Santiago** (`la-south-2`), permitiendo migracion dentro de la misma region (same-region) y entre regiones (cross-region).

---

## Tabla de Contenidos

1. [Resumen](#resumen)
2. [Arquitectura](#arquitectura)
3. [Flujo de Migracion](#flujo-de-migracion)
4. [Prerrequisitos](#prerrequisitos)
5. [Configuracion](#configuracion)
6. [Despliegue](#despliegue)
7. [Uso](#uso)
8. [Estructura del Proyecto](#estructura-del-proyecto)
9. [Frontend Web](#frontend-web)
10. [Monitoreo y Troubleshooting](#monitoreo-y-troubleshooting)
11. [Limitaciones](#limitaciones)
12. [Testing](#testing)
13. [FAQ](#faq)

---

## Resumen

CBR almacena los backups en almacenamiento interno propio. No existe una API para "descargar" un backup directamente. Esta herramienta implementa un flujo multi-paso que convierte cada backup de disco EVS en un archivo de imagen (formato VHD) almacenado en un bucket de OBS.

### Que hace

- Toma un backup de CBR (tipo EVS / `OS::Cinder::Volume`)
- Restaura el backup a un volumen EVS temporal
- Crea una imagen IMS desde ese volumen
- Exporta la imagen a un bucket de OBS como archivo `.vhd`
- Elimina los recursos temporales (volumen e imagen)

### Regiones soportadas

| Ciudad | Region ID | Bucket OBS |
|--------|-----------|------------|
| Buenos Aires | `sa-argentina-1` | `cbr-evs-buenosaires` |
| Santiago | `la-south-2` | `cbr-evs-santiago` |

---

## Arquitectura

Se utilizan **3 funciones de FunctionGraph** que trabajan en conjunto mediante un patron "start-and-poll" (iniciar y verificar), necesario porque las operaciones de CBR/EVS/IMS son asincronas y pueden tardar minutos u horas.

```
                    HTTP Request (APIG)
                           |
                           v
                +---------------------+
                |   ORCHESTRATOR     |  1. Recibe backup_id + regiones
                |   (trigger: APIG)   |  2. Inicia restore/replicate
                +---------------------+  3. Guarda estado en OBS
                           |
                           v
                    OBS (state/*.json)
                           |
                           v
                +---------------------+
                |  STATUS_CHECKER     |  1. Lee jobs pendientes
                |  (trigger: Timer    |  2. Verifica cada paso
                |   cada 5 min)        |  3. Avanza al siguiente paso
                +---------------------+  4. Guarda estado actualizado
                           |
                           v
                +---------------------+
                |     CLEANUP         |  1. Busca jobs completados
                |  (trigger: Timer    |  2. Elimina volumen EVS temporal
                |   cada 10 min)       |  3. Elimina imagen IMS temporal
                +---------------------+
```

### Por que 3 funciones?

FunctionGraph tiene un tiempo maximo de ejecucion de 15 minutos. Las operaciones de restauracion de backup, creacion de imagen y exportacion a OBS pueden tardar mucho mas. El patron start-and-poll permite:

1. **Orchestrator**: Inicia la operacion y retorna inmediatamente (segundos)
2. **Status Checker**: Se ejecuta periodicamente, verifica el estado y avanza al siguiente paso cuando corresponde
3. **Cleanup**: Limpia recursos temporales cuando la migracion termina

---

## Flujo de Migracion

### Same-region (ej: backup en BA -> OBS en BA)

```
1. Orchestrator recibe: backup_id + source_region=buenosaires
2. EVS: Crear volumen desde backup (CBR backup_id -> nuevo EVS volume)
3. [Status Checker] Espera volumen "available"
4. EVS: Crear imagen IMS desde volumen (os-volume_upload_image)
5. [Status Checker] Espera imagen "active"
6. IMS: Exportar imagen a OBS bucket (cbr-evs-buenosaires)
7. [Status Checker] Espera export "SUCCESS"
8. [Cleanup] Eliminar volumen e imagen temporal
9. Resultado: archivo .vhd en bucket OBS
```

### Cross-region (ej: backup en BA -> OBS en Santiago)

```
1. Orchestrator recibe: backup_id + source_region=buenosaires + target_region=santiago
2. CBR: Replicar backup a Santiago (API replicate)
3. [Status Checker] Espera replicacion "success"
4. EVS (en Santiago): Crear volumen desde backup replicado
5. [Status Checker] Espera volumen "available"
6. EVS (en Santiago): Crear imagen IMS desde volumen
7. [Status Checker] Espera imagen "active"
8. IMS (en Santiago): Exportar imagen a OBS bucket (cbr-evs-santiago)
9. [Status Checker] Espera export "SUCCESS"
10. [Cleanup] Eliminar volumen e imagen temporal
11. Resultado: archivo .vhd en bucket OBS de Santiago
```

### Volumenes grandes (> 1TB) — Ruta Raw Export

IMS tiene un limite de ~1TB para exportar imagenes. Para volumenes mayores
(ej. 5TB), la herramienta usa una ruta alternativa sin IMS:

```
1. Restaurar backup a volumen EVS (igual que antes)
2. Crear ECS temporal (Linux) con cloud-init que instala obsutil
3. Adjuntar volumen al ECS como /dev/vdb
4. Cloud-init ejecuta: dd if=/dev/vdb | obsutil cp - obs://bucket/key.raw
5. Al terminar, sube un marker .SUCCESS al bucket
6. [Status Checker] Detecta el marker -> avanza el job
7. [Cleanup] Elimina ECS temporal y volumen
8. Resultado: archivo .raw en bucket OBS (hasta 48.8TB)
```

Configuracion requerida (ver `.env.example`):

| Variable | Descripcion |
|---|---|
| `RAW_EXPORT_THRESHOLD_GB` | Umbral para usar ruta raw (default: 1024) |
| `TEMP_ECS_IMAGE_ID_BA/CL` | ID de imagen Linux (ej. Ubuntu 22.04) |
| `TEMP_ECS_FLAVOR_BA/CL` | Flavor ECS (>=4GB RAM recomendado) |
| `TEMP_ECS_NETWORK_ID_BA/CL` | ID de red/VPC |
| `TEMP_RAW_PART_MB` | Tamano de parte multipart (default: 600) |
| `TEMP_RAW_CONCURRENCY` | Subidas paralelas (default: 3) |

> **Nota de seguridad**: El AK/SK se inyecta en el user-data del ECS temporal
> (visible solo desde dentro de la instancia via metadata). Usar un sub-usuario
> IAM con permisos restringidos. El ECS se elimina automaticamente al terminar.

---

## Prerrequisitos

### 1. Cuenta de Huawei Cloud

- Una cuenta de Huawei Cloud con acceso a CBR, EVS, IMS, OBS y FunctionGraph
- AK/SK (Access Key / Secret Key) con permisos en ambas regiones
  - Crear en: **IAM > Access Keys > Create Access Key**

### 2. Project IDs

Necesitas el Project ID de cada region:
- Obtener en: **IAM > Projects** (buscar `sa-argentina-1` y `la-south-2`)

### 3. CBR Vaults (para cross-region)

Si vas a usar migracion cross-region, necesitas un CBR Vault en la region destino:
- Crear en: **CBR > Vaults > Create Vault** (en cada region)

### 4. Herramientas instaladas

| Herramienta | Version | Proposito |
|-------------|---------|-----------|
| Python | 3.9+ | Ejecutar funciones localmente, tests |
| hcloud CLI | ultima | Desplegar funciones, crear buckets |
| pip | ultima | Instalar dependencias |
| PowerShell | 5.1+ | Ejecutar scripts de despliegue |

Instalar hcloud CLI:
```powershell
pip install huaweicloud-sdk-python-cli
```

Configurar hcloud:
```powershell
hcloud configure set --cli-region=sa-argentina-1 --ak=YOUR_AK --sk=YOUR_SK
```

---

## Configuracion

### Paso 1: Variables de entorno

Copia `.env.example` a `.env` y completa tus valores:

```powershell
Copy-Item .env.example .env
notepad .env
```

Valores a configurar:

| Variable | Descripcion | Ejemplo |
|----------|-------------|---------|
| `HW_ACCESS_KEY` | Access Key de IAM | `AKXXXXXXX...` |
| `HW_SECRET_KEY` | Secret Key de IAM | `sk-xxxx...` |
| `HW_PROJECT_ID_BUENOSAIRES` | Project ID de sa-argentina-1 | `0a1b2c3d...` |
| `HW_PROJECT_ID_SANTIAGO` | Project ID de la-south-2 | `5e6f7g8h...` |
| `HW_VAULT_ID_BUENOSAIRES` | CBR Vault ID en BA (cross-region) | `uuid...` |
| `HW_VAULT_ID_SANTIAGO` | CBR Vault ID en Santiago (cross-region) | `uuid...` |
| `OBS_STATE_BUCKET` | Bucket para estado de jobs | `cbr-migration-state` |
| `OBS_STATE_REGION` | Region del bucket de estado | `sa-argentina-1` |
| `TEMP_VOLUME_TYPE` | Tipo de volumen temporal | `SATA` |
| `CLEANUP_AFTER_EXPORT` | Limpiar recursos temporales | `true` |

### Paso 2: Crear buckets OBS

```powershell
.\scripts\create_buckets.ps1
```

Esto crea:
- `cbr-evs-buenosaires` en `sa-argentina-1`
- `cbr-evs-santiago` en `la-south-2`
- `cbr-migration-state` en `sa-argentina-1`

### Paso 3: Configurar IAM

```powershell
.\scripts\setup_iam.ps1
```

Esto crea:
- Un rol personalizado `cbr_to_obs_role` con permisos CBR, EVS, IMS, OBS
- Una agency `cbr_to_obs_agency` para que FunctionGraph asuma el rol

---

## Despliegue

### Desplegar las funciones

```powershell
.\scripts\deploy.ps1
```

Por defecto despliega en `sa-argentina-1`. Para especificar otra region:

```powershell
.\scripts\deploy.ps1 -Region la-south-2
```

Esto crea 3 funciones en FunctionGraph:
1. `cbr-obs-orchestrator` - sin trigger (crear APIG manualmente)
2. `cbr-obs-status-checker` - Timer trigger cada 5 minutos
3. `cbr-obs-cleanup` - Timer trigger cada 10 minutos

### Crear trigger APIG para el orchestrator

Despues del despliegue, crear un trigger API Gateway para `cbr-obs-orchestrator`:

1. Ir a **FunctionGraph > cbr-obs-orchestrator > Triggers > Create Trigger**
2. Seleccionar **APIG (dedicated)**
3. Configurar:
   - API Name: `cbr-obs-migrate-api`
   - Method: `POST`
   - Path: `/migrate`
   - Security: IAM
4. Guardar y copiar la URL del API

---

## Uso

### Iniciar una migracion

Enviar una peticion HTTP al API Gateway:

**Same-region (Buenos Aires -> Buenos Aires):**
```bash
curl -X POST https://your-apig-url/migrate \
  -H "Content-Type: application/json" \
  -d '{
    "backup_id": "abc123-def456-...",
    "source_region": "buenosaires"
  }'
```

**Cross-region (Buenos Aires -> Santiago):**
```bash
curl -X POST https://your-apig-url/migrate \
  -H "Content-Type: application/json" \
  -d '{
    "backup_id": "abc123-def456-...",
    "source_region": "buenosaires",
    "target_region": "santiago"
  }'
```

**Respuesta:**
```json
{
  "job_id": "uuid-of-the-job",
  "step": "restoring",
  "message": "Migration job started. Use the status_checker to monitor progress."
}
```

### Listar backups disponibles

Para ver que backups de EVS existen en una region:

```powershell
hcloud CBR ListBackups --resource_type=OS::Cinder::Volume --status=available --cli-region=sa-argentina-1
```

### Verificar estado de un job

El estado de cada job se guarda en OBS como JSON en `s3://cbr-migration-state/state/{job_id}.json`:

```powershell
hcloud obs GetObject --bucket=cbr-migration-state --key=state/JOB_ID.json --cli-region=sa-argentina-1
```

**Estados posibles:**

| Estado | Descripcion |
|--------|-------------|
| `replicating` | Replicando backup cross-region (CBR) |
| `restoring` | Creando volumen EVS desde backup |
| `creating_image` | Creando imagen IMS desde volumen |
| `exporting` | Exportando imagen a OBS |
| `copying_obs` | Copiando objeto cross-region en OBS |
| `cleanup_pending` | Listo para limpiar recursos temporales |
| `completed` | Migracion completada |
| `failed` | Migracion fallo (ver campo `error`) |

### Ver resultado en OBS

```powershell
hcloud obs ListObjects --bucket=cbr-evs-buenosaires --prefix=backups/ --cli-region=sa-argentina-1
```

El archivo resultado es una imagen de disco en formato VHD:
```
backups/{backup_id}/{backup_name}.vhd
```

---

## Estructura del Proyecto

```
cbr-to-obs/
|
|-- src/
|   |-- shared/                        # Modulos compartidos entre funciones
|   |   |-- __init__.py
|   |   |-- regions.py                 # Definicion de regiones (BA, Santiago)
|   |   |-- config.py                  # Configuracion desde env vars
|   |   |-- huawei_auth.py             # Auth IAM (token) + OBS (V4 signing)
|   |   |-- obs_client.py             # Cliente OBS (estado de jobs, copy)
|   |   |-- cbr_client.py             # Cliente CBR (list, restore, replicate)
|   |   |-- evs_client.py             # Cliente EVS (create volume, image)
|   |   |-- ims_client.py             # Cliente IMS (export image, status)
|   |
|   |-- functions/                     # Funciones de FunctionGraph
|       |-- orchestrator/
|       |   |-- handler.py             # Entry point - inicia migracion
|       |   |-- job_model.py           # Modelo de estado del job
|       |   |-- requirements.txt       # Dependencias Python
|       |
|       |-- status_checker/
|       |   |-- handler.py             # Timer - verifica y avanza jobs
|       |   |-- requirements.txt
|       |
|       |-- cleanup/
|       |   |-- handler.py             # Timer - limpia recursos temporales
|       |   |-- requirements.txt
|       |
|       |-- api/
|           |-- handler.py             # REST API router para frontend
|           |-- requirements.txt
|
|-- frontend/                          # React + Vite + TailwindCSS
|   |-- package.json
|   |-- vite.config.ts
|   |-- tailwind.config.js
|   |-- index.html
|   |-- src/
|       |-- main.tsx                   # Entry point
|       |-- App.tsx                    # Router + layout
|       |-- api/                       # API client + tipos
|       |-- context/                   # AuthContext
|       |-- hooks/                     # useBackups, useJobs
|       |-- pages/                     # Login, Dashboard, Backups, Migrations, JobDetail
|       |-- components/               # Layout, RegionSelector, StatusBadge, etc.
|
|-- config/                            # Archivos de configuracion
|   |-- regions.json                   # Endpoints por region
|   |-- buckets.json                   # Nombres y config de buckets
|
|-- scripts/                           # Scripts de despliegue (PowerShell)
|   |-- create_buckets.ps1            # Crear buckets OBS
|   |-- setup_iam.ps1                 # Crear IAM agency y rol
|   |-- deploy.ps1                    # Desplegar funciones a FunctionGraph
|   |-- deploy_frontend.ps1           # Build + deploy frontend a OBS
|
|-- tests/                             # Tests unitarios
|   |-- test_regions.py
|   |-- test_cbr_client.py
|   |-- test_orchestrator.py
|   |-- test_status_checker.py
|
|-- .env.example                       # Template de configuracion
|-- README.md                          # Esta documentacion
```

---

## Frontend Web

El proyecto incluye un frontend web React que proporciona una interfaz visual para gestionar migraciones de CBR a OBS sin necesidad de usar la linea de comandos.

### Caracteristicas

- **Login con AK/SK**: El usuario ingresa sus credenciales de Huawei Cloud. Se guardan solo en la sesion del navegador (sessionStorage).
- **Dashboard**: Estadisticas de migraciones (total, activas, completadas, fallidas) y jobs recientes.
- **Listado de Backups**: Ve los backups de EVS disponibles en CBR, filtra por region (Buenos Aires / Santiago).
- **Migracion con un click**: Selecciona uno o multiples backups y elige la region destino para iniciar la migracion.
- **Seguimiento de Jobs**: Ve el estado de todas las migraciones con auto-refresh cada 10 segundos para jobs activos.
- **Detalle de Job**: Timeline visual de los pasos de migracion, recursos temporales (volumen, imagen, bucket) y errores.
- **Reintentar y Eliminar**: Reintenta jobs fallidos o elimina jobs completados.

### Arquitectura del Frontend

```
Browser (React SPA)
    |
    |-- Carga desde OBS Static Website (cbr-obs-frontend bucket)
    |
    |-- Llamadas API (fetch) con AK/SK en headers
    v
API Gateway (APIG) + CORS
    |
    v
FunctionGraph: cbr-obs-api (REST router)
    |
    |-- GET  /api/backups       -> Lista backups CBR
    |-- GET  /api/jobs          -> Lista jobs de migracion
    |-- POST /api/migrate       -> Inicia migracion
    |-- POST /api/jobs/:id/retry -> Reintenta job
    |-- DELETE /api/jobs/:id    -> Elimina job
    v
Huawei Cloud APIs (CBR, EVS, IMS, OBS)
```

### Stack Tecnologico

| Tecnologia | Version | Proposito |
|------------|---------|-----------|
| React | 18.2 | UI framework |
| Vite | 5.0 | Build tool + dev server |
| TailwindCSS | 3.4 | Styling |
| TypeScript | 5.3 | Type safety |
| React Router | 6.20 | SPA routing |
| Lucide React | 0.300 | Iconos |

### Despliegue del Frontend

**Prerequisitos adicionales:**
- Node.js 18+ y npm instalados

**Pasos:**

1. Configurar la URL del API Gateway en `frontend/.env`:
   ```
   VITE_API_BASE=https://your-apig-endpoint.myhuaweicloud.com
   ```

2. Construir y desplegar:
   ```powershell
   .\scripts\deploy_frontend.ps1
   ```

   Esto:
   - Instala dependencias (`npm install`)
   - Construye la app (`npm run build` -> `dist/`)
   - Crea el bucket OBS `cbr-obs-frontend`
   - Sube los archivos estaticos al bucket
   - Configura static website hosting
   - Imprime la URL del sitio

3. Configurar CORS en APIG:
   - Allow-Origin: `https://cbr-obs-frontend.obs.sa-argentina-1.myhuaweicloud.com`
   - Allow-Methods: `GET, POST, DELETE, OPTIONS`
   - Allow-Headers: `Content-Type, X-HW-AK, X-HW-SK, X-HW-Project-Id-BA, X-HW-Project-Id-CL`

4. Abrir la URL del frontend en el navegador e ingresar credenciales.

### Desarrollo Local

```powershell
cd frontend
npm install
npm run dev
```

El dev server corre en `http://localhost:3000`. Configurar el proxy en `vite.config.ts` para redirigir `/api` al APIG endpoint.

### Paginas del Frontend

| Pagina | Ruta | Descripcion |
|--------|------|-------------|
| Login | `/login` | Form de credenciales AK/SK + Project IDs |
| Dashboard | `/` | Stats + jobs recientes |
| Backups | `/backups` | Tabla de backups CBR + migrar |
| Migraciones | `/migrations` | Tabla de jobs + filtros + reintentar |
| Detalle Job | `/jobs/:id` | Timeline + recursos + error |

---

## Monitoreo y Troubleshooting

### Monitoreo

1. **FunctionGraph Console**: Ver ejecuciones de cada funcion, logs y errores
   - `cbr-obs-status-checker` se ejecuta cada 5 min - revisar sus logs
   - `cbr-obs-cleanup` se ejecuta cada 10 min

2. **OBS Console**: Ver archivos de estado en `cbr-migration-state` bucket
   - Cada job tiene un archivo `state/{job_id}.json`

3. **CBR Console**: Ver backups y replicaciones en progreso

### Troubleshooting

**Error: "Backup not found"**
- Verificar que el `backup_id` es correcto
- Verificar que `source_region` corresponde a la region del backup

**Error: "Project ID not configured"**
- Completar `HW_PROJECT_ID_BUENOSAIRES` y `HW_PROJECT_ID_SANTIAGO` en `.env`

**Error: "Vault ID not configured"**
- Solo para cross-region: crear CBR Vault en region destino y configurar `HW_VAULT_ID_*`

**Error: "Volume creation failed"**
- Verificar cuota de EVS (puede estar agotada)
- Verificar que el AZ configurado (`TEMP_AZ_*`) existe en la region

**Error: "Image export failed"**
- Verificar que el bucket OBS existe y tiene permisos de escritura
- Verificar cuota de IMS

**Job atascado en un paso**
- Revisar el campo `retry_count` en el estado del job
- Si `retry_count` >= `MAX_RETRIES`, el job se marca como failed
- Se puede reiniciar el job eliminando el archivo de estado y reinvocando

### Logs de FunctionGraph

```powershell
hcloud FunctionGraph ListFunctionStatistics --function_name=cbr-obs-status-checker --cli-region=sa-argentina-1
```

---

## Limitaciones

1. **Solo backups de EVS**: Esta herramienta migra backups de discos EVS (`OS::Cinder::Volume`). No soporta backups de ECS completos, SFS Turbo, o Workspaces.

2. **Tiempo de procesamiento**: Cada migracion puede tardar entre 10 minutos y varias horas, dependiendo del tamano del disco. El status checker poll cada 5 minutos.

3. **Recursos temporales**: Cada migracion crea temporalmente:
   - 1 volumen EVS (mismo tamano que el backup)
   - 1 imagen IMS (mismo tamano que el backup)
   Estos se eliminan automaticamente al completar la migracion.

4. **Costos**: La migracion genera costos por:
   - Volumen EVS temporal (por el tiempo que exista)
   - Imagen IMS temporal (por el tiempo que exista)
   - Almacenamiento OBS del archivo resultante
   - Ejecuciones de FunctionGraph
   - Transferencia de datos cross-region (si aplica)

5. **Cuotas**: Verificar que hay cuota suficiente para volumenes EVS e imagenes IMS temporales.

6. **Formato de salida**: El archivo en OBS es una imagen de disco completa en formato VHD, no archivos individuales. Para acceder a archivos individuales, montar la imagen VHD en un servidor.

7. **Cross-region**: Requiere un CBR Vault en la region destino. La replicacion cross-region de CBR tiene un costo adicional.

---

## Testing

### Ejecutar tests

```powershell
pip install pytest
pytest tests/ -v
```

### Tests disponibles

| Archivo | Que testea |
|---------|-----------|
| `test_regions.py` | Resolucion de regiones, endpoints, bucket names |
| `test_cbr_client.py` | Cliente CBR (list, get, restore backups) |
| `test_orchestrator.py` | Modelo de jobs, creacion, transiciones de estado |
| `test_status_checker.py` | Avance de steps, deteccion de completado |

### Test local de funciones

Para probar una funcion localmente sin desplegar:

```python
import json
from src.functions.orchestrator.handler import handler

event = {
    "httpMethod": "POST",
    "isBase64Encoded": False,
    "body": json.dumps({
        "backup_id": "your-backup-id",
        "source_region": "buenosaires"
    })
}

result = handler(event, None)
print(json.dumps(result, indent=2))
```

---

## FAQ

### Puedo migrar backups de ECS (servidores completos)?
No. Esta herramienta solo soporta backups de EVS (discos individuales). Para backups de ECS, se requiere un flujo diferente que restaure el servidor completo primero.

### Cuanto tarda una migracion?
Depende del tamano del disco:
- Disco de 40 GB: ~10-20 minutos
- Disco de 100 GB: ~20-40 minutos
- Disco de 500 GB: ~1-3 horas

### Puedo migrar multiples backups a la vez?
Si. Cada llamada al API del orchestrator inicia un job independiente. El status checker procesa todos los jobs pendientes en cada ejecucion. Sin embargo, ten en cuenta las cuotas de EVS e IMS.

### Que formato tiene el archivo en OBS?
Formato VHD (Virtual Hard Disk). Es una imagen completa del disco. Se puede convertir a QCOW2 cambiando el parametro `image_format` en el codigo.

### Como recupero un disco desde el archivo en OBS?
1. Crear una imagen IMS desde el archivo VHD en OBS (IMS `import` API)
2. Crear un volumen EVS desde la imagen
3. Adjuntar el volumen a un ECS

### Es seguro eliminar el backup de CBR despues de migrar?
Si, pero solo despues de verificar que el archivo en OBS esta completo y es valido. Recomendamos mantener el backup de CBR hasta verificar la integridad del archivo en OBS.

### Que pasa si FunctionGraph falla a mitad de migracion?
El estado del job se guarda en OBS despues de cada paso. Si FunctionGraph falla, el status checker retomara el job desde el ultimo paso guardado en la siguiente ejecucion (cada 5 minutos).

### Puedo usar otras regiones?
Actualmente soporta Buenos Aires y Santiago. Para agregar regiones, editar `src/shared/regions.py` y agregar la configuracion de endpoints correspondiente.
