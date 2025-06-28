# -*- coding: utf-8 -*-

from flask import render_template, session, flash, redirect, url_for, request, jsonify
import datetime
import uuid
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore

# Importe as suas funções utilitárias.
from utils import get_db, login_required, admin_required, SAO_PAULO_TZ, convert_doc_to_dict

# =================================================================
# FUNÇÕES DE TRANSAÇÃO (Helpers)
# =================================================================

@firestore.transactional
def _delete_goal_transaction(transaction, pei_ref, goal_id_to_delete):
    snapshot = pei_ref.get(transaction=transaction)
    if not snapshot.exists: raise Exception("PEI não encontrado.")
    goals = snapshot.to_dict().get('goals', [])
    updated_goals = [goal for goal in goals if goal.get('id') != goal_id_to_delete]
    if len(goals) == len(updated_goals): raise Exception("Meta não encontrada para exclusão.")
    transaction.update(pei_ref, {'goals': updated_goals})

@firestore.transactional
def _update_target_status_transaction(transaction, pei_ref, goal_id, target_id, concluido):
    snapshot = pei_ref.get(transaction=transaction)
    if not snapshot.exists: raise Exception("PEI não encontrado.")
    goals = snapshot.to_dict().get('goals', [])
    goal_found = False
    for goal in goals:
        if goal.get('id') == goal_id:
            goal_found = True
            target_found = False
            for target in goal.get('targets', []):
                if target.get('id') == target_id:
                    target['concluido'] = concluido
                    break
            if not target_found: raise Exception("Alvo não encontrado na meta.")
            break
    if not goal_found: raise Exception("Meta não encontrada no PEI.")
    transaction.update(pei_ref, {'goals': goals})

@firestore.transactional
def _finalize_goal_transaction(transaction, pei_ref, goal_id_to_finalize):
    snapshot = pei_ref.get(transaction=transaction)
    if not snapshot.exists: raise Exception("PEI não encontrado.")
    goals = snapshot.to_dict().get('goals', [])
    goal_found = False
    for goal in goals:
        if goal.get('id') == goal_id_to_finalize:
            goal['status'] = 'finalizado'
            # Mark all active targets within this goal as completed
            for target in goal.get('targets', []):
                if not target.get('concluido', False):
                    target['concluido'] = True
            goal_found = True
            break
    if not goal_found: raise Exception("Meta não encontrada para finalizar.")
    transaction.update(pei_ref, {'goals': goals})

@firestore.transactional
def _finalize_pei_transaction(transaction, pei_ref):
    """
    Finaliza um PEI, marcando-o como 'finalizado' e todas as suas metas ativas
    e respectivos alvos como 'finalizado'/'concluido'.
    """
    snapshot = pei_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise Exception("PEI não encontrado.")
    
    pei_data = snapshot.to_dict()
    updated_goals = pei_data.get('goals', [])
    
    # Mark all active goals and their targets as finalized
    for goal in updated_goals:
        if goal.get('status') == 'ativo':
            goal['status'] = 'finalizado'
            for target in goal.get('targets', []):
                if not target.get('concluido', False):
                    target['concluido'] = True

    transaction.update(pei_ref, {
        'status': 'finalizado', 
        'data_finalizacao': datetime.datetime.now(SAO_PAULO_TZ),
        'goals': updated_goals 
    })

@firestore.transactional
def _add_target_to_goal_transaction(transaction, pei_ref, goal_id, new_target_description):
    snapshot = pei_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise Exception("PEI não encontrado.")
    
    pei_data = snapshot.to_dict()
    goals = pei_data.get('goals', [])
    
    goal_found = False
    for goal in goals:
        if goal.get('id') == goal_id:
            goal_found = True
            new_target = {
                'id': str(uuid.uuid4()),
                'descricao': new_target_description,
                'concluido': False
            }
            if 'targets' not in goal:
                goal['targets'] = []
            goal['targets'].append(new_target)
            break
            
    if not goal_found:
        raise Exception("Meta não encontrada no PEI.")
        
    transaction.update(pei_ref, {'goals': goals})

@firestore.transactional
def _add_pei_activity_transaction(transaction, pei_ref, activity_content, user_name):
    snapshot = pei_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise Exception("PEI not found.")

    activities = snapshot.to_dict().get('activities', [])
    new_activity = {
        'id': str(uuid.uuid4()),
        'content': activity_content,
        'timestamp': datetime.datetime.now(SAO_PAULO_TZ), # Use datetime.now() instead of SERVER_TIMESTAMP
        'user_name': user_name
    }
    activities.append(new_activity)
    transaction.update(pei_ref, {'activities': activities})


# =================================================================
# FUNÇÃO DE REGISTO DE ROTAS
# =================================================================

