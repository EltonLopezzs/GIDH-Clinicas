import datetime
from flask import Blueprint, render_template, session, flash, redirect, url_for, request, jsonify, current_app
from google.cloud.firestore_v1.base_query import FieldFilter
from decorators.auth_decorators import login_required # Import decorators
from utils.firestore_utils import convert_doc_to_dict # Import utility functions

medical_records_bp = Blueprint('medical_records_bp', __name__)

@medical_records_bp.route('/prontuarios')
@login_required
def buscar_prontuario():
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    pacientes_para_busca = []
    search_query = request.args.get('search_query', '').strip()

    try:
        pacientes_ref = db.collection('clinicas').document(clinica_id).collection('pacientes')
        query = pacientes_ref.order_by('nome')

        docs = query.stream()
        for doc in docs:
            paciente_data = doc.to_dict()
            if paciente_data:
                paciente_data['id'] = doc.id
                if not search_query or search_query.lower() in paciente_data.get('nome', '').lower() or search_query in paciente_data.get('cpf', ''):
                    pacientes_para_busca.append(paciente_data)
            
    except Exception as e:
        flash(f'Erro ao carregar lista de pacientes para busca: {e}.', 'danger')
        print(f"Erro search_patient_record: {e}")

    return render_template('prontuario_busca.html', pacientes_para_busca=pacientes_para_busca, search_query=search_query)

@medical_records_bp.route('/prontuarios/<string:paciente_doc_id>')
@login_required
def ver_prontuario(paciente_doc_id):
    db = current_app.config['DB']
    SAO_PAULO_TZ = current_app.config['SAO_PAULO_TZ']

    clinica_id = session['clinica_id']
    paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
    convenios_ref = db.collection('clinicas').document(clinica_id).collection('convenios')

    paciente_data = None
    registros_prontuario = []
    peis_individuais_paciente = [] 

    try:
        paciente_doc = paciente_ref.get()
        if not paciente_doc.exists:
            flash('Paciente não encontrado.', 'danger')
            return redirect(url_for('medical_records_bp.buscar_prontuario'))

        paciente_data_raw = paciente_doc.to_dict()
        paciente_data = convert_doc_to_dict(paciente_doc)

        # Buscar nome do convênio para o paciente
        convenio_nome_paciente = 'Particular'
        if paciente_data_raw.get('convenio_id'):
            convenio_doc = convenios_ref.document(paciente_data_raw['convenio_id']).get()
            if convenio_doc.exists:
                convenio_nome_paciente = convenio_doc.to_dict().get('nome', 'Convênio Desconhecido')
        paciente_data['convenio_nome'] = convenio_nome_paciente

        peis_individuais_docs = paciente_ref.collection('peis_individuais').order_by('identificacao_pei').stream()
        for doc in peis_individuais_docs:
            pei_data = convert_doc_to_dict(doc)
            if 'metas' in pei_data and isinstance(pei_data['metas'], list):
                for meta in pei_data['metas']:
                    if 'status' not in meta:
                        meta['status'] = 'Não Iniciada'
                    if 'tempo_total_gasto' not in meta:
                        meta['tempo_total_gasto'] = 0
                    if 'cronometro_inicio' not in meta:
                        meta['cronometro_inicio'] = None
            peis_individuais_paciente.append(pei_data)

        prontuarios_docs = paciente_ref.collection('prontuarios').order_by('data_registro', direction=current_app.config['DB'].Query.DESCENDING).stream()
        for doc in prontuarios_docs:
            registros_prontuario.append(convert_doc_to_dict(doc))

    except Exception as e:
        flash(f'Erro fatal ao carregar prontuário: {e}.', 'danger')
        print(f"!!! ERRO FATAL em ver_prontuario: {e}")
        return redirect(url_for('medical_records_bp.buscar_prontuario'))

    return render_template('prontuario.html',
                            paciente=paciente_data,
                            registros=registros_prontuario,
                            peis_do_paciente=peis_individuais_paciente)

