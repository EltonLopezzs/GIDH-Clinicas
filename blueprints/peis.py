# blueprints/peis.py
from flask import Blueprint, render_template, session, flash, redirect, url_for, request, jsonify
import datetime
import uuid
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore

# Importe as suas funções utilitárias.
# Assumimos que 'utils' contém get_db, login_required, admin_required, SAO_PAULO_TZ, convert_doc_to_dict
from utils import get_db, login_required, admin_required, SAO_PAULO_TZ, convert_doc_to_dict

peis_bp = Blueprint('peis', __name__)

# =================================================================
# FUNÇÕES DE TRANSAÇÃO (Helpers para PEI)
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
def _update_target_status_transaction(transaction, pei_ref, goal_id, target_id, new_target_status):
    """
    Atualiza o status de um alvo específico dentro de uma meta do PEI.
    Args:
        transaction: Objeto de transação do Firestore.
        pei_ref: Referência do documento PEI.
        goal_id: ID da meta que contém o alvo.
        target_id: ID do alvo a ser atualizado.
        new_target_status: Novo status do alvo (pendente, andamento, finalizada).
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
                    target['status'] = new_target_status
                    # Se o alvo for marcado como finalizado, todas as ajudas associadas também são finalizadas.
                    if new_target_status == 'finalizada' and 'aids' in target:
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
                if target.get('status') != 'finalizada': # Só atualiza se não estiver finalizado
                    target['status'] = 'finalizada'
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
                if target.get('status') != 'finalizada': # Só atualiza se não estiver finalizado
                    target['status'] = 'finalizada'
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
                'concluido': False, # Manter 'concluido' para compatibilidade se ainda for usado em algum lugar
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
        'timestamp': datetime.datetime.now(SAO_PAULO_TZ),
        'user_name': user_name
    }
    activities.append(new_activity)
    transaction.update(pei_ref, {'activities': activities})

@firestore.transactional
def _update_target_and_aid_data_transaction(transaction, pei_ref, goal_id, target_id, aid_id=None, new_attempts_count=None, new_target_status=None):
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
        new_target_status: Opcional. Novo status do alvo (pendente, andamento, finalizada).
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
                        # Se o alvo for marcado como finalizado, todas as ajudas associadas também são finalizadas.
                        if new_target_status == 'finalizada' and 'aids' in target:
                            for aid in target['aids']:
                                aid['status'] = 'finalizada'
                        # Atualiza 'concluido' para compatibilidade
                        target['concluido'] = (new_target_status == 'finalizada')

                    # Atualiza dados de uma ajuda específica (contagem de tentativas)
                    if aid_id is not None and 'aids' in target:
                        aid_found = False
                        for aid in target['aids']:
                            if aid.get('id') == aid_id:
                                aid_found = True
                                if new_attempts_count is not None:
                                    aid['attempts_count'] = new_attempts_count
                                break
                        if not aid_found:
                            raise Exception("Ajuda (Aid) não encontrada no alvo.")
                    # Removido o bloco `elif aid_id is None:` que tentava atualizar `target['tentativas']`
                    # pois as tentativas são armazenadas por ajuda individualmente.
                    break
            if not target_found:
                raise Exception("Alvo não encontrado na meta.")
            break
    if not goal_found:
        raise Exception("Meta não encontrada no PEI.")

    transaction.update(pei_ref, {'goals': goals})


# =================================================================
# ROTAS DO PEI (Plano Educacional Individualizado)
# =================================================================

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis', endpoint='ver_peis_paciente')
@login_required
def ver_peis_paciente(paciente_doc_id):
    db_instance = get_db()
    clinica_id = session['clinica_id']
    paciente_data = None
    all_peis = []
    current_date_iso = datetime.date.today().isoformat()

    # Obter informações do usuário logado
    user_role = session.get('user_role')
    user_uid = session.get('user_uid')
    is_admin = user_role == 'admin'
    is_professional = user_role == 'medico'
    logged_in_professional_id = None

    print(f"DEBUG: ver_peis_paciente - user_role: {user_role}, user_uid: {user_uid}")

    if user_uid:
        try:
            user_doc = db_instance.collection('User').document(user_uid).get()
            if user_doc.exists:
                logged_in_professional_id = user_doc.to_dict().get('profissional_id')
                print(f"DEBUG: Profissional logado ID (do User): {logged_in_professional_id}")
            else:
                print(f"DEBUG: Documento do usuário {user_uid} não encontrado na coleção User.")
        except Exception as e:
            print(f"ERRO: Ao buscar ID do profissional para o usuário {user_uid}: {e}")
            flash("Ocorreu um erro ao verificar as suas permissões de profissional.", "danger")

    # Obter informações do paciente
    try:
        paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
        paciente_doc = paciente_ref.get()
        if not paciente_doc.exists:
            flash('Paciente não encontrado.', 'danger')
            return redirect(url_for('buscar_prontuario'))
        paciente_data = convert_doc_to_dict(paciente_doc)

        if paciente_data and 'data_nascimento' in paciente_data and isinstance(paciente_data['data_nascimento'], str):
            try:
                paciente_data['data_nascimento'] = datetime.datetime.strptime(paciente_data['data_nascimento'], '%Y-%m-%dT%H:%M:%S')
            except (ValueError, TypeError):
                paciente_data['data_nascimento'] = None

    except Exception as e:
        flash(f'Erro ao carregar dados do paciente: {e}.', 'danger')
        print(f"ERRO: Ao carregar paciente para PEI: {e}")
        return redirect(url_for('buscar_prontuario'))

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
        print(f"ERRO: Ao carregar profissionais para PEI: {e}")

    # Obter PEIs do paciente
    try:
        peis_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis')
        
        # --- CORREÇÃO AQUI: Consulta paciente_id usando DocumentReference ---
        # Cria uma DocumentReference para o paciente para a consulta
        paciente_doc_ref_for_query = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
        print(f"DEBUG: Consulta de PEIs - Comparando paciente_id com DocumentReference: {paciente_doc_ref_for_query.path}")
        peis_query = peis_ref.where(filter=FieldFilter('paciente_id', '==', paciente_doc_ref_for_query))

        if is_professional and not is_admin:
            if logged_in_professional_id:
                # --- CORREÇÃO AQUI: Consulta profissionais_ids usando DocumentReference ---
                # Cria uma DocumentReference para o profissional logado para a consulta
                logged_in_professional_doc_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(logged_in_professional_id)
                print(f"DEBUG: Consulta de PEIs - Comparando profissionais_ids com DocumentReference: {logged_in_professional_doc_ref.path}")
                peis_query = peis_query.where(
                    filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_doc_ref)
                )
            else:
                print("DEBUG: Profissional logado sem ID associado ou não encontrado, retornando PEIs vazios.")
                peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', 'ID_INVALIDO_PARA_NAO_RETORNAR_NADA'))

        peis_query = peis_query.order_by('data_criacao', direction=firestore.Query.DESCENDING)

        for pei_doc in peis_query.stream():
            pei = convert_doc_to_dict(pei_doc)
            
            # --- GARANTE QUE OS IDs SEJAM STRINGS PARA O FRONTEND ---
            # Converte DocumentReference de paciente_id para string ID para o frontend
            if isinstance(pei_doc.to_dict().get('paciente_id'), firestore.DocumentReference):
                pei['paciente_id'] = pei_doc.to_dict().get('paciente_id').id
            else:
                pei['paciente_id'] = pei_doc.to_dict().get('paciente_id')

            # Converte lista de DocumentReference de profissionais_ids para lista de string IDs para o frontend
            if all(isinstance(ref, firestore.DocumentReference) for ref in pei_doc.to_dict().get('profissionais_ids', [])):
                pei['profissionais_ids'] = [ref.id for ref in pei_doc.to_dict().get('profissionais_ids', [])]
            else:
                pei['profissionais_ids'] = pei_doc.to_dict().get('profissionais_ids', [])


            if 'data_criacao' in pei and isinstance(pei['data_criacao'], str):
                try:
                    dt_obj = datetime.datetime.strptime(pei['data_criacao'], '%Y-%m-%dT%H:%M:%S')
                    pei['data_criacao_iso'] = dt_obj.isoformat()
                    pei['data_criacao'] = dt_obj.strftime('%d/%m/%Y %H:%M')
                except (ValueError, TypeError):
                    pei['data_criacao_iso'] = None
                    pei['data_criacao'] = pei.get('data_criacao', 'N/A')
            else:
                pei['data_criacao'] = pei.get('data_criacao', 'N/A')
                pei['data_criacao_iso'] = None


            pei['profissionais_nomes_associados_fmt'] = ", ".join(pei.get('profissionais_nomes_associados', ['N/A']))

            if 'activities' in pei and isinstance(pei['activities'], list):
                for activity in pei['activities']:
                    activity_ts = activity.get('timestamp')
                    if isinstance(activity_ts, str):
                        try:
                            dt_obj = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                            activity['timestamp_fmt'] = dt_obj.strftime('%d/%m/%Y %H:%M')
                        except (ValueError, TypeError):
                            activity['timestamp_fmt'] = 'Data Inválida'
                    else:
                        activity['timestamp_fmt'] = 'N/A'
            all_peis.append(pei)
        print(f"DEBUG: Total de PEIs encontrados para o paciente {paciente_doc_id}: {len(all_peis)}")

    except Exception as e:
        flash(f'Erro ao carregar PEIs do paciente: {e}.', 'danger')
        print(f"ERRO: Ao carregar PEIs: {e}")

    return render_template('pei_page.html',
                           paciente=paciente_data,
                           paciente_doc_id=paciente_doc_id,
                           peis=all_peis,
                           current_date_iso=current_date_iso,
                           is_admin=is_admin,
                           is_professional=is_professional,
                           logged_in_professional_id=logged_in_professional_id,
                           all_professionals=profissionais_lista
                           )


@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/add', methods=['POST'], endpoint='add_pei')
@login_required
@admin_required
def add_pei(paciente_doc_id):
    db_instance = get_db()
    clinica_id = session['clinica_id']
    try:
        data = request.form
        titulo = data.get('titulo')
        data_criacao_str = data.get('data_criacao')
        profissionais_ids_selecionados = request.form.getlist('profissionais_ids[]')

        if not titulo or not data_criacao_str or not profissionais_ids_selecionados:
            flash('Título, data de criação e pelo menos um profissional associado do PEI são obrigatórios.', 'danger')
            return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))

        try:
            data_criacao_obj = datetime.datetime.strptime(data_criacao_str, '%Y-%m-%d')
        except ValueError:
            flash('Formato de data de criação inválido.', 'danger')
            return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))

        # --- CORREÇÃO AQUI: Salva profissionais_ids como DocumentReference ---
        profissionais_refs = []
        profissionais_nomes_associados = []
        for prof_id in profissionais_ids_selecionados:
            profissional_ref = db_instance.collection(f'clinicas/{clinica_id}/profissionais').document(prof_id)
            profissional_doc = profissional_ref.get()
            if profissional_doc.exists:
                profissionais_refs.append(profissional_ref) # Salva a DocumentReference
                profissionais_nomes_associados.append(profissional_doc.to_dict().get('nome', 'N/A'))
            else:
                profissionais_nomes_associados.append(f"Profissional Desconhecido ({prof_id})")
        print(f"DEBUG: Salvando profissionais_ids como lista de DocumentReference: {[ref.path for ref in profissionais_refs]}")

        # --- CORREÇÃO AQUI: Salva paciente_id como DocumentReference ---
        paciente_doc_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
        print(f"DEBUG: Salvando paciente_id como DocumentReference: {paciente_doc_ref.path}")
        
        peis_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis')

        new_pei_data = {
            'paciente_id': paciente_doc_ref, # Agora salva a DocumentReference do paciente
            'titulo': titulo,
            'data_criacao': data_criacao_obj,
            'status': 'ativo',
            'goals': [],
            'activities': [],
            'criado_em': datetime.datetime.now(SAO_PAULO_TZ),
            'profissional_criador_nome': session.get('user_name', 'N/A'),
            'profissionais_ids': profissionais_refs, # Agora é uma lista de DocumentReference
            'profissionais_nomes_associados': profissionais_nomes_associados
        }
        peis_ref.add(new_pei_data)
        flash('PEI adicionado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao adicionar PEI: {e}', 'danger')
        print(f"ERRO: add_pei: {e}")
    return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/delete_pei', methods=['POST'], endpoint='delete_pei')
@login_required
@admin_required
def delete_pei(paciente_doc_id):
    db_instance = get_db()
    clinica_id = session['clinica_id']
    try:
        if request.is_json:
            pei_id = request.json.get('pei_id')
        else:
            pei_id = request.form.get('pei_id')

        if not pei_id:
            return jsonify({'success': False, 'message': 'ID do PEI não fornecido.'}), 400
        
        db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id).delete()
        return jsonify({'success': True, 'message': 'PEI excluído com sucesso!'}), 200
    except Exception as e:
        print(f"ERRO: delete_pei: {e}")
        return jsonify({'success': False, 'message': f'Erro ao excluir PEI: {e}'}), 500


@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/finalize', methods=['POST'], endpoint='finalize_pei')
@login_required
def finalize_pei(paciente_doc_id):
    db_instance = get_db()
    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    user_uid = session.get('user_uid')
    is_admin = user_role == 'admin'
    logged_in_professional_id = None

    if user_uid:
        try:
            user_doc = db_instance.collection('User').document(user_uid).get()
            if user_doc.exists:
                logged_in_professional_id = user_doc.to_dict().get('profissional_id')
        except Exception as e:
            print(f"ERRO: Ao verificar permissões para finalizar PEI: {e}")
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
            if logged_in_professional_id not in associated_professionals_ids:
                return jsonify({'success': False, 'message': 'Você não tem permissão para finalizar este PEI.'}), 403

        _finalize_pei_transaction(db_instance.transaction(), pei_ref)

        all_peis = []
        # --- CORREÇÃO AQUI: Consulta paciente_id usando DocumentReference ---
        paciente_doc_ref_for_query = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_ref_for_query)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
        
        if not is_admin and logged_in_professional_id:
            # --- CORREÇÃO AQUI: Consulta profissionais_ids usando DocumentReference ---
            logged_in_professional_doc_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(logged_in_professional_id)
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_doc_ref))

        for doc in peis_query.stream():
            pei_data_converted = convert_doc_to_dict(doc)
            # Garante que paciente_id e profissionais_ids sejam strings para o frontend
            pei_data_converted['paciente_id'] = doc.to_dict().get('paciente_id').id if isinstance(doc.to_dict().get('paciente_id'), firestore.DocumentReference) else doc.to_dict().get('paciente_id')
            pei_data_converted['profissionais_ids'] = [ref.id for ref in doc.to_dict().get('profissionais_ids', [])] if all(isinstance(ref, firestore.DocumentReference) for ref in doc.to_dict().get('profissionais_ids', [])) else doc.to_dict().get('profissionais_ids', [])


            if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], str):
                try:
                    dt_obj = datetime.datetime.strptime(pei_data_converted['data_criacao'], '%Y-%m-%dT%H:%M:%S')
                    pei_data_converted['data_criacao'] = dt_obj.strftime('%d/%m/%Y %H:%M')
                except (ValueError, TypeError):
                    pei_data_converted['data_criacao'] = pei_data_converted.get('data_criacao', 'N/A')
            pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

            if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                for activity in pei_data_converted['activities']:
                    activity_ts = activity.get('timestamp')
                    if isinstance(activity_ts, str):
                        try:
                            dt_obj = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                            activity['timestamp_fmt'] = dt_obj.strftime('%d/%m/%Y %H:%M')
                        except (ValueError, TypeError):
                            activity['timestamp_fmt'] = 'Data Inválida'
                    else:
                        activity['timestamp_fmt'] = 'N/A'
            all_peis.append(pei_data_converted)

        return jsonify({'success': True, 'message': 'PEI finalizado com sucesso!', 'peis': all_peis}), 200
    except Exception as e:
        print(f"ERRO: Ao finalizar PEI: {e}")
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/add_goal', methods=['POST'], endpoint='add_goal')
@login_required
@admin_required
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
                        'status': 'pendente',
                        'aids': [aid.copy() for aid in fixed_aids_template]
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
        print(f"ERRO: add_goal: {e}")
    return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/add_target_to_goal', methods=['POST'], endpoint='add_target_to_goal')
@login_required
@admin_required
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
        user_uid = session.get('user_uid')
        logged_in_professional_id = None
        if user_uid:
            try:
                user_doc = db_instance.collection('User').document(user_uid).get()
                if user_doc.exists:
                    logged_in_professional_id = user_doc.to_dict().get('profissional_id')
            except Exception as e:
                print(f"ERRO: Ao buscar ID do profissional para o usuário {user_uid} em add_target_to_goal: {e}")

        # --- CORREÇÃO AQUI: Consulta paciente_id usando DocumentReference ---
        paciente_doc_ref_for_query = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_ref_for_query)).order_by('data_criacao', direction=firestore.Query.DESCENDING)

        if user_role == 'medico' and not (user_role == 'admin') and logged_in_professional_id:
            # --- CORREÇÃO AQUI: Consulta profissionais_ids usando DocumentReference ---
            logged_in_professional_doc_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(logged_in_professional_id)
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_doc_ref))

        for doc in peis_query.stream():
            pei_data_converted = convert_doc_to_dict(doc)
            # Garante que paciente_id e profissionais_ids sejam strings para o frontend
            pei_data_converted['paciente_id'] = doc.to_dict().get('paciente_id').id if isinstance(doc.to_dict().get('paciente_id'), firestore.DocumentReference) else doc.to_dict().get('paciente_id')
            pei_data_converted['profissionais_ids'] = [ref.id for ref in doc.to_dict().get('profissionais_ids', [])] if all(isinstance(ref, firestore.DocumentReference) for ref in doc.to_dict().get('profissionais_ids', [])) else doc.to_dict().get('profissionais_ids', [])


            if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], str):
                try:
                    dt_obj = datetime.datetime.strptime(pei_data_converted['data_criacao'], '%Y-%m-%dT%H:%M:%S')
                    pei_data_converted['data_criacao'] = dt_obj.strftime('%d/%m/%Y %H:%M')
                except (ValueError, TypeError):
                    pei_data_converted['data_criacao'] = pei_data_converted.get('data_criacao', 'N/A')
            pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

            if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                for activity in pei_data_converted['activities']:
                    activity_ts = activity.get('timestamp')
                    if isinstance(activity_ts, str):
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
        print(f"ERRO: Ao adicionar alvo à meta: {e}")
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/delete_goal', methods=['POST'], endpoint='delete_goal')
@login_required
@admin_required
def delete_goal(paciente_doc_id):
    db_instance = get_db()
    clinica_id = session['clinica_id']
    try:
        if request.is_json:
            pei_id = request.json.get('pei_id')
            goal_id = request.json.get('goal_id')
        else:
            pei_id = request.form.get('pei_id')
            goal_id = request.form.get('goal_id')

        if not pei_id or not goal_id:
            return jsonify({'success': False, 'message': 'Dados insuficientes para excluir meta.'}), 400
        
        pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
        transaction = db_instance.transaction()
        _delete_goal_transaction(transaction, pei_ref, goal_id)
        transaction.commit()
        return jsonify({'success': True, 'message': 'Meta excluída com sucesso!'}), 200
    except Exception as e:
        print(f"ERRO: delete_goal: {e}")
        return jsonify({'success': False, 'message': f'Erro ao excluir meta: {e}'}), 500

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/finalize_goal', methods=['POST'], endpoint='finalize_goal')
@login_required
def finalize_goal(paciente_doc_id):
    db_instance = get_db()
    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    user_uid = session.get('user_uid')
    is_admin = user_role == 'admin'
    logged_in_professional_id = None

    if user_uid:
        try:
            user_doc = db_instance.collection('User').document(user_uid).get()
            if user_doc.exists:
                logged_in_professional_id = user_doc.to_dict().get('profissional_id')
        except Exception as e:
            print(f"ERRO: Ao verificar permissões para finalizar meta: {e}")
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
        # --- CORREÇÃO AQUI: Consulta paciente_id usando DocumentReference ---
        paciente_doc_ref_for_query = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_ref_for_query)).order_by('data_criacao', direction=firestore.Query.DESCENDING)

        if not is_admin and logged_in_professional_id:
            # --- CORREÇÃO AQUI: Consulta profissionais_ids usando DocumentReference ---
            logged_in_professional_doc_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(logged_in_professional_id)
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_doc_ref))

        for doc in peis_query.stream():
            pei_data_converted = convert_doc_to_dict(doc)
            # Garante que paciente_id e profissionais_ids sejam strings para o frontend
            pei_data_converted['paciente_id'] = doc.to_dict().get('paciente_id').id if isinstance(doc.to_dict().get('paciente_id'), firestore.DocumentReference) else doc.to_dict().get('paciente_id')
            pei_data_converted['profissionais_ids'] = [ref.id for ref in doc.to_dict().get('profissionais_ids', [])] if all(isinstance(ref, firestore.DocumentReference) for ref in doc.to_dict().get('profissionais_ids', [])) else doc.to_dict().get('profissionais_ids', [])


            if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], str):
                try:
                    dt_obj = datetime.datetime.strptime(pei_data_converted['data_criacao'], '%Y-%m-%dT%H:%M:%S')
                    pei_data_converted['data_criacao'] = dt_obj.strftime('%d/%m/%Y %H:%M')
                except (ValueError, TypeError):
                    pei_data_converted['data_criacao'] = pei_data_converted.get('data_criacao', 'N/A')
            pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

            if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                for activity in pei_data_converted['activities']:
                    activity_ts = activity.get('timestamp')
                    if isinstance(activity_ts, str):
                        try:
                            dt_obj = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                            activity['timestamp_fmt'] = dt_obj.strftime('%d/%m/%Y %H:%M')
                        except (ValueError, TypeError):
                            activity['timestamp_fmt'] = 'Data Inválida'
                    else:
                        activity['timestamp_fmt'] = 'N/A'
            all_peis.append(pei_data_converted)

        return jsonify({'success': True, 'message': 'Meta finalizada com sucesso!', 'peis': all_peis}), 200
    except Exception as e:
        print(f"ERRO: Ao finalizar meta: {e}")
        return jsonify({'success': False, 'message': f'Erro interno ao finalizar meta: {e}'}), 500

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/add_activity', methods=['POST'], endpoint='add_pei_activity')
@login_required
def add_pei_activity(paciente_doc_id):
    db_instance = get_db()
    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    user_uid = session.get('user_uid')
    is_admin = user_role == 'admin'
    logged_in_professional_id = None

    if user_uid:
        try:
            user_doc = db_instance.collection('User').document(user_uid).get()
            if user_doc.exists:
                logged_in_professional_id = user_doc.to_dict().get('profissional_id')
        except Exception as e:
            print(f"ERRO: Ao verificar permissões para adicionar atividade: {e}")
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

        if not is_admin:
            associated_professionals_ids = pei_doc.to_dict().get('profissionais_ids', [])
            if logged_in_professional_id not in associated_professionals_ids:
                return jsonify({'success': False, 'message': 'Você não tem permissão para adicionar atividades a este PEI.'}), 403

        user_name = session.get('user_name', 'Desconhecido')
        _add_pei_activity_transaction(db_instance.transaction(), pei_ref, activity_content, user_name)

        all_peis = []
        # --- CORREÇÃO AQUI: Consulta paciente_id usando DocumentReference ---
        paciente_doc_ref_for_query = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_ref_for_query)).order_by('data_criacao', direction=firestore.Query.DESCENDING)

        if not is_admin and logged_in_professional_id:
            # --- CORREÇÃO AQUI: Consulta profissionais_ids usando DocumentReference ---
            logged_in_professional_doc_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(logged_in_professional_id)
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_doc_ref))

        for doc in peis_query.stream():
            pei_data_converted = convert_doc_to_dict(doc)
            # Garante que paciente_id e profissionais_ids sejam strings para o frontend
            pei_data_converted['paciente_id'] = doc.to_dict().get('paciente_id').id if isinstance(doc.to_dict().get('paciente_id'), firestore.DocumentReference) else doc.to_dict().get('paciente_id')
            pei_data_converted['profissionais_ids'] = [ref.id for ref in doc.to_dict().get('profissionais_ids', [])] if all(isinstance(ref, firestore.DocumentReference) for ref in doc.to_dict().get('profissionais_ids', [])) else doc.to_dict().get('profissionais_ids', [])


            if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], str):
                try:
                    dt_obj = datetime.datetime.strptime(pei_data_converted['data_criacao'], '%Y-%m-%dT%H:%M:%S')
                    pei_data_converted['data_criacao'] = dt_obj.strftime('%d/%m/%Y %H:%M')
                except (ValueError, TypeError):
                    pei_data_converted['data_criacao'] = pei_data_converted.get('data_criacao', 'N/A')
            pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

            if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                for activity in pei_data_converted['activities']:
                    activity_ts = activity.get('timestamp')
                    if isinstance(activity_ts, str):
                        try:
                            dt_obj = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                            activity['timestamp_fmt'] = dt_obj.strftime('%d/%m/%Y %H:%M')
                        except (ValueError, TypeError):
                            activity['timestamp_fmt'] = 'Data Inválida'
                    else:
                        activity['timestamp_fmt'] = 'N/A'
            all_peis.append(pei_data_converted)

        return jsonify({'success': True, 'message': 'Atividade adicionada com sucesso!', 'peis': all_peis}), 200

    except Exception as e:
        print(f"ERRO: Ao adicionar atividade ao PEI: {e}")
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/update_target_and_aid_data', methods=['POST'], endpoint='update_target_and_aid_data')
@login_required
def update_target_and_aid_data(paciente_doc_id):
    db_instance = get_db()
    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    user_uid = session.get('user_uid')
    is_admin = user_role == 'admin'
    logged_in_professional_id = None

    if user_uid:
        try:
            user_doc = db_instance.collection('User').document(user_uid).get()
            if user_doc.exists:
                logged_in_professional_id = user_doc.to_dict().get('profissional_id')
        except Exception as e:
            print(f"ERRO: Ao verificar permissões para atualizar alvo/ajuda: {e}")
            return jsonify({'success': False, 'message': f'Erro ao verificar permissões: {e}'}), 500

    try:
        data = request.get_json()
        pei_id = data.get('pei_id')
        goal_id = data.get('goal_id')
        target_id = data.get('target_id')
        aid_id = data.get('aid_id')
        new_attempts_count = data.get('new_attempts_count') # Novo nome para o campo de tentativas
        new_target_status = data.get('new_target_status')

        print(f"DEBUG: Recebida requisição update_target_and_aid_data: pei_id={pei_id}, goal_id={goal_id}, target_id={target_id}, aid_id={aid_id}, new_attempts_count={new_attempts_count}, new_target_status={new_target_status}")


        if not all([pei_id, goal_id, target_id]):
            return jsonify({'success': False, 'message': 'Dados insuficientes para atualizar alvo.'}), 400

        pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
        pei_doc = pei_ref.get()
        if not pei_doc.exists:
            return jsonify({'success': False, 'message': 'PEI não encontrado.'}), 404

        if not is_admin:
            associated_professionals_ids = pei_doc.to_dict().get('profissionais_ids', [])
            if logged_in_professional_id not in associated_professionals_ids:
                return jsonify({'success': False, 'message': 'Você não tem permissão para atualizar este item.'}), 403

        transaction = db_instance.transaction()
        
        _update_target_and_aid_data_transaction(
            transaction, 
            pei_ref, 
            goal_id, 
            target_id, 
            aid_id=aid_id, 
            new_attempts_count=new_attempts_count, # Passa o novo nome do campo
            new_target_status=new_target_status
        )
        transaction.commit()

        all_peis = []
        # --- CORREÇÃO AQUI: Consulta paciente_id usando DocumentReference ---
        paciente_doc_ref_for_query = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_ref_for_query)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
        
        if not is_admin and logged_in_professional_id:
            # --- CORREÇÃO AQUI: Consulta profissionais_ids usando DocumentReference ---
            logged_in_professional_doc_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(logged_in_professional_id)
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_doc_ref))

        for doc in peis_query.stream():
            pei_data_converted = convert_doc_to_dict(doc)
            # Garante que paciente_id e profissionais_ids sejam strings para o frontend
            pei_data_converted['paciente_id'] = doc.to_dict().get('paciente_id').id if isinstance(doc.to_dict().get('paciente_id'), firestore.DocumentReference) else doc.to_dict().get('paciente_id')
            pei_data_converted['profissionais_ids'] = [ref.id for ref in doc.to_dict().get('profissionais_ids', [])] if all(isinstance(ref, firestore.DocumentReference) for ref in doc.to_dict().get('profissionais_ids', [])) else doc.to_dict().get('profissionais_ids', [])


            if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], str):
                try:
                    dt_obj = datetime.datetime.strptime(pei_data_converted['data_criacao'], '%Y-%m-%dT%H:%M:%S')
                    pei_data_converted['data_criacao'] = dt_obj.strftime('%d/%m/%Y %H:%M')
                except (ValueError, TypeError):
                    pei_data_converted['data_criacao'] = pei_data_converted.get('data_criacao', 'N/A')
            pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

            if 'activities' in pei_data_converted and isinstance(pei_data_converted['activities'], list):
                for activity in pei_data_converted['activities']:
                    activity_ts = activity.get('timestamp')
                    if isinstance(activity_ts, str):
                        try:
                            dt_obj = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                            activity['timestamp_fmt'] = dt_obj.strftime('%d/%m/%Y %H:%M')
                        except (ValueError, TypeError):
                            activity['timestamp_fmt'] = 'Data Inválida'
                    else:
                        activity['timestamp_fmt'] = 'N/A'
            all_peis.append(pei_data_converted)

        return jsonify({'success': True, 'message': 'Dados atualizados com sucesso!', 'peis': all_peis}), 200
    except Exception as e:
        print(f"ERRO: Ao atualizar dados de alvo/ajuda: {e}")
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

@peis_bp.route('/api/pacientes/<string:paciente_doc_id>/peis', methods=['GET'], endpoint='api_get_peis')
@login_required
def api_get_peis(paciente_doc_id):
    db_instance = get_db()
    clinica_id = session['clinica_id']
    all_peis = []

    user_role = session.get('user_role')
    user_uid = session.get('user_uid')
    is_admin = user_role == 'admin'
    logged_in_professional_id = None

    print(f"DEBUG API: user_role: {user_role}, user_uid: {user_uid}, is_admin: {is_admin}")

    if user_uid:
        try:
            user_doc = db_instance.collection('User').document(user_uid).get()
            if user_doc.exists:
                logged_in_professional_id = user_doc.to_dict().get('profissional_id')
                print(f"DEBUG API: Profissional logado ID: {logged_in_professional_id}")
            else:
                print(f"DEBUG API: Documento do usuário {user_uid} não encontrado na coleção User.")
        except Exception as e:
            print(f"ERRO API: Ao buscar ID do profissional para o usuário {user_uid}: {e}")
            return jsonify({'success': False, 'message': 'Erro ao verificar permissões de profissional.'}), 500
    elif not is_admin:
        print("DEBUG API: Acesso não autorizado para API de PEIs (nem admin, nem profissional).")
        return jsonify({'success': False, 'message': 'Acesso não autorizado.'}), 403 

    try:
        peis_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis')
        
        # --- CORREÇÃO AQUI: Consulta paciente_id usando DocumentReference ---
        paciente_doc_ref_for_query = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
        peis_query = peis_ref.where(filter=FieldFilter('paciente_id', '==', paciente_doc_ref_for_query))

        if not is_admin:
            if logged_in_professional_id:
                # --- CORREÇÃO AQUI: Consulta profissionais_ids usando DocumentReference ---
                logged_in_professional_doc_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(logged_in_professional_id)
                print(f"DEBUG API: Aplicando filtro de profissional: {logged_in_professional_doc_ref.path}")
                peis_query = peis_query.where(
                    filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_doc_ref)
                )
            else:
                print("DEBUG API: Profissional logado sem ID associado ou não encontrado, retornando PEIs vazios.")
                peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', 'ID_INVALIDO_PARA_NAO_RETORNAR_NADA'))


        peis_query = peis_query.order_by('data_criacao', direction=firestore.Query.DESCENDING)

        for pei_doc in peis_query.stream():
            pei = convert_doc_to_dict(pei_doc)
            # Garante que paciente_id e profissionais_ids sejam strings para o frontend
            pei['paciente_id'] = pei_doc.to_dict().get('paciente_id').id if isinstance(pei_doc.to_dict().get('paciente_id'), firestore.DocumentReference) else pei_doc.to_dict().get('paciente_id')
            pei['profissionais_ids'] = [ref.id for ref in pei_doc.to_dict().get('profissionais_ids', [])] if all(isinstance(ref, firestore.DocumentReference) for ref in pei_doc.to_dict().get('profissionais_ids', [])) else pei_doc.to_dict().get('profissionais_ids', [])


            if 'data_criacao' in pei and isinstance(pei['data_criacao'], str):
                try:
                    dt_obj = datetime.datetime.strptime(pei['data_criacao'], '%Y-%m-%dT%H:%M:%S')
                    pei['data_criacao'] = dt_obj.strftime('%d/%m/%Y %H:%M')
                except (ValueError, TypeError):
                    pei['data_criacao'] = pei.get('data_criacao', 'N/A')
            
            pei['profissionais_nomes_associados_fmt'] = ", ".join(pei.get('profissionais_nomes_associados', ['N/A']))

            if 'activities' in pei and isinstance(pei['activities'], list):
                for activity in pei['activities']:
                    activity_ts = activity.get('timestamp')
                    if isinstance(activity_ts, str):
                        try:
                            dt_obj = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                            activity['timestamp'] = dt_obj.strftime('%d/%m/%Y %H:%M')
                        except (ValueError, TypeError):
                            activity['timestamp'] = 'Data Inválida'
                    else:
                        activity['timestamp'] = 'N/A'
            all_peis.append(pei)
        print(f"DEBUG API: Total de PEIs encontrados na API para o paciente {paciente_doc_id}: {len(all_peis)}")
        return jsonify(all_peis), 200
    except Exception as e:
        print(f"ERRO API: Ao carregar PEIs: {e}")
        return jsonify({'success': False, 'message': f'Erro ao carregar PEIs: {e}'}), 500

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/<string:pei_id>/goals/<string:goal_id>/targets/<string:target_id>/complete', methods=['POST'], endpoint='mark_target_complete')
@login_required
def mark_target_complete(paciente_doc_id, pei_id, goal_id, target_id):
    db_instance = get_db()
    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    user_uid = session.get('user_uid')
    is_admin = user_role == 'admin'
    logged_in_professional_id = None

    if user_uid:
        try:
            user_doc = db_instance.collection('User').document(user_uid).get()
            if user_doc.exists:
                logged_in_professional_id = user_doc.to_dict().get('profissional_id')
        except Exception as e:
            print(f"ERRO: Ao verificar permissões para marcar alvo como concluído: {e}")
            return jsonify({'success': False, 'message': f'Erro ao verificar permissões: {e}'}), 500

    try:
        pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
        pei_doc = pei_ref.get()
        if not pei_doc.exists:
            return jsonify({'success': False, 'message': 'PEI não encontrado.'}), 404

        if not is_admin:
            associated_professionals_ids = pei_doc.to_dict().get('profissionais_ids', [])
            if logged_in_professional_id not in associated_professionals_ids:
                return jsonify({'success': False, 'message': 'Você não tem permissão para marcar este alvo como concluído.'}), 403

        transaction = db_instance.transaction()
        _update_target_and_aid_data_transaction(transaction, pei_ref, goal_id, target_id, new_target_status='finalizada')
        transaction.commit()

        return jsonify({'success': True, 'message': 'Alvo marcado como concluído com sucesso!'}), 200
    except Exception as e:
        print(f"ERRO: Ao marcar alvo como concluído: {e}")
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500