def register_medical_records_routes(app):

    # --- ROTAS DE BUSCA E VISUALIZAÇÃO DE PRONTUÁRIO ---

    @app.route('/prontuarios', endpoint='buscar_prontuario')
    @login_required
    def buscar_prontuario():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        pacientes_para_busca = []
        search_query = request.args.get('search_query', '').strip()
        try:
            pacientes_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes')
            if search_query:
                pacientes_dict = {}
                query_nome = pacientes_ref.where(filter=FieldFilter('nome', '>=', search_query)).where(filter=FieldFilter('nome', '<=', search_query + '\uf8ff'))
                for doc in query_nome.stream():
                    paciente_data = doc.to_dict(); paciente_data['id'] = doc.id
                    pacientes_dict[doc.id] = paciente_data
                query_cpf = pacientes_ref.where(filter=FieldFilter('cpf', '==', search_query))
                for doc in query_cpf.stream():
                    paciente_data = doc.to_dict(); paciente_data['id'] = doc.id
                    pacientes_dict[doc.id] = paciente_data
                pacientes_para_busca = sorted(pacientes_dict.values(), key=lambda x: x.get('nome', ''))
            else:
                docs = pacientes_ref.order_by('nome').stream()
                for doc in docs:
                    paciente_data = doc.to_dict(); paciente_data['id'] = doc.id
                    pacientes_para_busca.append(paciente_data)
        except Exception as e:
            flash(f'Erro ao carregar lista de pacientes: {e}.', 'danger')
        return render_template('prontuario_busca.html', pacientes_para_busca=pacientes_para_busca, search_query=search_query)

    @app.route('/prontuarios/<string:paciente_doc_id>', endpoint='ver_prontuario')
    @login_required
    def ver_prontuario(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        paciente_data, registros_prontuario, peis_ativos, peis_finalizados = None, [], [], []
        current_date_iso = datetime.date.today().isoformat()
        
        # Obter informações do usuário logado
        user_role = session.get('user_role')
        is_admin = user_role == 'admin'
        is_professional = user_role == 'medico'
        logged_in_professional_id = session.get('professional_id')
        
        # DEBUG: Logar informações da sessão
        print(f"\n--- DEBUG: ver_prontuario ---")
        print(f"User Role: {user_role}")
        print(f"Is Admin: {is_admin}")
        print(f"Is Professional: {is_professional}")
        print(f"Logged-in Professional ID (from session): {logged_in_professional_id}")
        print(f"---------------------------\n")

        # Obter lista de profissionais para o dropdown no modal de criação de PEI
        profissionais_lista = []
        try:
            profissionais_docs = db_instance.collection(f'clinicas/{clinica_id}/profissionais').order_by('nome').stream()
            for doc in profissionais_docs:
                prof_data = doc.to_dict()
                if prof_data:
                    profissionais_lista.append({'id': doc.id, 'nome': prof_data.get('nome', 'N/A')})
        except Exception as e:
            flash(f'Erro ao carregar lista de profissionais: {e}', 'warning')
            print(f"Erro ao carregar profissionais para PEI: {e}")


        try:
            paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
            paciente_doc = paciente_ref.get()
            if not paciente_doc.exists:
                flash('Paciente não encontrado.', 'danger')
                return redirect(url_for('buscar_prontuario'))
            paciente_data = convert_doc_to_dict(paciente_doc)

            if paciente_data and 'data_nascimento' in paciente_data and isinstance(paciente_data['data_nascimento'], str):
                try:
                    paciente_data['data_nascimento'] = datetime.datetime.strptime(paciente_data['data_nascimento'], '%Y-%m-%d')
                except (ValueError, TypeError):
                    paciente_data['data_nascimento'] = None
            
            prontuarios_ref = paciente_ref.collection('prontuarios')
            docs_stream = prontuarios_ref.order_by('data_registro', direction=firestore.Query.DESCENDING).stream()
            for doc in docs_stream:
                registros_prontuario.append(convert_doc_to_dict(doc))
            
            peis_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis')
            
            # Construir a query de PEIs baseada na função do usuário
            peis_query = peis_ref.where(filter=FieldFilter('paciente_id', '==', paciente_doc_id))
            
            if is_professional and not is_admin and logged_in_professional_id:
                # Se for profissional e NÃO admin, filtra por profissional_id OU por PEIs sem profissional associado
                peis_query = peis_query.where(
                    filter=firestore.FieldFilter('profissional_id', '==', logged_in_professional_id)
                )
                print(f"DEBUG: Aplicando filtro de PEI para profissional: profissional_id == {logged_in_professional_id}")
            elif is_professional and not is_admin and logged_in_professional_id is None:
                # Se for profissional e NÃO admin, mas o professional_id não está na sessão,
                # pode ser um PEI sem profissional associado (o caso 'is none')
                # O Firestore não tem um filtro direto para 'is none' em um OR,
                # então buscaremos apenas os que têm o ID e o frontend filtrará os nulos
                # Ou, se for necessário, teríamos que buscar todos e filtrar em Python, o que pode ser ineficiente para muitos PEIs.
                # Por ora, mantemos o filtro apenas para o ID, se o ID estiver faltando na sessão, o PEI não aparecerá.
                # A menos que o PEI não tenha profissional_id definido e o professional_id logado seja None,
                # que é o caso da condição original: `pei.profissional_id_associado === null` no JS.
                # Para atender a isso no backend, precisaríamos de uma segunda query ou buscar todos e filtrar.
                # Dado que a instrução foi "somente o pei do medico associado caso ele não seja administrador",
                # a primeira condição (profissional_id == logged_in_professional_id) é a mais estrita.
                # Se a intenção é que um profissional sem 'professional_id' na sessão veja PEIs sem associação,
                # o filtro deve ser menos restritivo no backend.
                # Vamos manter o filtro estrito e logar o caso para depuração.
                print("DEBUG: Profissional sem professional_id na sessão. PEIs podem não aparecer se não tiverem profissional_id.")

            peis_query = peis_query.order_by('data_criacao', direction=firestore.Query.DESCENDING)

            for pei_doc in peis_query.stream():
                pei = convert_doc_to_dict(pei_doc)
                # Ensure data_criacao is formatted for consistent JS sorting
                if 'data_criacao' in pei and isinstance(pei['data_criacao'], datetime.datetime):
                    pei['data_criacao'] = pei['data_criacao'].strftime('%d/%m/%Y %H:%M')
                else:
                    pei['data_criacao'] = pei.get('data_criacao', 'N/A')

                # Adicionar o nome do profissional que criou o PEI ou está associado
                pei['profissional_nome_associado'] = pei.get('profissional_nome_associado', 'N/A')
                pei['profissional_id_associado'] = pei.get('profissional_id', None) # Manter o ID do profissional

                # DEBUG: Logar o PEI sendo processado e seu profissional_id
                print(f"DEBUG: PEI ID: {pei['id']}, PEI Título: {pei['titulo']}, PEI Profissional ID: {pei.get('profissional_id')}")

                # Processar atividades para formatação
                if 'activities' in pei and isinstance(pei['activities'], list):
                    for activity in pei['activities']:
                        if 'timestamp' in activity and isinstance(activity['timestamp'], datetime.datetime):
                            activity['timestamp_fmt'] = activity['timestamp'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        else:
                            activity['timestamp_fmt'] = 'N/A'
                
                if pei.get('status') == 'finalizado':
                    peis_finalizados.append(pei)
                else:
                    peis_ativos.append(pei)
        except Exception as e:
            flash(f'Erro ao carregar prontuário do paciente: {e}.', 'danger')
            print(f"Erro ao carregar prontuário: {e}")
        
        all_peis = peis_ativos + peis_finalizados
        print(f"DEBUG: Total de PEIs encontrados no backend (antes de enviar ao frontend): {len(all_peis)}")


        return render_template('prontuario.html', 
                               paciente=paciente_data, 
                               registros=registros_prontuario, 
                               peis_ativos=peis_ativos, 
                               peis_finalizados=peis_finalizados, 
                               peis=all_peis,
                               current_date_iso=current_date_iso,
                               is_admin=is_admin, # Passa a flag de admin para o template
                               is_professional=is_professional, # Passa a flag de profissional para o template
                               logged_in_professional_id=logged_in_professional_id, # Passa o ID do profissional logado
                               all_professionals=profissionais_lista # Passa a lista de profissionais
                               )

    # =================================================================
    # ROTAS DE ANAMNESE E REGISTOS GENÉRICOS
    # =================================================================

    @app.route('/prontuarios/<string:paciente_doc_id>/registrar_registro_generico', methods=['POST'], endpoint='registrar_registro_generico')
    @login_required
    def registrar_registro_generico(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            tipo_registro = request.form.get('tipo_registro')
            titulo = request.form.get('titulo', '').strip()
            conteudo = request.form.get('conteudo', '').strip()
            if not all([tipo_registro, titulo, conteudo]):
                flash(f'Por favor, preencha o título e o conteúdo para o registo.', 'danger')
            else:
                db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').add({
                    'data_registro': datetime.datetime.now(SAO_PAULO_TZ), 'tipo_registro': tipo_registro,
                    'titulo': titulo, 'conteudo': conteudo,
                    'profissional_nome': session.get('user_name', 'N/A') # Adiciona o profissional
                })
                flash(f'Registo de {tipo_registro} adicionado com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao adicionar registo: {e}', 'danger')
            print(f"Erro ao adicionar registro genérico: {e}")
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/prontuarios/<string:paciente_doc_id>/editar_registro_generico/<string:registro_doc_id>', methods=['POST'], endpoint='editar_registro_generico')
    @login_required
    def editar_registro_generico(paciente_doc_id, registro_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            titulo = request.form.get('titulo', '').strip()
            conteudo = request.form.get('conteudo', '').strip()
            if not all([titulo, conteudo]):
                flash(f'Por favor, preencha o título e o conteúdo para o registo.', 'danger')
            else:
                registro_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').document(registro_doc_id)
                registro_ref.update({
                    'titulo': titulo, 'conteudo': conteudo,
                    'atualizado_em': datetime.datetime.now(SAO_PAULO_TZ)
                })
                flash(f'Registo atualizado com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao atualizar registo: {e}', 'danger')
            print(f"Erro ao editar registro genérico: {e}")
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/prontuarios/<string:paciente_doc_id>/apagar_registro_generico', methods=['POST'], endpoint='apagar_registro_generico')
    @login_required
    def apagar_registro_generico(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            registro_id = request.form.get('registro_id')
            if not registro_id:
                flash('ID do registo não fornecido.', 'danger')
            else:
                db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').document(registro_id).delete()
                flash('Registo apagado com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao apagar registo: {e}', 'danger')
            print(f"Erro ao apagar registro genérico: {e}")
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/prontuarios/<string:paciente_doc_id>/anamnese/novo', methods=['GET', 'POST'], endpoint='adicionar_anamnese')
    @login_required
    def adicionar_anamnese(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
        paciente_doc = paciente_ref.get()
        if not paciente_doc.exists:
            flash('Paciente não encontrado.', 'danger')
            return redirect(url_for('buscar_prontuario'))
        
        paciente_nome = paciente_doc.to_dict().get('nome', 'Paciente Desconhecido')
        modelos_anamnese = []
        try:
            modelos_docs = db_instance.collection('clinicas').document(clinica_id).collection('modelos_anamnese').order_by('identificacao').stream()
            for doc in modelos_docs:
                modelos_anamnese.append(convert_doc_to_dict(doc))
        except Exception as e:
            flash('Erro ao carregar modelos de anamnese.', 'warning')
            print(f"Erro ao carregar modelos de anamnese (adicionar): {e}")

        if request.method == 'POST':
            try:
                conteudo = request.form.get('conteudo', '').strip()
                db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').add({
                    'data_registro': datetime.datetime.now(SAO_PAULO_TZ), 'tipo_registro': 'anamnese',
                    'titulo': 'Anamnese', 'conteudo': conteudo,
                    'profissional_nome': session.get('user_name', 'N/A') # Adiciona o profissional
                })
                flash('Anamnese adicionada com sucesso!', 'success')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
            except Exception as e:
                flash(f'Erro ao adicionar anamnese: {e}', 'danger')
                print(f"Erro ao adicionar anamnese: {e}")
        
        return render_template('anamnese_form.html', paciente_id=paciente_doc_id, paciente_nome=paciente_nome, modelos_anamnese=modelos_anamnese, action_url=url_for('adicionar_anamnese', paciente_doc_id=paciente_doc_id), page_title=f"Registar Anamnese para {paciente_nome}")

    # ROTA RESTAURADA
    @app.route('/prontuarios/<string:paciente_doc_id>/anamnese/editar/<string:anamnese_doc_id>', methods=['GET', 'POST'], endpoint='editar_anamnese')
    @login_required
    def editar_anamnese(paciente_doc_id, anamnese_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        anamnese_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').document(anamnese_doc_id)
        
        if request.method == 'POST':
            try:
                conteudo = request.form.get('conteudo', '').strip()
                anamnese_ref.update({
                    'conteudo': conteudo, 
                    'atualizado_em': datetime.datetime.now(SAO_PAULO_TZ)
                })
                flash('Anamnese atualizada com sucesso!', 'success')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
            except Exception as e:
                flash(f'Erro ao atualizar anamnese: {e}', 'danger')
                print(f"Erro ao editar anamnese: {e}")
        
        try:
            anamnese_doc = anamnese_ref.get()
            if anamnese_doc.exists:
                paciente_doc = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).get()
                paciente_nome = paciente_doc.to_dict().get('nome') if paciente_doc.exists else ''
                anamnese_data = anamnese_doc.to_dict()
                anamnese_data['profissional_nome'] = anamnese_data.get('profissional_nome', 'N/A') # Garante que o nome do profissional exista para exibição
                return render_template('anamnese_form.html', anamnese=anamnese_data, paciente_id=paciente_doc_id, paciente_nome=paciente_nome, action_url=url_for('editar_anamnese', paciente_doc_id=paciente_doc_id, anamnese_doc_id=anamnese_doc_id), page_title=f"Editar Anamnese para {paciente_nome}")
            else:
                flash('Anamnese não encontrada.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
        except Exception as e:
            flash(f'Erro ao carregar anamnese: {e}', 'danger')
            print(f"Erro ao carregar anamnese: {e}")
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/modelos_anamnese', endpoint='listar_modelos_anamnese')
    @login_required
    @admin_required
    def listar_modelos_anamnese():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        modelos_lista = []
        try:
            docs = db_instance.collection('clinicas').document(clinica_id).collection('modelos_anamnese').order_by('identificacao').stream()
            for doc in docs:
                modelos_lista.append(convert_doc_to_dict(doc))
        except Exception as e:
            flash(f'Erro ao listar modelos de anamnese: {e}.', 'danger')
            print(f"Erro ao listar modelos de anamnese: {e}")
        return render_template('modelos_anamnese.html', modelos=modelos_lista)

    @app.route('/modelos_anamnese/novo', methods=['GET', 'POST'], endpoint='adicionar_modelo_anamnese')
    @login_required
    @admin_required
    def adicionar_modelo_anamnese():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        if request.method == 'POST':
            try:
                identificacao = request.form['identificacao'].strip()
                conteudo_modelo = request.form['conteudo_modelo']
                if not identificacao:
                    flash('A identificação do modelo é obrigatória.', 'danger')
                else:
                    db_instance.collection('clinicas').document(clinica_id).collection('modelos_anamnese').add({
                        'identificacao': identificacao, 'conteudo_modelo': conteudo_modelo,
                        'criado_em': datetime.datetime.now(SAO_PAULO_TZ)
                    })
                    flash('Modelo de anamnese adicionado com sucesso!', 'success')
                    return redirect(url_for('listar_modelos_anamnese'))
            except Exception as e:
                flash(f'Erro ao adicionar modelo de anamnese: {e}', 'danger')
                print(f"Erro ao adicionar modelo de anamnese: {e}")
        return render_template('modelo_anamnese_form.html', modelo=None, action_url=url_for('adicionar_modelo_anamnese'))

    # =================================================================
    # ROTAS DO PEI (Plano Educacional Individualizado)
    # =================================================================

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/add_pei', methods=['POST'], endpoint='add_pei')
    @login_required
    @admin_required # Apenas admins podem criar PEIs
    def add_pei(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            data = request.form
            titulo = data.get('titulo')
            data_criacao_str = data.get('data_criacao')
            profissional_id_associado = data.get('profissional_associado_id') # Novo campo
            
            if not titulo or not data_criacao_str or not profissional_id_associado:
                flash('Título, data de criação e profissional associado do PEI são obrigatórios.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
            
            # Garante que data_criacao_obj seja um objeto datetime.date ou datetime.datetime
            try:
                data_criacao_obj = datetime.datetime.strptime(data_criacao_str, '%Y-%m-%d')
            except ValueError:
                flash('Formato de data de criação inválido.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
            
            # Obter o nome do profissional associado
            profissional_ref = db_instance.collection(f'clinicas/{clinica_id}/profissionais').document(profissional_id_associado)
            profissional_doc = profissional_ref.get()
            profissional_nome_associado = profissional_doc.to_dict().get('nome', 'N/A') if profissional_doc.exists else 'N/A'

            peis_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis')
            
            new_pei_data = {
                'paciente_id': paciente_doc_id, 
                'titulo': titulo,
                'data_criacao': data_criacao_obj, 
                'status': 'ativo',
                'goals': [], 
                'activities': [], # Initialize activities list
                'criado_em': datetime.datetime.now(SAO_PAULO_TZ), 
                'profissional_criador_nome': session.get('user_name', 'N/A'), # Quem criou (nome)
                'profissional_id': profissional_id_associado, # ID do profissional associado
                'profissional_nome_associado': profissional_nome_associado # Nome do profissional associado
            }
            peis_ref.add(new_pei_data)
            flash('PEI adicionado com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao adicionar PEI: {e}', 'danger')
            print(f"Erro add_pei: {e}") # Log the error for debugging
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
    
    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/delete_pei', methods=['POST'], endpoint='delete_pei')
    @login_required
    @admin_required # Apenas admins podem excluir PEIs
    def delete_pei(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            pei_id = request.form.get('pei_id')
            if not pei_id:
                flash('ID do PEI não fornecido.', 'danger')
            else:
                db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id).delete()
                flash('PEI excluído com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao excluir PEI: {e}', 'danger')
            print(f"Erro delete_pei: {e}") # Log the error for debugging
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/finalize_pei', methods=['POST'], endpoint='finalize_pei')
    @login_required # Admins e profissionais podem finalizar
    def finalize_pei(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        user_role = session.get('user_role')
        logged_in_professional_id = session.get('professional_id')

        try:
            data = request.get_json()
            pei_id = data.get('pei_id')
            if not pei_id:
                return jsonify({'success': False, 'message': 'ID do PEI não fornecido.'}), 400
            
            pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
            pei_doc = pei_ref.get()
            if not pei_doc.exists:
                return jsonify({'success': False, 'message': 'PEI não encontrado.'}), 404

            # Verifica permissões: Admin sempre pode. Profissional só se for o associado ou se o PEI não tiver um profissional associado.
            if user_role == 'medico':
                associated_professional_id = pei_doc.to_dict().get('profissional_id')
                # Permite se for o profissional associado OU se não houver profissional associado (para PEIs legados/sem vínculo)
                if associated_professional_id and associated_professional_id != logged_in_professional_id:
                    return jsonify({'success': False, 'message': 'Você não tem permissão para finalizar este PEI.'}), 403
            # Se for admin, não precisa de checagem adicional.

            # Use the transactional helper function
            _finalize_pei_transaction(db_instance.transaction(), pei_ref)

            # Re-fetch all PEIs to send the updated list back to the frontend
            all_peis = []
            
            # Re-aplicar a lógica de filtragem para a resposta AJAX
            peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
            if user_role == 'medico' and not (user_role == 'admin') and logged_in_professional_id: # Se for profissional e não admin
                peis_query = peis_query.where(filter=FieldFilter('profissional_id', '==', logged_in_professional_id))
            
            # DEBUG: Log do filtro aplicado no re-fetch
            print(f"DEBUG: Re-fetching PEIs para finalize_pei. Query filter applied: {peis_query._query.filters}")


            for doc in peis_query.stream():
                pei_data_converted = convert_doc_to_dict(doc)
                if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                    pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
                pei_data_converted['profissional_nome_associado'] = pei_data_converted.get('profissional_nome_associado', 'N/A') # Garante que o nome do profissional esteja presente
                pei_data_converted['profissional_id_associado'] = pei_data_converted.get('profissional_id', None) # Manter o ID do profissional

                # Processar atividades para formatação
                if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                    for activity in pei_data_converted['activities']:
                        if 'timestamp' in activity and isinstance(activity['timestamp'], datetime.datetime):
                            activity['timestamp_fmt'] = activity['timestamp'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        else:
                            activity['timestamp_fmt'] = 'N/A'

                all_peis.append(pei_data_converted)
            print(f"DEBUG: Total de PEIs encontrados no re-fetch (finalize_pei): {len(all_peis)}")

            return jsonify({'success': True, 'message': 'PEI finalizado com sucesso!', 'peis': all_peis}), 200
        except Exception as e:
            print(f"Erro ao finalizar PEI: {e}")
            return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/add_goal', methods=['POST'], endpoint='add_goal')
    @login_required
    @admin_required # Apenas admins podem adicionar metas
    def add_goal(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            data = request.form
            pei_id = data.get('pei_id')
            descricao_goal = data.get('descricao')
            # Extract targets from the form, which will be a list of strings
            targets_desc = request.form.getlist('targets[]')
            
            if not pei_id or not descricao_goal:
                flash('Dados insuficientes para adicionar meta.', 'danger')
            else:
                pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
                
                # Create target objects with unique IDs and initial status
                new_targets = []
                for desc in targets_desc:
                    if desc.strip(): # Only add non-empty target descriptions
                        new_targets.append({
                            'id': str(uuid.uuid4()),
                            'descricao': desc.strip(),
                            'concluido': False # Initial status is not completed
                        })

                new_goal = {
                    'id': str(uuid.uuid4()), 
                    'descricao': descricao_goal.strip(),
                    'status': 'ativo',
                    'targets': new_targets # Add the list of target objects
                }
                
                pei_ref.update({'goals': firestore.ArrayUnion([new_goal])})
                flash('Meta adicionada com sucesso ao PEI!', 'success')
        except Exception as e:
            flash(f'Erro ao adicionar meta: {e}', 'danger')
            print(f"Erro add_goal: {e}") # Log the error for debugging
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/add_target_to_goal', methods=['POST'], endpoint='add_target_to_goal')
    @login_required
    @admin_required # Apenas admins podem adicionar alvos
    def add_target_to_goal(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            data = request.get_json()
            pei_id = data.get('pei_id')
            goal_id = data.get('goal_id')
            target_description = data.get('target_description')

            if not all([pei_id, goal_id, target_description]):
                return jsonify({'success': False, 'message': 'Dados insuficientes para adicionar alvo.'}), 400

            pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
            transaction = db_instance.transaction()
            
            _add_target_to_goal_transaction(transaction, pei_ref, goal_id, target_description)
            transaction.commit()
            
            # Re-fetch all PEIs to send the updated list back to the frontend
            all_peis = []
            
            # Re-aplicar a lógica de filtragem para a resposta AJAX
            user_role = session.get('user_role')
            logged_in_professional_id = session.get('professional_id')
            peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
            if user_role == 'medico' and not (user_role == 'admin') and logged_in_professional_id: # Se for profissional e não admin
                peis_query = peis_query.where(filter=FieldFilter('profissional_id', '==', logged_in_professional_id))
            print(f"DEBUG: Re-fetching PEIs para add_target_to_goal. Query filter applied: {peis_query._query.filters}")


            for doc in peis_query.stream():
                pei_data_converted = convert_doc_to_dict(doc)
                if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                    pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
                pei_data_converted['profissional_nome_associado'] = pei_data_converted.get('profissional_nome_associado', 'N/A') # Garante que o nome do profissional esteja presente
                pei_data_converted['profissional_id_associado'] = pei_data_converted.get('profissional_id', None) # Manter o ID do profissional

                # Processar atividades para formatação
                if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                    for activity in pei_data_converted['activities']:
                        if 'timestamp' in activity and isinstance(activity['timestamp'], datetime.datetime):
                            activity['timestamp_fmt'] = activity['timestamp'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        else:
                            activity['timestamp_fmt'] = 'N/A'

                all_peis.append(pei_data_converted)
            print(f"DEBUG: Total de PEIs encontrados no re-fetch (add_target_to_goal): {len(all_peis)}")

            return jsonify({'success': True, 'message': 'Alvo adicionado com sucesso!', 'peis': all_peis}), 200

        except Exception as e:
            print(f"Erro ao adicionar alvo à meta: {e}")
            return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/delete_goal', methods=['POST'], endpoint='delete_goal')
    @login_required
    @admin_required # Apenas admins podem excluir metas
    def delete_goal(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            pei_id = request.form.get('pei_id')
            goal_id = request.form.get('goal_id')
            if not pei_id or not goal_id:
                flash('Dados insuficientes para excluir meta.', 'danger')
            else:
                pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
                transaction = db_instance.transaction()
                _delete_goal_transaction(transaction, pei_ref, goal_id)
                transaction.commit()
                flash('Meta excluída com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao excluir meta: {e}', 'danger')
            print(f"Erro delete_goal: {e}") # Log the error for debugging
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/finalize_goal', methods=['POST'], endpoint='finalize_goal')
    @login_required # Admins e profissionais podem finalizar metas
    def finalize_goal(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        user_role = session.get('user_role')
        logged_in_professional_id = session.get('professional_id')

        try:
            data = request.get_json()
            pei_id = data.get('pei_id')
            goal_id = data.get('goal_id')
            if not all([pei_id, goal_id]):
                return jsonify({'success': False, 'message': 'Dados insuficientes para finalizar meta.'}), 400

            pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
            pei_doc = pei_ref.get()
            if not pei_doc.exists:
                return jsonify({'success': False, 'message': 'PEI não encontrado.'}), 404

            # Verifica permissões: Admin sempre pode. Profissional só se for o associado ou se o PEI não tiver um profissional associado.
            if user_role == 'medico':
                associated_professional_id = pei_doc.to_dict().get('profissional_id')
                # Permite se for o profissional associado OU se não houver profissional associado
                if associated_professional_id and associated_professional_id != logged_in_professional_id:
                    return jsonify({'success': False, 'message': 'Você não tem permissão para finalizar esta meta.'}), 403

            transaction = db_instance.transaction()
            _finalize_goal_transaction(transaction, pei_ref, goal_id)
            transaction.commit()

            # Re-fetch all PEIs to send the updated list back to the frontend
            all_peis = []
            # Re-aplicar a lógica de filtragem para a resposta AJAX
            peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
            if user_role == 'medico' and not (user_role == 'admin') and logged_in_professional_id: # Se for profissional e não admin
                peis_query = peis_query.where(filter=FieldFilter('profissional_id', '==', logged_in_professional_id))
            print(f"DEBUG: Re-fetching PEIs para finalize_goal. Query filter applied: {peis_query._query.filters}")


            for doc in peis_query.stream():
                pei_data_converted = convert_doc_to_dict(doc)
                if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                    pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
                pei_data_converted['profissional_nome_associado'] = pei_data_converted.get('profissional_nome_associado', 'N/A') # Garante que o nome do profissional esteja presente
                pei_data_converted['profissional_id_associado'] = pei_data_converted.get('profissional_id', None) # Manter o ID do profissional


                # Processar atividades para formatação
                if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                    for activity in pei_data_converted['activities']:
                        if 'timestamp' in activity and isinstance(activity['timestamp'], datetime.datetime):
                            activity['timestamp_fmt'] = activity['timestamp'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        else:
                            activity['timestamp_fmt'] = 'N/A'

                all_peis.append(pei_data_converted)
            print(f"DEBUG: Total de PEIs encontrados no re-fetch (finalize_goal): {len(all_peis)}")

            return jsonify({'success': True, 'message': 'Meta finalizada com sucesso!', 'peis': all_peis}), 200
        except Exception as e:
            print(f"Erro ao finalizar meta: {e}")
            return jsonify({'success': False, 'message': f'Erro interno ao finalizar meta: {e}'}), 500

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/update_target_status', methods=['POST'], endpoint='update_target_status')
    @login_required # Admins e profissionais podem atualizar status de alvos
    def update_target_status(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        user_role = session.get('user_role')
        logged_in_professional_id = session.get('professional_id')

        try:
            data = request.get_json()
            pei_id = data.get('pei_id'); goal_id = data.get('goal_id')
            target_id = data.get('target_id'); concluido = data.get('concluido')
            if not all([pei_id, goal_id, target_id, isinstance(concluido, bool)]):
                return jsonify({'success': False, 'message': 'Dados insuficientes.'}), 400

            pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
            pei_doc = pei_ref.get()
            if not pei_doc.exists:
                return jsonify({'success': False, 'message': 'PEI não encontrado.'}), 404

            # Verifica permissões: Admin sempre pode. Profissional só se for o associado ou se o PEI não tiver um profissional associado.
            if user_role == 'medico':
                associated_professional_id = pei_doc.to_dict().get('profissional_id')
                # Permite se for o profissional associado OU se não houver profissional associado
                if associated_professional_id and associated_professional_id != logged_in_professional_id:
                    return jsonify({'success': False, 'message': 'Você não tem permissão para atualizar o status deste alvo.'}), 403

            transaction = db_instance.transaction()
            _update_target_status_transaction(transaction, pei_ref, goal_id, target_id, concluido)
            transaction.commit()
            
            # Re-fetch all PEIs to send the updated list back to the frontend
            all_peis = []
            # Re-aplicar a lógica de filtragem para a resposta AJAX
            peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
            if user_role == 'medico' and not (user_role == 'admin') and logged_in_professional_id: # Se for profissional e não admin
                peis_query = peis_query.where(filter=FieldFilter('profissional_id', '==', logged_in_professional_id))
            print(f"DEBUG: Re-fetching PEIs para update_target_status. Query filter applied: {peis_query._query.filters}")


            for doc in peis_query.stream():
                pei_data_converted = convert_doc_to_dict(doc)
                if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                    pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
                pei_data_converted['profissional_nome_associado'] = pei_data_converted.get('profissional_nome_associado', 'N/A') # Garante que o nome do profissional esteja presente
                pei_data_converted['profissional_id_associado'] = pei_data_converted.get('profissional_id', None) # Manter o ID do profissional

                # Processar atividades para formatação
                if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                    for activity in pei_data_converted['activities']:
                        if 'timestamp' in activity and isinstance(activity['timestamp'], datetime.datetime):
                            activity['timestamp_fmt'] = activity['timestamp'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        else:
                            activity['timestamp_fmt'] = 'N/A'

                all_peis.append(pei_data_converted)
            print(f"DEBUG: Total de PEIs encontrados no re-fetch (update_target_status): {len(all_peis)}")

            return jsonify({'success': True, 'message': 'Status do alvo atualizado.', 'peis': all_peis}), 200
        except Exception as e:
            print(f"Erro ao atualizar status do alvo: {e}")
            return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/add_pei_activity', methods=['POST'], endpoint='add_pei_activity')
    @login_required # Admins e profissionais podem adicionar atividades/comentários
    def add_pei_activity(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        user_role = session.get('user_role')
        logged_in_professional_id = session.get('professional_id')

        try:
            data = request.get_json()
            pei_id = data.get('pei_id')
            activity_content = data.get('content')

            if not all([pei_id, activity_content]):
                return jsonify({'success': False, 'message': 'Dados insuficientes para adicionar atividade.'}), 400
            
            pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
            pei_doc = pei_ref.get()
            if not pei_doc.exists:
                return jsonify({'success': False, 'message': 'PEI não encontrado.'}), 404

            # Verifica permissões: Admin sempre pode. Profissional só se for o associado ou se o PEI não tiver um profissional associado.
            if user_role == 'medico':
                associated_professional_id = pei_doc.to_dict().get('profissional_id')
                # Permite se for o profissional associado OU se não houver profissional associado
                if associated_professional_id and associated_professional_id != logged_in_professional_id:
                    return jsonify({'success': False, 'message': 'Você não tem permissão para adicionar atividades a este PEI.'}), 403


            user_name = session.get('user_name', 'Desconhecido')

            _add_pei_activity_transaction(db_instance.transaction(), pei_ref, activity_content, user_name)

            # Re-fetch all PEIs to send the updated list back to the frontend
            all_peis = []
            # Re-aplicar a lógica de filtragem para a resposta AJAX
            peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
            if user_role == 'medico' and not (user_role == 'admin') and logged_in_professional_id: # Se for profissional e não admin
                peis_query = peis_query.where(filter=FieldFilter('profissional_id', '==', logged_in_professional_id))
            print(f"DEBUG: Re-fetching PEIs para add_pei_activity. Query filter applied: {peis_query._query.filters}")


            for doc in peis_query.stream():
                pei_data_converted = convert_doc_to_dict(doc)
                if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                    pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M') # Format here
                pei_data_converted['profissional_nome_associado'] = pei_data_converted.get('profissional_nome_associado', 'N/A')
                pei_data_converted['profissional_id_associado'] = pei_data_converted.get('profissional_id', None) # Manter o ID do profissional


                if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                    for activity in pei_data_converted['activities']:
                        if 'timestamp' in activity and isinstance(activity['timestamp'], datetime.datetime):
                            activity['timestamp_fmt'] = activity['timestamp'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        else:
                            activity['timestamp_fmt'] = 'N/A' # Fallback for activities with no timestamp or invalid type

                all_peis.append(pei_data_converted)
            print(f"DEBUG: Total de PEIs encontrados no re-fetch (add_pei_activity): {len(all_peis)}")

            return jsonify({'success': True, 'message': 'Atividade adicionada com sucesso!', 'peis': all_peis}), 200

        except Exception as e:
            print(f"Erro ao adicionar atividade ao PEI: {e}")
            return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500
