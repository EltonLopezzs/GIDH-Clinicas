from flask import Blueprint, render_template, session, flash, redirect, url_for, request, jsonify
import datetime
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore # Importar firestore para DocumentReference
from utils import get_db, login_required, SAO_PAULO_TZ, convert_doc_to_dict

# Cria um novo Blueprint para o planejamento semanal
weekly_planning_bp = Blueprint('weekly_planning', __name__)

def _convert_doc_references_to_paths(data):
    """
    Converte objetos DocumentReference em um dicionário ou lista de dicionários
    para seus respectivos paths (strings) para que possam ser serializados em JSON.
    Esta função é recursiva.
    """
    if isinstance(data, dict):
        return {k: _convert_doc_references_to_paths(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_convert_doc_references_to_paths(elem) for elem in data]
    elif isinstance(data, firestore.DocumentReference):
        return data.path
    else:
        return data

@weekly_planning_bp.route('/pacientes/<patient_id>/planejamento_semanal', methods=['GET'], endpoint='planejamento_semanal')
@login_required
def planejamento_semanal(patient_id):
    """
    Exibe a página de planejamento semanal para um paciente específico.
    Carrega metas ativas e agendamentos da semana para o paciente e profissional logado.
    Permite filtrar por data e, se admin, por profissional.
    """
    db_instance = get_db()
    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    user_uid = session.get('user_uid')
    
    # Obter o profissional_id logado, se não for admin
    profissional_id_logado = None
    if user_role != 'admin':
        try:
            user_doc = db_instance.collection('User').document(user_uid).get()
            if user_doc.exists:
                profissional_id_logado = user_doc.to_dict().get('profissional_id')
            if not profissional_id_logado:
                flash("Sua conta de usuário não está associada a um perfil de profissional. Contate o administrador.", "danger")
                return redirect(url_for('listar_pacientes'))
        except Exception as e:
            flash(f"Erro ao buscar informações do profissional: {e}", "danger")
            return redirect(url_for('listar_pacientes'))

    # 1. Buscar dados do paciente
    patient_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id)
    patient_doc = patient_ref.get()
    if not patient_doc.exists:
        flash("Paciente não encontrado.", "danger")
        return redirect(url_for('listar_pacientes'))
    
    patient_data = convert_doc_to_dict(patient_doc)

    # Obter parâmetros de filtro da requisição
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    filter_professional_id = request.args.get('professional_id')

    # Definir período de datas para a consulta
    today = datetime.datetime.now(SAO_PAULO_TZ)
    if start_date_str:
        try:
            start_date = SAO_PAULO_TZ.localize(datetime.datetime.strptime(start_date_str, '%Y-%m-%d'))
        except ValueError:
            flash("Formato de data inicial inválido. Use YYYY-MM-DD.", "danger")
            start_date = today - datetime.timedelta(days=today.weekday())
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        # Padrão: início da semana atual (segunda-feira)
        start_date = today - datetime.timedelta(days=today.weekday())
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if end_date_str:
        try:
            end_date = SAO_PAULO_TZ.localize(datetime.datetime.strptime(end_date_str, '%Y-%m-%d'))
            end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
        except ValueError:
            flash("Formato de data final inválido. Use YYYY-MM-DD.", "danger")
            end_date = start_date + datetime.timedelta(days=6)
            end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
    else:
        # Padrão: fim da semana atual (domingo)
        end_date = start_date + datetime.timedelta(days=6)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)

    # 2. Buscar metas ativas do paciente (não são afetadas pelos filtros de agendamento)
    metas_ativas = []
    try:
        # Consulta PEIs ativos para o paciente
        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(
            filter=FieldFilter('paciente_id', '==', patient_id)
        ).where(
            filter=FieldFilter('status', '==', 'Ativo')
        )
        
        # Se não for admin, filtra por profissional associado ao PEI
        if user_role != 'admin' and profissional_id_logado:
            peis_query = peis_query.where(
                filter=FieldFilter('profissionais_ids', 'array_contains', profissional_id_logado)
            )

        peis_docs = peis_query.stream()

        for pei_doc in peis_docs:
            pei_id = pei_doc.id
            metas_ref = pei_doc.reference.collection('metas')
            metas_docs = metas_ref.stream()
            for meta_doc in metas_docs:
                meta_data = convert_doc_to_dict(meta_doc)
                if meta_data and meta_data.get('status') == 'Ativo':
                    meta_data['id'] = meta_doc.id
                    meta_data['pei_id'] = pei_id # Adiciona o ID do PEI para referência
                    
                    # Buscar alvos para cada meta
                    alvos_meta = []
                    alvos_ref = meta_doc.reference.collection('alvos')
                    alvos_docs = alvos_ref.stream()
                    for alvo_doc in alvos_docs:
                        alvo_data = convert_doc_to_dict(alvo_doc)
                        if alvo_data:
                            alvo_data['id'] = alvo_doc.id
                            alvos_meta.append(alvo_data)
                    meta_data['alvos'] = alvos_meta
                    
                    # Converte DocumentReferences em meta_data e seus alvos
                    metas_ativas.append(_convert_doc_references_to_paths(meta_data))
    except Exception as e:
        flash(f"Erro ao carregar metas do paciente: {e}", "danger")
        print(f"Erro ao carregar metas: {e}")

    # 3. Buscar agendamentos com filtros
    agendamentos_semana = []
    try:
        agendamentos_query = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').where(
            filter=FieldFilter('paciente_id', '==', patient_id)
        ).where(
            filter=FieldFilter('data_agendamento_ts', '>=', start_date)
        ).where(
            filter=FieldFilter('data_agendamento_ts', '<=', end_date)
        )
        
        # Aplicar filtro de profissional se for admin OU se for um profissional logado
        if user_role == 'admin' and filter_professional_id:
            agendamentos_query = agendamentos_query.where(
                filter=FieldFilter('profissional_id', '==', filter_professional_id)
            )
        elif user_role != 'admin' and profissional_id_logado:
            agendamentos_query = agendamentos_query.where(
                filter=FieldFilter('profissional_id', '==', profissional_id_logado)
            )

        agendamentos_docs = agendamentos_query.order_by('data_agendamento_ts').order_by('hora_agendamento').stream()
        
        for ag_doc in agendamentos_docs:
            ag_data = convert_doc_to_dict(ag_doc)
            if ag_data:
                ag_data['id'] = ag_doc.id
                # Formatar data para exibição
                if ag_data.get('data_agendamento_ts'):
                    ag_data['data_formatada'] = ag_data['data_agendamento_ts'].strftime('%d/%m/%Y')
                
                # NOVO: Buscar metas associadas da subcoleção
                ag_data['metas_associadas'] = []
                metas_associadas_ref = ag_doc.reference.collection('metas_associadas')
                metas_associadas_docs = metas_associadas_ref.stream()
                for meta_assoc_doc in metas_associadas_docs:
                    meta_assoc_data = convert_doc_to_dict(meta_assoc_doc)
                    if meta_assoc_data:
                        ag_data['metas_associadas'].append(meta_assoc_data)

                # Buscar nome do profissional para exibição
                if ag_data.get('profissional_id'):
                    prof_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(ag_data['profissional_id']).get()
                    if prof_doc.exists:
                        ag_data['profissional_nome'] = prof_doc.to_dict().get('nome', 'Desconhecido')
                    else:
                        ag_data['profissional_nome'] = 'Desconhecido'
                else:
                    ag_data['profissional_nome'] = 'Não Atribuído'
                
                # Converte DocumentReferences em ag_data
                agendamentos_semana.append(_convert_doc_references_to_paths(ag_data))
    except Exception as e:
        flash(f"Erro ao carregar agendamentos da semana: {e}", "danger")
        print(f"Erro ao carregar agendamentos da semana: {e}")

    # Buscar todos os profissionais se o usuário for admin
    professionals = []
    if user_role == 'admin':
        try:
            prof_docs = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').stream()
            for prof_doc in prof_docs:
                prof_data = convert_doc_to_dict(prof_doc)
                if prof_data:
                    professionals.append(prof_data)
        except Exception as e:
            print(f"Erro ao carregar lista de profissionais para admin: {e}")

    return render_template(
        'weekly_planning.html',
        patient=patient_data,
        metas_ativas=metas_ativas,
        agendamentos_semana=agendamentos_semana,
        current_week_start=start_date.strftime('%Y-%m-%d'),
        current_week_end=end_date.strftime('%Y-%m-%d'),
        is_admin=(user_role == 'admin'), # Passa a flag de admin para o template
        professionals=professionals # Passa a lista de profissionais para o template
    )

