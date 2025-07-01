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
    """
    Deleta uma meta específica de um PEI.
    Args:
        transaction: Objeto de transação do Firestore.
        pei_ref: Referência do documento PEI.
        goal_id_to_delete: ID da meta a ser deletada.
    Raises:
        Exception: Se o PEI ou a meta não forem encontrados.
    """
    snapshot = pei_ref.get(transaction=transaction)
    if not snapshot.exists: raise Exception("PEI não encontrado.")
    goals = snapshot.to_dict().get('goals', [])
    updated_goals = [goal for goal in goals if goal.get('id') != goal_id_to_delete]
    if len(goals) == len(updated_goals): raise Exception("Meta não encontrada para exclusão.")
    transaction.update(pei_ref, {'goals': updated_goals})

@firestore.transactional
def _update_target_status_transaction(transaction, pei_ref, goal_id, target_id, concluido):
    """
    Atualiza o status 'concluido' de um alvo específico dentro de uma meta do PEI.
    Se o alvo for marcado como concluído, todas as ajudas associadas também são finalizadas.
    Args:
        transaction: Objeto de transação do Firestore.
        pei_ref: Referência do documento PEI.
        goal_id: ID da meta que contém o alvo.
        target_id: ID do alvo a ser atualizado.
        concluido: Booleano indicando se o alvo está concluído.
    Raises:
        Exception: Se o PEI, a meta ou o alvo não forem encontrados.
    """
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
                    # Se o alvo for marcado como concluído, todas as ajudas também devem ser.
                    if concluido and 'aids' in target:
                        for aid in target['aids']:
                            aid['status'] = 'finalizada'
                    target_found = True
                    break
            if not target_found: raise Exception("Alvo não encontrado na meta.")
            break
    if not goal_found: raise Exception("Meta não encontrada no PEI.")
    transaction.update(pei_ref, {'goals': goals})

@firestore.transactional
def _finalize_goal_transaction(transaction, pei_ref, goal_id_to_finalize):
    """
    Finaliza uma meta específica dentro de um PEI, marcando-a como 'finalizado'
    e todos os seus alvos e ajudas como concluídos/finalizados.
    Args:
        transaction: Objeto de transação do Firestore.
        pei_ref: Referência do documento PEI.
        goal_id_to_finalize: ID da meta a ser finalizada.
    Raises:
        Exception: Se o PEI ou a meta não forem encontrados.
    """
    snapshot = pei_ref.get(transaction=transaction)
    if not snapshot.exists: raise Exception("PEI não encontrado.")
    goals = snapshot.to_dict().get('goals', [])
    goal_found = False
    for goal in goals:
        if goal.get('id') == goal_id_to_finalize:
            goal['status'] = 'finalizado'
            # Marca todos os alvos ativos dentro desta meta como concluídos
            for target in goal.get('targets', []):
                if not target.get('concluido', False):
                    target['concluido'] = True
                # Marca todas as ajudas dentro deste alvo como finalizadas
                if 'aids' in target:
                    for aid in target['aids']:
                        aid['status'] = 'finalizada'
            goal_found = True
            break
    if not goal_found: raise Exception("Meta não encontrada para finalizar.")
    transaction.update(pei_ref, {'goals': goals})

@firestore.transactional
def _finalize_pei_transaction(transaction, pei_ref):
    """
    Finaliza um PEI, marcando-o como 'finalizado' e todas as suas metas ativas
    e respectivos alvos como 'finalizado'/'concluido'.
    Args:
        transaction: Objeto de transação do Firestore.
        pei_ref: Referência do documento PEI.
    Raises:
        Exception: Se o PEI não for encontrado.
    """
    snapshot = pei_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise Exception("PEI não encontrado.")
    
    pei_data = snapshot.to_dict()
    updated_goals = pei_data.get('goals', [])
    
    # Marca todas as metas ativas e seus alvos como finalizados
    for goal in updated_goals:
        if goal.get('status') == 'ativo':
            goal['status'] = 'finalizado'
            for target in goal.get('targets', []):
                if not target.get('concluido', False):
                    target['concluido'] = True
                # Marca todas as ajudas dentro deste alvo como finalizadas
                if 'aids' in target:
                    for aid in target['aids']:
                        aid['status'] = 'finalizada'

    transaction.update(pei_ref, {
        'status': 'finalizado', 
        'data_finalizacao': datetime.datetime.now(SAO_PAULO_TZ),
        'goals': updated_goals 
    })

