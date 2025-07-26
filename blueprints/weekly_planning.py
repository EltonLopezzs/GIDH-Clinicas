from flask import Blueprint, render_template, session, flash, redirect, url_for, request, jsonify
import datetime
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore
from utils import get_db, login_required, SAO_PAULO_TZ, convert_doc_to_dict

weekly_planning_bp = Blueprint('weekly_planning', __name__)

def _convert_doc_references_to_paths(data):
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
    db_instance = get_db()
    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    user_uid = session.get('user_uid')
    
    # Adiciona a URL da logo da clínica à sessão
    try:
        clinica_doc = db_instance.collection('clinicas').document(clinica_id).get()
        if clinica_doc.exists:
            session['clinica_url_logo'] = clinica_doc.to_dict().get('url_logo', '')
        else:
            session['clinica_url_logo'] = ''
    except Exception as e:
        print(f"Erro ao carregar URL da logo da clínica: {e}")
        session['clinica_url_logo'] = ''

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

    patient_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id)
    patient_doc = patient_ref.get()
    if not patient_doc.exists:
        flash("Paciente não encontrado.", "danger")
        return redirect(url_for('listar_pacientes'))
    
    patient_data = convert_doc_to_dict(patient_doc)

    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    filter_professional_id = request.args.get('professional_id')

    today = datetime.datetime.now(SAO_PAULO_TZ)
    if start_date_str:
        try:
            start_date = SAO_PAULO_TZ.localize(datetime.datetime.strptime(start_date_str, '%Y-%m-%d'))
        except ValueError:
            flash("Formato de data inicial inválido. Use YYYY-MM-DD.", "danger")
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
            flash("Formato de data final inválido. Use YYYY-MM-DD.", "danger")
            end_date = start_date + datetime.timedelta(days=6)
            end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
    else:
        end_date = start_date + datetime.timedelta(days=6)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)

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
            pei_title = pei_doc.to_dict().get('titulo', 'PEI sem Título')
            metas_ref = pei_doc.reference.collection('metas')
            metas_docs = metas_ref.stream()
            for meta_doc in metas_docs:
                meta_data = convert_doc_to_dict(meta_doc)
                if meta_data and meta_data.get('status') == 'Ativo':
                    meta_data['id'] = meta_doc.id
                    meta_data['pei_id'] = pei_id
                    meta_data['pei_title'] = pei_title
                    
                    alvos_meta = []
                    alvos_ref = meta_doc.reference.collection('alvos')
                    alvos_docs = alvos_ref.stream()
                    for alvo_doc in alvos_docs:
                        alvo_data = convert_doc_to_dict(alvo_doc)
                        if alvo_data:
                            alvo_data['id'] = alvo_doc.id
                            alvos_meta.append(alvo_data)
                    meta_data['alvos'] = alvos_meta
                    
                    metas_ativas.append(_convert_doc_references_to_paths(meta_data))
    except Exception as e:
        flash(f"Erro ao carregar metas do paciente: {e}", "danger")
        print(f"Erro ao carregar metas: {e}")

    agendamentos_semana = []
    try:
        agendamentos_query = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').where(
            filter=FieldFilter('paciente_id', '==', patient_id)
        ).where(
            filter=FieldFilter('data_agendamento_ts', '>=', start_date)
        ).where(
            filter=FieldFilter('data_agendamento_ts', '<=', end_date)
        )
        
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
                
                ag_data['metas_associadas'] = []
                metas_associadas_ref = ag_doc.reference.collection('metas_associadas')
                metas_associadas_docs = metas_associadas_ref.stream()
                for meta_assoc_doc in metas_associadas_docs:
                    meta_assoc_data = convert_doc_to_dict(meta_assoc_doc)
                    if meta_assoc_data:
                        pei_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis').document(meta_assoc_data.get('pei_id'))
                        pei_doc = pei_ref.get()
                        if pei_doc.exists:
                            meta_assoc_data['pei_title'] = pei_doc.to_dict().get('titulo', 'PEI sem Título')
                        else:
                            meta_assoc_data['pei_title'] = 'PEI não encontrado'
                        ag_data['metas_associadas'].append(meta_assoc_data)

                if ag_data.get('profissional_id'):
                    prof_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(ag_data['profissional_id']).get()
                    if prof_doc.exists:
                        ag_data['profissional_nome'] = prof_doc.to_dict().get('nome', 'Desconhecido')
                    else:
                        ag_data['profissional_nome'] = 'Desconhecido'
                else:
                    ag_data['profissional_nome'] = 'Não Atribuído'
                
                agendamentos_semana.append(_convert_doc_references_to_paths(ag_data))
    except Exception as e:
        flash(f"Erro ao carregar agendamentos da semana: {e}", "danger")
        print(f"Erro ao carregar agendamentos da semana: {e}")

    professionals = []
    if user_role == 'admin':
        try:
            prof_docs = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').stream()
            for prof_doc in prof_docs:
                prof_data = convert_doc_to_dict(prof_doc)
                if prof_data:
                    prof_data['id'] = prof_doc.id
                    professionals.append(prof_data)
        except Exception as e:
            print(f"Erro ao carregar lista de profissionais para admin: {e}")

    logged_in_professional_name = 'N/A'
    if profissional_id_logado:
        try:
            prof_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_id_logado).get()
            if prof_doc.exists:
                logged_in_professional_name = prof_doc.to_dict().get('nome', 'N/A')
        except Exception as e:
            print(f"Erro ao buscar nome do profissional logado: {e}")

    return render_template(
        'weekly_planning.html',
        patient=patient_data,
        metas_ativas=metas_ativas,
        agendamentos_semana=agendamentos_semana,
        current_week_start=start_date.strftime('%Y-%m-%d'),
        current_week_end=end_date.strftime('%Y-%m-%d'),
        is_admin=(user_role == 'admin'),
        is_professional=(user_role == 'profissional'),
        logged_in_professional_id=profissional_id_logado,
        logged_in_professional_name=logged_in_professional_name,
        professionals=professionals
    )

