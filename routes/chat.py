from flask import Blueprint, render_template, session, flash, redirect, url_for, request, jsonify, current_app
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.field_path import FieldPath
from decorators.auth_decorators import login_required # Import decorators
from utils.firestore_utils import convert_doc_to_dict # Import utility functions

chat_bp = Blueprint('chat_bp', __name__)

@chat_bp.route('/chat')
@login_required
def chat():
    db = current_app.config['DB']
    clinica_id = session['clinica_id']
    user_uid = session['user_uid']
    grupos_usuario = []
    
    try:
        grupos_ref = db.collection(f'clinicas/{clinica_id}/grupos_chat')
        query = grupos_ref.where(filter=FieldFilter('membros_uid', 'array_contains', user_uid)).stream()
        
        for doc in query:
            grupo_data = convert_doc_to_dict(doc)
            if grupo_data:
                grupos_usuario.append(grupo_data)
                
    except Exception as e:
        flash(f'Erro ao carregar seus grupos de chat: {e}', 'danger')
        print(f"Erro em /chat: {e}")

    return render_template('chat.html', grupos=grupos_usuario)

@chat_bp.route('/criar_grupo', methods=['POST'])
@login_required
def criar_grupo():
    db = current_app.config['DB']

    """Lógica para criar um novo grupo de chat usando Firestore (via JSON POST)."""
    clinica_id = session['clinica_id']
    user_uid = session['user_uid']
    
    try:
        data = request.json # Usa request.json para pegar dados JSON
        nome_grupo = data.get('nome_grupo')
        ids_membros_selecionados = data.get('membros') # Lista de UIDs

        if not nome_grupo or not ids_membros_selecionados:
            return jsonify({'success': False, 'error': 'Nome do grupo e pelo menos um membro são obrigatórios.'}), 400

        grupos_ref = db.collection(f'clinicas/{clinica_id}/grupos_chat')
        nome_existente_query = grupos_ref.where(filter=FieldFilter('nome', '==', nome_grupo)).limit(1).stream()
        if len(list(nome_existente_query)) > 0:
            return jsonify({'success': False, 'error': 'Já existe um grupo com este nome.'}), 409

        if user_uid not in ids_membros_selecionados:
            ids_membros_selecionados.append(user_uid)
            
        membros_info = {}
        # Fetch detailed user info for selected members
        # Using a batch read for performance if many members are selected, or individual reads if few
        if ids_membros_selecionados:
            users_docs = db.collection('User').where(FieldPath.document_id(), 'in', ids_membros_selecionados).stream()
            for doc in users_docs:
                user_data = doc.to_dict()
                membros_info[doc.id] = user_data.get('nome_completo', user_data.get('email', doc.id))

        novo_grupo_data = {
            'nome': nome_grupo,
            'criado_por_uid': user_uid,
            'criado_em': db.SERVER_TIMESTAMP,
            'membros_uid': ids_membros_selecionados,
            'membros_info': membros_info,
            'is_direct_message': False # Flag para distinguir grupos de DMs
        }
        add_result = grupos_ref.add(novo_grupo_data)
        
        return jsonify({'success': True, 'message': f'Grupo "{nome_grupo}" criado com sucesso!', 'grupo_id': add_result[1].id}), 201
    except Exception as e:
        print(f'Erro ao criar o grupo: {e}')
        return jsonify({'success': False, 'error': f'Erro ao criar o grupo: {str(e)}'}), 500

@chat_bp.route('/iniciar_conversa_direta', methods=['POST'])
@login_required
def iniciar_conversa_direta():
    db = current_app.config['DB']

    """Lógica para iniciar uma nova conversa direta (DM) no Firestore."""
    clinica_id = session['clinica_id']
    user_uid = session['user_uid']
    user_name = session.get('user_name', session.get('user_email', 'Usuário Atual'))

    try:
        data = request.json
        target_uid = data.get('target_uid')

        if not target_uid:
            return jsonify({'success': False, 'error': 'UID do usuário alvo não fornecido.'}), 400
        
        if target_uid == user_uid:
            return jsonify({'success': False, 'error': 'Não é possível iniciar uma conversa direta consigo mesmo.'}), 400

        # Verifica se já existe uma conversa direta entre os dois usuários
        # A convenção para DMs será ter um nome de grupo padronizado (e.g., UIDA-UIDB)
        # E garantir que os membros são exatamente os dois envolvidos.
        
        # Para evitar duplicatas e padronizar, crie um ID de conversação único e ordenado
        members_sorted = sorted([user_uid, target_uid])
        dm_group_name = f"DM_{members_sorted[0]}_{members_sorted[1]}"

        grupos_ref = db.collection(f'clinicas/{clinica_id}/grupos_chat')
        
        # Tenta encontrar uma DM existente com os dois membros
        # Firestore não tem consulta 'AND' para array_contains em dois campos diferentes diretamente
        # Então, vamos consultar pelo nome padronizado para DMs
        existing_dm_query = grupos_ref.where(filter=FieldFilter('nome', '==', dm_group_name)) \
                                        .where(filter=FieldFilter('is_direct_message', '==', True)) \
                                        .limit(1).stream()
        
        for doc in existing_dm_query:
            existing_dm_data = doc.to_dict()
            if all(m in existing_dm_data.get('membros_uid', []) for m in members_sorted) and \
               len(existing_dm_data.get('membros_uid', [])) == 2:
                # DM existente encontrada, redireciona para ela
                return jsonify({'success': True, 'message': 'Conversa direta existente encontrada.', 'grupo_id': doc.id}), 200

        # Se não encontrou, cria uma nova DM
        target_user_doc = db.collection('User').document(target_uid).get()
        if not target_user_doc.exists:
            return jsonify({'success': False, 'error': 'Usuário alvo não encontrado.'}), 404
        target_user_name = target_user_doc.to_dict().get('nome_completo', target_user_doc.to_dict().get('email', target_uid))

        membros_info = {
            user_uid: user_name,
            target_uid: target_user_name
        }

        nova_dm_data = {
            'nome': dm_group_name, # Nome padronizado para DMs
            'display_name': f"{user_name} e {target_user_name}", # Nome amigável para exibição
            'criado_por_uid': user_uid,
            'criado_em': db.SERVER_TIMESTAMP,
            'membros_uid': members_sorted,
            'membros_info': membros_info,
            'is_direct_message': True
        }
        add_result = grupos_ref.add(nova_dm_data)
        
        return jsonify({'success': True, 'message': 'Conversa direta criada com sucesso!', 'grupo_id': add_result[1].id}), 201
    except Exception as e:
        print(f'Erro ao iniciar conversa direta: {e}')
        return jsonify({'success': False, 'error': f'Erro ao iniciar conversa direta: {str(e)}'}), 500


