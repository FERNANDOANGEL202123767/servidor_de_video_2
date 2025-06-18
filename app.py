from flask import Flask, request, Response, stream_with_context
from flask_cors import CORS
from pymongo import MongoClient
from dotenv import load_dotenv
import jwt
from functools import wraps
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os
import mimetypes
import re
import base64
import requests

# Cargar variables de entorno
load_dotenv()
MONGO_URI = os.getenv('MONGO_URI')
DB_NAME = os.getenv('DB_NAME')
COLLECTION_NAME = os.getenv('COLLECTION_NAME')
JWT_SECRET = os.getenv('JWT_SECRET')
DRIVE_FOLDER_ID = os.getenv('DRIVE_FOLDER_ID')

# Conexión a MongoDB
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

app = Flask(__name__)
Compress(app)
CORS(app, resources={r"/*": {"origins": "https://fon-yogm.onrender.com"}})

def get_drive_service():
    if not os.path.exists('token.json'):
        with open('token.json.b64', 'r') as f:
            token_b64 = f.read()
        with open('token.json', 'wb') as f:
            f.write(base64.b64decode(token_b64))
    creds = Credentials.from_authorized_user_file('token.json', ['https://www.googleapis.com/auth/drive.readonly'])
    return build('drive', 'v3', credentials=creds)

# Decorador para verificar JWT
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return {"error": "Token requerido"}, 401
        token = auth_header.split(' ')[1]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            request.user_id = payload.get('id')
        except jwt.ExpiredSignatureError:
            return {"error": "Token expirado"}, 401
        except jwt.InvalidTokenError:
            return {"error": "Token inválido"}, 401
        return f(*args, **kwargs)
    return decorated

# Ruta para sincronizar películas desde Google Drive
@app.route('/sync-movies', methods=['POST'])
@require_auth
def sync_movies():
    try:
        service = get_drive_service()
        query = f"'{DRIVE_FOLDER_ID}' in parents and mimeType contains 'video/'"
        results = service.files().list(q=query, fields="files(id, name, webContentLink, mimeType)").execute()
        files = results.get('files', [])

        for index, file in enumerate(files):
            file_name = file['name']
            file_id = file['id']
            web_content_link = file.get('webContentLink')
            # Limpiar título: quitar extensión, timestamps y enumerar si es necesario
            title = re.sub(r'\.[^.]+$', '', file_name)  # Quitar extensión
            title = re.sub(r'-\d{10,}', '', title)  # Quitar timestamps
            title = title.strip().replace('_', ' ').title()  # Reemplazar guiones y capitalizar
            # Asegurar título único
            if title.lower() == 'videoplayback':
                title = f"Video {index + 1}"
            exists = collection.find_one({'titulo': title})
            if exists:
                title = f"{title} ({index + 1})"
            # Verificar si la película ya existe
            exists = collection.find_one({'drive_file_id': file_id})
            if not exists:
                movie = {
                    'titulo': title,
                    'descripcion': f"Descripción de {title} (autogenerada)",
                    'duracion': 'Desconocida',
                    'generos': ['Género desconocido'],
                    'miniatura': 'https://placehold.co/224x126?text=Sin+Imagen',
                    'url_video': web_content_link or f"https://drive.google.com/uc?export=download&id={file_id}",
                    'drive_file_id': file_id,
                    'anio': '2023'
                }
                collection.insert_one(movie)
                print(f"Película '{title}' insertada en MongoDB")
            else:
                print(f"Película '{title}' ya existe en MongoDB")

        return {"message": "Sincronización completada"}, 200
    except Exception as e:
        print(f"Error en sincronización: {e}")
        return {"error": f"Error en sincronización: {e}"}, 500

@app.route('/video')
@require_auth
def video():
    nombre = request.args.get('nombre')
    if not nombre:
        return "Falta el parámetro 'nombre'", 400
    doc = collection.find_one({'titulo': nombre})
    if not doc:
        return f"No se encontró el video '{nombre}'", 404
    url_video = doc.get('url_video')
    if not url_video:
        return f"No se encontró URL para '{nombre}'", 500

    # Obtener información del archivo
    try:
        head_response = requests.head(url_video, allow_redirects=True)
        head_response.raise_for_status()
        content_length = head_response.headers.get('content-length')
        content_type = head_response.headers.get('content-type', 'video/mp4')
    except requests.RequestException:
        content_type = 'video/mp4'
        content_length = None

    # Manejar solicitudes Range
    range_header = request.headers.get('Range', None)
    if not range_header:
        def generate():
            try:
                with requests.get(url_video, stream=True) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=4096):
                        yield chunk
            except requests.RequestException as e:
                print(f"Error al transmitir video: {e}")
                yield b""

        headers = {
            'Content-Type': content_type,
            'Accept-Ranges': 'bytes'
        }
        if content_length:
            headers['Content-Length'] = content_length

        return Response(stream_with_context(generate()), headers=headers, status=200)

    # Procesar solicitud Range
    try:
        ranges = re.match(r'bytes=(\d+)-(\d*)', range_header)
        start = int(ranges.group(1))
        end = int(ranges.group(2)) if ranges.group(2) else None

        if content_length:
            end = min(end or int(content_length) - 1, int(content_length) - 1)
        else:
            end = None

        def generate_range():
            headers = {'Range': f'bytes={start}-{end or ""}'}
            try:
                with requests.get(url_video, stream=True, headers=headers) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=4096):
                        yield chunk
            except requests.RequestException as e:
                print(f"Error al transmitir video con range: {e}")
                yield b""

        headers = {
            'Content-Type': content_type,
            'Accept-Ranges': 'bytes',
            'Content-Range': f'bytes {start}-{end or "*"}/{content_length or "*"}'
        }
        if content_length and end is not None:
            headers['Content-Length'] = str(end - start + 1)

        return Response(stream_with_context(generate_range()), headers=headers, status=206)
    except (ValueError, AttributeError):
        return Response("Rango inválido", status=416)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9090, debug=True)
