"""
descargar_adjuntos.py
=====================
Descarga automáticamente todos los adjuntos que no sean imágenes
y los sube a Google Drive, separados en las carpetas `payroll` y `others`.

REQUISITOS:
    pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client python-dotenv

CONFIGURACIÓN (una sola vez):
    1. Ve a https://console.cloud.google.com/
    2. Crea un proyecto (o usa uno existente)
    3. Activa la Gmail API
    4. Crea credenciales OAuth 2.0 tipo "Desktop App"
    5. Descarga el archivo JSON y guárdalo como "credentials.json"
       en la misma carpeta que este script
    6. Ejecuta el script una vez: generará el archivo .env con el token
       y a partir de ahí ya no necesitas credentials.json ni token.json

USO:
    python descargar_adjuntos.py
"""

import os
import io
import base64
import mimetypes
from pathlib import Path
from dotenv import load_dotenv, set_key

# ────────────────────────────────────────────────────────
# Configuración
# ────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
ENV_FILE  = BASE_DIR / ".env"
load_dotenv(ENV_FILE)

DRIVE_ROOT_FOLDER_ID   = os.environ.get("DRIVE_ROOT_FOLDER_ID", "").strip() or None
DRIVE_ROOT_FOLDER_NAME = os.environ.get("DRIVE_ROOT_FOLDER_NAME", "1st_quarter_2026").strip() or None
DRIVE_PAYROLL_FOLDER_NAME = os.environ.get("DRIVE_PAYROLL_FOLDER_NAME", "payroll").strip() or "payroll"
DRIVE_OTHERS_FOLDER_NAME  = os.environ.get("DRIVE_OTHERS_FOLDER_NAME", "others").strip() or "others"
PAYROLL_SENDER = os.environ.get("PAYROLL_SENDER", "").strip()
GMAIL_DATE_AFTER = os.environ.get("GMAIL_DATE_AFTER", "2026/01/01").strip()
GMAIL_DATE_BEFORE = os.environ.get("GMAIL_DATE_BEFORE", "").strip()
GMAIL_OTHERS_IMPORTANT_ONLY = os.environ.get("GMAIL_OTHERS_IMPORTANT_ONLY", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}

def _build_date_filters() -> list[str]:
    filters = []
    if GMAIL_DATE_AFTER:
        filters.append(f"after:{GMAIL_DATE_AFTER}")
    if GMAIL_DATE_BEFORE:
        filters.append(f"before:{GMAIL_DATE_BEFORE}")
    return filters


def build_queries() -> tuple[str, str]:
    date_filters = _build_date_filters()

    payroll_query_parts = ["has:attachment", *date_filters, f"from:({PAYROLL_SENDER})"]
    others_query_parts = ["has:attachment", *date_filters, f"-from:({PAYROLL_SENDER})"]

    if GMAIL_OTHERS_IMPORTANT_ONLY:
        others_query_parts.append("is:important")

    return " ".join(payroll_query_parts), " ".join(others_query_parts)

if not PAYROLL_SENDER:
    raise EnvironmentError(
        "Falta la variable PAYROLL_SENDER en .env.\n"
        "Ejemplo: PAYROLL_SENDER=nominas@miempresa.com"
    )

QUERYPAYROLL, QUERY = build_queries()

SCOPES     = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive",
]
CREDS_FILE = BASE_DIR / "credentials.json"  # solo para el primer flujo OAuth


# ────────────────────────────────────────────────────────
# Autenticación
# ────────────────────────────────────────────────────────
def get_services():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    client_id     = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")
    token_uri     = os.environ.get("GMAIL_TOKEN_URI", "https://oauth2.googleapis.com/token")

    creds = None
    if client_id and client_secret and refresh_token:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=token_uri,
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            set_key(str(ENV_FILE), "GMAIL_REFRESH_TOKEN", creds.refresh_token)
        else:
            if not CREDS_FILE.exists():
                raise FileNotFoundError(
                    "No se encontraron credenciales.\n"
                    "Configura GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET y GMAIL_REFRESH_TOKEN en .env,\n"
                    "o descarga credentials.json desde Google Cloud Console para el flujo inicial."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
            # Guardar en .env para ejecuciones futuras
            ENV_FILE.touch(exist_ok=True)
            set_key(str(ENV_FILE), "GMAIL_CLIENT_ID",     creds.client_id)
            set_key(str(ENV_FILE), "GMAIL_CLIENT_SECRET", creds.client_secret)
            set_key(str(ENV_FILE), "GMAIL_REFRESH_TOKEN", creds.refresh_token)
            set_key(str(ENV_FILE), "GMAIL_TOKEN_URI",     creds.token_uri)

    gmail_service = build("gmail", "v1", credentials=creds)
    drive_service = build("drive", "v3", credentials=creds)
    return gmail_service, drive_service


# ────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────
def iter_parts(payload):
    """Recorre recursivamente todas las partes de un mensaje."""
    parts = payload.get("parts", [])
    if not parts:
        yield payload
        return
    for part in parts:
        yield from iter_parts(part)


def is_image(part):
    return part.get("mimeType", "").lower().startswith("image/")


def list_threads(service, query):
    """Devuelve todos los thread IDs que coincidan con la búsqueda."""
    threads = []
    page_token = None
    while True:
        resp = service.users().threads().list(
            userId="me", q=query, pageToken=page_token
        ).execute()
        threads.extend(t["id"] for t in resp.get("threads", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return threads


def escape_drive_query_value(value: str) -> str:
    return value.replace("'", "\\'")


def find_folder(service, name: str, parent_id: str | None):
    escaped_name = escape_drive_query_value(name)
    if parent_id:
        parent_clause = f"'{parent_id}' in parents"
    else:
        parent_clause = "'root' in parents"

    query = (
        "mimeType='application/vnd.google-apps.folder' "
        "and trashed=false "
        f"and name='{escaped_name}' "
        f"and {parent_clause}"
    )

    resp = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id,name)",
        pageSize=1,
    ).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def get_or_create_folder(service, name: str, parent_id: str | None, cache: dict):
    cache_key = (parent_id or "root", name)
    if cache_key in cache:
        return cache[cache_key]

    folder_id = find_folder(service, name, parent_id)
    if not folder_id:
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            metadata["parents"] = [parent_id]

        created = service.files().create(
            body=metadata,
            fields="id,name",
        ).execute()
        folder_id = created["id"]

    cache[cache_key] = folder_id
    return folder_id


def resolve_target_folders(service):
    cache = {}

    root_id = DRIVE_ROOT_FOLDER_ID
    if not root_id and DRIVE_ROOT_FOLDER_NAME:
        root_id = get_or_create_folder(service, DRIVE_ROOT_FOLDER_NAME, None, cache)

    payroll_id = get_or_create_folder(service, DRIVE_PAYROLL_FOLDER_NAME, root_id, cache)
    others_id = get_or_create_folder(service, DRIVE_OTHERS_FOLDER_NAME, root_id, cache)

    return {
        "payroll": payroll_id,
        "others": others_id,
        "cache": cache,
    }


def file_exists_in_folder(service, parent_id: str, filename: str) -> bool:
    escaped_name = escape_drive_query_value(filename)
    query = (
        "trashed=false "
        f"and name='{escaped_name}' "
        f"and '{parent_id}' in parents"
    )
    resp = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id)",
        pageSize=1,
    ).execute()
    return bool(resp.get("files", []))


