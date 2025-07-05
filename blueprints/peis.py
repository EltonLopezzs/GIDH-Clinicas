# blueprints/peis.py
from flask import Blueprint, render_template, session, flash, redirect, url_for, request, jsonify
import datetime
import uuid
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore

# Importe as suas funções utilitárias.
from utils import get_db, login_required, admin_required, SAO_PAULO_TZ, convert_doc_to_dict

peis_bp = Blueprint('peis', __name__)

# --- FUNÇÕES AUXILIARES ---
def _format_professional_names(db_instance, clinica_id, professional_ids):
    """
    Formata uma string com os nomes dos profissionais dados seus IDs.
    Busca os nomes dos profissionais no Firestore.
    """
    if not professional_ids:
        return 'N/A'
    
    professional_names = []
    for prof_id in professional_ids:
        try:
            prof_doc = db_instance.collection(f'clinicas/{clinica_id}/profissionais').document(prof_id).get()
            if prof_doc.exists:
                professional_names.append(prof_doc.to_dict().get('nome', 'N/A'))
            else:
                professional_names.append(f"Profissional Desconhecido ({prof_id})")
        except Exception as e:
            print(f"Erro ao buscar nome do profissional {prof_id}: {e}")
            professional_names.append(f"Erro ao Carregar Profissional ({prof_id})")
    
    return ", ".join(professional_names)