@medical_records_bp.route('/api/pacientes/<string:paciente_doc_id>/agendamentos', methods=['GET'])
@login_required
def api_paciente_agendamentos(paciente_doc_id):
    db = current_app.config['DB']
    SAO_PAULO_TZ = current_app.config['SAO_PAULO_TZ']

    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    user_uid = session.get('user_uid')
    
    agendamentos_paciente = []
    
    try:
        profissional_id_logado = None
        if user_role != 'admin':
            prof_doc = db.collection('User').document(user_uid).get()
            profissional_id_logado = prof_doc.to_dict().get('profissional_id') if prof_doc.exists else None
            if not profissional_id_logado:
                return jsonify({'success': False, 'message': 'Usuário não associado a um profissional.'}), 403

        query = db.collection('clinicas').document(clinica_id).collection('agendamentos') \
            .where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)) \
            .order_by('data_agendamento_ts', direction=current_app.config['DB'].Query.DESCENDING) \
            .limit(20)

        if user_role != 'admin':
            query = query.where(filter=FieldFilter('profissional_id', '==', professional_id_logado))

        docs = query.stream()
        for doc in docs:
            ag = doc.to_dict()
            if ag:
                ag['id'] = doc.id
                if ag.get('data_agendamento_ts'):
                    ag['data_agendamento_fmt'] = ag['data_agendamento_ts'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y')
                else:
                    ag['data_agendamento_fmt'] = 'N/A'
                
                ag['status'] = ag.get('status', 'pendente').lower() 
                agendamentos_paciente.append(ag)
        
        return jsonify({'success': True, 'agendamentos': agendamentos_paciente})

    except Exception as e:
        print(f"Erro API ao carregar agendamentos para paciente {paciente_doc_id}: {e}")
        return jsonify({'success': False, 'message': f'Erro interno do servidor: {e}'}), 500


@medical_records_bp.route('/agendamentos/<string:agendamento_id>/evolucao', methods=['GET'])
@login_required
def ver_evolucao_agendamento(agendamento_id):
    db = current_app.config['DB']
    SAO_PAULO_TZ = current_app.config['SAO_PAULO_TZ']

    clinica_id = session['clinica_id']
    agendamento_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_id)
    
    mensagens_evolucao = []

    try:
        agendamento_doc = agendamento_ref.get()
        if not agendamento_doc.exists:
            return jsonify({'success': False, 'message': 'Agendamento não encontrado.'}), 404
        
        agendamento_data = agendamento_doc.to_dict()
        if not agendamento_data:
            return jsonify({'success': False, 'message': 'Dados do agendamento ausentes.'}), 500

        if session.get('user_role') != 'admin':
            user_uid = session.get('user_uid')
            prof_doc = db.collection('User').document(user_uid).get()
            profissional_id_logado = prof_doc.to_dict().get('profissional_id') if prof_doc.exists else None

            if agendamento_data.get('profissional_id') != profissional_id_logado:
                return jsonify({'success': False, 'message': 'Acesso negado.'}), 403

        evolucao_ref = agendamento_ref.collection('evolucao')
        evolucao_docs = evolucao_ref.order_by('data_registro').stream()
        
        for doc in evolucao_docs:
            msg = convert_doc_to_dict(doc)
            if msg:
                mensagens_evolucao.append(msg)

    except Exception as e:
        print(f"Erro ver_evolucao_agendamento: {e}")
        return jsonify({'success': False, 'message': f'Erro ao carregar evolução: {e}'}), 500
    
    return jsonify({
        'agendamento': convert_doc_to_dict(agendamento_doc),
        'mensagens': mensagens_evolucao,
        'success': True
    })