def build_unique_filename(service, parent_id: str, filename: str) -> str:
    if not file_exists_in_folder(service, parent_id, filename):
        return filename

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1

    while True:
        candidate = f"{stem}_{counter}{suffix}"
        if not file_exists_in_folder(service, parent_id, candidate):
            return candidate
        counter += 1


def upload_bytes_to_drive(service, parent_id: str, filename: str, data: bytes):
    from googleapiclient.http import MediaIoBaseUpload

    final_name = build_unique_filename(service, parent_id, filename)
    mime_type = mimetypes.guess_type(final_name)[0] or "application/octet-stream"
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)
    metadata = {
        "name": final_name,
        "parents": [parent_id],
    }
    created = service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name",
    ).execute()

    return created["id"], created["name"], mime_type


# ────────────────────────────────────────────────────────
# Descarga y subida de adjuntos
# ────────────────────────────────────────────────────────
def download_and_upload_attachments(gmail_service, drive_service, thread_id, drive_folder_id: str):
    """Descarga adjuntos no-imagen de un thread y los sube a una carpeta de Drive."""
    thread = gmail_service.users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()

    saved = []
    for message in thread.get("messages", []):
        for part in iter_parts(message.get("payload", {})):
            if is_image(part):
                continue

            filename = part.get("filename")
            attachment_id = part.get("body", {}).get("attachmentId")

            if not filename or not attachment_id:
                continue

            att = gmail_service.users().messages().attachments().get(
                userId="me", messageId=message["id"], id=attachment_id
            ).execute()

            data = base64.urlsafe_b64decode(att["data"])

            file_id, final_name, mime_type = upload_bytes_to_drive(
                drive_service,
                drive_folder_id,
                filename,
                data,
            )
            saved.append((final_name, file_id, mime_type))

    return saved


# ────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────
def run_query(gmail_service, drive_service, label: str, query: str, drive_folder_id: str) -> int:
    """Ejecuta una query, sube adjuntos a Drive y devuelve el total subido."""
    print(f'[{label}] Buscando: "{query}"')
    thread_ids = list_threads(gmail_service, query)
    print(f"  → {len(thread_ids)} threads encontrados")

    total_saved = 0
    for i, thread_id in enumerate(thread_ids, 1):
        try:
            files = download_and_upload_attachments(
                gmail_service,
                drive_service,
                thread_id,
                drive_folder_id,
            )
            if files:
                for filename, file_id, mime_type in files:
                    print(f"  [{i}/{len(thread_ids)}] ✓ {filename} [{mime_type}] (fileId={file_id})")
                total_saved += len(files)
            else:
                print(f"  [{i}/{len(thread_ids)}] - {thread_id}: sin adjuntos descargables")
        except Exception as e:
            print(f"  [{i}/{len(thread_ids)}] x {thread_id}: error - {e}")

    print(f"  Subtotal: {total_saved} archivo(s) subido(s) a Drive (folderId={drive_folder_id})\n")
    return total_saved


def main():
    print("Conectando con Gmail y Drive...")
    gmail_service, drive_service = get_services()
    print("OK: autenticado correctamente.\n")

    target_folders = resolve_target_folders(drive_service)
    payroll_folder_id = target_folders["payroll"]
    others_folder_id = target_folders["others"]

    print("Carpetas objetivo en Drive:")
    print(f"  payroll -> {DRIVE_PAYROLL_FOLDER_NAME} (id={payroll_folder_id})")
    print(f"  others  -> {DRIVE_OTHERS_FOLDER_NAME} (id={others_folder_id})\n")

    total = 0
    total += run_query(gmail_service, drive_service, "PAYROLL", QUERYPAYROLL, payroll_folder_id)
    total += run_query(gmail_service, drive_service, "OTHERS", QUERY, others_folder_id)

    print(f"{'=' * 40}")
    print(f"Total de archivos subidos a Drive: {total}")


if __name__ == "__main__":
    main()
