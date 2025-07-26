from flask import Blueprint, request, jsonify, session
import datetime
from google.cloud import firestore
import firebase_admin 
from firebase_admin import credentials, storage 
import uuid 

from utils import get_db, login_required 

user_api_bp = Blueprint('user_api', __name__)

@user_api_bp.route('/api/upload_user_photo', methods=['POST'])
@login_required
def upload_user_photo():
    db = get_db()
    user_uid = request.form.get('user_uid')
    
    if not user_uid:
        return jsonify({"success": False, "message": "UID do usuário não fornecido."}), 400

    if 'profile_photo' not in request.files:
        return jsonify({"success": False, "message": "Nenhuma foto enviada."}), 400

    file = request.files['profile_photo']
    if file.filename == '':
        return jsonify({"success": False, "message": "Nenhum arquivo selecionado."}), 400

    if not file.content_type.startswith('image/'):
        return jsonify({"success": False, "message": "O arquivo enviado não é uma imagem."}), 400

    try:
        # Obtenha o bucket padrão (substitua pelo nome do seu bucket se não for o padrão)
        bucket = storage.bucket() # Ex: storage.bucket('seu-projeto.appspot.com')

        # Defina o caminho no Firebase Storage
        # Usando user_uid para garantir um caminho único por usuário, e uuid para nome de arquivo único
        file_extension = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'png'
        blob_name = f"profile_photos/{user_uid}/{uuid.uuid4()}.{file_extension}"
        blob = bucket.blob(blob_name)

        # Faça o upload do arquivo
        blob.upload_from_file(file, content_type=file.content_type)
        blob.make_public() # Torna a imagem publicamente acessível

        photo_url = blob.public_url

        # Atualize a photo_url no documento do usuário no Firestore
        user_ref = db.collection('User').document(user_uid)
        user_ref.update({'photo_url': photo_url})

        # Atualize a sessão Flask
        session['user_photo_url'] = photo_url
        session.modified = True # Importante: Marque a sessão como modificada

        return jsonify({"success": True, "message": "Foto de perfil atualizada com sucesso!", "photo_url": photo_url}), 200

    except Exception as e:
        print(f"Erro ao fazer upload da foto: {e}")
        return jsonify({"success": False, "message": f"Erro ao fazer upload da foto: {e}"}), 500

@user_api_bp.route('/api/remove_user_photo', methods=['POST'])
@login_required
def remove_user_photo():
    db = get_db()
    data = request.get_json()
    user_uid = data.get('user_uid')

    if not user_uid:
        return jsonify({"success": False, "message": "UID do usuário não fornecido."}), 400

    try:
        user_ref = db.collection('User').document(user_uid)
        user_doc = user_ref.get()
        if not user_doc.exists:
            return jsonify({"success": False, "message": "Usuário não encontrado."}), 404
        
        current_photo_url = user_doc.to_dict().get('photo_url')

        if current_photo_url:
            # Tente deletar a foto antiga do Firebase Storage
            try:
                bucket = storage.bucket()
                # Extraia o nome do blob da URL pública
                from urllib.parse import unquote
                path_in_bucket = current_photo_url.split('/o/', 1)[1].split('?alt=media', 1)[0]
                blob_name_to_delete = unquote(path_in_bucket)

                blob = bucket.blob(blob_name_to_delete)
                if blob.exists():
                    blob.delete()
                    print(f"Foto antiga {blob_name_to_delete} removida do Storage.")
                else:
                    print(f"Foto antiga {blob_name_to_delete} não encontrada no Storage (já removida ou URL inválida).")

            except Exception as storage_e:
                print(f"Erro ao tentar remover foto do Storage: {storage_e}")
                # Continue mesmo que a exclusão do Storage falhe, pois a atualização do Firestore é primária

        # Remova o campo photo_url do documento do usuário no Firestore
        user_ref.update({'photo_url': firestore.DELETE_FIELD})

        # Atualize a sessão Flask
        session['user_photo_url'] = ''
        session.modified = True

        return jsonify({"success": True, "message": "Foto de perfil removida com sucesso!"}), 200

    except Exception as e:
        print(f"Erro ao remover foto: {e}")
        return jsonify({"success": False, "message": f"Erro ao remover foto: {e}"}), 500
