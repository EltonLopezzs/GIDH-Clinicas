from flask import render_template, session, flash, redirect, url_for, request
import datetime
import json
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore # Importar no topo


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
                query_nome = pacientes_ref.where(filter=FieldFilter('nome', '>=', search_query))\
                                         .where(filter=FieldFilter('nome', '<=', search_query + '\uf8ff'))
                
                query_cpf = pacientes_ref.where(filter=FieldFilter('cpf', '==', search_query))

                pacientes_set = set()
                for doc in query_nome.stream():
                    paciente_data = doc.to_dict()
                    if paciente_data:
                        paciente_data['id'] = doc.id
                        pacientes_set.add(json.dumps(paciente_data, sort_keys=True))
                
                for doc in query_cpf.stream():
                    paciente_data = doc.to_dict()
                    if paciente_data:
                        paciente_data['id'] = doc.id
                        pacientes_set.add(json.dumps(paciente_data, sort_keys=True))
                
                pacientes_para_busca = [json.loads(p) for p in pacientes_set]
                pacientes_para_busca.sort(key=lambda x: x.get('nome', ''))

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
        
        try:
            paciente_doc = paciente_ref.get()
            if paciente_doc.exists:
                paciente_data = paciente_doc.to_dict()
                paciente_data['id'] = paciente_doc.id

                if paciente_data.get('convenio_id'):
                    convenio_doc = db_instance.collection('clinicas').document(clinica_id).collection('convenios').document(paciente_data['convenio_id']).get()
                    if convenio_doc.exists:
                        paciente_data['convenio_nome'] = convenio_doc.to_dict().get('nome', 'N/A')
                
                docs_stream = prontuarios_ref.order_by('data_registro', direction=firestore.Query.DESCENDING).stream()
                for doc in docs_stream:
                    registro = doc.to_dict()
                    if registro:
                        registro['id'] = doc.id
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

                        registros_prontuario.append(registro)
            else:
                flash('Paciente não encontrado.', 'danger')
                return redirect(url_for('buscar_prontuario'))
        except Exception as e:
            flash(f'Erro ao carregar prontuário do paciente: {e}.', 'danger')
            print(f"Erro view_patient_record: {e}")

        return render_template('prontuario.html', paciente=paciente_data, registros=registros_prontuario)

    @app.route('/prontuarios/<string:paciente_doc_id>/anamnese/novo', methods=['GET', 'POST'], endpoint='adicionar_anamnese')
    @login_required
    def adicionar_anamnese(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        profissional_logado_uid = session.get('user_uid')    

        profissional_doc_id = None
        profissional_nome = "Profissional Desconhecido"
        try:
            prof_query = db_instance.collection('clinicas').document(clinica_id).collection('profissionais')\
                                         .where(filter=FieldFilter('user_uid', '==', profissional_logado_uid)).limit(1).get()
            for doc in prof_query:
                profissional_doc_id = doc.id
                profissional_nome = doc.to_dict().get('nome', profissional_nome)
                break
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
        try:
            prof_query = db_instance.collection('clinicas').document(clinica_id).collection('profissionais')\
                                         .where(filter=FieldFilter('user_uid', '==', profissional_logado_uid)).limit(1).get()
            for doc in prof_query:
                profissional_doc_id = doc.id
                break
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
                anamnese_data['profissional_id_fk'] = profissional_doc_id
                
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
            prof_query = db_instance.collection('clinicas').document(clinica_id).collection('profissionais') \
                                         .where(filter=FieldFilter('user_uid', '==', profissional_logado_uid)).limit(1).get()
            for doc in prof_query:
                profissional_doc_id = doc.id
                profissional_nome = doc.to_dict().get('nome', profissional_nome)
                break
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
        clinica_id = session