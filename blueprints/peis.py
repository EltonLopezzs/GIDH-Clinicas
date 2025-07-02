# blueprints/peis.py
from flask import Blueprint, render_template, session, flash, redirect, url_for, request, jsonify
import datetime
import uuid
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore

# Importe as suas funções utilitárias.
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

                    # Atualiza dados de uma ajuda específica, se aid_id for fornecido
                    if aid_id is not None and 'aids' in target:
                        aid_found = False
                        for aid in target['aids']:
                            if aid.get('id') == aid_id:
                                aid_found = True
                                if new_attempts_count is not None:
                                    try:
                                        aid['attempts_count'] = int(new_attempts_count)
                                    except (ValueError, TypeError) as e:
                                        raise Exception(f"Valor inválido para tentativas: {new_attempts_count}. Erro: {e}")
                                break
                        if not aid_found:
                            raise Exception("Ajuda (Aid) não encontrada no alvo.")
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

    if is_professional and not is_admin and user_uid:
        try:
            user_doc = db_instance.collection('User').document(user_uid).get()
            if user_doc.exists:
                logged_in_professional_id = user_doc.to_dict().get('profissional_id')
        except Exception as e:
            print(f"Erro ao buscar ID do profissional para o usuário {user_uid}: {e}")
            flash("Ocorreu um erro ao verificar as suas permissões de profissional.", "danger")

    # Obter informações do paciente
    try:
        paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
        paciente_doc = paciente_ref.get()
        if not paciente_doc.exists:
            flash('Paciente não encontrado.', 'danger')
            return redirect(url_for('buscar_prontuario')) # Redireciona para a busca de prontuário se o paciente não existir
        paciente_data = convert_doc_to_dict(paciente_doc)

        if paciente_data and 'data_nascimento' in paciente_data and isinstance(paciente_data['data_nascimento'], str):
            try:
                paciente_data['data_nascimento'] = datetime.datetime.strptime(paciente_data['data_nascimento'], '%Y-%m-%d')
            except (ValueError, TypeError):
                paciente_data['data_nascimento'] = None

    except Exception as e:
        flash(f'Erro ao carregar dados do paciente: {e}.', 'danger')
        print(f"Erro ao carregar paciente para PEI: {e}")
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
        print(f"Erro ao carregar profissionais para PEI: {e}")

    # Obter PEIs do paciente
    try:
        peis_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis')
        peis_query = peis_ref.where(filter=FieldFilter('paciente_id', '==', paciente_doc_id))

        if is_professional and not is_admin:
            if logged_in_professional_id:
                peis_query = peis_query.where(
                    filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id)
                )
            else:
                peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', 'ID_INVALIDO_PARA_NAO_RETORNAR_NADA'))

        peis_query = peis_query.order_by('data_criacao', direction=firestore.Query.DESCENDING)

        for pei_doc in peis_query.stream():
            pei = convert_doc_to_dict(pei_doc)
            if 'data_criacao' in pei and isinstance(pei['data_criacao'], datetime.datetime):
                pei['data_criacao'] = pei['data_criacao'].strftime('%d/%m/%Y %H:%M')
            else:
                pei['data_criacao'] = pei.get('data_criacao', 'N/A')

            pei['profissionais_nomes_associados_fmt'] = ", ".join(pei.get('profissionais_nomes_associados', ['N/A']))

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
            all_peis.append(pei)

    except Exception as e:
        flash(f'Erro ao carregar PEIs do paciente: {e}.', 'danger')
        print(f"Erro ao carregar PEIs: {e}")

    # CORREÇÃO: Adicionando paciente_doc_id ao contexto do template
    return render_template('pei_page.html',
                           paciente=paciente_data,
                           paciente_doc_id=paciente_doc_id, # <-- LINHA CORRIGIDA
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
            'activities': [],
            'criado_em': datetime.datetime.now(SAO_PAULO_TZ),
            'profissional_criador_nome': session.get('user_name', 'N/A'),
            'profissionais_ids': profissionais_ids_selecionados,
            'profissionais_nomes_associados': profissionais_nomes_associados
        }
        peis_ref.add(new_pei_data)
        flash('PEI adicionado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao adicionar PEI: {e}', 'danger')
        print(f"Erro add_pei: {e}")
    return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/delete', methods=['POST'], endpoint='delete_pei')
@login_required
@admin_required
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
        print(f"Erro delete_pei: {e}")
    return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/finalize', methods=['POST'], endpoint='finalize_pei')
@login_required
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
                        'concluido': False, # Manter para compatibilidade
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
        print(f"Erro add_goal: {e}")
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
        logged_in_professional_id = None
        if user_role == 'medico':
            user_doc = db_instance.collection('User').document(session.get('user_uid')).get()
            if user_doc.exists:
                logged_in_professional_id = user_doc.to_dict().get('profissional_id')

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

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/delete_goal', methods=['POST'], endpoint='delete_goal')
@login_required
@admin_required
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
    return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/finalize_goal', methods=['POST'], endpoint='finalize_goal')
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

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/add_activity', methods=['POST'], endpoint='add_pei_activity')
@login_required
def add_pei_activity(paciente_doc_id):
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

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/update_target_and_aid_data', methods=['POST'], endpoint='update_target_and_aid_data')
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
        aid_id = data.get('aid_id')
        new_attempts_count = data.get('new_attempts_count')
        new_target_status = data.get('new_target_status')

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
        _update_target_and_aid_data_transaction(transaction, pei_ref, goal_id, target_id, aid_id, new_attempts_count, new_target_status)
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
        print(f"Erro ao atualizar tentativas/status do alvo: {e}")
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

