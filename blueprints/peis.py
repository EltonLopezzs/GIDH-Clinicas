from flask import Blueprint, render_template, session, flash, redirect, url_for, request, jsonify
import datetime
import uuid
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore
import pytz # Importar pytz para manipulação de fuso horário

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
                # Log the error but continue processing other professionals
            print(f"Erro ao buscar nome do profissional {prof_id}: {e}")
            professional_names.append(f"Erro ao Carregar Profissional ({prof_id})")

    return ", ".join(professional_names)

def _prepare_pei_for_display(db_instance, clinica_id, pei_doc, all_professionals_map=None):
    """
    Converte um documento PEI em um dicionário e formatar campos para exibição no template,
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
    # Para o PEI principal, a referência deve ser para a clínica.
    # Se o campo 'doc_reference' existir (de dados antigos) e for DocumentReference, converte para string e mantém,
    # caso contrário, define como o caminho da clínica.
    if 'doc_reference' in pei and isinstance(pei['doc_reference'], firestore.DocumentReference):
        pei['doc_reference'] = pei['doc_reference'].path
    elif 'doc_reference' in pei: # Se já for string (de dados antigos)
        pass # Mantém como está
    else:
        pei['doc_reference'] = f'clinicas/{clinica_id}' # Referência para a clínica pai


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
        # A referência da atividade deve ser para o PEI pai
        if 'doc_reference' in activity and isinstance(activity['doc_reference'], firestore.DocumentReference):
            activity['doc_reference'] = activity['doc_reference'].path
        else:
            # Se não for DocumentReference (e.g., string de dados antigos ou ausente),
            # usa o valor existente ou o caminho do PEI.
            activity['doc_reference'] = activity.get('doc_reference') or pei_doc.reference.path

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
        # A referência da meta deve ser para o PEI pai
        if 'doc_reference' in meta and isinstance(meta['doc_reference'], firestore.DocumentReference):
            meta['doc_reference'] = meta['doc_reference'].path
        else:
            meta['doc_reference'] = meta.get('doc_reference') or pei_doc.reference.path

        # Adiciona data_primeira_finalizacao se existir e tenta parsear se for string
        if 'data_primeira_finalizacao' in meta:
            if isinstance(meta['data_primeira_finalizacao'], datetime.datetime):
                meta['data_primeira_finalizacao_fmt'] = meta['data_primeira_finalizacao'].strftime('%d/%m/%Y %H:%M')
            elif isinstance(meta['data_primeira_finalizacao'], str):
                try:
                    # Tenta parsear a string para datetime, assumindo o formato ISO ou outro comum
                    # Se você tem um formato específico, ajuste aqui.
                    naive_dt = datetime.datetime.fromisoformat(meta['data_primeira_finalizacao'])
                    meta['data_primeira_finalizacao'] = SAO_PAULO_TZ.localize(naive_dt) # Localiza a data
                    meta['data_primeira_finalizacao_fmt'] = meta['data_primeira_finalizacao'].strftime('%d/%m/%Y %H:%M')
                except (ValueError, TypeError):
                    meta['data_primeira_finalizacao_fmt'] = 'Data Inválida'
                    meta['data_primeira_finalizacao'] = None # Define como None se não puder parsear
            else:
                meta['data_primeira_finalizacao_fmt'] = 'N/A'
                meta['data_primeira_finalizacao'] = None
        else:
            meta['data_primeira_finalizacao_fmt'] = 'N/A'
            meta['data_primeira_finalizacao'] = None
        
        # Garante que reactivated_count exista no dicionário da meta
        if 'reactivated_count' not in meta:
            meta['reactivated_count'] = 0

        meta['targets'] = [] # Inicializa a lista de alvos para esta meta

        alvos_ref = db_instance.collection(f'clinicas/{clinica_id}/peis/{pei_doc.id}/metas/{meta_doc.id}/alvos')
        alvos_docs = alvos_ref.stream()

        for alvo_doc in alvos_docs:
            alvo = convert_doc_to_dict(alvo_doc)
            alvo['id'] = alvo_doc.id
            # Garante que alvo_id esteja no dicionário, se for salvo como campo
            alvo['alvo_id'] = alvo_doc.id
            # A referência do alvo deve ser para a meta pai
            if 'doc_reference' in alvo and isinstance(alvo['doc_reference'], firestore.DocumentReference):
                alvo['doc_reference'] = alvo['doc_reference'].path
            else:
                alvo['doc_reference'] = alvo.get('doc_reference') or meta_doc.reference.path

            if 'status' not in alvo:
                alvo['status'] = 'Pendente'
            alvo['Concluido'] = (alvo['status'] == 'Finalizado') # Para compatibilidade

            # Busca as ajudas da subcoleção 'ajudas' para cada alvo
            alvo['aids'] = []
            ajudas_ref = db_instance.collection(f'clinicas/{clinica_id}/peis/{pei_doc.id}/metas/{meta_doc.id}/alvos/{alvo_doc.id}/ajudas')
            ajudas_docs = ajudas_ref.stream()
            for ajuda_doc in ajudas_docs:
                ajuda = convert_doc_to_dict(ajuda_doc)
                ajuda['id'] = ajuda_doc.id
                # Garante que ajuda_id esteja no dicionário, se for salvo como campo
                ajuda['ajuda_id'] = ajuda_doc.id
                # A referência da ajuda deve ser para o alvo pai
                if 'doc_reference' in ajuda and isinstance(ajuda['doc_reference'], firestore.DocumentReference):
                    ajuda['doc_reference'] = ajuda['doc_reference'].path
                else:
                    ajuda['doc_reference'] = ajuda.get('doc_reference') or alvo_doc.reference.path

                if 'status' not in ajuda:
                    ajuda['status'] = 'Pendente'
                if 'attempts_count' not in ajuda:
                    ajuda['attempts_count'] = 0
                if 'quant_max' not in ajuda: # Adicionado: Garante que quant_max exista
                    ajuda['quant_max'] = None
                alvo['aids'].append(ajuda)

            meta['targets'].append(alvo)
        pei['goals'].append(meta)

    return pei


# =================================================================
# FUNÇÕES DE TRANSAÇÃO (Helpers para PEI)
# =================================================================

def _recursive_delete_collection(db_instance, coll_ref, batch_size=500):
    """
    Deleta recursivamente documentos e subcoleções de uma coleção,
    gerenciando seus próprios lotes e commits.
    Args:
        db_instance: Instância do Firestore DB.
        coll_ref: Referência da coleção a ser deletada.
        batch_size: Número máximo de documentos a serem deletados em um único lote.
                    (Firestore tem um limite de 500 operações por lote).
    Returns:
        O número total de documentos deletados nesta chamada e suas subcoleções.
    """
    total_deleted = 0
    while True:
        # Stream documents in chunks
        docs = coll_ref.limit(batch_size).stream()
        
        # Collect documents for the current batch and check if any documents were found
        documents_in_current_chunk = list(docs)
        if not documents_in_current_chunk:
            break # No more documents to delete in this collection

        batch = db_instance.batch()
        deleted_in_this_batch_count = 0

        for doc in documents_in_current_chunk:
            # Recursively delete subcollections first
            # Note: doc.reference.collections() is a good general approach,
            # but for known fixed subcollections like 'alvos' and 'ajudas',
            # direct access is more explicit and potentially safer.
            if coll_ref.id == 'metas': # If we are deleting from 'metas' collection
                alvos_sub_coll_ref = doc.reference.collection('alvos')
                print(f"DEBUG: Chamando recursivamente para 'alvos' de meta: {doc.id}")
                total_deleted += _recursive_delete_collection(db_instance, alvos_sub_coll_ref, batch_size)
            elif coll_ref.id == 'alvos': # If we are deleting from 'alvos' collection
                ajudas_sub_coll_ref = doc.reference.collection('ajudas')
                print(f"DEBUG: Chamando recursivamente para 'ajudas' de alvo: {doc.id}")
                total_deleted += _recursive_delete_collection(db_instance, ajudas_sub_coll_ref, batch_size)

            # Handle generic subcollections (if doc.reference.collections() works)
            try:
                for sub_coll_ref_generic in doc.reference.collections():
                    # Avoid re-processing known subcollections that are already handled above
                    if sub_coll_ref_generic.id not in ['alvos', 'ajudas', 'activities', 'metas']:
                        print(f"DEBUG: Chamando recursivamente para subcoleção genérica: {sub_coll_ref_generic.id} de documento: {doc.id}")
                        total_deleted += _recursive_delete_collection(db_instance, sub_coll_ref_generic, batch_size)
            except AttributeError:
                print(f"AVISO: DocumentReference {doc.id} não possui o método 'collections()'. "
                      "Subcoleções genéricas deste documento podem não ser deletadas recursivamente.")
                pass

            # Add the current document to the batch for deletion
            batch.delete(doc.reference)
            deleted_in_this_batch_count += 1

        try:
            batch.commit()
            print(f"DEBUG: Lote de {deleted_in_this_batch_count} documentos da coleção {coll_ref.id} comitado com sucesso.")
            total_deleted += deleted_in_this_batch_count
        except Exception as e:
            print(f"ERROR: Falha ao comitar lote para coleção {coll_ref.id}: {e}")
            raise # Re-raise the exception if batch commit fails

        if deleted_in_this_batch_count < batch_size:
            # If we retrieved fewer documents than batch_size, it means we've reached the end of the collection
            break
    return total_deleted


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
    # A operação de exclusão da meta principal é adicionada à transação.
    transaction.delete(goal_doc_ref)

@firestore.transactional
def _update_target_status_transaction(transaction, target_ref, new_target_status):
    """
    Atualiza o status de um alvo específico.
    Args:
        transaction: Objeto de transação do Firestore.
        target_ref: Referência do documento do alvo.
        new_target_status: Novo status do alvo (Pendente, Andamento, Finalizado).
    Raises:
        Exception: Se o alvo não for encontrado.
    """
    snapshot = target_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise Exception("Alvo não encontrado.")

    updated_data = {'status': new_target_status}
    transaction.update(target_ref, updated_data)

    # Se o alvo for marcado como finalizado, todas as ajudas associadas também são Finalizados.
    if new_target_status == 'Finalizado':
        ajudas_ref = target_ref.collection('ajudas')
        ajudas_docs = ajudas_ref.stream()
        for ajuda_doc in ajudas_docs:
            transaction.update(ajuda_doc.reference, {'status': 'Finalizado'})


@firestore.transactional
def _finalize_goal_transaction(transaction, goal_ref, db_instance):
    """
    Finaliza uma meta específica, marcando-a como 'Manutenção' ou 'Finalizado'
    e atualizando seus alvos e ajudas de acordo com o ciclo de vida.
    Args:
        transaction: Objeto de transação do Firestore.
        goal_ref: Referência do documento da meta.
        db_instance: Instância do Firestore DB (necessário para buscar subcoleções).
    Raises:
        Exception: Se a meta não for encontrada.
    """
    print(f"DEBUG: Iniciando _finalize_goal_transaction para meta: {goal_ref.id}")
    snapshot = goal_ref.get(transaction=transaction)
    if not snapshot.exists:
        print(f"ERROR: Em _finalize_goal_transaction: Meta {goal_ref.id} não encontrada.")
        raise Exception("Meta não encontrada para finalizar.")

    current_meta_data = snapshot.to_dict()
    
    # Get current reactivated_count, default to 0 if not present
    current_reactivated_count = current_meta_data.get('reactivated_count', 0)

    if current_reactivated_count == 0:
        # Primeira vez que a meta é finalizada (vai para Manutenção)
        updated_goal_data = {
            'status': 'Manutenção',
            'data_primeira_finalizacao': datetime.datetime.now(SAO_PAULO_TZ),
            'reactivated_count': 1 # Marca como tendo passado pela primeira finalização
        }
        transaction.update(goal_ref, updated_goal_data)
        print(f"DEBUG: Meta {goal_ref.id} atualizada para status 'Manutenção' e data de primeira finalização. reactivated_count: 1")

        # Resetar todos os alvos e ajudas para 'Pendente' e attempts_count para 0
        alvos_ref = goal_ref.collection('alvos')
        alvos_docs = alvos_ref.stream()

        for alvo_doc in alvos_docs:
            print(f"DEBUG: Resetando alvo {alvo_doc.id} para 'Pendente' dentro da meta {goal_ref.id}.")
            updated_alvo_data = {'status': 'Pendente'}
            transaction.update(alvo_doc.reference, updated_alvo_data)

            ajudas_ref = alvo_doc.reference.collection('ajudas')
            ajudas_docs = ajudas_ref.stream()
            for ajuda_doc in ajudas_docs:
                print(f"DEBUG: Resetando ajuda {ajuda_doc.id} para 'Pendente' e attempts_count=0 dentro do alvo {alvo_doc.id}.")
                transaction.update(ajuda_doc.reference, {'status': 'Pendente', 'attempts_count': 0})
    else:
        # Segunda ou subsequente vez que a meta é finalizada (após reativação)
        updated_goal_data = {'status': 'Finalizado'} # Finalização definitiva
        transaction.update(goal_ref, updated_goal_data)
        print(f"DEBUG: Meta {goal_ref.id} atualizada para status 'Finalizado' (finalização definitiva).")

        # Finalizar todos os alvos e ajudas
        alvos_ref = goal_ref.collection('alvos')
        alvos_docs = alvos_ref.stream()

        for alvo_doc in alvos_docs:
            print(f"DEBUG: Atualizando alvo {alvo_doc.id} para 'Finalizado' dentro da meta {goal_ref.id}.")
            updated_alvo_data = {'status': 'Finalizado'}
            transaction.update(alvo_doc.reference, updated_alvo_data)

            ajudas_ref = alvo_doc.reference.collection('ajudas')
            ajudas_docs = ajudas_ref.stream()
            for ajuda_doc in ajudas_docs:
                print(f"DEBUG: Atualizando ajuda {ajuda_doc.id} para 'Finalizado' dentro do alvo {alvo_doc.id}.")
                transaction.update(ajuda_doc.reference, {'status': 'Finalizado'})
    print(f"DEBUG: Finalizado _finalize_goal_transaction para meta: {goal_ref.id}")


@firestore.transactional
def _reactivate_goal_after_maintenance_transaction(transaction, goal_ref, db_instance):
    """
    Reativa uma meta que estava em 'Manutenção' após 15 dias,
    voltando-a para 'Ativo' e incrementando o contador de reativações.
    Args:
        transaction: Objeto de transação do Firestore.
        goal_ref: Referência do documento da meta.
        db_instance: Instância do Firestore DB (necessário para buscar subcoleções).
    Raises:
        Exception: Se a meta não for encontrada.
    """
    print(f"DEBUG: Iniciando _reactivate_goal_after_maintenance_transaction para meta: {goal_ref.id}")
    snapshot = goal_ref.get(transaction=transaction)
    if not snapshot.exists:
        print(f"ERROR: Em _reactivate_goal_after_maintenance_transaction: Meta {goal_ref.id} não encontrada.")
        raise Exception("Meta não encontrada para reativação.")

    current_meta_data = snapshot.to_dict()
    # Increment reactivated_count when re-activating
    updated_reactivated_count = current_meta_data.get('reactivated_count', 0) + 1

    # Atualiza o status da meta para 'Ativo' e incrementa o contador
    updated_goal_data = {
        'status': 'Ativo',
        'reactivated_count': updated_reactivated_count
    }
    transaction.update(goal_ref, updated_goal_data)
    print(f"DEBUG: Meta {goal_ref.id} atualizada para status 'Ativo' após manutenção. reactivated_count: {updated_reactivated_count}")

    # Resetar todos os alvos para 'Pendente' e suas ajudas para 'Pendente' com attempts_count para 0
    alvos_ref = goal_ref.collection('alvos')
    alvos_docs = alvos_ref.stream()

    for alvo_doc in alvos_docs:
        print(f"DEBUG: Resetando alvo {alvo_doc.id} para 'Pendente' dentro da meta {goal_ref.id} durante reativação.")
        updated_alvo_data = {'status': 'Pendente'}
        transaction.update(alvo_doc.reference, updated_alvo_data)

        ajudas_ref = alvo_doc.reference.collection('ajudas')
        ajudas_docs = ajudas_ref.stream()
        for ajuda_doc in ajudas_docs:
            print(f"DEBUG: Resetando ajuda {ajuda_doc.id} para 'Pendente' e attempts_count=0 dentro do alvo {alvo_doc.id}.")
            transaction.update(ajuda_doc.reference, {'status': 'Pendente', 'attempts_count': 0})
    print(f"DEBUG: Finalizado _reactivate_goal_after_maintenance_transaction para meta: {goal_ref.id}")


@firestore.transactional
def _finalize_pei_transaction(transaction, pei_ref, db_instance):
    """
    Finaliza um PEI, marcando-o como 'finalizado' e todas as suas metas ativas
    e respectivos alvos como 'finalizado'/'Concluido'.
    Args:
        transaction: Objeto de transação do Firestore.
        pei_ref: Referência do documento PEI.
        db_instance: Instância do Firestore DB (necessário para buscar subcoleções).
        Raises:
        Exception: Se o PEI não for encontrado.
    """
    print(f"DEBUG: Iniciando _finalize_pei_transaction para PEI: {pei_ref.id}")
    snapshot = pei_ref.get(transaction=transaction)
    if not snapshot.exists:
        print(f"ERROR: Em _finalize_pei_transaction: PEI {pei_ref.id} não encontrado.")
        raise Exception("PEI não encontrado.")

    try:
        transaction.update(pei_ref, {
            'status': 'finalizado',
            'data_finalizacao': datetime.datetime.now(SAO_PAULO_TZ),
        })
        print(f"DEBUG: PEI {pei_ref.id} atualizado para status 'finalizado' e data de finalização.")
    except Exception as e:
        print(f"ERROR: Falha ao atualizar o documento PEI {pei_ref.id}: {e}")
        raise # Re-raise the exception to fail the transaction

    # Finalizar todas as metas e seus alvos
    metas_ref = pei_ref.collection('metas')
    metas_docs = metas_ref.stream()

    for meta_doc in metas_docs:
        meta_data = meta_doc.to_dict()
        if meta_data.get('status') in ['Ativo', 'Manutenção']: # Inclui metas em manutenção para finalização
            try:
                print(f"DEBUG: Atualizando meta {meta_doc.id} para 'finalizado' dentro do PEI {pei_ref.id}.")
                transaction.update(meta_doc.reference, {'status': 'finalizado'})
            except Exception as e:
                print(f"ERROR: Falha ao atualizar meta {meta_doc.id}: {e}")
                raise # Re-raise the exception

            alvos_ref = meta_doc.reference.collection('alvos')
            alvos_docs = alvos_ref.stream()

            for alvo_doc in alvos_docs:
                try:
                    print(f"DEBUG: Atualizando alvo {alvo_doc.id} para 'Finalizado' dentro da meta {meta_doc.id}.")
                    updated_alvo_data = {'status': 'Finalizado'}
                    transaction.update(alvo_doc.reference, updated_alvo_data)
                except Exception as e:
                    print(f"ERROR: Falha ao atualizar alvo {alvo_doc.id}: {e}")
                    raise # Re-raise the exception

                # Atualizar ajudas na subcoleção do alvo
                ajudas_ref = alvo_doc.reference.collection('ajudas')
                ajudas_docs = ajudas_ref.stream()
                for ajuda_doc in ajudas_docs:
                    try:
                        print(f"DEBUG: Atualizando ajuda {ajuda_doc.id} para 'Finalizado' dentro do alvo {alvo_doc.id}.")
                        transaction.update(ajuda_doc.reference, {'status': 'Finalizado'})
                    except Exception as e:
                        print(f"ERROR: Falha ao atualizar ajuda {ajuda_doc.id}: {e}")
                        raise # Re-raise the exception
        else:
            print(f"DEBUG: Meta {meta_doc.id} já está Finalizado ou inativa, pulando atualização.")
    print(f"DEBUG: Finalizado _finalize_pei_transaction para PEI: {pei_ref.id}")


@firestore.transactional
def _add_target_to_goal_transaction(transaction, goal_ref, new_target_description, selected_aids_data):
    """
    Adiciona um novo alvo a uma meta existente dentro de um PEI, com ajudas selecionadas e quant_max.
    Args:
        transaction: Objeto de transação do Firestore.
        goal_ref: Referência do documento da meta.
        new_target_description: Descrição do novo alvo.
        selected_aids_data: Lista de dicionários com as ajudas selecionadas e suas quant_max.
    Raises:
        Exception: Se a meta não for encontrada.
    """
    snapshot = goal_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise Exception("Meta não encontrada.")

    # Cria o novo alvo como um documento na subcoleção 'alvos'
    new_alvo_data = {
        'descricao': new_target_description,
        'status': 'Pendente',
        'meta_id': goal_ref.id,
        'pei_id': goal_ref.parent.parent.id # Obtém o ID do PEI pai
    }
    # Obtém uma nova referência de documento para o alvo dentro da transação
    alvo_doc_ref = goal_ref.collection('alvos').document()
    new_alvo_data['alvo_id'] = alvo_doc_ref.id # Adiciona o ID do alvo ao dado
    # Adiciona o doc_reference para o alvo, referenciando a meta pai
    new_alvo_data['doc_reference'] = goal_ref # Salva a referência do documento (DocumentReference)
    transaction.set(alvo_doc_ref, new_alvo_data) # Usa transaction.set() para adicionar o alvo

    # Adicionando as ajudas selecionadas com suas quant_max
    for aid_data in selected_aids_data:
        ajuda_doc_ref = alvo_doc_ref.collection('ajudas').document()
        # Cria uma cópia para não modificar o dicionário original da lista de ajudas
        aid_to_save = aid_data.copy()
        aid_to_save['ajuda_id'] = ajuda_doc_ref.id
        aid_to_save['pei_id'] = new_alvo_data['pei_id']
        aid_to_save['meta_id'] = new_alvo_data['meta_id']
        aid_to_save['alvo_id'] = new_alvo_data['alvo_id']
        aid_to_save['doc_reference'] = alvo_doc_ref
        
        # Garante que status e attempts_count existam, se não vierem do frontend
        if 'status' not in aid_to_save:
            aid_to_save['status'] = 'Pendente'
        if 'attempts_count' not in aid_to_save:
            aid_to_save['attempts_count'] = 0

        transaction.set(ajuda_doc_ref, aid_to_save)


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
    # Adiciona o doc_reference para a atividade, referenciando o PEI pai
    new_activity_data['doc_reference'] = pei_ref # Salva a referência do documento (DocumentReference)
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
        # Se o alvo for marcado como finalizado, todas as ajudas devem ser Finalizados
        if new_target_status == 'Finalizado':
            ajudas_ref = target_ref.collection('ajudas')
            ajudas_docs = ajudas_ref.stream()
            for ajuda_doc in ajudas_docs:
                transaction.update(ajuda_doc.reference, {'status': 'Finalizado'})

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


@firestore.transactional
def _activate_goal_transaction(transaction, goal_ref, db_instance):
    """
    Ativa uma meta específica, marcando-a como 'Ativo'
    e todos os seus alvos e ajudas como Pendentes.
    Args:
        transaction: Objeto de transação do Firestore.
        goal_ref: Referência do documento da meta.
        db_instance: Instância do Firestore DB (necessário para buscar subcoleções).
    Raises:
        Exception: Se a meta não for encontrada.
    """
    print(f"DEBUG: Iniciando _activate_goal_transaction para meta: {goal_ref.id}")
    snapshot = goal_ref.get(transaction=transaction)
    if not snapshot.exists:
        print(f"ERROR: Em _activate_goal_transaction: Meta {goal_ref.id} não encontrada.")
        raise Exception("Meta não encontrada para ativar.")

    updated_goal_data = {'status': 'Ativo'}
    transaction.update(goal_ref, updated_goal_data)
    print(f"DEBUG: Meta {goal_ref.id} atualizada para status 'Ativo'.")

    # Atualizar alvos na subcoleção
    alvos_ref = goal_ref.collection('alvos')
    alvos_docs = alvos_ref.stream()

    for alvo_doc in alvos_docs:
        print(f"DEBUG: Atualizando alvo {alvo_doc.id} para 'Pendente' dentro da meta {goal_ref.id}.")
        updated_alvo_data = {'status': 'Pendente'}
        transaction.update(alvo_doc.reference, updated_alvo_data)

        # Atualizar ajudas na subcoleção do alvo
        ajudas_ref = alvo_doc.reference.collection('ajudas')
        ajudas_docs = ajudas_ref.stream()
        for ajuda_doc in ajudas_docs:
            print(f"DEBUG: Atualizando ajuda {ajuda_doc.id} para 'Pendente' dentro do alvo {alvo_doc.id}.")
            transaction.update(ajuda_doc.reference, {'status': 'Pendente'})
    print(f"DEBUG: Finalizado _activate_goal_transaction para meta: {goal_ref.id}")


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

    # Definir as ajudas disponíveis para seleção
    available_aids = [
        {'sigla': 'AFT', 'description': 'Ajuda Física Total'},
        {'sigla': 'AFP', 'description': 'Ajuda Física Parcial'},
        {'sigla': 'AG', 'description': 'Ajuda Gestual'},
        {'sigla': 'AE', 'description': 'Ajuda Ecóica'},
        {'sigla': 'I', 'description': 'Independente'},
    ]

    # Obter PEIs do paciente
    try:
        peis_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis')
        peis_query = peis_ref.where(filter=FieldFilter('paciente_id', '==', paciente_doc_id))

        if is_professional and not is_admin:
            peis_query = peis_query.where(
                filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id)
            )

        peis_query = peis_query.order_by('data_criacao', direction=firestore.Query.DESCENDING)

        for pei_doc in peis_query.stream():
            # Antes de preparar para exibição, verificar e reativar metas se necessário
            metas_ref = db_instance.collection(f'clinicas/{clinica_id}/peis/{pei_doc.id}/metas')
            metas_docs = metas_ref.stream()

            for meta_doc in metas_docs:
                meta_data = meta_doc.to_dict() # Obter os dados brutos do Firestore
                
                # Verifica se a meta está em 'Manutenção' e se tem uma data de primeira finalização
                if meta_data.get('status') == 'Manutenção' and \
                   'data_primeira_finalizacao' in meta_data and \
                   meta_data['data_primeira_finalizacao'] is not None:
                    
                    first_finalization_date = meta_data['data_primeira_finalizacao']
                    
                    # Tenta converter a string para datetime.datetime se for uma string
                    if isinstance(first_finalization_date, str):
                        try:
                            # Assumindo o formato ISO que utils.py usa para formatar
                            naive_dt = datetime.datetime.fromisoformat(first_finalization_date)
                            first_finalization_date = SAO_PAULO_TZ.localize(naive_dt)
                        except (ValueError, TypeError):
                            print(f"AVISO: data_primeira_finalizacao para meta {meta_doc.id} é uma string inválida e não pode ser parseada. Valor: '{first_finalization_date}'. Não será reativada automaticamente.")
                            first_finalization_date = None # Define como None se não puder parsear

                    if isinstance(first_finalization_date, datetime.datetime):
                        if first_finalization_date.tzinfo is None:
                            # Assume naive datetimes from Firestore are in SAO_PAULO_TZ if not specified
                            first_finalization_date = SAO_PAULO_TZ.localize(first_finalization_date)
                        else:
                            # If it has tzinfo, convert to SAO_PAULO_TZ for consistent comparison
                            first_finalization_date = first_finalization_date.astimezone(SAO_PAULO_TZ)

                        time_difference = datetime.datetime.now(SAO_PAULO_TZ) - first_finalization_date
                        print(f"DEBUG: Meta {meta_doc.id} - first_finalization_date: {first_finalization_date}, current_time: {datetime.datetime.now(SAO_PAULO_TZ)}, time_difference_days: {time_difference.days}")

                        if time_difference.days >= 15:
                            print(f"DEBUG: Meta {meta_doc.id} precisa ser reativada. Diferença: {time_difference.days} dias.")
                            try:
                                transaction = db_instance.transaction()
                                _reactivate_goal_after_maintenance_transaction(transaction, meta_doc.reference, db_instance)
                                transaction.commit()
                                flash(f"Meta '{meta_data.get('descricao', meta_doc.id)}' reativada automaticamente após manutenção.", "info")
                                # Re-fetch the meta data after transaction to ensure updated status is reflected
                                meta_doc = meta_doc.reference.get()
                            except Exception as e:
                                print(f"ERROR: Falha ao reativar meta {meta_doc.id}: {e}")
                                flash(f"Erro ao reativar meta '{meta_data.get('descricao', meta_doc.id)}'.", "danger")
                    else:
                        # Este bloco será atingido se first_finalization_date for None após a tentativa de parse
                        print(f"AVISO: data_primeira_finalizacao para meta {meta_doc.id} não é um objeto datetime.datetime válido. Tipo: {type(first_finalization_date)}. Não será reativada automaticamente.")


            # Agora prepare o PEI para exibição, que incluirá o status da meta potencialmente atualizado
            # É importante usar pei_doc.reference.get() aqui para garantir que os dados mais recentes sejam usados
            # caso uma meta tenha sido reativada na iteração acima.
            updated_pei_doc_for_display = pei_doc.reference.get()
            pei = _prepare_pei_for_display(db_instance, clinica_id, updated_pei_doc_for_display, profissionais_map)
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
                           all_professionals=profissionais_lista,
                           available_aids=available_aids # Passa as ajudas disponíveis para o template
                           )


@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/add', methods=['POST'], endpoint='add_pei')
@login_required
@admin_required
def add_pei(paciente_doc_id):
    db_instance = get_db()
    clinica_id = session['clinica_id'] # Corrigido de session['clinica'] para session['clinica_id']
    user_role = session.get('user_role') # Explicitly define user_role
    is_admin = user_role == 'admin' # Explicitly define is_admin

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
        clinica_ref = db_instance.collection('clinicas').document(clinica_id) # Referência para o documento da clínica

        new_pei_data = {
            'paciente_id': paciente_doc_id,
            'titulo': titulo,
            'data_criacao': data_criacao_obj,
            'status': 'Ativo',
            'criado_em': datetime.datetime.now(SAO_PAULO_TZ),
            'profissional_criador_nome': session.get('user_name', 'N/A'),
            'profissionais_ids': profissionais_ids_selecionados,
            'doc_reference': clinica_ref # Adiciona a referência para a clínica
        }

        # Adiciona o PEI e obtém a referência do documento
        _, pei_doc_ref = peis_ref.add(new_pei_data)

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
            metas_ref = pei_ref.collection('metas')
            _recursive_delete_collection(db_instance, metas_ref) # _recursive_delete_collection now manages its own batches
            print(f"Exclusão de metas concluída para PEI: {pei_id}")

            print(f"Iniciando exclusão recursiva de atividades para PEI: {pei_id}")
            activities_ref = pei_ref.collection('activities')
            _recursive_delete_collection(db_instance, activities_ref) # _recursive_delete_collection now manages its own batches
            print(f"Exclusão de atividades concluída para PEI: {pei_id}")

            # Delete the main PEI document directly after subcollections are deleted
            pei_ref.delete()
            print(f"PEI principal {pei_id} excluído com sucesso.")
            flash('PEI excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir PEI: {e}', 'danger')
        print(f"Erro crítico ao excluir PEI {pei_id}: {str(e)}") # Usando str(e) para evitar problemas de formatação
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
            else:
                print(f"Documento de usuário não encontrado para UID: {user_uid}")
        except Exception as e:
            print(f"Erro ao buscar ID do profissional para o usuário {user_uid}: {e}")
            return jsonify({'success': False, 'message': f'Erro ao verificar permissões: {e}'}), 500

    try:
        data = request.get_json()
        pei_id = data.get('pei_id')
        print(f"Requisição de finalização recebida para PEI ID: {pei_id}")
        if not pei_id:
            print("Erro: ID do PEI não fornecido na requisição.")
            return jsonify({'success': False, 'message': 'ID do PEI não fornecido.'}), 400

        pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
        pei_doc = pei_ref.get()
        if not pei_doc.exists:
            print(f"Erro: PEI com ID {pei_id} não encontrado no Firestore.")
            return jsonify({'success': False, 'message': 'PEI não encontrado.'}), 404

        if not is_admin:
            associated_professionals_ids = pei_doc.to_dict().get('profissionais_ids', [])
            print(f"Profissionais associados ao PEI: {associated_professionals_ids}")
            if logged_in_professional_id not in associated_professionals_ids:
                return jsonify({'success': False, 'message': 'Você não tem permissão para finalizar este PEI.'}), 403
            print(f"Permissão concedida: Usuário é administrador.")
        else:
            print("Permissão concedida: Usuário é administrador.")

        print(f"Iniciando transação de finalização para PEI: {pei_id}")
        transaction = db_instance.transaction()
        _finalize_pei_transaction(transaction, pei_ref, db_instance)
        transaction.commit()
        print(f"Transação de finalização para PEI {pei_id} concluída com sucesso.")

        all_peis = []
        profissionais_map = {}
        profissionais_docs = db_instance.collection(f'clinicas/{clinica_id}/profissionais').stream()
        for doc in profissionais_docs:
            profissionais_map[doc.id] = doc.to_dict().get('nome', 'N/A')

        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
        if not is_admin and logged_in_professional_id:
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

        for doc in peis_query.stream():
            # Re-fetch the PEI document to ensure latest state after potential reactivation
            updated_pei_doc = doc.reference.get()
            pei_data_converted = _prepare_pei_for_display(db_instance, clinica_id, updated_pei_doc, profissionais_map)
            all_peis.append(pei_data_converted)

        return jsonify({'success': True, 'message': 'PEI finalizado com sucesso!', 'peis': all_peis}), 200
    except Exception as e:
        print(f"Erro crítico ao finalizar PEI {pei_id}: {str(e)}")
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/add_goal', methods=['POST'], endpoint='add_goal')
@login_required
@admin_required
def add_goal(paciente_doc_id):
    db_instance = get_db()
    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    is_admin = user_role == 'admin'

    try:
        data = request.form
        pei_id = data.get('pei_id')
        descricao_goal = data.get('descricao')
        targets_desc = request.form.getlist('targets[]')
        
        # Coleta as ajudas selecionadas e suas quant_max
        selected_aids_data = []
        # As chaves virão no formato 'aid_selected_AFT', 'aid_quant_max_AFT'
        # Itera sobre as siglas das ajudas fixas para verificar quais foram selecionadas
        fixed_aids_template = [
            {'description': 'Ajuda Física Total', 'sigla': 'AFT', 'id_ordenacao': 1},
            {'description': 'Ajuda Física Parcial', 'sigla': 'AFP', 'id_ordenacao': 2},
            {'description': 'Ajuda Gestual', 'sigla': 'AG', 'id_ordenacao': 3},
            {'description': 'Ajuda Ecóica', 'sigla': 'AE', 'id_ordenacao': 4},
            {'description': 'IndePendente', 'sigla': 'I', 'id_ordenacao': 5},
        ]
        
        for aid_info in fixed_aids_template:
            sigla = aid_info['sigla']
            if data.get(f'aid_selected_{sigla}') == 'on': # Verifica se o checkbox foi marcado
                quant_max_str = data.get(f'aid_quant_max_{sigla}')
                # Corrigido: Se quant_max_str não for válido, define como 1
                quant_max = int(quant_max_str) if quant_max_str and quant_max_str.isdigit() else 1 
                
                selected_aids_data.append({
                    'description': aid_info['description'],
                    'sigla': sigla,
                    'id_ordenacao': aid_info['id_ordenacao'],
                    'quant_max': quant_max,
                    'attempts_count': 0, # Inicializa com 0 tentativas
                    'status': 'Pendente' # Inicializa como Pendente
                })

        # Adicionado: Verifica se pelo menos uma ajuda foi selecionada
        if not selected_aids_data:
            flash('Pelo menos uma Ajuda deve ser selecionada para a meta.', 'danger')
            return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))

        if not pei_id or not descricao_goal:
            flash('Dados insuficientes para adicionar meta.', 'danger')
            return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))

        pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
        metas_ref = pei_ref.collection('metas')

        new_goal_data = {
            'descricao': descricao_goal.strip(),
            'status': 'Ativo',
            'pei_id': pei_id,
            'data_primeira_finalizacao': None, # Novo campo para a data da primeira finalização
            'reactivated_count': 0 # Inicializa o contador de reativações
        }

        meta_doc_ref = metas_ref.document()
        new_goal_data['meta_id'] = meta_doc_ref.id
        new_goal_data['doc_reference'] = pei_ref
        meta_doc_ref.set(new_goal_data)

        for desc in targets_desc:
            if desc.strip():
                new_alvo_data = {
                    'descricao': desc.strip(),
                    'status': 'Pendente',
                    'meta_id': meta_doc_ref.id,
                    'pei_id': pei_id
                }
                alvo_doc_ref = meta_doc_ref.collection('alvos').document()
                new_alvo_data['alvo_id'] = alvo_doc_ref.id
                new_alvo_data['doc_reference'] = meta_doc_ref
                alvo_doc_ref.set(new_alvo_data)

                # Adiciona apenas as ajudas selecionadas para este alvo
                for aid_data in selected_aids_data:
                    ajuda_doc_ref = alvo_doc_ref.collection('ajudas').document()
                    aid_to_save = aid_data.copy() # Copia para não modificar o original
                    aid_to_save['ajuda_id'] = ajuda_doc_ref.id
                    aid_to_save['pei_id'] = pei_id
                    aid_to_save['meta_id'] = meta_doc_ref.id
                    aid_to_save['alvo_id'] = alvo_doc_ref.id
                    aid_to_save['doc_reference'] = alvo_doc_ref
                    
                    # Garante que status e attempts_count existam, se não vierem do frontend
                    if 'status' not in aid_to_save:
                        aid_to_save['status'] = 'Pendente'
                    if 'attempts_count' not in aid_to_save:
                        aid_to_save['attempts_count'] = 0

                    # Use transaction.set() if this is part of a larger transaction or if you want to ensure atomicity
                    # For now, keeping it as .set() if not explicitly in a transaction context
                    # However, if this is called from add_goal, it should be part of a transaction.
                    # The original code for add_goal does not use a transaction for adding targets/aids,
                    # which might be a point of improvement for data consistency.
                    # For now, I'll keep the .set() as is, assuming it's intended to be outside a transaction
                    # or that the transaction is managed at a higher level if this function is called by one.
                    ajuda_doc_ref.set(aid_to_save)


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
    user_role = session.get('user_role')
    is_admin = user_role == 'admin'

    try:
        data = request.get_json()
        pei_id = data.get('pei_id')
        goal_id = data.get('goal_id')
        target_description = data.get('target_description')
        selected_aids_data = data.get('selected_aids', []) # Recebe as ajudas selecionadas do frontend

        if not all([pei_id, goal_id, target_description]):
            return jsonify({'success': False, 'message': 'Dados insuficientes para adicionar alvo.'}), 400

        goal_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id).collection('metas').document(goal_id)

        transaction = db_instance.transaction()
        _add_target_to_goal_transaction(transaction, goal_ref, target_description, selected_aids_data)
        transaction.commit()

        all_peis = []
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
            # Re-fetch the PEI document to ensure latest state after potential reactivation
            updated_pei_doc = doc.reference.get()
            pei_data_converted = _prepare_pei_for_display(db_instance, clinica_id, updated_pei_doc, profissionais_map)
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
    user_role = session.get('user_role')
    is_admin = user_role == 'admin'

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

            # Delete recursively the 'alvos' subcollection of the goal, including their 'ajudas' subcollections
            print(f"Iniciando exclusão recursiva de alvos para meta: {goal_id}")
            alvos_ref = goal_doc_ref.collection('alvos')
            _recursive_delete_collection(db_instance, alvos_ref) # _recursive_delete_collection now manages its own batches
            print(f"Exclusão de alvos concluída para meta: {goal_id}")

            # Delete the main goal document directly after subcollections are deleted
            goal_doc_ref.delete()
            print(f"Meta principal {goal_id} excluída com sucesso.")

            flash('Meta excluída com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir meta: {e}', 'danger')
        print(f"Erro crítico ao excluir meta {goal_id}: {str(e)}")
    return redirect(url_for('peis.ver_peis_paciente', paciente_doc_id=paciente_doc_id))

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/delete_target', methods=['POST'], endpoint='delete_target')
@login_required
@admin_required
def delete_target(paciente_doc_id):
    db_instance = get_db()
    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    is_admin = user_role == 'admin'

    try:
        data = request.get_json()
        pei_id = data.get('pei_id')
        goal_id = data.get('goal_id')
        target_id = data.get('target_id')

        print(f"Tentando excluir alvo {target_id} da meta {goal_id} do PEI {pei_id}")

        if not all([pei_id, goal_id, target_id]):
            return jsonify({'success': False, 'message': 'Dados insuficientes para excluir alvo.'}), 400

        target_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id).collection('metas').document(goal_id).collection('alvos').document(target_id)
        target_doc_snapshot = target_ref.get()

        if not target_doc_snapshot.exists:
            return jsonify({'success': False, 'message': 'Alvo não encontrado para exclusão.'}), 404

        if not is_admin:
            # Check if the logged-in professional is associated with the PEI
            pei_doc = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id).get()
            if not pei_doc.exists:
                return jsonify({'success': False, 'message': 'PEI associado não encontrado.'}), 404
            associated_professionals_ids = pei_doc.to_dict().get('profissionais_ids', [])
            logged_in_professional_id = None
            if session.get('user_uid'):
                user_doc = db_instance.collection('User').document(session.get('user_uid')).get()
                if user_doc.exists:
                    logged_in_professional_id = user_doc.to_dict().get('profissional_id')

            if logged_in_professional_id not in associated_professionals_ids:
                return jsonify({'success': False, 'message': 'Você não tem permissão para excluir este alvo.'}), 403

        # Delete recursively the 'ajudas' subcollection of the target
        print(f"Iniciando exclusão recursiva de ajudas para alvo: {target_id}")
        ajudas_ref = target_ref.collection('ajudas')
        _recursive_delete_collection(db_instance, ajudas_ref)
        print(f"Exclusão de ajudas concluída para alvo: {target_id}")

        # Delete the main target document
        target_ref.delete()
        print(f"Alvo principal {target_id} excluído com sucesso.")

        # Re-fetch all PEIs for the patient and return them
        all_peis = []
        profissionais_map = {}
        profissionais_docs = db_instance.collection(f'clinicas/{clinica_id}/profissionais').stream()
        for doc in profissionais_docs:
            profissionais_map[doc.id] = doc.to_dict().get('nome', 'N/A')

        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)).order_by('data_criacao', direction=firestore.Query.DESCENDING)
        if not is_admin and logged_in_professional_id:
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', logged_in_professional_id))

        for doc in peis_query.stream():
            # Re-fetch the PEI document to ensure latest state after potential reactivation
            updated_pei_doc = doc.reference.get()
            pei_data_converted = _prepare_pei_for_display(db_instance, clinica_id, updated_pei_doc, profissionais_map)
            all_peis.append(pei_data_converted)

        return jsonify({'success': True, 'message': 'Alvo excluído com sucesso!', 'peis': all_peis}), 200

    except Exception as e:
        print(f"Erro crítico ao excluir alvo {target_id}: {str(e)}")
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

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
            else:
                print(f"Documento de usuário não encontrado para UID: {user_uid}")
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
            # Re-fetch the PEI document to ensure latest state after potential reactivation
            updated_pei_doc = doc.reference.get()
            pei_data_converted = _prepare_pei_for_display(db_instance, clinica_id, updated_pei_doc, profissionais_map)
            all_peis.append(pei_data_converted)

        return jsonify({'success': True, 'message': 'Meta Finalizado com sucesso!', 'peis': all_peis}), 200
    except Exception as e:
        print(f"Erro ao finalizar meta: {e}")
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500

@peis_bp.route('/pacientes/<string:paciente_doc_id>/peis/activate_goal', methods=['POST'], endpoint='activate_goal')
@login_required
def activate_goal(paciente_doc_id):
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
            else:
                print(f"Documento de usuário não encontrado para UID: {user_uid}")
        except Exception as e:
            return jsonify({'success': False, 'message': f'Erro ao verificar permissões: {e}'}), 500

    try:
        data = request.get_json()
        pei_id = data.get('pei_id')
        goal_id = data.get('goal_id')
        if not all([pei_id, goal_id]):
            return jsonify({'success': False, 'message': 'Dados insuficientes para ativar meta.'}), 400

        goal_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id).collection('metas').document(goal_id)
        goal_doc = goal_ref.get()
        if not goal_doc.exists:
            return jsonify({'success': False, 'message': 'Meta não encontrada.'}), 404

        # Verifica permissão do profissional associado ao PEI
        pei_doc = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(pei_id).get()
        if not is_admin:
            associated_professionals_ids = pei_doc.to_dict().get('profissionais_ids', [])
            if logged_in_professional_id not in associated_professionals_ids:
                return jsonify({'success': False, 'message': 'Você não tem permissão para ativar esta meta.'}), 403

        transaction = db_instance.transaction()
        _activate_goal_transaction(transaction, goal_ref, db_instance)
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
            # Re-fetch the PEI document to ensure latest state after potential reactivation
            updated_pei_doc = doc.reference.get()
            pei_data_converted = _prepare_pei_for_display(db_instance, clinica_id, updated_pei_doc, profissionais_map)
            all_peis.append(pei_data_converted)

        return jsonify({'success': True, 'message': 'Meta ativada com sucesso!', 'peis': all_peis}), 200
    except Exception as e:
        print(f"Erro ao ativar meta: {e}")
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
            else:
                print(f"Documento de usuário não encontrado para UID: {user_uid}")
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
            # Re-fetch the PEI document to ensure latest state after potential reactivation
            updated_pei_doc = doc.reference.get()
            pei_data_converted = _prepare_pei_for_display(db_instance, clinica_id, updated_pei_doc, profissionais_map)
            all_peis.append(pei_data_converted)

        return jsonify({'success': True, 'message': 'Alvo atualizado com sucesso!', 'peis': all_peis}), 200
    except Exception as e:
        print(f"Erro ao atualizar tentativas/status do alvo: {e}")
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500