@medical_records_bp.route('/agendamentos/<string:agendamento_id>/evolucao/adicionar', methods=['POST'])
@login_required
def adicionar_evolucao_agendamento(agendamento_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    agendamento_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_id)
    
    conteudo_mensagem = request.json.get('conteudo', '').strip()
    
    if not conteudo_mensagem:
        return jsonify({'success': False, 'message': 'Mensagem vazia não pode ser registrada.'}), 400

    profissional_logado_uid = session.get('user_uid')
    profissional_nome = session.get('user_name', session.get('user_email', 'Desconhecido'))
    profissional_id = None
    
    try:
        user_doc = db.collection('User').document(profissional_logado_uid).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            profissional_id = user_data.get('profissional_id')
        
        if not profissional_id:
            profissional_id = profissional_logado_uid
            print(f"Aviso: Usuário {profissional_logado_uid} não tem profissional_id associado. Usando UID para registro de evolução.")

        agendamento_doc = agendamento_ref.get()
        if not agendamento_doc.exists:
            return jsonify({'success': False, 'message': 'Agendamento não encontrado.'}), 404
        
        if session.get('user_role') != 'admin':
            if agendamento_doc.to_dict().get('profissional_id') != profissional_id:
                return jsonify({'success': False, 'message': 'Acesso negado.'}), 403

        evolucao_ref = agendamento_ref.collection('evolucao')
        
        nova_mensagem = {
            'conteudo': conteudo_mensagem,
            'data_registro': db.SERVER_TIMESTAMP,
            'registrado_por_uid': professional_logado_uid,
            'registrado_por_nome': professional_nome,
            'profissional_id': professional_id
        }
        
        update_time, new_doc_ref = evolucao_ref.add(nova_mensagem)
        
        new_doc = new_doc_ref.get()
        final_message = convert_doc_to_dict(new_doc)

        return jsonify({'success': True, 'message': 'Evolução registrada!', 'nova_mensagem': final_message}), 201

    except Exception as e:
        print(f"Erro ao adicionar evolução: {e}")
        return jsonify({'success': False, 'message': f'Erro ao registrar evolução: {e}'}), 500
    
@medical_records_bp.route('/prontuarios/<string:paciente_doc_id>/anamnese/novo', methods=['GET', 'POST'])
@login_required
def adicionar_anamnese(paciente_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    profissional_logado_uid = session.get('user_uid')   

    profissional_doc_id = None
    try:
        user_doc = db.collection('User').document(profissional_logado_uid).get()
        if user_doc.exists:
            profissional_doc_id = user_doc.to_dict().get('profissional_id')
        if not profissional_doc_id:
            flash('Seu usuário não está associado a um profissional. Contate o administrador.', 'danger')
            return redirect(url_for('medical_records_bp.ver_prontuario', paciente_doc_id=paciente_doc_id))
    except Exception as e:
        flash(f'Erro ao verificar profissional associado: {e}', 'danger')
        return redirect(url_for('medical_records_bp.ver_prontuario', paciente_doc_id=paciente_doc_id))

    paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
    paciente_doc = paciente_ref.get()
    if not paciente_doc.exists:
        flash('Paciente não encontrado.', 'danger')
        return redirect(url_for('medical_records_bp.buscar_prontuario'))
    
    paciente_nome = paciente_doc.to_dict().get('nome', 'Paciente Desconhecido')

    modelos_anamnese = []
    try:
        modelos_docs = db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').order_by('identificacao').stream()
        for doc in modelos_docs:
            modelos_anamnese.append(convert_doc_to_dict(doc))
    except Exception as e:
        flash('Erro ao carregar modelos de anamnese.', 'warning')
        print(f"Erro ao carregar modelos de anamnese: {e}")

    if request.method == 'POST':
        conteudo = request.form.get('conteudo', '').strip()   
        modelo_base_id = request.form.get('modelo_base_id')
        
        try:
            db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').add({
                'profissional_id': professional_doc_id,
                'data_registro': db.SERVER_TIMESTAMP,
                'tipo_registro': 'anamnese',
                'titulo': 'Anamnese',
                'conteudo': conteudo,
                'modelo_base_id': modelo_base_id if modelo_base_id else None
            })
            flash('Anamnese adicionada com sucesso!', 'success')
            return redirect(url_for('medical_records_bp.ver_prontuario', paciente_doc_id=paciente_doc_id))
        except Exception as e:
            flash(f'Erro ao adicionar anamnese: {e}', 'danger')
            print(f"Erro add_anamnesis (POST): {e}")
    
    return render_template('anamnese_form.html',   
                            paciente_id=paciente_doc_id,   
                            paciente_nome=paciente_nome,   
                            modelos_anamnese=modelos_anamnese,   
                            action_url=url_for('medical_records_bp.adicionar_anamnese', paciente_doc_id=paciente_doc_id),
                            page_title=f"Registrar Anamnese para {paciente_nome}")

