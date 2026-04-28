# Descarga de adjuntos Gmail a Google Drive

Este proyecto busca adjuntos en Gmail y los sube directamente a Google Drive, separados en dos carpetas:

- `payroll` para la query de nomina (`QUERYPAYROLL`)
- `others` para la query general (`QUERY`)

El flujo es **solo Drive**: no guarda adjuntos en disco local.

## 1) Requisitos

- macOS con `python3`
- Un proyecto en Google Cloud con Gmail API y Drive API habilitadas
- Credenciales OAuth tipo Desktop App (`credentials.json`) para el primer inicio

## 2) Crear y activar entorno virtual (macOS)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 3) Configurar variables de entorno

Crea tu archivo `.env` a partir de `.env.example` y completa valores reales.

Variables Gmail:

- `GMAIL_CLIENT_ID`
- `GMAIL_CLIENT_SECRET`
- `GMAIL_REFRESH_TOKEN`
- `GMAIL_TOKEN_URI` (normalmente `https://oauth2.googleapis.com/token`)
- `PAYROLL_SENDER` (remitente de nómina)
- `GMAIL_DATE_AFTER` (default: `2026/01/01`)
- `GMAIL_DATE_BEFORE` (opcional; deja vacio para incluir hasta hoy)
- `GMAIL_OTHERS_IMPORTANT_ONLY` (default: `false`)

Variables Drive:

- `DRIVE_ROOT_FOLDER_ID` (opcional, recomendado si ya tienes carpeta raiz)
- `DRIVE_ROOT_FOLDER_NAME` (se usa si no hay `DRIVE_ROOT_FOLDER_ID`)
- `DRIVE_PAYROLL_FOLDER_NAME` (default: `payroll`)
- `DRIVE_OTHERS_FOLDER_NAME` (default: `others`)

Nota:

- Si no tienes todavia `GMAIL_REFRESH_TOKEN`, deja variables Gmail vacias y ejecuta una vez con `credentials.json` para completar `.env` automaticamente.

## 4) Primer OAuth (solo la primera vez)

1. Coloca `credentials.json` en la raiz del proyecto.
2. Ejecuta el script.
3. Autoriza en el navegador.
4. El script guarda/actualiza tokens en `.env`.

## 5) Ejecutar

```bash
python descargar_adjuntos.py
```

El script:

- construye `QUERYPAYROLL` y `QUERY` desde variables en `.env`
- por defecto **no** limita `others` a `is:important`
- crea/resuelve carpetas en Drive de forma idempotente
- sube adjuntos con nombre unico (agrega `_1`, `_2`, etc. si hay colision)
- imprime `fileId` y subtotales por carpeta

## 6) Seguridad y Git

Este repo ignora archivos sensibles y adjuntos reales:

- `.env`
- `credentials.json`
- `token.json`
- contenido dentro de `payroll/` y `others/`

Las carpetas se conservan en Git unicamente con:

- `payroll/.gitkeep`
- `others/.gitkeep`

Verifica antes de commit:

```bash
git status
```

No deben aparecer secretos ni adjuntos reales.
