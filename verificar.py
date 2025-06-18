from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
import os

# Define tus alcances (scopes)
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

def listar_archivos_de_drive(carpeta_id):
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    try:
        service = build('drive', 'v3', credentials=creds)
        query = f"'{carpeta_id}' in parents and trashed=false"
        resultados = service.files().list(q=query, fields="files(id, name)").execute()
        archivos = resultados.get('files', [])
        
        if not archivos:
            print('No se encontraron archivos.')
            return

        for archivo in archivos:
            print(f"📁 {archivo['name']} (ID: {archivo['id']})")

    except HttpError as error:
        print(f'Ocurrió un error: {error}')

# Ejecutar
if __name__ == '__main__':
    listar_archivos_de_drive('1OAbutWNBP1nfzlYaTkS99a_rrYt55GdF')