@chat_bp.route('/grupo/<string:grupo_id>')
@login_required
def ver_grupo(grupo_id):
    db = current_app.config['DB']

    """Página de um grupo de chat específico. Carrega o histórico inicial."""
    clinica_id = session['clinica_id']
    user_uid = session['user_uid']
    user_name = session.get('user_name', session.get('user_email', 'Você')) # Nome do usuário logado
    
    try:
        grupo_ref = db.collection(f'clinicas/{clinica_id}/grupos_chat').document(grupo_id)
        grupo_doc = grupo_ref.get()

        if not grupo_doc.exists:
            flash('Grupo não encontrado.', 'danger')
            return redirect(url_for('chat_bp.chat'))

        grupo_data = convert_doc_to_dict(grupo_doc)
        
        if user_uid not in grupo_data.get('membros_uid', []):
            flash('Você não tem permissão para acessar este grupo.', 'danger')
            return redirect(url_for('chat_bp.chat'))
        
        # Ajusta o nome de exibição para DMs
        if grupo_data.get('is_direct_message', False):
            # Encontrar o nome do outro participante da DM
            other_member_uid = [uid for uid in grupo_data.get('membros_uid', []) if uid != user_uid]
            if other_member_uid:
                other_user_name = grupo_data.get('membros_info', {}).get(other_member_uid[0], 'Outro Usuário')
                grupo_data['display_name'] = f"Conversa com {other_user_name}"
            else:
                grupo_data['display_name'] = "Conversa Direta" # Fallback
        else:
            grupo_data['display_name'] = grupo_data.get('nome', 'Grupo de Chat') # Para grupos normais

        # Carrega o histórico inicial de mensagens para a primeira renderização
        mensagens_iniciais = []
        mensagens_docs = grupo_ref.collection('mensagens').order_by('timestamp', direction=db.Query.ASCENDING).limit(50).stream()
        for msg_doc in mensagens_docs:
            mensagens_iniciais.append(convert_doc_to_dict(msg_doc))

        return render_template('grupo_chat.html', 
                               grupo=grupo_data, 
                               mensagens_iniciais=mensagens_iniciais,
                               user_uid=user_uid, # Passa o UID do usuário para o frontend
                               user_name=user_name # Passa o nome do usuário para o frontend
                               )
    except Exception as e:
        flash(f'Erro ao carregar o grupo: {e}', 'danger')
        print(f"Erro em /grupo/<grupo_id>: {e}")
        return redirect(url_for('chat_bp.chat'))

@chat_bp.route('/api/grupo/<string:grupo_id>/enviar_mensagem', methods=['POST'])
@login_required
def enviar_mensagem(grupo_id):
    db = current_app.config['DB']

    """API para receber uma nova mensagem e salvar no Firestore."""
    clinica_id = session['clinica_id']
    user_uid = session['user_uid']
    user_name = session.get('user_name', 'Nome Desconhecido')
    
    data = request.json
    msg_content = data.get('conteudo', '').strip()

    if not msg_content:
        return jsonify({'success': False, 'message': 'A mensagem não pode estar vazia.'}), 400

    try:
        grupo_ref = db.collection(f'clinicas/{clinica_id}/grupos_chat').document(grupo_id)
        grupo_doc = grupo_ref.get()
        if not grupo_doc.exists or user_uid not in grupo_doc.to_dict().get('membros_uid', []):
            return jsonify({'success': False, 'message': 'Acesso negado a este grupo.'}), 403

        mensagens_ref = grupo_ref.collection('mensagens')
        nova_mensagem_data = {
            'conteudo': msg_content,
            'user_uid': user_uid,
            'user_nome': user_name,
            'timestamp': db.SERVER_TIMESTAMP
        }
        mensagens_ref.add(nova_mensagem_data)
        
        return jsonify({'success': True, 'message': 'Mensagem enviada.'})
        
    except Exception as e:
        print(f"Erro em enviar_mensagem API: {e}")
        return jsonify({'success': False, 'message': 'Erro interno do servidor.'}), 500