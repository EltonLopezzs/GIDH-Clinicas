from flask import render_template, session, flash, redirect, url_for, request, jsonify
from firebase_admin import auth as firebase_auth_admin
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore # Importar no topo

# Importar utils
from utils import get_db, login_required, admin_required

def register_users_routes(app):
    @app.route('/usuarios', endpoint='listar_usuarios')
    @login_required
    @admin_required
    def listar_usuarios():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        usuarios_ref = db_instance.collection('User')
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

    @app.route('/usuarios/novo', methods=['GET', 'POST'], endpoint='adicionar_usuario')
    @login_required
    @admin_required
    def adicionar_usuario():
        db_instance = get_db()
        clinica_id = session.get('clinica_id')
        if not clinica_id:
            flash('ID da clínica não encontrado na sessão.', 'danger')
            return redirect(url_for('index'))

        profissionais_disponiveis = []
        try:
            profissionais_docs = db_instance.collection(f'clinicas/{clinica_id}/profissionais').stream()
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
                
                batch = db_instance.batch()

                user_data_firestore = {
                    'email': email,
                    'clinica_id': clinica_id,
                    'nome_clinica_display': session.get('clinica_nome_display', 'Clínica On'),
                    'role': role,
                    'nome_completo': nome_completo,
                    'associado_em': firestore.SERVER_TIMESTAMP
                }

                if role == 'medico' and profissional_associado_id:
                    prof_ref = db_instance.collection(f'clinicas/{clinica_id}/profissionais').document(profissional_associado_id)
                    batch.update(prof_ref, {'user_uid': user.uid})

                user_ref = db_instance.collection('User').document(user.uid)
                batch.set(user_ref, user_data_firestore)
                
                batch.commit()
                
                flash(f'Utilizador {email} ({role}) criado com sucesso!', 'success')
                return redirect(url_for('listar_usuarios'))
                
            except firebase_auth_admin.EmailAlreadyExistsError:
                flash('O e-mail fornecido já está em uso.', 'danger')
            except Exception as e:
                flash(f'Erro ao adicionar utilizador: {e}', 'danger')

        return render_template('usuario_form.html', page_title="Adicionar Novo Utilizador", action_url=url_for('adicionar_usuario'), roles=['admin', 'medico'], profissionais=profissionais_disponiveis)


    @app.route('/usuarios/editar/<string:user_uid>', methods=['GET', 'POST'], endpoint='editar_usuario')
    @login_required
    @admin_required
    def editar_usuario(user_uid):
        db_instance = get_db()
        clinica_id = session.get('clinica_id')
        if not clinica_id:
            flash('ID da clínica não encontrado na sessão.', 'danger')
            return redirect(url_for('index'))
            
        user_ref = db_instance.collection('User').document(user_uid)
        
        profissionais_disponiveis = []
        try:
            profissionais_docs = db_instance.collection(f'clinicas/{clinica_id}/profissionais').stream()
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
                return redirect(url_for('listar_usuarios'))
            user_data_original = user_doc.to_dict()
            old_profissional_id = user_data_original.get('profissional_id')
        except Exception as e:
            flash(f'Erro ao carregar dados do utilizador: {e}', 'danger')
            return redirect(url_for('listar_usuarios'))

        if request.method == 'POST':
            email = request.form['email'].strip()
            role = request.form['role']
            nome_completo = request.form.get('nome_completo', '').strip()
            new_profissional_id = request.form.get('profissional_associado_id')

            try:
                batch = db_instance.batch()

                firebase_auth_admin.update_user(user_uid, email=email, display_name=nome_completo)
                
                user_data_update = {
                    'email': email, 'role': role, 'nome_completo': nome_completo,
                    'atualizado_em': firestore.SERVER_TIMESTAMP
                }
                
                if old_profissional_id != new_profissional_id:
                    if old_profissional_id:
                        old_prof_ref = db_instance.collection(f'clinicas/{clinica_id}/profissionais').document(old_profissional_id)
                        batch.update(old_prof_ref, {'user_uid': firestore.DELETE_FIELD})
                    
                    if role == 'medico' and new_profissional_id:
                        new_prof_ref = db_instance.collection(f'clinicas/{clinica_id}/profissionais').document(new_profissional_id)
                        batch.update(new_prof_ref, {'user_uid': user_uid})
                        user_data_update['profissional_id'] = new_profissional_id
                    else:
                        user_data_update['profissional_id'] = firestore.DELETE_FIELD
                
                batch.update(user_ref, user_data_update)

                batch.commit()
                
                flash(f'Utilizador {email} atualizado com sucesso!', 'success')
                return redirect(url_for('listar_usuarios'))
                
            except Exception as e:
                flash(f'Erro ao atualizar utilizador: {e}', 'danger')
                
        user_data_original['uid'] = user_uid
        return render_template(
            'usuario_form.html',    
            user=user_data_original,    
            page_title="Editar Utilizador",    
            action_url=url_for('editar_usuario', user_uid=user_uid),    
            roles=['admin', 'medico'],    
            profissionais=profissionais_disponiveis
        )


    @app.route('/usuarios/ativar_desativar/<string:user_uid>', methods=['POST'], endpoint='ativar_desativar_usuario')
    @login_required
    @admin_required
    def ativar_desativar_usuario(user_uid):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            user_map_doc = db_instance.collection('User').document(user_uid).get()
            if user_map_doc.exists:
                user_data = user_map_doc.to_dict()
                current_status_firebase = firebase_auth_admin.get_user(user_uid).disabled
                new_status_firebase = not current_status_firebase

                firebase_auth_admin.update_user(user_uid, disabled=new_status_firebase)
                
                if user_data.get('role') == 'medico':
                    profissionais_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais')
                    prof_query = profissionais_ref.where(filter=FieldFilter('user_uid', '==', user_uid)).limit(1).stream()
                    prof_doc = next(prof_query, None)
                    if prof_doc:
                        profissionais_ref.document(prof_doc.id).update({
                            'ativo': not new_status_firebase,
                            'atualizado_em': firestore.SERVER_TIMESTAMP
                        })

                flash(f'Usuário {user_data.get("email")} {"ativado" if not new_status_firebase else "desativado"} com sucesso!', 'success')
            else:
                flash('Usuário não encontrado no mapeamento.', 'danger')
        except firebase_admin.auth.UserNotFoundError:
            flash('Usuário não encontrado na Autenticação do Firebase.', 'danger')
        except Exception as e:
            flash(f'Erro ao alterar o status do usuário: {e}', 'danger')
            print(f"Erro activate_deactivate_user: {e}")
        return redirect(url_for('listar_usuarios'))