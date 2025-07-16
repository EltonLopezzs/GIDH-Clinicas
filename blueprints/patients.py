from flask import render_template, session, flash, redirect, url_for, request, jsonify
import json
import datetime
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore # Importar no topo


# Importar utils
from utils import get_db, login_required, SAO_PAULO_TZ, parse_date_input


def register_patients_routes(app):
    @app.route('/pacientes', endpoint='listar_pacientes')
    @login_required
    def listar_pacientes():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        pacientes_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes')
        convenios_ref = db_instance.collection('clinicas').document(clinica_id).collection('convenios')
        pacientes_lista = []
        
        convenios_dict = {}
        convenios_lista = [] # Inicializa a lista de convênios para passar ao template
        try:
            convenios_docs = convenios_ref.stream()
            for doc in convenios_docs:
                conv_data = doc.to_dict()
                if conv_data:
                    convenios_dict[doc.id] = conv_data.get('nome', 'Convênio Desconhecido')
                    convenios_lista.append({'id': doc.id, 'nome': conv_data.get('nome', doc.id)}) # Adiciona à lista
        except Exception as e:
            print(f"Erro ao carregar convênios para pacientes: {e}")
            flash('Erro ao carregar informações de convênios.', 'danger')

        try:
            search_query = request.args.get('search', '').strip()
            
            query = pacientes_ref.order_by('nome')

            if search_query:
                # Busca por nome do paciente
                query_nome_paciente = pacientes_ref.where(filter=FieldFilter('nome', '>=', search_query))\
                                             .where(filter=FieldFilter('nome', '<=', search_query + '\uf8ff'))
                # Busca por telefone do paciente
                query_telefone_paciente = pacientes_ref.order_by('contato_telefone')\
                                             .where(filter=FieldFilter('contato_telefone', '>=', search_query))\
                                             .where(filter=FieldFilter('contato_telefone', '<=', search_query + '\uf8ff'))
                # Busca por nome do responsável 1
                query_responsavel1_nome = pacientes_ref.where(filter=FieldFilter('responsavel1_nome', '>=', search_query))\
                                             .where(filter=FieldFilter('responsavel1_nome', '<=', search_query + '\uf8ff'))
                # Busca por telefone do responsável 1
                query_responsavel1_telefone = pacientes_ref.order_by('responsavel1_telefone')\
                                             .where(filter=FieldFilter('responsavel1_telefone', '>=', search_query))\
                                             .where(filter=FieldFilter('responsavel1_telefone', '<=', search_query + '\uf8ff'))
                # Busca por nome do responsável 2
                query_responsavel2_nome = pacientes_ref.where(filter=FieldFilter('responsavel2_nome', '>=', search_query))\
                                             .where(filter=FieldFilter('responsavel2_nome', '<=', search_query + '\uf8ff'))
                # Busca por telefone do responsável 2
                query_responsavel2_telefone = pacientes_ref.order_by('responsavel2_telefone')\
                                             .where(filter=FieldFilter('responsavel2_telefone', '>=', search_query))\
                                             .where(filter=FieldFilter('responsavel2_telefone', '<=', search_query + '\uf8ff'))
                
                pacientes_set = set()
                
                # Adiciona resultados de todas as consultas ao set
                for doc in query_nome_paciente.stream():
                    paciente_data = doc.to_dict()
                    if paciente_data:
                        paciente_data['id'] = doc.id
                        if paciente_data.get('convenio_id') and paciente_data['convenio_id'] in convenios_dict:
                            paciente_data['convenio_nome'] = convenios_dict[paciente_data['convenio_id']]
                        else:
                            paciente_data['convenio_nome'] = 'Particular'
                        pacientes_set.add(json.dumps(paciente_data, sort_keys=True))
                
                for doc in query_telefone_paciente.stream():
                    paciente_data = doc.to_dict()
                    if paciente_data:
                        paciente_data['id'] = doc.id
                        if paciente_data.get('convenio_id') and paciente_data['convenio_id'] in convenios_dict:
                            paciente_data['convenio_nome'] = convenios_dict[paciente_data['convenio_id']]
                        else:
                            paciente_data['convenio_nome'] = 'Particular'
                        pacientes_set.add(json.dumps(paciente_data, sort_keys=True))

                for doc in query_responsavel1_nome.stream():
                    paciente_data = doc.to_dict()
                    if paciente_data:
                        paciente_data['id'] = doc.id
                        if paciente_data.get('convenio_id') and paciente_data['convenio_id'] in convenios_dict:
                            paciente_data['convenio_nome'] = convenios_dict[paciente_data['convenio_id']]
                        else:
                            paciente_data['convenio_nome'] = 'Particular'
                        pacientes_set.add(json.dumps(paciente_data, sort_keys=True))

                for doc in query_responsavel1_telefone.stream():
                    paciente_data = doc.to_dict()
                    if paciente_data:
                        paciente_data['id'] = doc.id
                        if paciente_data.get('convenio_id') and paciente_data['convenio_id'] in convenios_dict:
                            paciente_data['convenio_nome'] = convenios_dict[paciente_data['convenio_id']]
                        else:
                            paciente_data['convenio_nome'] = 'Particular'
                        pacientes_set.add(json.dumps(paciente_data, sort_keys=True))

                for doc in query_responsavel2_nome.stream():
                    paciente_data = doc.to_dict()
                    if paciente_data:
                        paciente_data['id'] = doc.id
                        if paciente_data.get('convenio_id') and paciente_data['convenio_id'] in convenios_dict:
                            paciente_data['convenio_nome'] = convenios_dict[paciente_data['convenio_id']]
                        else:
                            paciente_data['convenio_nome'] = 'Particular'
                        pacientes_set.add(json.dumps(paciente_data, sort_keys=True))

                for doc in query_responsavel2_telefone.stream():
                    paciente_data = doc.to_dict()
                    if paciente_data:
                        paciente_data['id'] = doc.id
                        if paciente_data.get('convenio_id') and paciente_data['convenio_id'] in convenios_dict:
                            paciente_data['convenio_nome'] = convenios_dict[paciente_data['convenio_id']]
                        else:
                            paciente_data['convenio_nome'] = 'Particular'
                        pacientes_set.add(json.dumps(paciente_data, sort_keys=True))
                
                pacientes_lista = [json.loads(p) for p in pacientes_set]
                pacientes_lista.sort(key=lambda x: x.get('nome', ''))
            else:
                docs = query.stream()
                for doc in docs:
                    paciente = doc.to_dict()
                    if paciente:
                        paciente['id'] = doc.id
                        if paciente.get('convenio_id') and paciente['convenio_id'] in convenios_dict:
                            paciente['convenio_nome'] = convenios_dict[paciente['convenio_id']]
                        else:
                            paciente['convenio_nome'] = 'Particular'
                        pacientes_lista.append(paciente)

        except Exception as e:
            flash(f'Erro ao listar pacientes: {e}. Verifique seus índices do Firestore.', 'danger')
            print(f"Erro list_patients: {e}")
        
        stats_cards = {
            'confirmado': {'count': 0, 'total_valor': 0.0},
            'concluido': {'count': 0, 'total_valor': 0.0},
            'cancelado': {'count': 0, 'total_valor': 0.0},
            'pendente': {'count': 0, 'total_valor': 0.0}
        }

        return render_template('pacientes.html', pacientes=pacientes_lista, search_query=search_query, convenios=convenios_lista)

    @app.route('/pacientes/novo', methods=['GET', 'POST'], endpoint='adicionar_paciente')
    @login_required
    def adicionar_paciente():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        
        convenios_lista = []
        try:
            convenios_docs = db_instance.collection('clinicas').document(clinica_id).collection('convenios').order_by('nome').stream()
            for doc in convenios_docs:
                conv_data = doc.to_dict()
                if conv_data:
                    convenios_lista.append({'id': doc.id, 'nome': conv_data.get('nome', doc.id)})
        except Exception as e:
            flash('Erro ao carregar convênios.', 'danger')
            print(f"Erro ao carregar convênios (add_patient GET): {e}")

        if request.method == 'POST':
            nome = request.form['nome'].strip()
            data_nascimento = request.form.get('data_nascimento', '').strip()
            cpf = request.form.get('cpf', '').strip()
            rg = request.form.get('rg', '').strip()
            genero = request.form.get('genero', '').strip()
            estado_civil = request.form.get('estado_civil', '').strip()
            telefone = request.form.get('telefone', '').strip()
            email = request.form.get('email', '').strip()
            indicacao = request.form.get('indicacao', '').strip()
            convenio_id = request.form.get('convenio_id', '').strip()
            observacoes = request.form.get('observacoes', '').strip()

            cep = request.form.get('cep', '').strip()
            logradouro = request.form.get('logradouro', '').strip()
            numero = request.form.get('numero', '').strip()
            complemento = request.form.get('complemento', '').strip()
            bairro = request.form.get('bairro', '').strip()
            cidade = request.form.get('cidade', '').strip()
            estado = request.form.get('estado', '').strip()

            # Novos campos de responsável
            responsavel1_nome = request.form.get('responsavel1_nome', '').strip()
            responsavel1_telefone = request.form.get('responsavel1_telefone', '').strip()
            responsavel2_nome = request.form.get('responsavel2_nome', '').strip()
            responsavel2_telefone = request.form.get('responsavel2_telefone', '').strip()


            if not nome:
                flash('O nome do paciente é obrigatório.', 'danger')
                return render_template('paciente_form.html', paciente=request.form, action_url=url_for('adicionar_paciente'), convenios=convenios_lista)

            try:
                data_nascimento_dt = parse_date_input(data_nascimento)
                
                if data_nascimento and data_nascimento_dt is None:
                    flash('Formato de data de nascimento inválido. Use AAAA-MM-DD ou DD/MM/YYYY.', 'danger')
                    return render_template('paciente_form.html', paciente=request.form, action_url=url_for('adicionar_paciente'), convenios=convenios_lista)

                paciente_data = {
                    'nome': nome,
                    'data_nascimento': data_nascimento_dt,
                    'cpf': cpf if cpf else None,
                    'rg': rg if rg else None,
                    'genero': genero if genero else None,
                    'estado_civil': estado_civil if estado_civil else None,
                    'contato_telefone': telefone if telefone else None,
                    'contato_email': email if email else None,
                    'indicacao': indicacao if indicacao else None,
                    'convenio_id': convenio_id if convenio_id else None,
                    'observacoes': observacoes if observacoes else None,
                    'endereco': {
                        'cep': cep if cep else None,
                        'logradouro': logradouro if logradouro else None,
                        'numero': numero if numero else None,
                        'complemento': complemento if complemento else None,
                        'bairro': bairro if bairro else None,
                        'cidade': cidade if cidade else None,
                        'estado': estado if estado else None,
                    },
                    'responsavel1_nome': responsavel1_nome if responsavel1_nome else None,
                    'responsavel1_telefone': responsavel1_telefone if responsavel1_telefone else None,
                    'responsavel2_nome': responsavel2_nome if responsavel2_nome else None,
                    'responsavel2_telefone': responsavel2_telefone if responsavel2_telefone else None,
                    'data_cadastro': firestore.SERVER_TIMESTAMP
                }
                
                # Adiciona o paciente e obtém a referência do documento
                # Correção: Desempacota a tupla retornada por .add() para obter a referência do documento
                _, doc_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').add(paciente_data)
                
                # Atualiza o documento recém-criado com o id_paciente, usando o ID gerado pelo Firestore
                doc_ref.update({'id_paciente': doc_ref.id})

                flash('Paciente adicionado com sucesso!', 'success')
                return redirect(url_for('listar_pacientes'))
            except Exception as e:
                flash(f'Erro ao adicionar paciente: {e}', 'danger')
                print(f"Erro add_patient: {e}")
        
        return render_template('paciente_form.html', paciente=None, action_url=url_for('adicionar_paciente'), convenios=convenios_lista)

    @app.route('/pacientes/editar/<string:paciente_doc_id>', methods=['GET', 'POST'], endpoint='editar_paciente')
    @login_required
    def editar_paciente(paciente_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
        
        convenios_lista = []
        try:
            convenios_docs = db_instance.collection('clinicas').document(clinica_id).collection('convenios').order_by('nome').stream()
            for doc in convenios_docs:
                conv_data = doc.to_dict()
                if conv_data:
                    convenios_lista.append({'id': doc.id, 'nome': conv_data.get('nome', doc.id)})
        except Exception as e:
            flash('Erro ao carregar convênios.', 'danger')
            print(f"Erro ao carregar convênios (edit_patient GET): {e}")

        if request.method == 'POST':
            nome = request.form['nome'].strip()
            data_nascimento = request.form.get('data_nascimento', '').strip()
            cpf = request.form.get('cpf', '').strip()
            rg = request.form.get('rg', '').strip()
            genero = request.form.get('genero', '').strip()
            estado_civil = request.form.get('estado_civil', '').strip()
            telefone = request.form.get('telefone', '').strip()
            email = request.form.get('email', '').strip()
            indicacao = request.form.get('indicacao', '').strip()
            convenio_id = request.form.get('convenio_id', '').strip()
            observacoes = request.form.get('observacoes', '').strip()

            cep = request.form.get('cep', '').strip()
            logradouro = request.form.get('logradouro', '').strip()
            numero = request.form.get('numero', '').strip()
            complemento = request.form.get('complemento', '').strip()
            bairro = request.form.get('bairro', '').strip()
            cidade = request.form.get('cidade', '').strip()
            estado = request.form.get('estado', '').strip()

            # Novos campos de responsável
            responsavel1_nome = request.form.get('responsavel1_nome', '').strip()
            responsavel1_telefone = request.form.get('responsavel1_telefone', '').strip()
            responsavel2_nome = request.form.get('responsavel2_nome', '').strip()
            responsavel2_telefone = request.form.get('responsavel2_telefone', '').strip()

            if not nome:
                flash('O nome do paciente é obrigatório.', 'danger')
                return render_template('paciente_form.html', paciente=request.form, action_url=url_for('editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista)

            try:
                data_nascimento_dt = parse_date_input(data_nascimento)
                
                if data_nascimento and data_nascimento_dt is None:
                    flash('Formato de data de nascimento inválido. Use AAAA-MM-DD ou DD/MM/YYYY.', 'danger')
                    return render_template('paciente_form.html', paciente=request.form, action_url=url_for('editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista)

                paciente_data_update = {
                    'nome': nome,
                    'data_nascimento': data_nascimento_dt,
                    'cpf': cpf if cpf else None,
                    'rg': rg if rg else None,
                    'genero': genero if genero else None,
                    'estado_civil': estado_civil if estado_civil else None,
                    'contato_telefone': telefone if telefone else None,
                    'contato_email': email if email else None,
                    'indicacao': indicacao if indicacao else None,
                    'convenio_id': convenio_id if convenio_id else None,
                    'observacoes': observacoes if observacoes else None,
                    'endereco': {
                        'cep': cep if cep else None,
                        'logradouro': logradouro if logradouro else None,
                        'numero': numero if numero else None,
                        'complemento': complemento if complemento else None,
                        'bairro': bairro if bairro else None,
                        'cidade': cidade if cidade else None,
                        'estado': estado if estado else None,
                    },
                    'responsavel1_nome': responsavel1_nome if responsavel1_nome else None,
                    'responsavel1_telefone': responsavel1_telefone if responsavel1_telefone else None,
                    'responsavel2_nome': responsavel2_nome if responsavel2_nome else None,
                    'responsavel2_telefone': responsavel2_telefone if responsavel2_telefone else None,
                    'atualizado_em': firestore.SERVER_TIMESTAMP
                }
                
                paciente_ref.update(paciente_data_update)
                flash('Paciente atualizado com sucesso!', 'success')
                return redirect(url_for('listar_pacientes'))
            except Exception as e:
                flash(f'Erro ao atualizar paciente: {e}', 'danger')
                print(f"Erro edit_patient (POST): {e}")

        try:
            paciente_doc = paciente_ref.get()
            if paciente_doc.exists:
                paciente = paciente_doc.to_dict()
                if paciente:
                    paciente['id'] = paciente_doc.id
                    if paciente.get('data_nascimento') and isinstance(paciente['data_nascimento'], datetime.date):
                        paciente['data_nascimento'] = paciente['data_nascimento'].strftime('%Y-%m-%d')
                    elif isinstance(paciente.get('data_nascimento'), datetime.datetime):
                        paciente['data_nascimento'] = paciente['data_nascimento'].date().strftime('%Y-%m-%d')
                    else:
                        paciente['data_nascimento'] = ''

                    return render_template('paciente_form.html', paciente=paciente, action_url=url_for('editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista)
            else:
                flash('Paciente não encontrado.', 'danger')
                return redirect(url_for('listar_pacientes'))
        except Exception as e:
            flash(f'Erro ao carregar paciente para edição: {e}', 'danger')
            print(f"Erro edit_patient (GET): {e}")
            return redirect(url_for('listar_pacientes'))

    @app.route('/pacientes/<paciente_doc_id>/excluir', methods=['POST'], endpoint='excluir_paciente')
    @login_required
    def excluir_paciente(paciente_doc_id):
        try:
            db_instance = get_db()
            clinica_id = session['clinica_id']
            
            # Verificar se o paciente existe
            paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
            paciente_doc = paciente_ref.get()
            
            if not paciente_doc.exists:
                return jsonify({'success': False, 'message': 'Paciente não encontrado'}), 404
            
            paciente_nome = paciente_doc.to_dict().get('nome', 'Paciente')
            
            # Excluir o paciente
            paciente_ref.delete()
            
            flash(f'Paciente {paciente_nome} excluído com sucesso.', 'success')
            return jsonify({'success': True, 'message': f'Paciente {paciente_nome} excluído com sucesso'}), 200
            
        except Exception as e:
            print(f"Erro ao excluir paciente: {e}")
            return jsonify({'success': False, 'message': 'Erro interno do servidor'}), 500
