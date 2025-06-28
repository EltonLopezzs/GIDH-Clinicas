from flask import render_template, session, flash, redirect, url_for, request, jsonify
import datetime
import json
import uuid # Importar para gerar IDs únicos
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore # Importar no topo
# Removed: from google.cloud.firestore import Timestamp # Importar Timestamp explicitamente


# Importar utils
from utils import get_db, login_required, admin_required, SAO_PAULO_TZ, convert_doc_to_dict


def register_medical_records_routes(app):
    @app.route('/prontuarios', endpoint='buscar_prontuario')
    @login_required
    def buscar_prontuario():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        pacientes_para_busca = []
        search_query = request.args.get('search_query', '').strip()

        try:
            pacientes_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes')
            query = pacientes_ref.order_by('nome')

            if search_query:
                # Correção: Adicionado distinct para evitar duplicatas em buscas múltiplas
                pacientes_set = set()

                # Busca por nome
                query_nome = pacientes_ref.where(filter=FieldFilter('nome', '>=', search_query))\
                                         .where(filter=FieldFilter('nome', '<=', search_query + '\uf8ff'))
                for doc in query_nome.stream():
                    paciente_data = doc.to_dict()
                    if paciente_data:
                        paciente_data['id'] = doc.id
                        pacientes_set.add(json.dumps(paciente_data, sort_keys=True))
                
                # Busca por CPF
                query_cpf = pacientes_ref.where(filter=FieldFilter('cpf', '==', search_query))
                for doc in query_cpf.stream():
                    paciente_data = doc.to_dict()
                    if paciente_data:
                        paciente_data['id'] = doc.id
                        pacientes_set.add(json.dumps(paciente_data, sort_keys=True))
                
                pacientes_para_busca = [json.loads(p) for p in pacientes_set]
                pacientes_para_busca.sort(key=lambda x: x.get('nome', '')) # Garante ordenação por nome

            else:
                docs = query.stream()
                for doc in docs:
                    paciente_data = doc.to_dict()
                    if paciente_data:
                        pacientes_para_busca.append({'id': doc.id, 'nome': paciente_data.get('nome', doc.id)})
                    
        except Exception as e:
            flash(f'Erro ao carregar lista de pacientes para busca: {e}. Verifique seus índices do Firestore.', 'danger')
            print(f"Erro search_patient_record: {e}")

        return render_template('prontuario_busca.html', pacientes_para_busca=pacientes_para_busca, search_query=search_query)

    @app.route('/prontuarios/<string:paciente_doc_id>', endpoint='ver_prontuario')
    @login_required
    def ver_prontuario(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
        prontuarios_ref = paciente_ref.collection('prontuarios')
        
        paciente_data = None
        registros_prontuario = []
        peis_data = [] # Lista para armazenar os PEIs
        
        # Get current date for default value in PEI creation form
        current_date_iso = datetime.date.today().isoformat()
        
        try:
            paciente_doc = paciente_ref.get()
            if paciente_doc.exists:
                paciente_data = paciente_doc.to_dict()
                paciente_data['id'] = paciente_doc.id

                # Buscar nome do convênio
                if paciente_data.get('convenio_id'):
                    convenio_doc = db_instance.collection('clinicas').document(clinica_id).collection('convenios').document(paciente_data['convenio_id']).get()
                    if convenio_doc.exists:
                        paciente_data['convenio_nome'] = convenio_doc.to_dict().get('nome', 'N/A')
                    else:
                        paciente_data['convenio_nome'] = 'Particular' # Se o convênio não existir mais, marque como Particular
                else:
                    paciente_data['convenio_nome'] = 'Particular'
                
                docs_stream = prontuarios_ref.order_by('data_registro', direction=firestore.Query.DESCENDING).stream()
                for doc in docs_stream:
                    registro = doc.to_dict()
                    if registro:
                        registro['id'] = doc.id
                        # Use datetime.datetime from the standard library for type checking
                        if registro.get('data_registro') and isinstance(registro['data_registro'], datetime.datetime):
                            registro['data_registro_fmt'] = registro['data_registro'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                        else:
                            registro['data_registro_fmt'] = "N/A"
                        
                        profissional_doc_id = registro.get('profissional_id')
                        if profissional_doc_id:
                            prof_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).get()
                            if prof_doc.exists:
                                registro['profissional_nome'] = prof_doc.to_dict().get('nome', 'Desconhecido')
                            else:
                                registro['profissional_nome'] = 'Desconhecido'
                        else:
                            registro['profissional_nome'] = 'N/A' 
                        
                        registros_prontuario.append(registro)
                
                # --- Get PEIs data ---
                if 'peis' in paciente_data and isinstance(paciente_data['peis'], list):
                    for pei in paciente_data['peis']:
                        # Convert Timestamp objects to formatted strings for display
                        # Use firestore.Timestamp instead of a direct import
                        if isinstance(pei.get('data_criacao'), firestore.Timestamp): 
                            pei['data_criacao'] = pei['data_criacao'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y')
                        # Add a raw timestamp for sorting purposes on the frontend if needed
                        if isinstance(pei.get('data_criacao_raw'), firestore.Timestamp):
                             pei['data_criacao_timestamp'] = pei['data_criacao_raw'].timestamp() # Unix timestamp
                        elif isinstance(pei.get('data_criacao'), firestore.Timestamp): # If only data_criacao has the timestamp
                             pei['data_criacao_timestamp'] = pei['data_criacao'].timestamp()
                        else:
                             pei['data_criacao_timestamp'] = 0 # Fallback

                        if 'goals' not in pei or not isinstance(pei['goals'], list):
                            pei['goals'] = [] # Initialize if missing or not a list

                        for goal in pei['goals']:
                            if 'targets' not in goal or not isinstance(goal['targets'], list):
                                goal['targets'] = [] # Initialize if missing or not a list

                    peis_data = paciente_data['peis']
                else:
                    paciente_data['peis'] = [] # Initialize if not existing
                    peis_data = []

            else:
                flash('Paciente não encontrado.', 'danger')
                return redirect(url_for('buscar_prontuario'))
        except Exception as e:
            flash(f'Erro ao carregar prontuário do paciente: {e}.', 'danger')
            print(f"Erro ver_prontuario: {e}")

        return render_template('prontuario.html', paciente=paciente_data, registros=registros_prontuario, peis=peis_data, current_date_iso=current_date_iso)

    @app.route('/prontuarios/<string:paciente_doc_id>/anamnese/novo', methods=['GET', 'POST'], endpoint='adicionar_anamnese')
    @login_required
    def adicionar_anamnese(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        profissional_logado_uid = session.get('user_uid')    

        profissional_doc_id = None
        profissional_nome = "Profissional Desconhecido"
        try:
            user_mapping_doc = db_instance.collection('User').document(profissional_logado_uid).get()
            if user_mapping_doc.exists:
                user_data = user_mapping_doc.to_dict()
                profissional_doc_id = user_data.get('profissional_id')
                
                if profissional_doc_id:
                    prof_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).get()
                    if prof_doc.exists:
                        profissional_nome = prof_doc.to_dict().get('nome', 'Profissional Desconhecido')
            
            if not profissional_doc_id:
                flash('Seu usuário não está associado a um profissional. Entre em contato com o administrador.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

        except Exception as e:
            flash(f'Erro ao verificar profissional associado: {e}', 'danger')
            print(f"Erro add_anamnesis (GET - professional check): {e}")
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

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
                modelo = convert_doc_to_dict(doc)
                modelos_anamnese.append(modelo)
        except Exception as e:
            flash('Erro ao carregar modelos de anamnese.', 'warning')
            print(f"Erro ao carregar modelos de anamnese: {e}")

        if request.method == 'POST':
            conteudo = request.form.get('conteudo', '').strip()    
            modelo_base_id = request.form.get('modelo_base_id')
            
            print(f"DEBUG (adicionar_anamnese - POST): Conteúdo recebido (primeiros 100 caracteres): {conteudo[:100]}...")    
            print(f"DEBUG (adicionar_anamnese - POST): Todos os dados do formulário: {request.form}")    
            
            try:
                db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').add({
                    'profissional_id': profissional_doc_id, 
                    'profissional_nome': profissional_nome, 
                    'data_registro': firestore.SERVER_TIMESTAMP,
                    'tipo_registro': 'anamnese',
                    'titulo': 'Anamnese',
                    'conteudo': conteudo,
                    'modelo_base_id': modelo_base_id if modelo_base_id else None
                })
                flash('Anamnese adicionada com sucesso!', 'success')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
            except Exception as e:
                flash(f'Erro ao adicionar anamnese: {e}', 'danger')
                print(f"Erro add_anamnesis (POST): {e}")
        
        return render_template('anamnese_form.html',    
                                paciente_id=paciente_doc_id,    
                                paciente_nome=paciente_nome,    
                                modelos_anamnese=modelos_anamnese,    
                                action_url=url_for('adicionar_anamnese', paciente_doc_id=paciente_doc_id),
                                page_title=f"Registrar Anamnese para {paciente_nome}")

    @app.route('/prontuarios/<string:paciente_doc_id>/anamnese/editar/<string:anamnese_doc_id>', methods=['GET', 'POST'], endpoint='editar_anamnese')
    @login_required
    def editar_anamnese(paciente_doc_id, anamnese_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        profissional_logado_uid = session.get('user_uid')

        profissional_doc_id = None
        profissional_nome = "Profissional Desconhecido" 
        try:
            user_mapping_doc = db_instance.collection('User').document(profissional_logado_uid).get()
            if user_mapping_doc.exists:
                user_data = user_mapping_doc.to_dict()
                profissional_doc_id = user_data.get('profissional_id')
                
                if profissional_doc_id:
                    prof_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).get()
                    if prof_doc.exists:
                        profissional_nome = prof_doc.to_dict().get('nome', 'Profissional Desconhecido')
            
            if not profissional_doc_id:
                flash('Seu usuário não está associado a um profissional. Entre em contato com o administrador.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

        except Exception as e:
            flash(f'Erro ao verificar profissional associado para edição: {e}', 'danger')
            print(f"Erro edit_anamnesis (GET - professional check): {e}")
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

        anamnese_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').document(anamnese_doc_id)
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
                modelo = convert_doc_to_dict(doc)
                modelos_anamnese.append(modelo)
        except Exception as e:
            flash('Erro ao carregar modelos de anamnese.', 'warning')
            print(f"Erro ao carregar modelos de anamnese (edit): {e}")

        if request.method == 'POST':
            conteudo = request.form.get('conteudo', '').strip()
            modelo_base_id = request.form.get('modelo_base_id')
            
            print(f"DEBUG (editar_anamnese - POST): Conteúdo recebido (primeiros 100 caracteres): {conteudo[:100]}...")    
            print(f"DEBUG (editar_anamnese - POST): Todos os dados do formulário: {request.form}")
            
            try:
                anamnese_ref.update({
                    'conteudo': conteudo,
                    'modelo_base_id': modelo_base_id if modelo_base_id else None,
                    'profissional_id': profissional_doc_id, 
                    'profissional_nome': profissional_nome, 
                    'atualizado_em': firestore.SERVER_TIMESTAMP
                })
                flash('Anamnese atualizada com sucesso!', 'success')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
            except Exception as e:
                flash(f'Erro ao atualizar anamnese: {e}', 'danger')
                print(f"Erro edit_anamnesis (POST): {e}")
        
        try:
            anamnese_doc = anamnese_ref.get()
            if anamnese_doc.exists and anamnese_doc.to_dict().get('tipo_registro') == 'anamnese':
                anamnese_data = anamnese_doc.to_dict()
                anamnese_data['id'] = anamnese_doc.id    
                
                return render_template('anamnese_form.html',    
                                        paciente_id=paciente_doc_id,    
                                        paciente_nome=paciente_nome,    
                                        anamnese=anamnese_data,    
                                        modelos_anamnese=modelos_anamnese,
                                        action_url=url_for('editar_anamnese', paciente_doc_id=paciente_doc_id, anamnese_doc_id=anamnese_doc_id),
                                        page_title=f"Editar Anamnese para {paciente_nome}")
            else:
                flash('Anamnese não encontrada ou tipo de registro inválido.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
        except Exception as e:
            flash(f'Erro ao carregar anamnese para edição: {e}', 'danger')
            print(f"Erro edit_anamnesis (GET): {e}")
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/prontuarios/<string:paciente_doc_id>/registrar_registro_generico', methods=['POST'], endpoint='registrar_registro_generico')
    @login_required
    def registrar_registro_generico(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        profissional_logado_uid = session.get('user_uid')

        profissional_doc_id = None
        profissional_nome = "Profissional Desconhecido"
        try:
            user_mapping_doc = db_instance.collection('User').document(profissional_logado_uid).get()
            if user_mapping_doc.exists:
                user_data = user_mapping_doc.to_dict()
                profissional_doc_id = user_data.get('profissional_id')
                
                if profissional_doc_id:
                    prof_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).get()
                    if prof_doc.exists:
                        profissional_nome = prof_doc.to_dict().get('nome', 'Profissional Desconhecido')
            
            if not profissional_doc_id:
                flash('Seu usuário não está associado a um profissional. Não foi possível registrar.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

        except Exception as e:
            flash(f'Erro ao verificar profissional para registro: {e}', 'danger')
            print(f"Erro registrar_registro_generico (professional check): {e}")
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

        try:
            tipo_registro = request.form.get('tipo_registro')
            titulo = request.form.get('titulo', '').strip()
            conteudo = request.form.get('conteudo', '').strip()
            agendamento_id_referencia = request.form.get('agendamento_id_referencia', '').strip()

            if not all([tipo_registro, titulo, conteudo]):
                flash(f'Por favor, preencha o título e o conteúdo para o registro de {tipo_registro}.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

            db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').add({
                'profissional_id': profissional_doc_id, 
                'profissional_nome': profissional_nome, 
                'data_registro': firestore.SERVER_TIMESTAMP,
                'tipo_registro': tipo_registro,
                'titulo': titulo,
                'conteudo': conteudo,
                'agendamento_id_referencia': agendamento_id_referencia if agendamento_id_referencia else None,
                'atualizado_em': firestore.SERVER_TIMESTAMP
            })
            flash(f'Registro de {tipo_registro} adicionado com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao adicionar registro de {tipo_registro}: {e}', 'danger')
            print(f"Erro registrar_registro_generico: {e}")
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/prontuarios/<string:paciente_doc_id>/editar_registro_generico/<string:registro_doc_id>', methods=['POST'], endpoint='editar_registro_generico')
    @login_required
    def editar_registro_generico(paciente_doc_id, registro_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        registro_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').document(registro_doc_id)

        profissional_logado_uid = session.get('user_uid')
        profissional_nome = "Profissional Desconhecido"
        try:
            user_mapping_doc = db_instance.collection('User').document(profissional_logado_uid).get()
            if user_mapping_doc.exists:
                user_data = user_mapping_doc.to_dict()
                profissional_doc_id = user_data.get('profissional_id')
                if profissional_doc_id:
                    prof_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).get()
                    if prof_doc.exists:
                        profissional_nome = prof_doc.to_dict().get('nome', 'Profissional Desconhecido')
        except Exception as e:
            print(f"Erro ao obter nome do profissional logado para edição de registro: {e}")

        try:
            titulo = request.form.get('titulo', '').strip()
            conteudo = request.form.get('conteudo', '').strip()
            agendamento_id_referencia = request.form.get('agendamento_id_referencia', '').strip()
            tipo_registro = request.form.get('tipo_registro')

            if not all([titulo, conteudo]):
                flash(f'Por favor, preencha o título e o conteúdo para o registro de {tipo_registro}.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

            registro_ref.update({
                'titulo': titulo,
                'conteudo': conteudo,
                'agendamento_id_referencia': agendamento_id_referencia if agendamento_id_referencia else None,
                'profissional_nome': profissional_nome, 
                'atualizado_em': firestore.SERVER_TIMESTAMP
            })
            flash(f'Registro de {tipo_registro} atualizado com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao atualizar registro: {e}', 'danger')
            print(f"Erro editar_registro_generico: {e}")
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/prontuarios/<string:paciente_doc_id>/apagar_registro_generico', methods=['POST'], endpoint='apagar_registro_generico')
    @login_required
    def apagar_registro_generico(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        registro_id = request.form.get('registro_id')

        if not registro_id:
            flash('ID do registro não fornecido para exclusão.', 'danger')
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

        try:
            db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').document(registro_id).delete()
            flash('Registro apagado com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao apagar registro: {e}', 'danger')
            print(f"Erro apagar_registro_generico: {e}")
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    # --- NEW PEI ROUTES ---

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/add_pei', methods=['POST'], endpoint='add_pei')
    @login_required
    def add_pei(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        
        try:
            data = request.form
            titulo = data.get('titulo')
            data_criacao_str = data.get('data_criacao')

            if not titulo or not data_criacao_str:
                flash('Título e data de criação do PEI são obrigatórios.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
            
            # Convert string date to datetime object
            try:
                data_criacao_obj = datetime.datetime.strptime(data_criacao_str, '%Y-%m-%d').date() # Get only date part
            except ValueError:
                flash('Formato de data inválido. Use AAAA-MM-DD.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

            paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
            paciente_doc = paciente_ref.get()
            
            if not paciente_doc.exists:
                flash('Paciente não encontrado.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
            
            paciente_data = paciente_doc.to_dict()
            peis = paciente_data.get('peis', [])

            new_pei = {
                'id': str(uuid.uuid4()),
                'titulo': titulo,
                'data_criacao': data_criacao_str, # Store as string 'YYYY-MM-DD' for display
                'data_criacao_raw': firestore.SERVER_TIMESTAMP, # Use SERVER_TIMESTAMP for consistent sorting in Firestore
                'status': 'ativo',
                'goals': []
            }
            peis.append(new_pei)
            
            paciente_ref.update({'peis': peis})
            flash('PEI adicionado com sucesso!', 'success')

        except Exception as e:
            flash(f'Erro ao adicionar PEI: {e}', 'danger')
            print(f"Erro add_pei: {e}")
        
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/add_goal', methods=['POST'], endpoint='add_goal')
    @login_required
    def add_goal(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        
        try:
            data = request.form
            pei_id = data.get('pei_id')
            descricao_goal = data.get('descricao')
            targets_raw = request.form.getlist('targets[]') # Get all target inputs
            
            if not pei_id or not descricao_goal or not targets_raw:
                flash('Dados insuficientes para adicionar meta.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

            paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
            paciente_doc = paciente_ref.get()
            
            if not paciente_doc.exists:
                flash('Paciente não encontrado.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
            
            paciente_data = paciente_doc.to_dict()
            peis = paciente_data.get('peis', [])
            
            target_pei = None
            for pei in peis:
                if pei.get('id') == pei_id:
                    target_pei = pei
                    break
            
            if not target_pei:
                flash('PEI não encontrado.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

            new_targets = []
            for target_desc in targets_raw:
                if target_desc.strip(): # Only add non-empty targets
                    new_targets.append({
                        'id': str(uuid.uuid4()),
                        'descricao': target_desc.strip(),
                        'concluido': False
                    })

            new_goal = {
                'id': str(uuid.uuid4()),
                'descricao': descricao_goal.strip(),
                'status': 'ativo',
                'targets': new_targets
            }
            
            if 'goals' not in target_pei:
                target_pei['goals'] = []
            target_pei['goals'].append(new_goal)
            
            paciente_ref.update({'peis': peis})
            flash('Meta adicionada com sucesso ao PEI!', 'success')

        except Exception as e:
            flash(f'Erro ao adicionar meta: {e}', 'danger')
            print(f"Erro add_goal: {e}")
        
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/update_target_status', methods=['POST'], endpoint='update_target_status')
    @login_required
    def update_target_status(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        
        try:
            data = request.get_json()
            pei_id = data.get('pei_id')
            goal_id = data.get('goal_id')
            target_id = data.get('target_id')
            concluido = data.get('concluido') # This will be a boolean

            if not all([pei_id, goal_id, target_id, isinstance(concluido, bool)]):
                return jsonify({'success': False, 'message': 'Dados insuficientes para atualizar status do alvo.'}), 400

            paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
            paciente_doc = paciente_ref.get()
            
            if not paciente_doc.exists:
                return jsonify({'success': False, 'message': 'Paciente não encontrado.'}), 404
            
            paciente_data = paciente_doc.to_dict()
            peis = paciente_data.get('peis', [])
            
            pei_found = False
            for pei in peis:
                if pei.get('id') == pei_id:
                    pei_found = True
                    goal_found = False
                    for goal in pei.get('goals', []):
                        if goal.get('id') == goal_id:
                            goal_found = True
                            target_found = False
                            for target in goal.get('targets', []):
                                if target.get('id') == target_id:
                                    target['concluido'] = concluido
                                    target_found = True
                                    break
                            if not target_found:
                                return jsonify({'success': False, 'message': 'Alvo não encontrado.'}), 404
                            break
                    if not goal_found:
                        return jsonify({'success': False, 'message': 'Meta não encontrada.'}), 404
                    break
            if not pei_found:
                return jsonify({'success': False, 'message': 'PEI não encontrado.'}), 404

            paciente_ref.update({'peis': peis})

            # Re-fetch the updated PEIs to send back, converting Timestamps
            updated_paciente_doc = paciente_ref.get()
            updated_peis_data = updated_paciente_doc.to_dict().get('peis', [])
            for pei in updated_peis_data:
                # Convert Timestamp objects to formatted strings for display
                # Data de criação pode ser uma string, então só converta se for Timestamp
                if isinstance(pei.get('data_criacao'), firestore.Timestamp):
                    pei['data_criacao'] = pei['data_criacao'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y')
                # data_criacao_raw sempre será um Timestamp
                if isinstance(pei.get('data_criacao_raw'), firestore.Timestamp):
                    pei['data_criacao_timestamp'] = pei['data_criacao_raw'].timestamp()
                else:
                    pei['data_criacao_timestamp'] = 0

            return jsonify({'success': True, 'message': 'Status do alvo atualizado com sucesso!', 'peis': updated_peis_data}), 200
        except Exception as e:
            print(f"Erro update_target_status: {e}")
            return jsonify({'success': False, 'message': f'Erro interno ao atualizar status do alvo: {e}'}), 500

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/finalize_goal', methods=['POST'], endpoint='finalize_goal')
    @login_required
    def finalize_goal(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        
        try:
            data = request.get_json()
            pei_id = data.get('pei_id')
            goal_id = data.get('goal_id')

            if not all([pei_id, goal_id]):
                return jsonify({'success': False, 'message': 'Dados insuficientes para finalizar meta.'}), 400

            paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
            paciente_doc = paciente_ref.get()
            
            if not paciente_doc.exists:
                return jsonify({'success': False, 'message': 'Paciente não encontrado.'}), 404
            
            paciente_data = paciente_doc.to_dict()
            peis = paciente_data.get('peis', [])
            
            pei_found = False
            for pei in peis:
                if pei.get('id') == pei_id:
                    pei_found = True
                    goal_found = False
                    for goal in pei.get('goals', []):
                        if goal.get('id') == goal_id:
                            goal_found = True
                            # Check if all targets are completed
                            all_targets_completed = all(target.get('concluido', False) for target in goal.get('targets', []))
                            
                            if all_targets_completed:
                                goal['status'] = 'finalizado'
                                # Also, set all targets to completed explicitly, in case not all were true
                                for target in goal.get('targets', []):
                                    target['concluido'] = True
                            else:
                                return jsonify({'success': False, 'message': 'Nem todos os alvos desta meta foram concluídos.'}), 400
                            break
                    if not goal_found:
                        return jsonify({'success': False, 'message': 'Meta não encontrada.'}), 404
                    break
            if not pei_found:
                return jsonify({'success': False, 'message': 'PEI não encontrado.'}), 404

            paciente_ref.update({'peis': peis})

            # Re-fetch the updated PEIs to send back, converting Timestamps
            updated_paciente_doc = paciente_ref.get()
            updated_peis_data = updated_paciente_doc.to_dict().get('peis', [])
            for pei in updated_peis_data:
                # Convert Timestamp objects to formatted strings for display
                if isinstance(pei.get('data_criacao'), firestore.Timestamp):
                    pei['data_criacao'] = pei['data_criacao'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y')
                if isinstance(pei.get('data_criacao_raw'), firestore.Timestamp):
                    pei['data_criacao_timestamp'] = pei['data_criacao_raw'].timestamp()
                else:
                    pei['data_criacao_timestamp'] = 0
            
            return jsonify({'success': True, 'message': 'Meta finalizada com sucesso!', 'peis': updated_peis_data}), 200
        except Exception as e:
            print(f"Erro finalize_goal: {e}")
            return jsonify({'success': False, 'message': f'Erro interno ao finalizar meta: {e}'}), 500

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/finalize_pei', methods=['POST'], endpoint='finalize_pei')
    @login_required
    def finalize_pei(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        
        try:
            data = request.get_json()
            pei_id = data.get('pei_id')

            if not pei_id:
                return jsonify({'success': False, 'message': 'ID do PEI não fornecido.'}), 400

            paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
            paciente_doc = paciente_ref.get()
            
            if not paciente_doc.exists:
                return jsonify({'success': False, 'message': 'Paciente não encontrado.'}), 404
            
            paciente_data = paciente_doc.to_dict()
            peis = paciente_data.get('peis', [])
            
            pei_found = False
            for pei in peis:
                if pei.get('id') == pei_id:
                    pei_found = True
                    pei['status'] = 'finalizado'
                    # Mark all active goals within this PEI as finalized and all their targets as completed
                    for goal in pei.get('goals', []):
                        if goal.get('status') == 'ativo':
                            goal['status'] = 'finalizado'
                            for target in goal.get('targets', []):
                                target['concluido'] = True
                    break
            if not pei_found:
                return jsonify({'success': False, 'message': 'PEI não encontrado.'}), 404

            paciente_ref.update({'peis': peis})

            # Re-fetch the updated PEIs to send back, converting Timestamps
            updated_paciente_doc = paciente_ref.get()
            updated_peis_data = updated_paciente_doc.to_dict().get('peis', [])
            for pei in updated_peis_data:
                # Convert Timestamp objects to formatted strings for display
                if isinstance(pei.get('data_criacao'), firestore.Timestamp):
                    pei['data_criacao'] = pei['data_criacao'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y')
                if isinstance(pei.get('data_criacao_raw'), firestore.Timestamp):
                    pei['data_criacao_timestamp'] = pei['data_criacao_raw'].timestamp()
                else:
                    pei['data_criacao_timestamp'] = 0

            return jsonify({'success': True, 'message': 'PEI finalizado com sucesso!', 'peis': updated_peis_data}), 200
        except Exception as e:
            print(f"Erro finalize_pei: {e}")
            return jsonify({'success': False, 'message': f'Erro interno ao finalizar PEI: {e}'}), 500

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/delete_pei', methods=['POST'], endpoint='delete_pei')
    @login_required
    def delete_pei(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        
        try:
            pei_id = request.form.get('pei_id')

            if not pei_id:
                flash('ID do PEI não fornecido para exclusão.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

            paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
            paciente_doc = paciente_ref.get()
            
            if not paciente_doc.exists:
                flash('Paciente não encontrado.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
            
            paciente_data = paciente_doc.to_dict()
            peis = paciente_data.get('peis', [])
            
            original_len = len(peis)
            peis = [pei for pei in peis if pei.get('id') != pei_id]

            if len(peis) == original_len:
                flash('PEI não encontrado para exclusão.', 'warning')
            else:
                paciente_ref.update({'peis': peis})
                flash('PEI excluído com sucesso!', 'success')

        except Exception as e:
            flash(f'Erro ao excluir PEI: {e}', 'danger')
            print(f"Erro delete_pei: {e}")
        
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    @app.route('/pacientes/<string:paciente_doc_id>/prontuario/delete_goal', methods=['POST'], endpoint='delete_goal')
    @login_required
    def delete_goal(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        
        try:
            pei_id = request.form.get('pei_id')
            goal_id = request.form.get('goal_id')

            if not all([pei_id, goal_id]):
                flash('Dados insuficientes para excluir meta.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

            paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
            paciente_doc = paciente_ref.get()
            
            if not paciente_doc.exists:
                flash('Paciente não encontrado.', 'danger')
                return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
            
            paciente_data = paciente_doc.to_dict()
            peis = paciente_data.get('peis', [])
            
            pei_found = False
            for pei in peis:
                if pei.get('id') == pei_id:
                    pei_found = True
                    original_goals_len = len(pei.get('goals', []))
                    pei['goals'] = [goal for goal in pei.get('goals', []) if goal.get('id') != goal_id]
                    
                    if len(pei['goals']) == original_goals_len:
                        flash('Meta não encontrada para exclusão.', 'warning')
                    else:
                        paciente_ref.update({'peis': peis})
                        flash('Meta excluída com sucesso!', 'success')
                    break
            if not pei_found:
                flash('PEI da meta não encontrado.', 'danger')

        except Exception as e:
            flash(f'Erro ao excluir meta: {e}', 'danger')
            print(f"Erro delete_goal: {e}")
        
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
                modelo = convert_doc_to_dict(doc)
                modelos_lista.append(modelo)
        except Exception as e:
            flash(f'Erro ao listar modelos de anamnese: {e}.', 'danger')
            print(f"Erro list_anamnesis_templates: {e}")
        return render_template('modelos_anamnese.html', modelos=modelos_lista)

    @app.route('/modelos_anamnese/novo', methods=['GET', 'POST'], endpoint='adicionar_modelo_anamnese')
    @login_required
    @admin_required
    def adicionar_modelo_anamnese():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        if request.method == 'POST':
            identificacao = request.form['identificacao'].strip()
            conteudo_modelo = request.form['conteudo_modelo']
            
            if not identificacao:
                flash('A identificação do modelo é obrigatória.', 'danger')
                return render_template('modelo_anamnese_form.html', modelo=request.form, action_url=url_for('adicionar_modelo_anamnese'))
            try:
                db_instance.collection('clinicas').document(clinica_id).collection('modelos_anamnese').add({
                    'identificacao': identificacao,
                    'conteudo_modelo': conteudo_modelo,
                    'criado_em': firestore.SERVER_TIMESTAMP
                })
                flash('Modelo de anamnese adicionado com sucesso!', 'success')
                return redirect(url_for('listar_modelos_anamnese'))
            except Exception as e:
                flash(f'Erro ao adicionar modelo de anamnese: {e}', 'danger')
                print(f"Erro add_anamnesis_template: {e}")
        return render_template('modelo_anamnese_form.html', modelo=None, action_url=url_for('adicionar_modelo_anamnese'))

    @app.route('/modelos_anamnese/editar/<string:modelo_doc_id>', methods=['GET', 'POST'], endpoint='editar_modelo_anamnese')
    @login_required
    @admin_required
    def editar_modelo_anamnese(modelo_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        modelo_ref = db_instance.collection('clinicas').document(clinica_id).collection('modelos_anamnese').document(modelo_doc_id)
        
        if request.method == 'POST':
            identificacao = request.form['identificacao'].strip()
            conteudo_modelo = request.form['conteudo_modelo']
            
            if not identificacao:
                flash('A identificação do modelo é obrigatória.', 'danger')
                return render_template('modelo_anamnese_form.html', modelo=request.form, action_url=url_for('editar_modelo_anamnese', modelo_doc_id=modelo_doc_id))
            try:
                modelo_ref.update({
                    'identificacao': identificacao,
                    'conteudo_modelo': conteudo_modelo,
                    'atualizado_em': firestore.SERVER_TIMESTAMP
                })
                flash('Modelo de anamnese atualizado com sucesso!', 'success')
                return redirect(url_for('listar_modelos_anamnese'))
            except Exception as e:
                flash(f'Erro ao atualizar modelo de anamnese: {e}', 'danger')
                print(f"Erro edit_anamnesis_template (POST): {e}")

        try:
            modelo_doc = modelo_ref.get()
            if modelo_doc.exists:
                modelo = convert_doc_to_dict(modelo_doc)
                return render_template('modelo_anamnese_form.html', modelo=modelo, action_url=url_for('editar_modelo_anamnese', modelo_doc_id=modelo_doc_id))
            else:
                flash('Modelo de anamnese não encontrado.', 'danger')
                return redirect(url_for('listar_modelos_anamnese'))
        except Exception as e:
            flash(f'Erro ao carregar modelo de anamnese para edição: {e}', 'danger')
            print(f"Erro edit_anamnesis_template (GET): {e}")
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
            flash(f'Erro ao excluir modelo de anamnese: {e}', 'danger')
            print(f"Erro delete_anamnesis_template: {e}")
        return redirect(url_for('listar_modelos_anamnese'))