def _prepare_pei_for_display(db_instance, clinica_id, pei_doc, all_professionals_map=None):
    """
    Converte um documento PEI em um dicionário e formata campos para exibição no template,
    incluindo metas, alvos e ajudas de subcoleções.
    Args:
        db_instance: Instância do Firestore DB.
        clinica_id: ID da clínica.
        pei_doc: DocumentSnapshot do PEI.
        all_professionals_map: Opcional. Um dicionário de {id: nome} de todos os profissionais para lookup rápido.
    Returns:
        Dicionário formatado do PEI com metas e alvos aninhados.
    """
    pei = convert_doc_to_dict(pei_doc)
    pei['id'] = pei_doc.id # Adiciona o ID do PEI

    # Formata data de criação
    if 'data_criacao' in pei and isinstance(pei['data_criacao'], datetime.datetime):
        pei['data_criacao_iso'] = pei['data_criacao'].isoformat()
        pei['data_criacao'] = pei['data_criacao'].strftime('%d/%m/%Y %H:%M')
    else:
        pei['data_criacao'] = pei.get('data_criacao', 'N/A')
        pei['data_criacao_iso'] = None

    # Formata nomes dos profissionais associados usando os IDs
    prof_ids = pei.get('profissionais_ids', [])
    if all_professionals_map:
        pei['profissionais_nomes_associados_fmt'] = ", ".join(
            [all_professionals_map.get(prof_id, f"Profissional Desconhecido ({prof_id})") for prof_id in prof_ids]
        ) if prof_ids else 'N/A'
    else:
        pei['profissionais_nomes_associados_fmt'] = _format_professional_names(db_instance, clinica_id, prof_ids)

    # Busca atividades da subcoleção 'activities'
    pei['activities'] = []
    activities_ref = db_instance.collection(f'clinicas/{clinica_id}/peis/{pei_doc.id}/activities')
    activities_docs = activities_ref.order_by('timestamp', direction=firestore.Query.ASCENDING).stream()
    for activity_doc in activities_docs:
        activity = convert_doc_to_dict(activity_doc)
        activity['id'] = activity_doc.id
        if 'timestamp' in activity and isinstance(activity['timestamp'], datetime.datetime):
            activity['timestamp_fmt'] = activity['timestamp'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
        elif isinstance(activity.get('timestamp'), str):
            try:
                naive_dt = datetime.datetime.strptime(activity['timestamp'], '%Y-%m-%dT%H:%M:%S')
                activity['timestamp_fmt'] = naive_dt.strftime('%d/%m/%Y %H:%M')
            except (ValueError, TypeError):
                activity['timestamp_fmt'] = 'Data Inválida'
        else:
            activity['timestamp_fmt'] = 'N/A'
        pei['activities'].append(activity)
    
    # Busca metas e alvos das subcoleções
    pei['goals'] = []
    metas_ref = db_instance.collection(f'clinicas/{clinica_id}/peis/{pei_doc.id}/metas')
    metas_docs = metas_ref.stream()

    for meta_doc in metas_docs:
        meta = convert_doc_to_dict(meta_doc)
        meta['id'] = meta_doc.id
        # Garante que meta_id esteja no dicionário, se for salvo como campo
        meta['meta_id'] = meta_doc.id 
        meta['targets'] = [] # Inicializa a lista de alvos para esta meta

        alvos_ref = db_instance.collection(f'clinicas/{clinica_id}/peis/{pei_doc.id}/metas/{meta_doc.id}/alvos')
        alvos_docs = alvos_ref.stream()

        for alvo_doc in alvos_docs:
            alvo = convert_doc_to_dict(alvo_doc)
            alvo['id'] = alvo_doc.id
            # Garante que alvo_id esteja no dicionário, se for salvo como campo
            alvo['alvo_id'] = alvo_doc.id
            if 'status' not in alvo:
                alvo['status'] = 'pendente'
            alvo['concluido'] = (alvo['status'] == 'finalizada') # Para compatibilidade

            # Busca as ajudas da subcoleção 'ajudas' para cada alvo
            alvo['aids'] = []
            ajudas_ref = db_instance.collection(f'clinicas/{clinica_id}/peis/{pei_doc.id}/metas/{meta_doc.id}/alvos/{alvo_doc.id}/ajudas')
            ajudas_docs = ajudas_ref.stream()
            for ajuda_doc in ajudas_docs:
                ajuda = convert_doc_to_dict(ajuda_doc)
                ajuda['id'] = ajuda_doc.id
                # Garante que ajuda_id esteja no dicionário, se for salvo como campo
                ajuda['ajuda_id'] = ajuda_doc.id
                if 'status' not in ajuda:
                    ajuda['status'] = 'pendente'
                if 'attempts_count' not in ajuda:
                    ajuda['attempts_count'] = 0
                alvo['aids'].append(ajuda)

            meta['targets'].append(alvo)
        pei['goals'].append(meta)

    return pei


# =================================================================
# FUNÇÕES DE TRANSAÇÃO (Helpers para PEI)
# =================================================================

def _recursive_delete_collection(db_instance, coll_ref, batch_size=50):
    """
    Deleta recursivamente documentos e subcoleções de uma coleção.
    Args:
        db_instance: Instância do Firestore DB.
        coll_ref: Referência da coleção a ser deletada.
        batch_size: Número de documentos a serem deletados por lote.
    """
    docs = coll_ref.limit(batch_size).stream()
    deleted = 0
    for doc in docs:
        # Deleta subcoleções primeiro
        # Usando doc.reference.collections() que é o método recomendado em versões mais recentes
        # Adicionado try-except para compatibilidade com versões mais antigas que podem não ter 'collections()'
        try:
            for sub_coll_ref in doc.reference.collections():
                print(f"Deletando subcoleção: {sub_coll_ref.id} de documento {doc.id}")
                _recursive_delete_collection(db_instance, sub_coll_ref)
        except AttributeError:
            print(f"AVISO: DocumentReference {doc.id} não possui o método 'collections()'. "
                  "Isso pode indicar uma versão desatualizada da biblioteca google-cloud-firestore. "
                  "Subcoleções deste documento podem não ser deletadas recursivamente.")
            # Se 'collections()' não estiver disponível, não podemos deletar subcoleções genéricas.
            # As subcoleções conhecidas (alvos, ajudas) são tratadas por chamadas explícitas nas rotas de exclusão.
            pass

        try:
            doc.reference.delete()
            print(f"Documento deletado: {doc.id}")
        except Exception as e:
            print(f"Erro ao deletar documento {doc.id}: {e}")
            # Loga o erro, mas continua se possível, ou relança se for crítico
            raise # Relança para garantir que a transação/operação falhe se um documento não puder ser deletado
        deleted += 1
    if deleted >= batch_size:
        print(f"Deletados {deleted} documentos, verificando por mais no caminho: {coll_ref.path}")
        _recursive_delete_collection(db_instance, coll_ref, batch_size)

@firestore.transactional
def _delete_goal_transaction(transaction, pei_ref, goal_id_to_delete, db_instance):
    """
    Deleta uma meta específica de um PEI e suas subcoleções (alvos).
    Args:
        transaction: Objeto de transação do Firestore.
        pei_ref: Referência do documento PEI.
        goal_id_to_delete: ID da meta a ser deletada.
        db_instance: Instância do Firestore DB (necessário para deletar subcoleções).
    Raises:
        Exception: Se a meta não for encontrada.
    """
    goal_doc_ref = pei_ref.collection('metas').document(goal_id_to_delete)
    goal_snapshot = goal_doc_ref.get(transaction=transaction)

    if not goal_snapshot.exists:
        raise Exception("Meta não encontrada para exclusão.")
    
    # A deleção recursiva das subcoleções 'alvos' e 'ajudas' será feita na rota Flask.
    transaction.delete(goal_doc_ref)

@firestore.transactional
def _update_target_status_transaction(transaction, target_ref, new_target_status):
    """
    Atualiza o status de um alvo específico.
    Args:
        transaction: Objeto de transação do Firestore.
        target_ref: Referência do documento do alvo.
        new_target_status: Novo status do alvo (pendente, andamento, finalizada).
    Raises:
        Exception: Se o alvo não for encontrado.
    """
    snapshot = target_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise Exception("Alvo não encontrado.")
    
    updated_data = {'status': new_target_status}
    transaction.update(target_ref, updated_data)

    # Se o alvo for marcado como finalizado, todas as ajudas associadas também são finalizadas.
    if new_target_status == 'finalizada':
        ajudas_ref = target_ref.collection('ajudas')
        ajudas_docs = ajudas_ref.stream()
        for ajuda_doc in ajudas_docs:
            transaction.update(ajuda_doc.reference, {'status': 'finalizada'})


@firestore.transactional
def _finalize_goal_transaction(transaction, goal_ref, db_instance):
    """
    Finaliza uma meta específica, marcando-a como 'finalizado'
    e todos os seus alvos e ajudas como concluídos/finalizados.
    Args:
        transaction: Objeto de transação do Firestore.
        goal_ref: Referência do documento da meta.
        db_instance: Instância do Firestore DB (necessário para buscar subcoleções).
    Raises:
        Exception: Se a meta não for encontrada.
    """
    snapshot = goal_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise Exception("Meta não encontrada para finalizar.")

    updated_goal_data = {'status': 'finalizado'}
    transaction.update(goal_ref, updated_goal_data)

    # Atualizar alvos na subcoleção
    alvos_ref = goal_ref.collection('alvos')
    alvos_docs = alvos_ref.stream()

    for alvo_doc in alvos_docs:
        updated_alvo_data = {'status': 'finalizada'}
        transaction.update(alvo_doc.reference, updated_alvo_data)

        # Atualizar ajudas na subcoleção do alvo
        ajudas_ref = alvo_doc.reference.collection('ajudas')
        ajudas_docs = ajudas_ref.stream()
        for ajuda_doc in ajudas_docs:
            transaction.update(ajuda_doc.reference, {'status': 'finalizada'})


@firestore.transactional
def _finalize_pei_transaction(transaction, pei_ref, db_instance):
    """
    Finaliza um PEI, marcando-o como 'finalizado' e todas as suas metas ativas
    e respectivos alvos como 'finalizado'/'concluido'.
    Args:
        transaction: Objeto de transação do Firestore.
        pei_ref: Referência do documento PEI.
        db_instance: Instância do Firestore DB (necessário para buscar subcoleções).
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

    # Finalizar todas as metas e seus alvos
    metas_ref = pei_ref.collection('metas')
    metas_docs = metas_ref.stream()

    for meta_doc in metas_docs:
        meta_data = meta_doc.to_dict()
        if meta_data.get('status') == 'ativo':
            transaction.update(meta_doc.reference, {'status': 'finalizado'})

            alvos_ref = meta_doc.reference.collection('alvos')
            alvos_docs = alvos_ref.stream()

            for alvo_doc in alvos_docs:
                updated_alvo_data = {'status': 'finalizada'}
                transaction.update(alvo_doc.reference, updated_alvo_data)

                # Atualizar ajudas na subcoleção do alvo
                ajudas_ref = alvo_doc.reference.collection('ajudas')
                ajudas_docs = ajudas_ref.stream()
                for ajuda_doc in ajudas_docs:
                    transaction.update(ajuda_doc.reference, {'status': 'finalizada'})


@firestore.transactional
def _add_target_to_goal_transaction(transaction, goal_ref, new_target_description):
    """
    Adiciona um novo alvo a uma meta existente dentro de um PEI.
    Args:
        transaction: Objeto de transação do Firestore.
        goal_ref: Referência do documento da meta.
        new_target_description: Descrição do novo alvo.
    Raises:
        Exception: Se a meta não for encontrada.
    """
    snapshot = goal_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise Exception("Meta não encontrada.")

    # Cria o novo alvo como um documento na subcoleção 'alvos'
    new_alvo_data = {
        'descricao': new_target_description,
        'status': 'pendente',
        'meta_id': goal_ref.id,
        'pei_id': goal_ref.parent.parent.id # Obtém o ID do PEI pai
    }
    # Obtém uma nova referência de documento para o alvo dentro da transação
    alvo_doc_ref = goal_ref.collection('alvos').document()
    new_alvo_data['alvo_id'] = alvo_doc_ref.id # Adiciona o ID do alvo ao dado
    transaction.set(alvo_doc_ref, new_alvo_data) # Usa transaction.set() para adicionar o alvo

    # Definindo as ajudas fixas para o novo alvo e adicionando-as como subcoleção
    fixed_aids = [
        {'description': 'Ajuda Física Total', 'attempts_count': 0, 'status': 'pendente'},
        {'description': 'Ajuda Física Parcial', 'attempts_count': 0, 'status': 'pendente'},
        {'description': 'Ajuda Gestual', 'attempts_count': 0, 'status': 'pendente'},
        {'description': 'Ajuda Ecóica', 'attempts_count': 0, 'status': 'pendente'},
        {'description': 'Independente', 'attempts_count': 0, 'status': 'pendente'},
    ]
    for aid_data in fixed_aids:
        # Adiciona cada ajuda como um documento na subcoleção 'ajudas' do alvo
        # Obtém uma nova referência de documento para a ajuda dentro da transação
        aid_doc_ref = alvo_doc_ref.collection('ajudas').document()
        aid_data['ajuda_id'] = aid_doc_ref.id # Adiciona o ID da ajuda ao dado
        # Adiciona os IDs dos ancestrais (pei_id, meta_id, alvo_id) ao documento da ajuda
        aid_data['pei_id'] = new_alvo_data['pei_id']
        aid_data['meta_id'] = new_alvo_data['meta_id']
        aid_data['alvo_id'] = new_alvo_data['alvo_id']
        transaction.set(aid_doc_ref, aid_data) # Usa transaction.set() para adicionar a ajuda


@firestore.transactional
def _add_pei_activity_transaction(transaction, pei_ref, activity_content, user_name):
    """
    Adiciona uma nova atividade ao histórico de atividades de um PEI como um documento em subcoleção.
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

    new_activity_data = {
        'content': activity_content,
        'timestamp': datetime.datetime.now(SAO_PAULO_TZ),
        'user_name': user_name,
        'pei_id': pei_ref.id # Adiciona o ID do PEI
    }
    # Obtém uma nova referência de documento para a atividade dentro da transação
    activity_doc_ref = pei_ref.collection('activities').document()
    new_activity_data['activity_id'] = activity_doc_ref.id # Adiciona o ID da atividade ao dado
    transaction.set(activity_doc_ref, new_activity_data) # Usa transaction.set() para adicionar a atividade

@firestore.transactional
def _update_target_and_aid_data_transaction(transaction, target_ref, aid_id=None, new_attempts_count=None, new_target_status=None):
    """
    Atualiza os dados de um alvo específico ou de uma ajuda dentro de um alvo no PEI.
    Pode atualizar a contagem de tentativas de uma ajuda ou o status geral de um alvo.
    Args:
        transaction: Objeto de transação do Firestore.
        target_ref: Referência do documento do alvo.
        aid_id: Opcional. ID da ajuda específica a ser atualizada.
        new_attempts_count: Opcional. O novo valor TOTAL da contagem de tentativas para a ajuda.
        new_target_status: Opcional. Novo status geral do alvo.
    Raises:
        Exception: Se o alvo ou a ajuda não forem encontrados, ou se houver erro de tipo.
    """
    snapshot = target_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise Exception("Alvo não encontrado.")

    # Atualiza o status geral do alvo, se fornecido
    if new_target_status is not None:
        transaction.update(target_ref, {'status': new_target_status})
        # Se o alvo for marcado como finalizado, todas as ajudas devem ser finalizadas
        if new_target_status == 'finalizada':
            ajudas_ref = target_ref.collection('ajudas')
            ajudas_docs = ajudas_ref.stream()
            for ajuda_doc in ajudas_docs:
                transaction.update(ajuda_doc.reference, {'status': 'finalizada'})

    # Atualiza dados de uma ajuda específica, se aid_id for fornecido
    if aid_id is not None:
        aid_ref = target_ref.collection('ajudas').document(aid_id)
        aid_snapshot = aid_ref.get(transaction=transaction)
        if not aid_snapshot.exists:
            raise Exception("Ajuda (Aid) não encontrada no alvo.")
        
        if new_attempts_count is not None:
            try:
                # Define a contagem de tentativas para o novo valor fornecido
                transaction.update(aid_ref, {'attempts_count': max(0, int(new_attempts_count))})
            except (ValueError, TypeError) as e:
                raise Exception(f"Valor inválido para tentativas: {new_attempts_count}. Erro: {e}")


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

    # Obter lista de profissionais para o dropdown no modal de criação de PEI e para lookup de nomes
    profissionais_lista = []
    profissionais_map = {}
    try:
        profissionais_docs = db_instance.collection(f'clinicas/{clinica_id}/profissionais').order_by('nome').stream()
        for doc in profissionais_docs:
            prof_data = doc.to_dict()
            if prof_data:
                profissionais_lista.append({'id': doc.id, 'nome': prof_data.get('nome', 'N/A')})
                profissionais_map[doc.id] = prof_data.get('nome', 'N/A')
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
            pei = _prepare_pei_for_display(db_instance, clinica_id, pei_doc, profissionais_map)
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

        peis_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis')

        new_pei_data = {
            'paciente_id': paciente_doc_id,
            'titulo': titulo,
            'data_criacao': data_criacao_obj,
            'status': 'ativo',
            'criado_em': datetime.datetime.now(SAO_PAULO_TZ),
            'profissional_criador_nome': session.get('user_name', 'N/A'),
            'profissionais_ids': profissionais_ids_selecionados,
        }
        
        # Adiciona o PEI e obtém a referência do documento
        _, pei_doc_ref = peis_ref.add(new_pei_data)
        
        # Salva o ID do PEI no próprio documento, conforme solicitado
        pei_doc_ref.update({'pei_id': pei_doc_ref.id})

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
    user_role = session.get('user_role') # Explicitly define user_role
    is_admin = user_role == 'admin' # Explicitly define is_admin
    
    try:
        pei_id = request.form.get('pei_id')
        print(f"Tentando excluir PEI com ID: {pei_id} para clínica: {clinica_id}")
        if not pei_id:
            flash('ID do PEI não fornecido.', 'danger')
            print("Erro: ID do PEI não fornecido para exclusão.")
        else:
            pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
            
            # Verifica se o PEI existe antes de tentar deletar subcoleções
            pei_doc_snapshot = pei_ref.get()
            if not pei_doc_snapshot.exists:
                flash('PEI não encontrado para exclusão.', 'danger')
                print(f"Erro: PEI com ID {pei_id} não encontrado.")
                return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))


            print(f"Iniciando exclusão recursiva de metas para PEI: {pei_id}")
            metas_ref = pei_ref.collection('metas') # This is a CollectionReference
            _recursive_delete_collection(db_instance, metas_ref) # This call is correct
            print(f"Exclusão de metas concluída para PEI: {pei_id}")

            print(f"Iniciando exclusão recursiva de atividades para PEI: {pei_id}")
            activities_ref = pei_ref.collection('activities') # This is a CollectionReference
            _recursive_delete_collection(db_instance, activities_ref) # This call is correct
            print(f"Exclusão de atividades concluída para PEI: {pei_id}")

            # Finalmente, deletar o documento PEI
            pei_ref.delete()
            print(f"PEI principal deletado: {pei_id}")
            flash('PEI excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir PEI: {e}', 'danger')
        print(f"Erro crítico ao excluir PEI {pei_id}: {e}")
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

        transaction = db_instance.transaction()
        _finalize_pei_transaction(transaction, pei_ref, db_instance)
        transaction.commit()

        all_peis = []
        profissionais_map = {}
        profissionais_docs = db_instance.collection(f'clinicas/{clinica_id}/profissionais').stream()
        for doc in profissionais_docs:
            profissionais_map[doc.id] = doc.to_dict().get('nome', 'N/A')

        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
        if not is_admin and logged_in_professional_id:
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

        for doc in peis_query.stream():
            pei_data_converted = _prepare_pei_for_display(db_instance, clinica_id, doc, profissionais_map)
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
    user_role = session.get('user_role') # Explicitly define user_role
    is_admin = user_role == 'admin' # Explicitly define is_admin

    try:
        data = request.form
        pei_id = data.get('pei_id')
        descricao_goal = data.get('descricao')
        targets_desc = request.form.getlist('targets[]')

        if not pei_id or not descricao_goal:
            flash('Dados insuficientes para adicionar meta.', 'danger')
            return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))

        pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
        metas_ref = pei_ref.collection('metas')

        new_goal_data = {
            'descricao': descricao_goal.strip(),
            'status': 'ativo',
            'pei_id': pei_id
        }
        
        meta_doc_ref = metas_ref.document()
        new_goal_data['meta_id'] = meta_doc_ref.id
        meta_doc_ref.set(new_goal_data)

        fixed_aids_template = [
            {'description': 'Ajuda Física Total', 'attempts_count': 0, 'status': 'pendente'},
            {'description': 'Ajuda Física Parcial', 'attempts_count': 0, 'status': 'pendente'},
            {'description': 'Ajuda Gestual', 'attempts_count': 0, 'status': 'pendente'},
            {'description': 'Ajuda Ecóica', 'attempts_count': 0, 'status': 'pendente'},
            {'description': 'Independente', 'attempts_count': 0, 'status': 'pendente'},
        ]
        for desc in targets_desc:
            if desc.strip():
                new_alvo_data = {
                    'descricao': desc.strip(),
                    'status': 'pendente',
                    'meta_id': meta_doc_ref.id,
                    'pei_id': pei_id
                }
                alvo_doc_ref = meta_doc_ref.collection('alvos').document()
                new_alvo_data['alvo_id'] = alvo_doc_ref.id
                alvo_doc_ref.set(new_alvo_data)

                for aid_data in fixed_aids_template:
                    ajuda_doc_ref = alvo_doc_ref.collection('ajudas').document()
                    aid_data['ajuda_id'] = ajuda_doc_ref.id
                    aid_data['pei_id'] = pei_id
                    aid_data['meta_id'] = meta_doc_ref.id
                    aid_data['alvo_id'] = alvo_doc_ref.id
                    ajuda_doc_ref.set(aid_data)

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
    user_role = session.get('user_role') # Explicitly define user_role
    is_admin = user_role == 'admin' # Explicitly define is_admin

    try:
        data = request.get_json()
        pei_id = data.get('pei_id')
        goal_id = data.get('goal_id')
        target_description = data.get('target_description')

        if not all([pei_id, goal_id, target_description]):
            return jsonify({'success': False, 'message': 'Dados insuficientes para adicionar alvo.'}), 400

        goal_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id).collection('metas').document(goal_id)
        
        transaction = db_instance.transaction()
        _add_target_to_goal_transaction(transaction, goal_ref, target_description)
        transaction.commit()

        all_peis = []
        # user_role = session.get('user_role') # Redundant here, already defined above
        logged_in_professional_id = None
        if user_role == 'medico':
            user_doc = db_instance.collection('User').document(session.get('user_uid')).get()
            if user_doc.exists:
                logged_in_professional_id = user_doc.to_dict().get('profissional_id')

        profissionais_map = {}
        profissionais_docs = db_instance.collection(f'clinicas/{clinica_id}/profissionais').stream()
        for doc in profissionais_docs:
            profissionais_map[doc.id] = doc.to_dict().get('nome', 'N/A')

        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
        if not is_admin and logged_in_professional_id:
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

        for doc in peis_query.stream():
            pei_data_converted = _prepare_pei_for_display(db_instance, clinica_id, doc, profissionais_map)
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
    user_role = session.get('user_role') # Explicitly define user_role
    is_admin = user_role == 'admin' # Explicitly define is_admin

    try:
        pei_id = request.form.get('pei_id')
        goal_id = request.form.get('goal_id')
        print(f"Tentando excluir meta {goal_id} do PEI {pei_id}")
        if not pei_id or not goal_id:
            flash('Dados insuficientes para excluir meta.', 'danger')
            print("Erro: Dados insuficientes para excluir meta.")
        else:
            pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
            goal_doc_ref = pei_ref.collection('metas').document(goal_id)

            goal_doc_snapshot = goal_doc_ref.get()
            if not goal_doc_snapshot.exists:
                flash('Meta não encontrada para exclusão.', 'danger')
                print(f"Erro: Meta com ID {goal_id} não encontrada no PEI {pei_id}.")
                return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))

            # Deletar recursivamente a subcoleção 'alvos' da meta, incluindo suas subcoleções 'ajudas'
            print(f"Iniciando exclusão recursiva de alvos para meta: {goal_id}")
            alvos_ref = goal_doc_ref.collection('alvos')
            _recursive_delete_collection(db_instance, alvos_ref)
            print(f"Exclusão de alvos concluída para meta: {goal_id}")

            # Deletar o documento da meta
            transaction = db_instance.transaction()
            _delete_goal_transaction(transaction, pei_ref, goal_id, db_instance)
            transaction.commit()
            print(f"Meta principal deletada: {goal_id}")
            
            flash('Meta excluída com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir meta: {e}', 'danger')
        print(f"Erro crítico ao excluir meta {goal_id}: {e}")
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

        goal_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id).collection('metas').document(goal_id)
        goal_doc = goal_ref.get()
        if not goal_doc.exists:
            return jsonify({'success': False, 'message': 'Meta não encontrada.'}), 404

        # Verifica permissão do profissional associado ao PEI
        pei_doc = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id).get()
        if not is_admin:
            associated_professionals_ids = pei_doc.to_dict().get('profissionais_ids', [])
            if logged_in_professional_id not in associated_professionals_ids:
                return jsonify({'success': False, 'message': 'Você não tem permissão para finalizar esta meta.'}), 403

        transaction = db_instance.transaction()
        _finalize_goal_transaction(transaction, goal_ref, db_instance)
        transaction.commit()

        all_peis = []
        profissionais_map = {}
        profissionais_docs = db_instance.collection(f'clinicas/{clinica_id}/profissionais').stream()
        for doc in profissionais_docs:
            profissionais_map[doc.id] = doc.to_dict().get('nome', 'N/A')

        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
        if not is_admin and logged_in_professional_id:
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

        for doc in peis_query.stream():
            pei_data_converted = _prepare_pei_for_display(db_instance, clinica_id, doc, profissionais_map)
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
        transaction = db_instance.transaction()
        _add_pei_activity_transaction(transaction, pei_ref, activity_content, user_name)
        transaction.commit()

        all_peis = []
        profissionais_map = {}
        profissionais_docs = db_instance.collection(f'clinicas/{clinica_id}/profissionais').stream()
        for doc in profissionais_docs:
            profissionais_map[doc.id] = doc.to_dict().get('nome', 'N/A')

        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
        if not is_admin and logged_in_professional_id:
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

        for doc in peis_query.stream():
            pei_data_converted = _prepare_pei_for_display(db_instance, clinica_id, doc, profissionais_map)
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

        target_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id).collection('metas').document(goal_id).collection('alvos').document(target_id)
        target_doc = target_ref.get()
        if not target_doc.exists:
            return jsonify({'success': False, 'message': 'Alvo não encontrado.'}), 404

        # Verifica permissão do profissional associado ao PEI
        pei_doc = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id).get()
        if not is_admin:
            associated_professionals_ids = pei_doc.to_dict().get('profissionais_ids', [])
            if logged_in_professional_id not in associated_professionals_ids:
                return jsonify({'success': False, 'message': 'Você não tem permissão para atualizar este alvo.'}), 403

        transaction = db_instance.transaction()
        _update_target_and_aid_data_transaction(transaction, target_ref, aid_id, new_attempts_count, new_target_status)
        transaction.commit()

        all_peis = []
        profissionais_map = {}
        profissionais_docs = db_instance.collection(f'clinicas/{clinica_id}/profissionais').stream()
        for doc in profissionais_docs:
            profissionais_map[doc.id] = doc.to_dict().get('nome', 'N/A')

        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
        if not is_admin and logged_in_professional_id:
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

        for doc in peis_query.stream():
            pei_data_converted = _prepare_pei_for_display(db_instance, clinica_id, doc, profissionais_map)
            all_peis.append(pei_data_converted)

        return jsonify({'success': True, 'message': 'Alvo atualizado com sucesso!', 'peis': all_peis}), 200
    except Exception as e:
        print(f"Erro ao atualizar tentativas/status do alvo: {e}")
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

