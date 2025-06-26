from flask import Blueprint, render_template, session, flash, redirect, url_for, request, jsonify, current_app
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.field_path import FieldPath
from decorators.auth_decorators import login_required, admin_required # Import decorators

auth_users_bp = Blueprint('auth_users_bp', __name__)

@auth_users_bp.route('/login', methods=['GET'])
def login_page():
    if 'logged_in' in session:
        return redirect(url_for('dashboard_bp.index'))
    return render_template('login.html')

@auth_users_bp.route('/session-login', methods=['POST'])
def session_login():
    db = current_app.config['DB']
    firebase_auth_admin = current_app.config['FIREBASE_AUTH_ADMIN']

    if not db:
        return jsonify({"success": False, "message": "Erro crítico do servidor (DB não inicializado)."}), 500

    id_token = request.json.get('idToken')
    if not id_token:
        return jsonify({"success": False, "message": "ID Token não fornecido."}), 400

    try:
        decoded_token = firebase_auth_admin.verify_id_token(id_token)
        uid_from_token = decoded_token['uid']
        email = decoded_token.get('email', '')

        mapeamento_ref = db.collection('User').document(uid_from_token.strip())
        mapeamento_doc = mapeamento_ref.get()

        if mapeamento_doc.exists:
            mapeamento_data = mapeamento_doc.to_dict()
            if not mapeamento_data or 'clinica_id' not in mapeamento_data or 'role' not in mapeamento_data:
                return jsonify({"success": False, "message": "Configuração de usuário incompleta. Entre em contato com o administrador."}), 500

            clinica_id = mapeamento_data['clinica_id']
            clinica_doc_ref = db.collection('clinicas').document(clinica_id)
            clinica_doc = clinica_doc_ref.get()
            
            clinica_logo_url = None
            if clinica_doc.exists:
                clinica_data = clinica_doc.to_dict()
                clinica_logo_url = clinica_data.get('url_logo') # Obtém a URL da logo do Firestore

            session['logged_in'] = True
            session['user_uid'] = uid_from_token
            session['user_email'] = email
            session['clinica_id'] = clinica_id
            session['clinica_nome_display'] = mapeamento_data.get('nome_clinica_display', 'Clínica On')
            session['user_role'] = mapeamento_data['role']
            session['user_name'] = mapeamento_data.get('nome_completo', email)
            session['clinica_logo_url'] = clinica_logo_url # Salva a URL da logo na sessão

            print(f"Usuário {email} logado com sucesso. Função: {session['user_role']}")
            return jsonify({"success": True, "message": "Login bem-sucedido!"})
        else:
            return jsonify({"success": False, "message": "Usuário não autorizado ou não associado a uma clínica."}), 403

    except firebase_auth_admin.RevokedIdTokenError:
        return jsonify({"success": False, "message": "ID Token revogado. Faça login novamente."}), 401
    except firebase_auth_admin.UserDisabledError:
        return jsonify({"success": False, "message": "Sua conta de usuário foi desativada. Entre em contato com o administrador."}), 403
    except firebase_auth_admin.InvalidIdTokenError:
        return jsonify({"success": False, "message": "Credenciais inválidas. Verifique seu e-mail e senha."}), 401
    except Exception as e:
        print(f"Erro na verificação de token/mapeamento: {type(e).__name__} - {e}")
        return jsonify({"success": False, "message": f"Erro do servidor durante o login: {str(e)}"}), 500

@auth_users_bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True, "message": "Sessão do servidor limpa."})
    
@auth_users_bp.route('/usuarios')
@login_required
@admin_required
def listar_usuarios():
    db = current_app.config['DB']
    firebase_auth_admin = current_app.config['FIREBASE_AUTH_ADMIN']

    clinica_id = session['clinica_id']
    usuarios_ref = db.collection('User')
    usuarios_lista = []
    try:
        docs = usuarios_ref.where(filter=FieldFilter('clinica_id', '==', clinica_id)).order_by('email').stream()
        for doc in docs:
            user_data = doc.to_dict()
            if user_data:
                user_data['uid'] = doc.id
                
                try:
                    firebase_user = firebase_auth_admin.get_user(doc.id)
                    user_data['disabled'] = firebase_user.disabled
                except firebase_auth_admin.UserNotFoundError:
                    user_data['disabled'] = True

                usuarios_lista.append(user_data)
    except Exception as e:
        flash(f'Erro ao listar usuários: {e}.', 'danger')
        print(f"Erro list_users: {e}")
        
    return render_template('usuarios.html', usuarios=usuarios_lista)

