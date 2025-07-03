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
# FUNÇÕES DE TRANSAÇÃO (Helpers para PEI) - ATUALIZADAS PARA SUBCOLEÇÕES
# =================================================================

@firestore.transactional
def _delete_goal_transaction(transaction, pei_ref, goal_id_to_delete):
    """
    Deleta uma meta específica de um PEI, incluindo seus alvos e ajudas.
    Args:
        transaction: Objeto de transação do Firestore.
        pei_ref: Referência do documento PEI.
        goal_id_to_delete: ID da meta a ser deletada.
    Raises:
        Exception: Se a meta não for encontrada.
    """
    goal_ref = pei_ref.collection('goals').document(goal_id_to_delete)
    goal_snapshot = goal_ref.get(transaction=transaction)
    if not goal_snapshot.exists:
        raise Exception("Meta não encontrada para exclusão.")

    # Deletar subcoleção 'aids' de cada alvo
    targets_ref = goal_ref.collection('targets')
    targets_docs = targets_ref.stream() # Não usa transação para stream, apenas para leitura de docs específicos
    for target_doc in targets_docs:
        aids_ref = target_doc.reference.collection('aids')
        aids_docs = aids_ref.stream()
        for aid_doc in aids_docs:
            transaction.delete(aid_doc.reference)
        transaction.delete(target_doc.reference) # Deleta o alvo após deletar suas ajudas

    transaction.delete(goal_ref) # Deleta a meta após deletar seus alvos e ajudas

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
        Exception: Se o alvo não for encontrado.
    """
    target_ref = pei_ref.collection('goals').document(goal_id).collection('targets').document(target_id)
    target_snapshot = target_ref.get(transaction=transaction)
    if not target_snapshot.exists:
        raise Exception("Alvo não encontrado.")

    update_data = {'status': new_target_status}
    transaction.update(target_ref, update_data)

    # Se o alvo for marcado como finalizado, todas as ajudas associadas também são finalizadas.
    if new_target_status == 'finalizada':
        aids_ref = target_ref.collection('aids')
        aids_docs = aids_ref.stream()
        for aid_doc in aids_docs:
            transaction.update(aid_doc.reference, {'status': 'finalizada'})

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
        Exception: Se a meta não for encontrada.
    """
    goal_ref = pei_ref.collection('goals').document(goal_id_to_finalize)
    goal_snapshot = goal_ref.get(transaction=transaction)
    if not goal_snapshot.exists:
        raise Exception("Meta não encontrada para finalizar.")

    transaction.update(goal_ref, {'status': 'finalizado'})

    # Marca todos os alvos ativos dentro desta meta como concluídos
    targets_ref = goal_ref.collection('targets')
    targets_docs = targets_ref.stream()
    for target_doc in targets_docs:
        if target_doc.to_dict().get('status') != 'finalizada':
            transaction.update(target_doc.reference, {'status': 'finalizada'})
        # Marca todas as ajudas dentro deste alvo como finalizadas
        aids_ref = target_doc.reference.collection('aids')
        aids_docs = aids_ref.stream()
        for aid_doc in aids_docs:
            transaction.update(aid_doc.reference, {'status': 'finalizada'})

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

    transaction.update(pei_ref, {
        'status': 'finalizado',
        'data_finalizacao': datetime.datetime.now(SAO_PAULO_TZ),
    })

    # Marca todas as metas ativas e seus alvos como finalizados
    goals_ref = pei_ref.collection('goals')
    goals_docs = goals_ref.stream()
    for goal_doc in goals_docs:
        if goal_doc.to_dict().get('status') == 'ativo':
            transaction.update(goal_doc.reference, {'status': 'finalizado'})
            targets_ref = goal_doc.reference.collection('targets')
            targets_docs = targets_ref.stream()
            for target_doc in targets_docs:
                if target_doc.to_dict().get('status') != 'finalizada':
                    transaction.update(target_doc.reference, {'status': 'finalizada'})
                aids_ref = target_doc.reference.collection('aids')
                aids_docs = aids_ref.stream()
                for aid_doc in aids_docs:
                    transaction.update(aid_doc.reference, {'status': 'finalizada'})

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
    goal_ref = pei_ref.collection('goals').document(goal_id)
    goal_snapshot = goal_ref.get(transaction=transaction)
    if not goal_snapshot.exists:
        raise Exception("Meta não encontrada no PEI.")

    targets_ref = goal_ref.collection('targets')

    # Definindo as ajudas fixas para cada novo alvo
    fixed_aids = [
        {'description': 'Ajuda Física Total', 'attempts_count': 0, 'status': 'pendente'},
        {'description': 'Ajuda Física Parcial', 'attempts_count': 0, 'status': 'pendente'},
        {'description': 'Ajuda Gestual', 'attempts_count': 0, 'status': 'pendente'},
        {'description': 'Ajuda Ecóica', 'attempts_count': 0, 'status': 'pendente'},
        {'description': 'Independente', 'attempts_count': 0, 'status': 'pendente'},
    ]

    new_target_doc_ref = targets_ref.document() # Firestore gera o ID automaticamente
    new_target_data = {
        'descricao': new_target_description,
        'concluido': False, # Manter 'concluido' para compatibilidade se ainda for usado em algum lugar
        'status': 'pendente', # Novo campo de status para o alvo
    }
    transaction.set(new_target_doc_ref, new_target_data)

    # Adiciona as ajudas como subcoleção do novo alvo
    aids_ref = new_target_doc_ref.collection('aids')
    for aid_data in fixed_aids:
        aid_doc_ref = aids_ref.document() # Firestore gera o ID automaticamente
        transaction.set(aid_doc_ref, aid_data)


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
    # Não é necessário verificar a existência do PEI aqui se a pei_ref já é válida
    activities_ref = pei_ref.collection('activities')
    new_activity = {
        'content': activity_content,
        'timestamp': datetime.datetime.now(SAO_PAULO_TZ),
        'user_name': user_name
    }
    transaction.set(activities_ref.document(), new_activity) # Adiciona um novo documento na subcoleção de atividades

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
        Exception: Se o alvo ou a ajuda não forem encontrados, ou se houver erro de tipo.
    """
    target_ref = pei_ref.collection('goals').document(goal_id).collection('targets').document(target_id)
    target_snapshot = target_ref.get(transaction=transaction)
    if not target_snapshot.exists:
        raise Exception("Alvo não encontrado.")

    # Atualiza o status geral do alvo, se fornecido
    if new_target_status is not None:
        transaction.update(target_ref, {'status': new_target_status})
        # Se o alvo for marcado como finalizado, todas as ajudas devem ser finalizadas
        if new_target_status == 'finalizada':
            aids_ref = target_ref.collection('aids')
            aids_docs = aids_ref.stream()
            for aid_doc in aids_docs:
                transaction.update(aid_doc.reference, {'status': 'finalizada'})

    # Atualiza dados de uma ajuda específica, se aid_id for fornecido
    if aid_id is not None:
        aid_ref = target_ref.collection('aids').document(aid_id)
        aid_snapshot = aid_ref.get(transaction=transaction)
        if not aid_snapshot.exists:
            raise Exception("Ajuda (Aid) não encontrada no alvo.")

        if new_attempts_count is not None:
            try:
                transaction.update(aid_ref, {'attempts_count': int(new_attempts_count)})
            except (ValueError, TypeError) as e:
                raise Exception(f"Valor inválido para tentativas: {new_attempts_count}. Erro: {e}")


# =================================================================
# ROTAS DO PEI (Plano Educacional Individualizado) - ATUALIZADAS PARA SUBCOLEÇÕES
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
            return redirect(url_for('buscar_prontuario'))
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

    # Obter PEIs do paciente e suas subcoleções
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
            pei_id = pei_doc.id # Obter o ID do documento PEI

            if 'data_criacao' in pei and isinstance(pei['data_criacao'], datetime.datetime):
                pei['data_criacao_iso'] = pei['data_criacao'].isoformat()
                pei['data_criacao'] = pei['data_criacao'].strftime('%d/%m/%Y %H:%M')
            else:
                pei['data_criacao'] = pei.get('data_criacao', 'N/A')
                pei['data_criacao_iso'] = None

            pei['profissionais_nomes_associados_fmt'] = ", ".join(pei.get('profissionais_nomes_associados', ['N/A']))

            # Carregar Metas (Goals)
            pei['goals'] = []
            goals_docs = pei_doc.reference.collection('goals').order_by('descricao').stream()
            for goal_doc in goals_docs:
                goal = convert_doc_to_dict(goal_doc)
                goal_id = goal_doc.id

                # Carregar Alvos (Targets) para cada Meta
                goal['targets'] = []
                targets_docs = goal_doc.reference.collection('targets').order_by('descricao').stream()
                for target_doc in targets_docs:
                    target = convert_doc_to_dict(target_doc)
                    target_id = target_doc.id

                    # Carregar Ajudas (Aids) para cada Alvo
                    target['aids'] = []
                    aids_docs = target_doc.reference.collection('aids').order_by('description').stream()
                    for aid_doc in aids_docs:
                        aid = convert_doc_to_dict(aid_doc)
                        target['aids'].append(aid)
                    goal['targets'].append(target)
                pei['goals'].append(goal)

            # Carregar Atividades (Activities)
            pei['activities'] = []
            activities_docs = pei_doc.reference.collection('activities').order_by('timestamp', direction=firestore.Query.DESCENDING).stream()
            for activity_doc in activities_docs:
                activity = convert_doc_to_dict(activity_doc)
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
                pei['activities'].append(activity)

            all_peis.append(pei)

    except Exception as e:
        flash(f'Erro ao carregar PEIs do paciente: {e}.', 'danger')
        print(f"Erro ao carregar PEIs: {e}")

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
            'criado_em': datetime.datetime.now(SAO_PAULO_TZ),
            'profissional_criador_nome': session.get('user_name', 'N/A'),
            'profissionais_ids': profissionais_ids_selecionados,
            'profissionais_nomes_associados': profissionais_nomes_associados
        }
        peis_ref.add(new_pei_data) # Adiciona o documento PEI principal
        flash('PEI adicionado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao adicionar PEI: {e}', 'danger')
        print(f"Erro add_pei: {e}")
    return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/delete_pei', methods=['POST'], endpoint='delete_pei')
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
            pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
            # Para deletar um documento com subcoleções, é necessário deletar as subcoleções primeiro.
            # Isso pode ser feito recursivamente ou de forma manual se o número de subcoleções for conhecido e limitado.
            # Para simplificar, vamos assumir que o Firestore Security Rules ou uma Cloud Function lidaria com a deleção em cascata.
            # Ou, como alternativa mais robusta, implementar a deleção de subcoleções aqui.
            # Por enquanto, apenas o documento PEI principal será deletado.
            # A deleção completa em cascata será abordada se houver problemas de dados órfãos.

            # Deletar subcoleções de goals, activities, etc.
            # Esta é uma operação que pode ser custosa e deve ser tratada com cuidado.
            # Para um ambiente de produção, considere Cloud Functions para exclusão em cascata.
            # Aqui, faremos uma exclusão "manual" para fins de demonstração.

            # Deletar atividades
            activities_ref = pei_ref.collection('activities')
            for doc in activities_ref.stream():
                doc.reference.delete()

            # Deletar metas e suas subcoleções (alvos e ajudas)
            goals_ref = pei_ref.collection('goals')
            for goal_doc in goals_ref.stream():
                targets_ref = goal_doc.reference.collection('targets')
                for target_doc in targets_ref.stream():
                    aids_ref = target_doc.reference.collection('aids')
                    for aid_doc in aids_ref.stream():
                        aid_doc.reference.delete()
                    target_doc.reference.delete()
                goal_doc.reference.delete()

            pei_ref.delete() # Deleta o documento PEI principal
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

        # Re-fetch PEIs to send updated data to frontend
        all_peis = []
        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
        if not is_admin and logged_in_professional_id:
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

        for doc in peis_query.stream():
            pei_data_converted = convert_doc_to_dict(doc)
            pei_id_fetched = doc.id # Obter o ID do documento PEI

            if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
            pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

            # Carregar Metas (Goals)
            pei_data_converted['goals'] = []
            goals_docs = doc.reference.collection('goals').order_by('descricao').stream()
            for goal_doc in goals_docs:
                goal = convert_doc_to_dict(goal_doc)
                goal_id = goal_doc.id

                # Carregar Alvos (Targets) para cada Meta
                goal['targets'] = []
                targets_docs = goal_doc.reference.collection('targets').order_by('descricao').stream()
                for target_doc in targets_docs:
                    target = convert_doc_to_dict(target_doc)
                    target_id = target_doc.id

                    # Carregar Ajudas (Aids) para cada Alvo
                    target['aids'] = []
                    aids_docs = target_doc.reference.collection('aids').order_by('description').stream()
                    for aid_doc in aids_docs:
                        aid = convert_doc_to_dict(aid_doc)
                        target['aids'].append(aid)
                    goal['targets'].append(target)
                pei_data_converted['goals'].append(goal)

            # Carregar Atividades (Activities)
            pei_data_converted['activities'] = []
            activities_docs = doc.reference.collection('activities').order_by('timestamp', direction=firestore.Query.DESCENDING).stream()
            for activity in activities_docs:
                activity_data = convert_doc_to_dict(activity)
                activity_ts = activity_data.get('timestamp')
                if isinstance(activity_ts, datetime.datetime):
                    activity_data['timestamp_fmt'] = activity_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                elif isinstance(activity_ts, str):
                    try:
                        naive_dt = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                        activity_data['timestamp_fmt'] = naive_dt.strftime('%d/%m/%Y %H:%M')
                    except (ValueError, TypeError):
                        activity_data['timestamp_fmt'] = 'Data Inválida'
                else:
                    activity_data['timestamp_fmt'] = 'N/A'
                pei_data_converted['activities'].append(activity_data)

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
            goals_ref = pei_ref.collection('goals')

            new_goal_doc_ref = goals_ref.document() # Firestore gera o ID para a nova meta
            new_goal_data = {
                'descricao': descricao_goal.strip(),
                'status': 'ativo',
            }
            new_goal_doc_ref.set(new_goal_data) # Cria o documento da meta

            # Adiciona os alvos como subcoleção da nova meta
            targets_ref = new_goal_doc_ref.collection('targets')
            fixed_aids_template = [
                {'description': 'Ajuda Física Total', 'attempts_count': 0, 'status': 'pendente'},
                {'description': 'Ajuda Física Parcial', 'attempts_count': 0, 'status': 'pendente'},
                {'description': 'Ajuda Gestual', 'attempts_count': 0, 'status': 'pendente'},
                {'description': 'Ajuda Ecóica', 'attempts_count': 0, 'status': 'pendente'},
                {'description': 'Independente', 'attempts_count': 0, 'status': 'pendente'},
            ]
            for desc in targets_desc:
                if desc.strip():
                    new_target_doc_ref = targets_ref.document() # Firestore gera o ID para o novo alvo
                    new_target_data = {
                        'descricao': desc.strip(),
                        'concluido': False,
                        'status': 'pendente',
                    }
                    new_target_doc_ref.set(new_target_data) # Cria o documento do alvo

                    # Adiciona as ajudas como subcoleção do novo alvo
                    aids_ref = new_target_doc_ref.collection('aids')
                    for aid_data in fixed_aids_template:
                        aids_ref.add(aid_data) # Firestore gera o ID para a ajuda

            flash('Meta e alvos adicionados com sucesso ao PEI!', 'success')
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

        # Re-fetch PEIs to send updated data to frontend
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
            pei_id_fetched = doc.id # Obter o ID do documento PEI

            if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
            pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

            # Carregar Metas (Goals)
            pei_data_converted['goals'] = []
            goals_docs = doc.reference.collection('goals').order_by('descricao').stream()
            for goal_doc in goals_docs:
                goal = convert_doc_to_dict(goal_doc)
                goal_id = goal_doc.id

                # Carregar Alvos (Targets) para cada Meta
                goal['targets'] = []
                targets_docs = goal_doc.reference.collection('targets').order_by('descricao').stream()
                for target_doc in targets_docs:
                    target = convert_doc_to_dict(target_doc)
                    target_id = target_doc.id

                    # Carregar Ajudas (Aids) para cada Alvo
                    target['aids'] = []
                    aids_docs = target_doc.reference.collection('aids').order_by('description').stream()
                    for aid_doc in aids_docs:
                        aid = convert_doc_to_dict(aid_doc)
                        target['aids'].append(aid)
                    goal['targets'].append(target)
                pei_data_converted['goals'].append(goal)

            # Carregar Atividades (Activities)
            pei_data_converted['activities'] = []
            activities_docs = doc.reference.collection('activities').order_by('timestamp', direction=firestore.Query.DESCENDING).stream()
            for activity in activities_docs:
                activity_data = convert_doc_to_dict(activity)
                activity_ts = activity_data.get('timestamp')
                if isinstance(activity_ts, datetime.datetime):
                    activity_data['timestamp_fmt'] = activity_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                elif isinstance(activity_ts, str):
                    try:
                        naive_dt = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                        activity_data['timestamp_fmt'] = naive_dt.strftime('%d/%m/%Y %H:%M')
                    except (ValueError, TypeError):
                        activity_data['timestamp_fmt'] = 'Data Inválida'
                else:
                    activity_data['timestamp_fmt'] = 'N/A'
                pei_data_converted['activities'].append(activity_data)

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

        # Re-fetch PEIs to send updated data to frontend
        all_peis = []
        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
        if not is_admin and logged_in_professional_id:
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

        for doc in peis_query.stream():
            pei_data_converted = convert_doc_to_dict(doc)
            pei_id_fetched = doc.id # Obter o ID do documento PEI

            if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
            pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

            # Carregar Metas (Goals)
            pei_data_converted['goals'] = []
            goals_docs = doc.reference.collection('goals').order_by('descricao').stream()
            for goal_doc in goals_docs:
                goal = convert_doc_to_dict(goal_doc)
                goal_id = goal_doc.id

                # Carregar Alvos (Targets) para cada Meta
                goal['targets'] = []
                targets_docs = goal_doc.reference.collection('targets').order_by('descricao').stream()
                for target_doc in targets_docs:
                    target = convert_doc_to_dict(target_doc)
                    target_id = target_doc.id

                    # Carregar Ajudas (Aids) para cada Alvo
                    target['aids'] = []
                    aids_docs = target_doc.reference.collection('aids').order_by('description').stream()
                    for aid_doc in aids_docs:
                        aid = convert_doc_to_dict(aid_doc)
                        target['aids'].append(aid)
                    goal['targets'].append(target)
                pei_data_converted['goals'].append(goal)

            # Carregar Atividades (Activities)
            pei_data_converted['activities'] = []
            activities_docs = doc.reference.collection('activities').order_by('timestamp', direction=firestore.Query.DESCENDING).stream()
            for activity in activities_docs:
                activity_data = convert_doc_to_dict(activity)
                activity_ts = activity_data.get('timestamp')
                if isinstance(activity_ts, datetime.datetime):
                    activity_data['timestamp_fmt'] = activity_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                elif isinstance(activity_ts, str):
                    try:
                        naive_dt = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                        activity_data['timestamp_fmt'] = naive_dt.strftime('%d/%m/%Y %H:%M')
                    except (ValueError, TypeError):
                        activity_data['timestamp_fmt'] = 'Data Inválida'
                else:
                    activity_data['timestamp_fmt'] = 'N/A'
                pei_data_converted['activities'].append(activity_data)

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

        # Re-fetch PEIs to send updated data to frontend
        all_peis = []
        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
        if not is_admin and logged_in_professional_id:
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

        for doc in peis_query.stream():
            pei_data_converted = convert_doc_to_dict(doc)
            pei_id_fetched = doc.id # Obter o ID do documento PEI

            if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
            pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

            # Carregar Metas (Goals)
            pei_data_converted['goals'] = []
            goals_docs = doc.reference.collection('goals').order_by('descricao').stream()
            for goal_doc in goals_docs:
                goal = convert_doc_to_dict(goal_doc)
                goal_id = goal_doc.id

                # Carregar Alvos (Targets) para cada Meta
                goal['targets'] = []
                targets_docs = goal_doc.reference.collection('targets').order_by('descricao').stream()
                for target_doc in targets_docs:
                    target = convert_doc_to_dict(target_doc)
                    target_id = target_doc.id

                    # Carregar Ajudas (Aids) para cada Alvo
                    target['aids'] = []
                    aids_docs = target_doc.reference.collection('aids').order_by('description').stream()
                    for aid_doc in aids_docs:
                        aid = convert_doc_to_dict(aid_doc)
                        target['aids'].append(aid)
                    goal['targets'].append(target)
                pei_data_converted['goals'].append(goal)

            # Carregar Atividades (Activities)
            pei_data_converted['activities'] = []
            activities_docs = doc.reference.collection('activities').order_by('timestamp', direction=firestore.Query.DESCENDING).stream()
            for activity in activities_docs:
                activity_data = convert_doc_to_dict(activity)
                activity_ts = activity_data.get('timestamp')
                if isinstance(activity_ts, datetime.datetime):
                    activity_data['timestamp_fmt'] = activity_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                elif isinstance(activity_ts, str):
                    try:
                        naive_dt = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                        activity_data['timestamp_fmt'] = naive_dt.strftime('%d/%m/%Y %H:%M')
                    except (ValueError, TypeError):
                        activity_data['timestamp_fmt'] = 'Data Inválida'
                else:
                    activity_data['timestamp_fmt'] = 'N/A'
                pei_data_converted['activities'].append(activity_data)

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

        # Re-fetch PEIs to send updated data to frontend
        all_peis = []
        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
        if not is_admin and logged_in_professional_id:
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

        for doc in peis_query.stream():
            pei_data_converted = convert_doc_to_dict(doc)
            pei_id_fetched = doc.id # Obter o ID do documento PEI

            if 'data_criacao' in pei_data_converted and isinstance(pei_data_converted['data_criacao'], datetime.datetime):
                pei_data_converted['data_criacao'] = pei_data_converted['data_criacao'].strftime('%d/%m/%Y %H:%M')
            pei_data_converted['profissionais_nomes_associados_fmt'] = ", ".join(pei_data_converted.get('profissionais_nomes_associados', ['N/A']))

            # Carregar Metas (Goals)
            pei_data_converted['goals'] = []
            goals_docs = doc.reference.collection('goals').order_by('descricao').stream()
            for goal_doc in goals_docs:
                goal = convert_doc_to_dict(goal_doc)
                goal_id = goal_doc.id

                # Carregar Alvos (Targets) para cada Meta
                goal['targets'] = []
                targets_docs = goal_doc.reference.collection('targets').order_by('descricao').stream()
                for target_doc in targets_docs:
                    target = convert_doc_to_dict(target_doc)
                    target_id = target_doc.id

                    # Carregar Ajudas (Aids) para cada Alvo
                    target['aids'] = []
                    aids_docs = target_doc.reference.collection('aids').order_by('description').stream()
                    for aid_doc in aids_docs:
                        aid = convert_doc_to_dict(aid_doc)
                        target['aids'].append(aid)
                    goal['targets'].append(target)
                pei_data_converted['goals'].append(goal)

            # Carregar Atividades (Activities)
            pei_data_converted['activities'] = []
            activities_docs = doc.reference.collection('activities').order_by('timestamp', direction=firestore.Query.DESCENDING).stream()
            for activity in activities_docs:
                activity_data = convert_doc_to_dict(activity)
                activity_ts = activity_data.get('timestamp')
                if isinstance(activity_ts, datetime.datetime):
                    activity_data['timestamp_fmt'] = activity_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                elif isinstance(activity_ts, str):
                    try:
                        naive_dt = datetime.datetime.strptime(activity_ts, '%Y-%m-%dT%H:%M:%S')
                        activity_data['timestamp_fmt'] = naive_dt.strftime('%d/%m/%Y %H:%M')
                    except (ValueError, TypeError):
                        activity_data['timestamp_fmt'] = 'Data Inválida'
                else:
                    activity_data['timestamp_fmt'] = 'N/A'
                pei_data_converted['activities'].append(activity_data)

            all_peis.append(pei_data_converted)

        return jsonify({'success': True, 'message': 'Alvo atualizado com sucesso!', 'peis': all_peis}), 200
    except Exception as e:
        print(f"Erro ao atualizar tentativas/status do alvo: {e}")
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500
