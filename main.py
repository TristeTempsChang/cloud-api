import os
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContentSettings

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

app = FastAPI(title="API Fichiers Azure")

CONTAINER_NAME = "fichiers-api"


def get_container_client(create_if_missing: bool = False):
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")

    if not connection_string:
        raise HTTPException(
            status_code=500,
            detail="La variable AZURE_STORAGE_CONNECTION_STRING est absente du fichier .env"
        )

    try:
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)

        if create_if_missing:
            try:
                container_client.create_container()
            except ResourceExistsError:
                pass

        return container_client

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur de connexion à Azure Blob Storage : {str(error)}"
        )


def clean_filename(filename: str) -> str:
    if not filename:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide")

    # Évite les chemins du style ../../fichier.txt
    cleaned = Path(filename).name

    if cleaned == "":
        raise HTTPException(status_code=400, detail="Nom de fichier invalide")

    return cleaned


def upload_to_blob(file: UploadFile) -> dict:
    filename = clean_filename(file.filename)

    try:
        container_client = get_container_client(create_if_missing=True)
        blob_client = container_client.get_blob_client(filename)

        file.file.seek(0)

        blob_client.upload_blob(
            file.file,
            overwrite=True,
            content_settings=ContentSettings(content_type=file.content_type)
        )

        return {
            "message": "Fichier envoyé avec succès",
            "filename": filename
        }

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur pendant l'envoi du fichier : {str(error)}"
        )


def delete_blob(filename: str) -> dict:
    filename = clean_filename(filename)

    try:
        container_client = get_container_client()
        blob_client = container_client.get_blob_client(filename)
        blob_client.delete_blob()

        return {
            "message": "Fichier supprimé avec succès",
            "filename": filename
        }

    except ResourceNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Le fichier '{filename}' n'existe pas dans le conteneur"
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur pendant la suppression du fichier : {str(error)}"
        )


@app.get("/", response_class=HTMLResponse)
def upload_page(status: str = "", filename: str = "") -> str:
    message = ""

    if status == "uploaded":
        message = f"<p class='success'>Fichier envoyé : {escape(filename)}</p>"
    elif status == "deleted":
        message = f"<p class='success'>Fichier supprimé : {escape(filename)}</p>"
    elif status == "error":
        message = f"<p class='error'>Erreur : {escape(filename)}</p>"

    try:
        files = list_files()["files"]
        files_html = ""

        if files:
            for blob_name in files:
                safe_name = escape(blob_name)
                safe_value = escape(blob_name, quote=True)

                files_html += f"""
                <li>
                    <span>{safe_name}</span>
                    <form method="post" action="/delete" style="display:inline;">
                        <input type="hidden" name="filename" value="{safe_value}">
                        <button type="submit">Supprimer</button>
                    </form>
                </li>
                """
        else:
            files_html = "<li>Aucun fichier dans le conteneur.</li>"

    except HTTPException as error:
        files_html = f"<li class='error'>{escape(str(error.detail))}</li>"

    return f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <title>API Fichiers Azure</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 40px;
                background-color: #f5f5f5;
            }}
            main {{
                background: white;
                padding: 25px;
                border-radius: 8px;
                max-width: 700px;
                margin: auto;
            }}
            h1, h2 {{
                color: #333;
            }}
            .success {{
                color: green;
                font-weight: bold;
            }}
            .error {{
                color: red;
                font-weight: bold;
            }}
            li {{
                margin-bottom: 10px;
            }}
            button {{
                cursor: pointer;
            }}
        </style>
    </head>
    <body>
        <main>
            <h1>Gestion des fichiers Azure Blob</h1>

            {message}

            <h2>Déposer un fichier</h2>
            <form method="post" action="/" enctype="multipart/form-data">
                <input type="file" name="file" required>
                <button type="submit">Envoyer</button>
            </form>

            <h2>Fichiers présents dans Azure</h2>
            <ul>
                {files_html}
            </ul>
        </main>
    </body>
    </html>
    """


@app.post("/")
def upload_from_root(file: UploadFile = File(...)):
    try:
        result = upload_to_blob(file)
        query = urlencode({
            "status": "uploaded",
            "filename": result["filename"]
        })
        return RedirectResponse(url=f"/?{query}", status_code=303)

    except HTTPException as error:
        query = urlencode({
            "status": "error",
            "filename": str(error.detail)
        })
        return RedirectResponse(url=f"/?{query}", status_code=303)


@app.get("/files")
def list_files() -> dict:
    try:
        container_client = get_container_client()
        blobs = container_client.list_blobs()

        filenames = sorted([blob.name for blob in blobs])

        return {
            "container": CONTAINER_NAME,
            "count": len(filenames),
            "files": filenames
        }

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur pendant la récupération des fichiers : {str(error)}"
        )


@app.post("/delete")
def delete_from_root(filename: str = Form(...)):
    try:
        result = delete_blob(filename)
        query = urlencode({
            "status": "deleted",
            "filename": result["filename"]
        })
        return RedirectResponse(url=f"/?{query}", status_code=303)

    except HTTPException as error:
        query = urlencode({
            "status": "error",
            "filename": str(error.detail)
        })
        return RedirectResponse(url=f"/?{query}", status_code=303)


@app.post("/upload")
def upload_file(file: UploadFile = File(...)) -> dict:
    return upload_to_blob(file)


@app.delete("/remove")
def remove_file(filename: str = Query(..., min_length=1)) -> dict:
    return delete_blob(filename)