@firestore.transactional
def _add_target_to_goal_transaction(transaction, pei_ref, goal_id, new_target_description):
    """
    Adiciona um novo alvo a uma meta existente dentro de um PEI.
    Args:
        transaction: Objeto de transação do Firestore.
        pei_ref: Referência do documento PEI.
        goal_id: ID da meta à qual o alvo será adicionado.
        new_target_description: Descrição do novo alvo.
    Raises:
        Exception: Se o PEI ou a meta não forem encontrados.
    """
    snapshot = pei_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise Exception("PEI não encontrado.")
    
    pei_data = snapshot.to_dict()
    goals = pei_data.get('goals', [])
    
    # Definindo as ajudas fixas para cada novo alvo
    fixed_aids = [
        {'id': str(uuid.uuid4()), 'description': 'Ajuda Física Total', 'attempts_count': 0, 'status': 'pendente'},
        {'id': str(uuid.uuid4()), 'description': 'Ajuda Física Parcial', 'attempts_count': 0, 'status': 'pendente'},
        {'id': str(uuid.uuid4()), 'description': 'Ajuda Gestual', 'attempts_count': 0, 'status': 'pendente'},
        {'id': str(uuid.uuid4()), 'description': 'Ajuda Ecóica', 'attempts_count': 0, 'status': 'pendente'},
        {'id': str(uuid.uuid4()), 'description': 'Independente', 'attempts_count': 0, 'status': 'pendente'},
    ]

    goal_found = False
    for goal in goals:
        if goal.get('id') == goal_id:
            goal_found = True
            new_target = {
                'id': str(uuid.uuid4()),
                'descricao': new_target_description,
                'concluido': False,
                'status': 'pendente', # Novo campo de status para o alvo
                'aids': fixed_aids # Adiciona as ajudas fixas
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
    """
    Adiciona uma nova atividade ao histórico de atividades de um PEI.
    Args:
        transaction: Objeto de transação do Firestore.
        pei_ref: Referência do documento PEI.
        activity_content: Conteúdo da atividade.
        user_name: Nome do usuário que registrou a atividade.
    Raises:
        Exception: Se o PEI não for encontrado.
    """
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

@firestore.transactional
def _update_target_and_aid_data_transaction(transaction, pei_ref, goal_id, target_id, aid_id=None, new_attempts_count=None, new_help_content=None, new_target_status=None):
    """
    Atualiza os dados de um alvo específico ou de uma ajuda dentro de um alvo no PEI.
    Pode atualizar a contagem de tentativas de uma ajuda ou o status geral de um alvo.
    Args:
        transaction: Objeto de transação do Firestore.
        pei_ref: Referência do documento PEI.
        goal_id: ID da meta que contém o alvo.
        target_id: ID do alvo a ser atualizado.
        aid_id: Opcional. ID da ajuda específica a ser atualizada.
        new_attempts_count: Opcional. Nova contagem de tentativas para a ajuda.
        new_help_content: Opcional. Conteúdo de ajuda (não usado na estrutura atual para aids fixas).
        new_target_status: Opcional. Novo status geral do alvo.
    Raises:
        Exception: Se o PEI, a meta, o alvo ou a ajuda não forem encontrados, ou se houver erro de tipo.
    """
    snapshot = pei_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise Exception("PEI não encontrado.")

    pei_data = snapshot.to_dict()
    goals = pei_data.get('goals', [])
    
    goal_found = False
    for goal in goals:
        if goal.get('id') == goal_id:
            goal_found = True
            target_found = False
            for target in goal.get('targets', []):
                if target.get('id') == target_id:
                    target_found = True
                    
                    # Atualiza o status geral do alvo, se fornecido
                    if new_target_status is not None:
                        target['status'] = new_target_status
                        # Se o alvo for marcado como finalizado, todas as ajudas devem ser finalizadas
                        if new_target_status == 'finalizada' and 'aids' in target:
                            for aid in target['aids']:
                                aid['status'] = 'finalizada'
                                # Garante que attempts_count exista e seja um número
                                aid['attempts_count'] = int(aid.get('attempts_count', 0))

                    # Atualiza dados de uma ajuda específica, se aid_id for fornecido
                    if aid_id is not None and 'aids' in target:
                        aid_found = False
                        for aid in target['aids']:
                            if aid.get('id') == aid_id:
                                aid_found = True
                                if new_attempts_count is not None:
                                    try:
                                        # CORREÇÃO: Converte new_attempts_count para int
                                        aid['attempts_count'] = int(new_attempts_count)
                                    except (ValueError, TypeError) as e:
                                        raise Exception(f"Valor inválido para tentativas: {new_attempts_count}. Erro: {e}")
                                # new_help_content não é usado aqui, pois 'description' é fixa
                                break
                        if not aid_found:
                            raise Exception("Ajuda (Aid) não encontrada no alvo.")
                    elif new_help_content is not None:
                        # Este bloco pode ser removido se 'new_help_content' não for mais usado para atualização.
                        # Na estrutura atual, a descrição da ajuda é fixa e não editável via este endpoint.
                        pass

                    break
            if not target_found:
                raise Exception("Alvo não encontrado na meta.")
            break
    if not goal_found:
        raise Exception("Meta não encontrada no PEI.")
        
    transaction.update(pei_ref, {'goals': goals})


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
        user_uid = session.get('user_uid') # Obter o UID do usuário da sessão
        is_admin = user_role == 'admin'
        is_professional = user_role == 'medico'
        logged_in_professional_id = None # Inicializa como Nulo

        # Se o usuário for um profissional (e não admin), busca o ID do profissional associado no banco de dados.
        if is_professional and not is_admin and user_uid:
            try:
                user_doc = db_instance.collection('User').document(user_uid).get()
                if user_doc.exists:
                    logged_in_professional_id = user_doc.to_dict().get('profissional_id')
            except Exception as e:
                print(f"Erro ao buscar ID do profissional para o usuário {user_uid}: {e}")
                flash("Ocorreu um erro ao verificar as suas permissões de profissional.", "danger")
        
        # DEBUG: Logar informações da sessão
        print(f"\n--- DEBUG: ver_prontuario (CORRIGIDO) ---")
        print(f"User Role: {user_role}")
        print(f"Is Admin: {is_admin}")
        print(f"Is Professional: {is_professional}")
        print(f"Logged-in User UID: {user_uid}")
        print(f"Logged-in Professional ID (Buscado do DB): {logged_in_professional_id}")
        print(f"-----------------------------------------\n")

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
            
            # Aplica o filtro para usuários que são profissionais mas não administradores.
            if is_professional and not is_admin:
                if logged_in_professional_id:
                    # Filtra para mostrar apenas os PEIs associados a este profissional
                    # Usa array_contains para verificar se o ID do profissional logado está na lista de profissionais do PEI
                    peis_query = peis_query.where(
                        filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id)
                    )
                    print(f"DEBUG: Aplicando filtro de PEI para profissional: profissionais_ids array_contains {logged_in_professional_id}")
                else:
                    # Se o usuário é 'medico' mas não está associado a nenhum perfil profissional,
                    # ele não deve ver nenhum PEI. Criamos uma consulta que não retornará nada.
                    print("DEBUG: Usuário 'medico' sem associação a um profissional. Nenhum PEI será exibido.")
                    peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', 'ID_INVALIDO_PARA_NAO_RETORNAR_NADA'))


            peis_query = peis_query.order_by('data_criacao', direction=firestore.Query.DESCENDING)

            for pei_doc in peis_query.stream():
                pei = convert_doc_to_dict(pei_doc)
                # Ensure data_criacao is formatted for consistent JS sorting
                if 'data_criacao' in pei and isinstance(pei['data_criacao'], datetime.datetime):
                    pei['data_criacao'] = pei['data_criacao'].strftime('%d/%m/%Y %H:%M')
                else:
                    pei['data_criacao'] = pei.get('data_criacao', 'N/A')

                # Adicionar o nome do profissional que criou o PEI ou está associado
                # Agora, `profissionais_nomes_associados` é um array
                pei['profissionais_nomes_associados_fmt'] = ", ".join(pei.get('profissionais_nomes_associados', ['N/A']))
                
                # DEBUG: Logar o PEI sendo processado e seu profissional_id
                print(f"DEBUG: PEI ID: {pei['id']}, PEI Título: {pei['titulo']}, PEI Profissionais IDs: {pei.get('profissionais_ids')}")

                # Processar atividades para formatação
                if 'activities' in pei and isinstance(pei['activities'], list):
                    for activity in pei['activities']:
                        activity_ts = activity.get('timestamp')
                        if isinstance(activity_ts, datetime.datetime):
                            activity['timestamp_fmt'] = activity_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        elif isinstance(activity_ts, str):
                            try:
                                naive_dt = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                                activity['timestamp_fmt'] = naive_dt.strftime('%d/%m/%Y %H:%M')
                            except (ValueError, TypeError):
                                activity['timestamp_fmt'] = 'Data Inválida'
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

    @app.route('/modelos_anamnese/editar/<string:modelo_doc_id>', methods=['GET', 'POST'], endpoint='editar_modelo_anamnese')
    @login_required
    @admin_required
    def editar_modelo_anamnese(modelo_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        modelo_ref = db_instance.collection('clinicas').document(clinica_id).collection('modelos_anamnese').document(modelo_doc_id)

        if request.method == 'POST':
            try:
                identificacao = request.form['identificacao'].strip()
                conteudo_modelo = request.form['conteudo_modelo']
                if not identificacao:
                    flash('A identificação do modelo é obrigatória.', 'danger')
                else:
                    modelo_ref.update({
                        'identificacao': identificacao,
                        'conteudo_modelo': conteudo_modelo,
                        'atualizado_em': datetime.datetime.now(SAO_PAULO_TZ)
                    })
                    flash('Modelo de anamnese atualizado com sucesso!', 'success')
                    return redirect(url_for('listar_modelos_anamnese'))
            except Exception as e:
                flash(f'Erro ao atualizar modelo de anamnese: {e}', 'danger')
                print(f"Erro ao atualizar modelo de anamnese: {e}")

        try:
            modelo_doc = modelo_ref.get()
            if modelo_doc.exists:
                modelo = modelo_doc.to_dict()
                if modelo:
                    modelo['id'] = modelo_doc.id
                    return render_template('modelo_anamnese_form.html', modelo=modelo, action_url=url_for('editar_modelo_anamnese', modelo_doc_id=modelo_doc_id))
            else:
                flash('Modelo de anamnese não encontrado.', 'danger')
                return redirect(url_for('listar_modelos_anamnese'))
        except Exception as e:
            flash(f'Erro ao carregar modelo de anamnese para edição: {e}', 'danger')
            print(f"Erro ao carregar modelo de anamnese para edição: {e}")
            return redirect(url_for('listar_modelos_anamnese'))

    @app.route('/modelos_anamnese/excluir/<string:modelo_doc_id>', methods=['POST'], endpoint='excluir_modelo_anamnese')
    @login_required
    @admin_required
    def excluir_modelo_anamnese(modelo_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            db_instance.collection('clinicas').document(clinica_id).collection('modelos_anamnese').document(modelo_doc_id).delete()
            flash('Modelo de anamnese excluído com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao excluir modelo de anamnese: {e}.', 'danger')
            print(f"Erro ao excluir modelo de anamnese: {e}")
        return redirect(url_for('listar_modelos_anamnese'))

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
            # Agora aceita múltiplos profissionais
            profissionais_ids_selecionados = request.form.getlist('profissionais_ids[]')
            
            if not titulo or not data_criacao_str or not profissionais_ids_selecionados:
                flash('Título, data de criação e pelo menos um profissional associado do PEI são obrigatórios.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
            
            # Garante que data_criacao_obj seja um objeto datetime.date ou datetime.datetime
            try:
                data_criacao_obj = datetime.datetime.strptime(data_criacao_str, '%Y-%m-%d')
            except ValueError:
                flash('Formato de data de criação inválido.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
            
            # Obter os nomes dos profissionais associados
            profissionais_nomes_associados = []
            for prof_id in profissionais_ids_selecionados:
                profissional_ref = db_instance.collection(f'clinicas/{clinica_id}/profissionais').document(prof_id)
                profissional_doc = profissional_ref.get()
                if profissional_doc.exists:
                    profissionais_nomes_associados.append(profissional_doc.to_dict().get('nome', 'N/A'))
                else:
                    profissionais_nomes_associados.append(f"Profissional Desconhecido ({prof_id})")

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
                'profissionais_ids': profissionais_ids_selecionados, # IDs dos profissionais associados (array)
                'profissionais_nomes_associados': profissionais_nomes_associados # Nomes dos profissionais associados (array)
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
        user_uid = session.get('user_uid')
        is_admin = user_role == 'admin'
        logged_in_professional_id = None

        if not is_admin and user_uid:
            try:
                user_doc = db_instance.collection('User').document(user_uid).get()
                if user_doc.exists:
                    logged_in_professional_id = user_doc.to_dict().get('profissional_id')
            except Exception as e:
                return jsonify({'success': False, 'message': f'Erro ao verificar permissões: {e}'}), 500

        try:
            data = request.get_json()
            pei_id = data.get('pei_id')
            if not pei_id:
                return jsonify({'success': False, 'message': 'ID do PEI não fornecido.'}), 400
            
            pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
            pei_doc = pei_ref.get()
            if not pei_doc.exists:
                return jsonify({'success': False, 'message': 'PEI não encontrado.'}), 404

            if not is_admin:
                associated_professionals_ids = pei_doc.to_dict().get('profissionais_ids', [])
                # Verifica se o profissional logado está na lista de profissionais associados ao PEI
                if logged_in_professional_id not in associated_professionals_ids:
                    return jsonify({'success': False, 'message': 'Você não tem permissão para finalizar este PEI.'}), 403
            
            _finalize_pei_transaction(db_instance.transaction(), pei_ref)
            
            all_peis = []
            peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
            if not is_admin and logged_in_professional_id:
                peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

            for doc in peis_query.stream():
                pei_data_converted = convert_doc_to_dict(doc)
                if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                    pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
                pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

                if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                    for activity in pei_data_converted['activities']:
                        activity_ts = activity.get('timestamp')
                        if isinstance(activity_ts, datetime.datetime):
                            activity['timestamp_fmt'] = activity_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        elif isinstance(activity_ts, str):
                            try:
                                naive_dt = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                                activity['timestamp_fmt'] = naive_dt.strftime('%d/%m/%Y %H:%M')
                            except (ValueError, TypeError):
                                activity['timestamp_fmt'] = 'Data Inválida'
                        else:
                            activity['timestamp_fmt'] = 'N/A'
                all_peis.append(pei_data_converted)

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
            targets_desc = request.form.getlist('targets[]')
            
            if not pei_id or not descricao_goal:
                flash('Dados insuficientes para adicionar meta.', 'danger')
            else:
                pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
                new_targets = []
                # Definindo as ajudas fixas para cada novo alvo
                fixed_aids_template = [
                    {'id': str(uuid.uuid4()), 'description': 'Ajuda Física Total', 'attempts_count': 0, 'status': 'pendente'},
                    {'id': str(uuid.uuid4()), 'description': 'Ajuda Física Parcial', 'attempts_count': 0, 'status': 'pendente'},
                    {'id': str(uuid.uuid4()), 'description': 'Ajuda Gestual', 'attempts_count': 0, 'status': 'pendente'},
                    {'id': str(uuid.uuid4()), 'description': 'Ajuda Ecóica', 'attempts_count': 0, 'status': 'pendente'},
                    {'id': str(uuid.uuid4()), 'description': 'Independente', 'attempts_count': 0, 'status': 'pendente'},
                ]
                for desc in targets_desc:
                    if desc.strip():
                        new_targets.append({
                            'id': str(uuid.uuid4()),
                            'descricao': desc.strip(),
                            'concluido': False,
                            'status': 'pendente', # Status inicial do alvo
                            'aids': [aid.copy() for aid in fixed_aids_template] # Adiciona cópias das ajudas fixas
                        })
                new_goal = {
                    'id': str(uuid.uuid4()), 
                    'descricao': descricao_goal.strip(),
                    'status': 'ativo',
                    'targets': new_targets
                }
                pei_ref.update({'goals': firestore.ArrayUnion([new_goal])})
                flash('Meta adicionada com sucesso ao PEI!', 'success')
        except Exception as e:
            flash(f'Erro ao adicionar meta: {e}', 'danger')
            print(f"Erro add_goal: {e}")
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
            
            all_peis = []
            user_role = session.get('user_role')
            logged_in_professional_id = session.get('professional_id')
            peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
            if user_role == 'medico' and not (user_role == 'admin') and logged_in_professional_id:
                peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

            for doc in peis_query.stream():
                pei_data_converted = convert_doc_to_dict(doc)
                if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                    pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
                pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

                if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                    for activity in pei_data_converted['activities']:
                        activity_ts = activity.get('timestamp')
                        if isinstance(activity_ts, datetime.datetime):
                            activity['timestamp_fmt'] = activity_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        elif isinstance(activity_ts, str):
                            try:
                                naive_dt = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                                activity['timestamp_fmt'] = naive_dt.strftime('%d/%m/%Y %H:%M')
                            except (ValueError, TypeError):
                                activity['timestamp_fmt'] = 'Data Inválida'
                        else:
                            activity['timestamp_fmt'] = 'N/A'
                all_peis.append(pei_data_converted)

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
            print(f"Erro delete_goal: {e}")
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/finalize_goal', methods=['POST'], endpoint='finalize_goal')
    @login_required
    def finalize_goal(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        user_role = session.get('user_role')
        user_uid = session.get('user_uid')
        is_admin = user_role == 'admin'
        logged_in_professional_id = None
        
        if not is_admin and user_uid:
            try:
                user_doc = db_instance.collection('User').document(user_uid).get()
                if user_doc.exists:
                    logged_in_professional_id = user_doc.to_dict().get('profissional_id')
            except Exception as e:
                return jsonify({'success': False, 'message': f'Erro ao verificar permissões: {e}'}), 500

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

            if not is_admin:
                associated_professionals_ids = pei_doc.to_dict().get('profissionais_ids', [])
                if logged_in_professional_id not in associated_professionals_ids:
                    return jsonify({'success': False, 'message': 'Você não tem permissão para finalizar esta meta.'}), 403

            transaction = db_instance.transaction()
            _finalize_goal_transaction(transaction, pei_ref, goal_id)
            transaction.commit()

            all_peis = []
            peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
            if not is_admin and logged_in_professional_id:
                peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

            for doc in peis_query.stream():
                pei_data_converted = convert_doc_to_dict(doc)
                if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                    pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
                pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

                if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                    for activity in pei_data_converted['activities']:
                        activity_ts = activity.get('timestamp')
                        if isinstance(activity_ts, datetime.datetime):
                            activity['timestamp_fmt'] = activity_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        elif isinstance(activity_ts, str):
                            try:
                                naive_dt = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                                activity['timestamp_fmt'] = naive_dt.strftime('%d/%m/%Y %H:%M')
                            except (ValueError, TypeError):
                                activity['timestamp_fmt'] = 'Data Inválida'
                        else:
                            activity['timestamp_fmt'] = 'N/A'
                all_peis.append(pei_data_converted)

            return jsonify({'success': True, 'message': 'Meta finalizada com sucesso!', 'peis': all_peis}), 200
        except Exception as e:
            print(f"Erro ao finalizar meta: {e}")
            return jsonify({'success': False, 'message': f'Erro interno ao finalizar meta: {e}'}), 500

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/update_target_status', methods=['POST'], endpoint='update_target_status')
    @login_required
    def update_target_status(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        user_role = session.get('user_role')
        user_uid = session.get('user_uid')
        is_admin = user_role == 'admin'
        logged_in_professional_id = None

        if not is_admin and user_uid:
            try:
                user_doc = db_instance.collection('User').document(user_uid).get()
                if user_doc.exists:
                    logged_in_professional_id = user_doc.to_dict().get('profissional_id')
            except Exception as e:
                return jsonify({'success': False, 'message': f'Erro ao verificar permissões: {e}'}), 500

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

            if not is_admin:
                associated_professionals_ids = pei_doc.to_dict().get('profissionais_ids', [])
                if logged_in_professional_id not in associated_professionals_ids:
                    return jsonify({'success': False, 'message': 'Você não tem permissão para atualizar o status deste alvo.'}), 403

            transaction = db_instance.transaction()
            _update_target_status_transaction(transaction, pei_ref, goal_id, target_id, concluido)
            transaction.commit()
            
            all_peis = []
            peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
            if not is_admin and logged_in_professional_id:
                peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

            for doc in peis_query.stream():
                pei_data_converted = convert_doc_to_dict(doc)
                if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                    pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
                pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

                if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                    for activity in pei_data_converted['activities']:
                        activity_ts = activity.get('timestamp')
                        if isinstance(activity_ts, datetime.datetime):
                            activity['timestamp_fmt'] = activity_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        elif isinstance(activity_ts, str):
                            try:
                                naive_dt = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                                activity['timestamp_fmt'] = naive_dt.strftime('%d/%m/%Y %H:%M')
                            except (ValueError, TypeError):
                                activity['timestamp_fmt'] = 'Data Inválida'
                        else:
                            activity['timestamp_fmt'] = 'N/A'
                all_peis.append(pei_data_converted)

            return jsonify({'success': True, 'message': 'Status do alvo atualizado.', 'peis': all_peis}), 200
        except Exception as e:
            print(f"Erro ao atualizar status do alvo: {e}")
            return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/add_pei_activity', methods=['POST'], endpoint='add_pei_activity')
    @login_required
    def add_pei_activity(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        user_role = session.get('user_role')
        user_uid = session.get('user_uid')
        is_admin = user_role == 'admin'
        logged_in_professional_id = None
        
        # CORREÇÃO: Busca o ID do profissional associado se o usuário for 'medico'
        if not is_admin and user_uid:
            try:
                user_doc = db_instance.collection('User').document(user_uid).get()
                if user_doc.exists:
                    logged_in_professional_id = user_doc.to_dict().get('profissional_id')
            except Exception as e:
                return jsonify({'success': False, 'message': f'Erro ao verificar permissões: {e}'}), 500

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

            # CORREÇÃO: Lógica de permissão corrigida
            if not is_admin:
                associated_professionals_ids = pei_doc.to_dict().get('profissionais_ids', [])
                if logged_in_professional_id not in associated_professionals_ids:
                    return jsonify({'success': False, 'message': 'Você não tem permissão para adicionar atividades a este PEI.'}), 403

            user_name = session.get('user_name', 'Desconhecido')
            _add_pei_activity_transaction(db_instance.transaction(), pei_ref, activity_content, user_name)
            
            all_peis = []
            peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
            if not is_admin and logged_in_professional_id:
                peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))
            
            for doc in peis_query.stream():
                pei_data_converted = convert_doc_to_dict(doc)
                if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                    pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
                pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

                if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                    for activity in pei_data_converted['activities']:
                        activity_ts = activity.get('timestamp')
                        if isinstance(activity_ts, datetime.datetime):
                            activity['timestamp_fmt'] = activity_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        elif isinstance(activity_ts, str):
                            try:
                                naive_dt = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                                activity['timestamp_fmt'] = naive_dt.strftime('%d/%m/%Y %H:%M')
                            except (ValueError, TypeError):
                                activity['timestamp_fmt'] = 'Data Inválida'
                        else:
                            activity['timestamp_fmt'] = 'N/A'
                all_peis.append(pei_data_converted)

            return jsonify({'success': True, 'message': 'Atividade adicionada com sucesso!', 'peis': all_peis}), 200

        except Exception as e:
            print(f"Erro ao adicionar atividade ao PEI: {e}")
            return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/update_target_and_aid_data', methods=['POST'], endpoint='update_target_and_aid_data')
    @login_required
    def update_target_and_aid_data(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        user_role = session.get('user_role')
        user_uid = session.get('user_uid')
        is_admin = user_role == 'admin'
        logged_in_professional_id = None

        if not is_admin and user_uid:
            try:
                user_doc = db_instance.collection('User').document(user_uid).get()
                if user_doc.exists:
                    logged_in_professional_id = user_doc.to_dict().get('profissional_id')
            except Exception as e:
                return jsonify({'success': False, 'message': f'Erro ao verificar permissões: {e}'}), 500

        try:
            data = request.get_json()
            pei_id = data.get('pei_id')
            goal_id = data.get('goal_id')
            target_id = data.get('target_id')
            aid_id = data.get('aid_id') # Novo: ID da ajuda específica
            new_attempts_count = data.get('new_attempts_count')
            new_help_content = data.get('new_help_content') # Conteúdo de ajuda (se for para uma ajuda específica)
            new_target_status = data.get('new_target_status') # Novo: Status geral do alvo

            # DEBUG: Logar os dados recebidos
            print(f"DEBUG (update_target_and_aid_data): Dados recebidos: pei_id={pei_id}, goal_id={goal_id}, target_id={target_id}, aid_id={aid_id}, new_attempts_count={new_attempts_count} (type: {type(new_attempts_count)}), new_help_content={new_help_content}, new_target_status={new_target_status} (type: {type(new_target_status)})")


            if not all([pei_id, goal_id, target_id]):
                return jsonify({'success': False, 'message': 'Dados insuficientes para atualizar alvo.'}), 400

            pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
            pei_doc = pei_ref.get()
            if not pei_doc.exists:
                return jsonify({'success': False, 'message': 'PEI não encontrado.'}), 404

            if not is_admin:
                associated_professionals_ids = pei_doc.to_dict().get('profissionais_ids', [])
                if logged_in_professional_id not in associated_professionals_ids:
                    return jsonify({'success': False, 'message': 'Você não tem permissão para atualizar este alvo.'}), 403

            transaction = db_instance.transaction()
            _update_target_and_aid_data_transaction(transaction, pei_ref, goal_id, target_id, aid_id, new_attempts_count, new_help_content, new_target_status)
            transaction.commit()
            
            all_peis = []
            peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
            if not is_admin and logged_in_professional_id:
                peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

            for doc in peis_query.stream():
                pei_data_converted = convert_doc_to_dict(doc)
                if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                    pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
                pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

                if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                    for activity in pei_data_converted['activities']:
                        activity_ts = activity.get('timestamp')
                        if isinstance(activity_ts, datetime.datetime):
                            activity['timestamp_fmt'] = activity_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        elif isinstance(activity_ts, str):
                            try:
                                naive_dt = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                                activity['timestamp_fmt'] = naive_dt.strftime('%d/%m/%Y %H:%M')
                            except (ValueError, TypeError):
                                activity['timestamp_fmt'] = 'Data Inválida'
                        else:
                            activity['timestamp_fmt'] = 'N/A'
                all_peis.append(pei_data_converted)

            return jsonify({'success': True, 'message': 'Alvo atualizado com sucesso!', 'peis': all_peis}), 200
        except Exception as e:
            print(f"Erro ao atualizar tentativas/ajuda/status do alvo: {e}")
            return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