@weekly_planning_bp.route('/api/planejamento_semanal/associar_meta', methods=['POST'])
@login_required
def associar_meta_agendamento():
    db_instance = get_db()
    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    user_uid = session.get('user_uid')

    data = request.json

    agendamento_id = data.get('agendamento_id')
    meta_id = data.get('meta_id')
    meta_nome = data.get('meta_nome')
    pei_id = data.get('pei_id')
    action = data.get('action')

    if not all([agendamento_id, meta_id, meta_nome, pei_id, action]):
        return jsonify({"success": False, "message": "Dados incompletos."}), 400

    try:
        agendamento_ref = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_id)
        
        metas_associadas_subcollection_ref = agendamento_ref.collection('metas_associadas')

        meta_to_associate_data = {
            'meta_id': meta_id,
            'meta_nome': meta_nome,
            'pei_id': pei_id,
            'timestamp': firestore.SERVER_TIMESTAMP
        }

        if action == 'associar':
            existing_meta_query = metas_associadas_subcollection_ref.where(
                filter=FieldFilter('meta_id', '==', meta_id)
            ).limit(1).stream()
            
            existing_meta_docs = list(existing_meta_query)

            if not existing_meta_docs:
                metas_associadas_subcollection_ref.add(meta_to_associate_data)
                return jsonify({"success": True, "message": "Meta associada com sucesso!"}), 200
            else:
                return jsonify({"success": False, "message": "Meta já associada a este agendamento."}), 409
        
        elif action == 'desassociar':
            meta_to_delete_query = metas_associadas_subcollection_ref.where(
                filter=FieldFilter('meta_id', '==', meta_id)
            ).limit(1).stream()
            
            meta_to_delete_docs = list(meta_to_delete_query)

            if meta_to_delete_docs:
                for doc in meta_to_delete_docs:
                    doc.reference.delete()
                return jsonify({"success": True, "message": "Meta desassociada com sucesso!"}), 200
            else:
                return jsonify({"success": False, "message": "Meta não encontrada neste agendamento para desassociar."}), 404
        
        else:
            return jsonify({"success": False, "message": "Ação inválida."}), 400

    except Exception as e:
        return jsonify({"success": False, "message": f"Erro interno do servidor: {e}"}), 500