@weekly_planning_bp.route('/api/planejamento_semanal/<patient_id>', methods=['GET'])
@login_required
def get_planning_data(patient_id):
    """
    Endpoint API para buscar dados de planejamento semanal (metas e agendamentos)
    para um paciente e profissional específico.
    """
    db_instance = get_db()
    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    user_uid = session.get('user_uid')

    profissional_id_logado = None
    if user_role != 'admin':
        try:
            user_doc = db_instance.collection('User').document(user_uid).get()
            if user_doc.exists:
                profissional_id_logado = user_doc.to_dict().get('profissional_id')
        except Exception as e:
            return jsonify({"error": f"Erro ao buscar informações do profissional: {e}"}), 500
        
        if not profissional_id_logado:
            return jsonify({"error": "Sua conta de usuário não está associada a um perfil de profissional."}), 403

    # Obter parâmetros de filtro da requisição
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    filter_professional_id = request.args.get('professional_id')

    # Definir período de datas para a consulta
    today = datetime.datetime.now(SAO_PAULO_TZ)
    if start_date_str:
        try:
            start_date = SAO_PAULO_TZ.localize(datetime.datetime.strptime(start_date_str, '%Y-%m-%d'))
        except ValueError:
            start_date = today - datetime.timedelta(days=today.weekday())
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_date = today - datetime.timedelta(days=today.weekday())
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if end_date_str:
        try:
            end_date = SAO_PAULO_TZ.localize(datetime.datetime.strptime(end_date_str, '%Y-%m-%d'))
            end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
        except ValueError:
            end_date = start_date + datetime.timedelta(days=6)
            end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
    else:
        end_date = start_date + datetime.timedelta(days=6)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)

    # Buscar metas ativas
    metas_ativas = []
    try:
        peis_query = db_instance.collection('clinicas').document(clinica_id).collection('peis').where(
            filter=FieldFilter('paciente_id', '==', patient_id)
        ).where(
            filter=FieldFilter('status', '==', 'Ativo')
        )
        if user_role != 'admin' and profissional_id_logado:
            peis_query = peis_query.where(
                filter=FieldFilter('profissionais_ids', 'array_contains', profissional_id_logado)
            )

        peis_docs = peis_query.stream()
        for pei_doc in peis_docs:
            pei_id = pei_doc.id
            metas_ref = pei_doc.reference.collection('metas')
            metas_docs = metas_ref.stream()
            for meta_doc in metas_docs:
                meta_data = convert_doc_to_dict(meta_doc)
                if meta_data and meta_data.get('status') == 'Ativo':
                    meta_data['id'] = meta_doc.id
                    meta_data['pei_id'] = pei_id
                    
                    alvos_meta = []
                    alvos_ref = meta_doc.reference.collection('alvos')
                    alvos_docs = alvos_ref.stream()
                    for alvo_doc in alvos_docs:
                        alvo_data = convert_doc_to_dict(alvo_doc)
                        if alvo_data:
                            alvo_data['id'] = alvo_doc.id
                            alvos_meta.append(alvo_data)
                    meta_data['alvos'] = alvos_meta
                    metas_ativas.append(_convert_doc_references_to_paths(meta_data)) # Aplica a conversão
    except Exception as e:
        print(f"Erro ao carregar metas ativas (API): {e}")
        return jsonify({"error": f"Erro ao carregar metas: {e}"}), 500

    # Buscar agendamentos da semana com filtros
    agendamentos_semana = []
    try:
        agendamentos_query = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').where(
            filter=FieldFilter('paciente_id', '==', patient_id)
        ).where(
            filter=FieldFilter('data_agendamento_ts', '>=', start_date)
        ).where(
            filter=FieldFilter('data_agendamento_ts', '<=', end_date)
        )
        
        # Aplicar filtro de profissional se for admin OU se for um profissional logado
        if user_role == 'admin' and filter_professional_id:
            agendamentos_query = agendamentos_query.where(
                filter=FieldFilter('profissional_id', '==', filter_professional_id)
            )
        elif user_role != 'admin' and profissional_id_logado:
            agendamentos_query = agendamentos_query.where(
                filter=FieldFilter('profissional_id', '==', profissional_id_logado)
            )

        agendamentos_docs = agendamentos_query.order_by('data_agendamento_ts').order_by('hora_agendamento').stream()
        for ag_doc in agendamentos_docs:
            ag_data = convert_doc_to_dict(ag_doc)
            if ag_data:
                ag_data['id'] = ag_doc.id
                if ag_data.get('data_agendamento_ts'):
                    ag_data['data_formatada'] = ag_data['data_agendamento_ts'].strftime('%d/%m/%Y')
                
                # NOVO: Buscar metas associadas da subcoleção
                ag_data['metas_associadas'] = []
                metas_associadas_ref = ag_doc.reference.collection('metas_associadas')
                metas_associadas_docs = metas_associadas_ref.stream()
                for meta_assoc_doc in metas_associadas_docs:
                    meta_assoc_data = convert_doc_to_dict(meta_assoc_doc)
                    if meta_assoc_data:
                        ag_data['metas_associadas'].append(meta_assoc_data)

                # Buscar nome do profissional para exibição
                if ag_data.get('profissional_id'):
                    prof_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(ag_data['profissional_id']).get()
                    if prof_doc.exists:
                        ag_data['profissional_nome'] = prof_doc.to_dict().get('nome', 'Desconhecido')
                    else:
                        ag_data['profissional_nome'] = 'Desconhecido'
                else:
                    ag_data['profissional_nome'] = 'Não Atribuído'

                agendamentos_semana.append(_convert_doc_references_to_paths(ag_data)) # Aplica a conversão
    except Exception as e:
        print(f"Erro ao carregar agendamentos da semana (API): {e}")
        return jsonify({"error": f"Erro ao carinhosamente carregar agendamentos: {e}"}), 500

    return jsonify({
        "metas_ativas": metas_ativas,
        "agendamentos_semana": agendamentos_semana
    })