@auth_users_bp.route('/usuarios/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_usuario():
    db = current_app.config['DB']
    firebase_auth_admin = current_app.config['FIREBASE_AUTH_ADMIN']

    clinica_id = session.get('clinica_id')
    if not clinica_id:
        flash('ID da clínica não encontrado na sessão.', 'danger')
        return redirect(url_for('dashboard_bp.index'))

    profissionais_disponiveis = []
    try:
        profissionais_docs = db.collection(f'clinicas/{clinica_id}/profissionais').stream()
        for doc in profissionais_docs:
            prof_data = doc.to_dict()
            profissionais_disponiveis.append({'id': doc.id, 'nome': prof_data.get('nome')})
        profissionais_disponiveis.sort(key=lambda x: x.get('nome', '').lower())
    except Exception as e:
        flash(f'Erro ao carregar a lista de profissionais: {e}', 'danger')

    if request.method == 'POST':
        email = request.form['email'].strip()
        password = request.form['password']
        role = request.form['role']
        nome_completo = request.form.get('nome_completo', '').strip()
        profissional_associado_id = request.form.get('profissional_associado_id')

        if not all([email, password, role]):
            flash('E-mail, senha e função são obrigatórios.', 'danger')
            return render_template('usuario_form.html', page_title="Adicionar Novo Utilizador", roles=['admin', 'medico'], profissionais=profissionais_disponiveis, user=request.form)

        try:
            user = firebase_auth_admin.create_user(
                email=email,
                password=password,
                display_name=nome_completo
            )
            
            batch = db.batch()

            user_data_firestore = {
                'email': email,
                'clinica_id': clinica_id,
                'nome_clinica_display': session.get('clinica_nome_display', 'Clínica On'),
                'role': role,
                'nome_completo': nome_completo,
                'associado_em': db.SERVER_TIMESTAMP
            }

            if role == 'medico' and profissional_associado_id:
                user_data_firestore['profissional_id'] = profissional_associado_id
                
                prof_ref = db.collection(f'clinicas/{clinica_id}/profissionais').document(profissional_associado_id)
                batch.update(prof_ref, {'user_uid': user.uid})

            user_ref = db.collection('User').document(user.uid)
            batch.set(user_ref, user_data_firestore)
            
            batch.commit()
            
            flash(f'Utilizador {email} ({role}) criado com sucesso!', 'success')
            return redirect(url_for('auth_users_bp.listar_usuarios'))
            
        except firebase_auth_admin.EmailAlreadyExistsError:
            flash('O e-mail fornecido já está em uso.', 'danger')
        except Exception as e:
            flash(f'Erro ao adicionar utilizador: {e}', 'danger')

    return render_template('usuario_form.html', page_title="Adicionar Novo Utilizador", action_url=url_for('auth_users_bp.adicionar_usuario'), roles=['admin', 'medico'], profissionais=profissionais_disponiveis)


@auth_users_bp.route('/usuarios/editar/<string:user_uid>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_usuario(user_uid):
    db = current_app.config['DB']
    firebase_auth_admin = current_app.config['FIREBASE_AUTH_ADMIN']

    clinica_id = session.get('clinica_id')
    if not clinica_id:
        flash('ID da clínica não encontrado na sessão.', 'danger')
        return redirect(url_for('dashboard_bp.index'))
        
    user_ref = db.collection('User').document(user_uid)
    
    profissionais_disponiveis = []
    try:
        profissionais_docs = db.collection(f'clinicas/{clinica_id}/profissionais').stream()
        for doc in profissionais_docs:
            prof_data = doc.to_dict()
            profissionais_disponiveis.append({'id': doc.id, 'nome': prof_data.get('nome')})
        profissionais_disponiveis.sort(key=lambda x: x.get('nome', '').lower())
    except Exception as e:
        flash(f'Erro ao carregar a lista de profissionais: {e}', 'danger')

    try:
        user_doc = user_ref.get()
        if not user_doc.exists:
            flash('Utilizador não encontrado.', 'danger')
            return redirect(url_for('auth_users_bp.listar_usuarios'))
        user_data_original = user_doc.to_dict()
        old_profissional_id = user_data_original.get('profissional_id')
    except Exception as e:
        flash(f'Erro ao carregar dados do utilizador: {e}', 'danger')
        return redirect(url_for('auth_users_bp.listar_usuarios'))

    if request.method == 'POST':
        email = request.form['email'].strip()
        role = request.form['role']
        nome_completo = request.form.get('nome_completo', '').strip()
        new_profissional_id = request.form.get('profissional_associado_id')

        try:
            batch = db.batch()

            firebase_auth_admin.update_user(user_uid, email=email, display_name=nome_completo)
            
            user_data_update = {
                'email': email, 'role': role, 'nome_completo': nome_completo,
                'atualizado_em': db.SERVER_TIMESTAMP
            }
            
            if old_profissional_id != new_profissional_id:
                if old_profissional_id:
                    old_prof_ref = db.collection(f'clinicas/{clinica_id}/profissionais').document(old_profissional_id)
                    batch.update(old_prof_ref, {'user_uid': db.DELETE_FIELD})
                
                if role == 'medico' and new_profissional_id:
                    new_prof_ref = db.collection(f'clinicas/{clinica_id}/profissionais').document(new_profissional_id)
                    batch.update(new_prof_ref, {'user_uid': user_uid})
                    user_data_update['profissional_id'] = new_profissional_id
                else:
                    user_data_update['profissional_id'] = db.DELETE_FIELD
            
            batch.update(user_ref, user_data_update)
            batch.commit()
            
            flash(f'Utilizador {email} atualizado com sucesso!', 'success')
            return redirect(url_for('auth_users_bp.listar_usuarios'))
            
        except Exception as e:
            flash(f'Erro ao atualizar utilizador: {e}', 'danger')
            
    user_data_original['uid'] = user_uid
    return render_template(
        'usuario_form.html',   
        user=user_data_original,   
        page_title="Editar Utilizador",   
        action_url=url_for('auth_users_bp.editar_usuario', user_uid=user_uid),   
        roles=['admin', 'medico'],   
        profissionais=profissionais_disponiveis
    )

@auth_users_bp.route('/usuarios/ativar_desativar/<string:user_uid>', methods=['POST'])
@login_required
@admin_required
def ativar_desativar_usuario(user_uid):
    db = current_app.config['DB']
    firebase_auth_admin = current_app.config['FIREBASE_AUTH_ADMIN']

    clinica_id = session['clinica_id']
    try:
        user_map_doc = db.collection('User').document(user_uid).get()
        if user_map_doc.exists:
            user_data = user_map_doc.to_dict()
            current_status_firebase = firebase_auth_admin.get_user(user_uid).disabled
            new_status_firebase = not current_status_firebase

            firebase_auth_admin.update_user(user_uid, disabled=new_status_firebase)
            
            if user_data.get('role') == 'medico' and user_data.get('profissional_id'):
                profissionais_ref = db.collection('clinicas').document(clinica_id).collection('profissionais')
                prof_doc_ref = profissionais_ref.document(user_data['profissional_id'])
                if prof_doc_ref.get().exists:
                    prof_doc_ref.update({
                        'ativo': not new_status_firebase
                    })

            flash(f'Usuário {user_data.get("email")} {"ativado" if not new_status_firebase else "desativado"} com sucesso!', 'success')
        else:
            flash('Usuário não encontrado no mapeamento.', 'danger')
    except firebase_auth_admin.UserNotFoundError:
        flash('Usuário não encontrado na Autenticação do Firebase.', 'danger')
    except Exception as e:
        flash(f'Erro ao alterar o status do usuário: {e}', 'danger')
        print(f"Erro in activate_deactivate_user: {e}")
    return redirect(url_for('auth_users_bp.listar_usuarios'))