@medical_records_bp.route('/prontuarios/<string:paciente_doc_id>/anamnese/editar/<string:anamnese_doc_id>', methods=['GET', 'POST'])
@login_required
def editar_anamnese(paciente_doc_id, anamnese_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']

    anamnese_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').document(anamnese_doc_id)
    paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
    
    paciente_doc = paciente_ref.get()
    if not paciente_doc.exists:
        flash('Paciente não encontrado.', 'danger')
        return redirect(url_for('medical_records_bp.buscar_prontuario'))
    
    paciente_nome = paciente_doc.to_dict().get('nome', 'Paciente Desconhecido')

    modelos_anamnese = []
    try:
        modelos_docs = db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').order_by('identificacao').stream()
        for doc in modelos_docs:
            modelos_anamnese.append(convert_doc_to_dict(doc))
    except Exception as e:
        flash('Erro ao carregar modelos de anamnese.', 'warning')
        print(f"Erro ao carregar modelos de anamnese (edit): {e}")

    if request.method == 'POST':
        conteudo = request.form.get('conteudo', '').strip()   
        modelo_base_id = request.form.get('modelo_base_id')
        
        try:
            anamnese_ref.update({
                'conteudo': conteudo,
                'modelo_base_id': modelo_base_id if modelo_base_id else None,
                'atualizado_em': db.SERVER_TIMESTAMP
            })
            flash('Anamnese atualizada com sucesso!', 'success')
            return redirect(url_for('medical_records_bp.ver_prontuario', paciente_doc_id=paciente_doc_id))
        except Exception as e:
            flash(f'Erro ao atualizar anamnese: {e}', 'danger')
            print(f"Erro edit_anamnesis (POST): {e}")
    
    try:
        anamnese_doc = anamnese_ref.get()
        if anamnese_doc.exists and anamnese_doc.to_dict().get('tipo_registro') == 'anamnese':
            anamnese_data = convert_doc_to_dict(anamnese_doc)
            
            return render_template('anamnese_form.html',   
                                   paciente_id=paciente_doc_id,   
                                   paciente_nome=paciente_nome,   
                                   anamnese=anamnese_data,   
                                   modelos_anamnese=modelos_anamnese,
                                   action_url=url_for('medical_records_bp.editar_anamnese', paciente_doc_id=paciente_doc_id, anamnese_doc_id=anamnese_doc_id),
                                   page_title=f"Editar Anamnese para {paciente_nome}")
        else:
            flash('Anamnese não encontrada ou tipo de registro inválido.', 'danger')
            return redirect(url_for('medical_records_bp.ver_prontuario', paciente_doc_id=paciente_doc_id))
    except Exception as e:
        flash(f'Erro ao carregar anamnese para edição: {e}', 'danger')
        print(f"Erro edit_anamnesis (GET): {e}")
        return redirect(url_for('medical_records_bp.ver_prontuario', paciente_doc_id=paciente_doc_id))

@medical_records_bp.route('/prontuarios/<string:paciente_doc_id>/registrar_registro_generico', methods=['POST'])
@login_required
def registrar_registro_generico(paciente_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    profissional_logado_uid = session.get('user_uid')

    profissional_doc_id = None
    profissional_nome = "Profissional Desconhecido"
    try:
        user_doc = db.collection('User').document(profissional_logado_uid).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            profissional_doc_id = user_data.get('profissional_id')
            profissional_nome = user_data.get('nome_completo', professional_nome)

        if not profissional_doc_id:
            flash('Seu usuário não está associado a um profissional. Não foi possível registrar.', 'danger')
            return redirect(url_for('medical_records_bp.ver_prontuario', paciente_doc_id=paciente_doc_id))
    except Exception as e:
        flash(f'Erro ao verificar profissional para registro: {e}', 'danger')
        return redirect(url_for('medical_records_bp.ver_prontuario', paciente_doc_id=paciente_doc_id))

    try:
        # Get data from JSON body since Content-Type is application/json
        request_data = request.json
        tipo_registro = request_data.get('tipo_registro')
        titulo = request_data.get('titulo', '').strip()
        conteudo = request_data.get('conteudo', '').strip()
        
        # Capture PEI references from JSON body
        referencia_pei_id = request_data.get('referencia_pei_id')
        referencia_meta_titulo = request_data.get('referencia_meta_titulo')

        # If the record type is 'evolucao_pei' and the title is empty, set a default
        if tipo_registro == 'evolucao_pei' and not titulo:
            titulo = f"Evolução - {referencia_meta_titulo or 'Atividade'}"

        if not all([tipo_registro, titulo, conteudo]):
            # Return JSON response for frontend to handle, instead of flash and redirect
            return jsonify({'success': False, 'message': 'Por favor, preencha o título e o conteúdo para o registro.'}), 400

        novo_registro_data = {
            'profissional_id': professional_doc_id,
            'profissional_nome': professional_nome,
            'data_registro': db.SERVER_TIMESTAMP,
            'tipo_registro': tipo_registro,
            'titulo': titulo,
            'conteudo': conteudo,
            'atualizado_em': db.SERVER_TIMESTAMP
        }

        # Add PEI references to the document if it's an evolution
        if tipo_registro == 'evolucao_pei' and referencia_pei_id and referencia_meta_titulo:
            novo_registro_data['referencia_pei_id'] = referencia_pei_id
            novo_registro_data['referencia_meta_titulo'] = referencia_meta_titulo

        # The path to save the record is always in the patient's 'prontuarios' subcollection
        _, new_doc_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').add(novo_registro_data)
        
        # To get the ID of the newly created document for the frontend
        new_doc = new_doc_ref.get()
        return jsonify({'success': True, 'message': f'Registro de {tipo_registro.replace("_", " ")} adicionado com sucesso!', 'registro_id': new_doc.id}), 201

    except Exception as e:
        print(f"Erro registrar_registro_generico: {e}")
        return jsonify({'success': False, 'message': f'Erro ao adicionar registro: {e}'}), 500


@medical_records_bp.route('/prontuarios/<string:paciente_doc_id>/editar_registro_generico/<string:registro_doc_id>', methods=['POST'])
@login_required
def editar_registro_generico(paciente_doc_id, registro_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    registro_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').document(registro_doc_id)

    try:
        titulo = request.form.get('titulo', '').strip()
        conteudo = request.form.get('conteudo', '').strip()
        tipo_registro = request.form.get('tipo_registro_feedback', 'Registro') # Campo para feedback

        if not all([titulo, conteudo]):
            flash('Por favor, preencha o título e o conteúdo para o registro.', 'danger')
            return redirect(url_for('medical_records_bp.ver_prontuario', paciente_doc_id=paciente_doc_id))

        registro_ref.update({
            'titulo': titulo,
            'conteudo': conteudo,
            'atualizado_em': db.SERVER_TIMESTAMP
        })
        flash(f'{tipo_registro.capitalize()} atualizado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao atualizar registro: {e}', 'danger')
        print(f"Erro editar_registro_generico: {e}")
    return redirect(url_for('medical_records_bp.ver_prontuario', paciente_doc_id=paciente_doc_id))


@medical_records_bp.route('/prontuarios/<string:paciente_doc_id>/apagar_registro_generico', methods=['POST'])
@login_required
def apagar_registro_generico(paciente_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    registro_id = request.form.get('registro_id')

    if not registro_id:
        flash('ID do registro não fornecido para exclusão.', 'danger')
        return redirect(url_for('medical_records_bp.ver_prontuario', paciente_doc_id=paciente_doc_id))

    try:
        db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').document(registro_id).delete()
        flash('Registro apagado com sucesso!', 'success')
        
    except Exception as e:
        flash(f'Erro ao apagar registro: {e}', 'danger')
        print(f"Erro apagar_registro_generico: {e}")
    return redirect(url_for('medical_records_bp.ver_prontuario', paciente_doc_id=paciente_doc_id))