# NOVO: Endpoint para associar/desassociar meta a um agendamento
@weekly_planning_bp.route('/api/planejamento_semanal/associar_meta', methods=['POST'])
@login_required
def associar_meta_agendamento():
    db_instance = get_db()
    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    user_uid = session.get('user_uid')

    data = request.json
    print(f"DEBUG: Dados recebidos para associar_meta_agendamento: {data}") # DEBUG PRINT

    agendamento_id = data.get('agendamento_id')
    meta_id = data.get('meta_id')
    meta_nome = data.get('meta_nome')
    pei_id = data.get('pei_id')
    action = data.get('action') # 'associar' ou 'desassociar'

    # DEBUG PRINT for individual variables
    print(f"DEBUG: agendamento_id: {agendamento_id}, type: {type(agendamento_id)}")
    print(f"DEBUG: meta_id: {meta_id}, type: {type(meta_id)}")
    print(f"DEBUG: meta_nome: {meta_nome}, type: {type(meta_nome)}")
    print(f"DEBUG: pei_id: {pei_id}, type: {type(pei_id)}")
    print(f"DEBUG: action: {action}, type: {type(action)}")


    if not all([agendamento_id, meta_id, meta_nome, pei_id, action]):
        print("DEBUG: Falha na validação 'not all()'. Um ou mais campos estão faltando ou são falsos.") # DEBUG PRINT
        return jsonify({"success": False, "message": "Dados incompletos."}), 400

    try:
        agendamento_ref = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_id)
        
        # Referência para a subcoleção de metas associadas
        metas_associadas_subcollection_ref = agendamento_ref.collection('metas_associadas')

        meta_to_associate_data = {
            'meta_id': meta_id,
            'meta_nome': meta_nome,
            'pei_id': pei_id,
            'timestamp': firestore.SERVER_TIMESTAMP # Adiciona um timestamp para ordenação, se necessário
        }

        if action == 'associar':
            # Verifica se a meta já está associada para evitar duplicatas
            existing_meta_query = metas_associadas_subcollection_ref.where(
                filter=FieldFilter('meta_id', '==', meta_id)
            ).limit(1).stream()
            
            existing_meta_docs = list(existing_meta_query)

            if not existing_meta_docs:
                # Adiciona um novo documento na subcoleção
                metas_associadas_subcollection_ref.add(meta_to_associate_data)
                print(f"DEBUG: Meta {meta_id} associada com sucesso ao agendamento {agendamento_id} na subcoleção.") # DEBUG PRINT
                return jsonify({"success": True, "message": "Meta associada com sucesso!"}), 200
            else:
                print(f"DEBUG: Meta {meta_id} já associada ao agendamento {agendamento_id}.") # DEBUG PRINT
                return jsonify({"success": False, "message": "Meta já associada a este agendamento."}), 409
        
        elif action == 'desassociar':
            # Busca o documento da meta na subcoleção para deletar
            meta_to_delete_query = metas_associadas_subcollection_ref.where(
                filter=FieldFilter('meta_id', '==', meta_id)
            ).limit(1).stream()
            
            meta_to_delete_docs = list(meta_to_delete_query)

            if meta_to_delete_docs:
                for doc in meta_to_delete_docs:
                    doc.reference.delete() # Deleta o documento da subcoleção
                print(f"DEBUG: Meta {meta_id} desassociada com sucesso do agendamento {agendamento_id} da subcoleção.") # DEBUG PRINT
                return jsonify({"success": True, "message": "Meta desassociada com sucesso!"}), 200
            else:
                print(f"DEBUG: Meta {meta_id} não encontrada no agendamento {agendamento_id} para desassociar.") # DEBUG PRINT
                return jsonify({"success": False, "message": "Meta não encontrada neste agendamento para desassociar."}), 404
        
        else:
            print(f"DEBUG: Ação inválida: {action}.") # DEBUG PRINT
            return jsonify({"success": False, "message": "Ação inválida."}), 400

    except Exception as e:
        print(f"ERROR: Erro ao associar/desassociar meta: {e}") # DEBUG PRINT
        return jsonify({"success": False, "message": f"Erro interno do servidor: {e}"}), 500
