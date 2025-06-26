import datetime
from flask import Blueprint, render_template, session, flash, redirect, url_for, request, current_app
from google.cloud.firestore_v1.base_query import FieldFilter
from decorators.auth_decorators import login_required # Import decorators
from utils.firestore_utils import convert_doc_to_dict, parse_date_input # Import utility functions

patients_bp = Blueprint('patients_bp', __name__)

@patients_bp.route('/pacientes')
@login_required
def listar_pacientes():
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    pacientes_ref = db.collection('clinicas').document(clinica_id).collection('pacientes')
    convenios_ref = db.collection('clinicas').document(clinica_id).collection('convenios')
    pacientes_lista = []
    
    convenios_dict = {}
    try:
        convenios_docs = convenios_ref.stream()
        for doc in convenios_docs:
            convenios_dict[doc.id] = doc.to_dict().get('nome', 'Convênio Desconhecido')
    except Exception as e:
        print(f"Erro ao carregar convênios para pacientes: {e}")
        flash('Erro ao carregar informações de convênios.', 'danger')

    try:
        search_query = request.args.get('search', '').strip()
        
        query = pacientes_ref.order_by('nome')

        docs = query.stream()
        for doc in docs:
            paciente = doc.to_dict()
            if paciente:
                paciente['id'] = doc.id
                if paciente.get('convenio_id') and paciente['convenio_id'] in convenios_dict:
                    paciente['convenio_nome'] = convenios_dict[paciente['convenio_id']]
                else:
                    paciente['convenio_nome'] = 'Particular'
                
                # Simple client-side search simulation
                if search_query:
                    if search_query.lower() in paciente.get('nome', '').lower() or \
                       search_query in paciente.get('contato_telefone', ''):
                        pacientes_lista.append(paciente)
                else:
                    pacientes_lista.append(paciente)

    except Exception as e:
        flash(f'Erro ao listar pacientes: {e}.', 'danger')
        print(f"Erro list_patients: {e}")
    
    return render_template('pacientes.html', pacientes=pacientes_lista, search_query=search_query)

@patients_bp.route('/pacientes/novo', methods=['GET', 'POST'])
@login_required
def adicionar_paciente():
    db = current_app.config['DB']

    if not db:
        flash('Erro crítico: A conexão com o banco de dados não está disponível.', 'danger')
        return redirect(url_for('dashboard_bp.index'))

    clinica_id = session['clinica_id']
    
    convenios_lista = []
    peis_disponiveis = []
    try:
        convenios_docs = db.collection('clinicas').document(clinica_id).collection('convenios').order_by('nome').stream()
        for doc in convenios_docs:
            conv_data = doc.to_dict()
            if conv_data:
                convenios_lista.append({'id': doc.id, 'nome': conv_data.get('nome', doc.id)})

        peis_docs = db.collection('clinicas').document(clinica_id).collection('peis').order_by('identificacao_pei').stream()
        for doc in peis_docs:
            pei_data_template = doc.to_dict()
            if pei_data_template:
                peis_disponiveis.append({'id': doc.id, 'identificacao': pei_data_template.get('identificacao_pei', doc.id)})

    except Exception as e:
        flash('Erro ao carregar convênios ou PEIs.', 'danger')
        print(f"Erro ao carregar convênios/PEIs (add_patient GET): {e}")

    if request.method == 'POST':
        nome = request.form['nome'].strip()
        data_nascimento = request.form.get('data_nascimento', '').strip()
        cpf = request.form.get('cpf', '').strip()
        rg = request.form.get('rg', '').strip()
        genero = request.form.get('genero', '').strip()
        # Removido: estado_civil = request.form.get('estado_civil', '').strip() # Não existe no formulário
        telefone = request.form.get('telefone', '').strip()
        email = request.form.get('email', '').strip()
        # Removido: indicacao = request.form.get('indicacao', '').strip() # Não existe no formulário
        convenio_id = request.form.get('convenio_id', '').strip()
        observacoes = request.form.get('observacoes', '').strip()
        cep = request.form.get('cep', '').strip()
        logradouro = request.form.get('logradouro', '').strip()
        numero = request.form.get('numero', '').strip()
        complemento = request.form.get('complemento', '').strip()
        bairro = request.form.get('bairro', '').strip()
        cidade = request.form.get('cidade', '').strip()
        estado = request.form.get('estado', '').strip()
        peis_associados_ids = request.form.getlist('peis_associados')

        if not nome:
            flash('O nome do paciente é obrigatório.', 'danger')
            return render_template('paciente_form.html', paciente=request.form, action_url=url_for('patients_bp.adicionar_paciente'), convenios=convenios_lista, peis_disponiveis=peis_disponiveis)

        try:
            data_nascimento_dt = parse_date_input(data_nascimento)
            
            if data_nascimento and data_nascimento_dt is None:
                flash('Formato de data de nascimento inválido. Use AAAA-MM-DD ou DD/MM/YYYY.', 'danger')
                return render_template('paciente_form.html', paciente=request.form, action_url=url_for('patients_bp.adicionar_paciente'), convenios=convenios_lista, peis_disponiveis=peis_disponiveis)

            paciente_data = {
                'nome': nome,
                'data_nascimento': data_nascimento_dt,
                'cpf': cpf if cpf else None,
                'rg': rg if rg else None,
                'genero': genero if genero else None,
                'contato_telefone': telefone if telefone else None,
                'contato_email': email if email else None,
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
                'peis_associados': peis_associados_ids,
                'data_cadastro': db.SERVER_TIMESTAMP
            }
            
            batch = db.batch()
            pacientes_col_ref = db.collection('clinicas').document(clinica_id).collection('pacientes')
            new_paciente_ref = pacientes_col_ref.document()
            batch.set(new_paciente_ref, paciente_data)

            for pei_template_id in peis_associados_ids:
                pei_template_doc = db.collection('clinicas').document(clinica_id).collection('peis').document(pei_template_id).get()
                if pei_template_doc.exists:
                    pei_data = pei_template_doc.to_dict()
                    if pei_data is None:
                        print(f"Aviso: pei_template_doc.to_dict() retornou None para ID {pei_template_id}")
                        continue
                    
                    pei_data['pei_template_id'] = pei_template_id
                    
                    # Certifica-se de que 'metas' é uma lista antes de iterar
                    metas_list = pei_data.get('metas')
                    if not isinstance(metas_list, list):
                        print(f"Aviso: 'metas' para PEI {pei_template_id} não é uma lista. Forçando para lista vazia.")
                        metas_list = []
                    
                    for meta in metas_list:
                        if 'status' not in meta:
                            meta['status'] = 'Não Iniciada'
                        if 'tempo_total_gasto' not in meta:
                            meta['tempo_total_gasto'] = 0
                        if 'cronometro_inicio' not in meta:
                            meta['cronometro_inicio'] = None
                    pei_data['metas'] = metas_list # Atribui a lista tratada de volta

                    individual_pei_ref = new_paciente_ref.collection('peis_individuais').document(pei_template_id)
                    batch.set(individual_pei_ref, pei_data)

            batch.commit()

            flash('Paciente adicionado com sucesso!', 'success')
            return redirect(url_for('patients_bp.listar_pacientes'))
        except Exception as e:
            flash(f'Erro ao adicionar paciente: {e}', 'danger')
            print(f"Erro add_patient: {e}")
    
    return render_template('paciente_form.html', paciente=None, action_url=url_for('patients_bp.adicionar_paciente'), convenios=convenios_lista, peis_disponiveis=peis_disponiveis)

@patients_bp.route('/pacientes/editar/<string:paciente_doc_id>', methods=['GET', 'POST'])
@login_required
def editar_paciente(paciente_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
    
    convenios_lista = []
    peis_disponiveis = []
    
    # Existing associated PEIs (template IDs) from the patient document
    current_associated_peis = [] 

    try:
        convenios_docs = db.collection('clinicas').document(clinica_id).collection('convenios').order_by('nome').stream()
        for doc in convenios_docs:
            conv_data = doc.to_dict()
            if conv_data:
                convenios_lista.append({'id': doc.id, 'nome': conv_data.get('nome', doc.id)})

        # Fetch all available PEI templates
        peis_docs = db.collection('clinicas').document(clinica_id).collection('peis').order_by('identificacao_pei').stream()
        for doc in peis_docs:
            pei_data = doc.to_dict()
            if pei_data:
                peis_disponiveis.append({'id': doc.id, 'identificacao': pei_data.get('identificacao_pei', doc.id)})

        # Get the patient's current data to retrieve existing associated PEIs
        paciente_doc_current = paciente_ref.get()
        if paciente_doc_current.exists:
            current_associated_peis = paciente_doc_current.to_dict().get('peis_associados', [])

    except Exception as e:
        flash('Erro ao carregar convênios ou PEIs.', 'danger')
        print(f"Erro ao carregar convênios/PEIs (edit_patient GET): {e}")

    if request.method == 'POST':
        nome = request.form['nome'].strip()
        data_nascimento = request.form.get('data_nascimento', '').strip()
        cpf = request.form.get('cpf', '').strip()
        rg = request.form.get('rg', '').strip()
        genero = request.form.get('genero', '').strip()
        # Removido: estado_civil = request.form.get('estado_civil', '').strip()
        telefone = request.form.get('telefone', '').strip()
        email = request.form.get('email', '').strip()  
        # Removido: indicacao = request.form.get('indicacao', '').strip()
        convenio_id = request.form.get('convenio_id', '').strip()
        observacoes = request.form.get('observacoes', '').strip()
        
        # Get the new list of associated PEI template IDs from the form
        new_peis_associados_ids = request.form.getlist('peis_associados')

        cep = request.form.get('cep', '').strip()
        logradouro = request.form.get('logradouro', '').strip()
        numero = request.form.get('numero', '').strip()
        complemento = request.form.get('complemento', '').strip()
        bairro = request.form.get('bairro', '').strip()
        cidade = request.form.get('cidade', '').strip()
        estado = request.form.get('estado', '').strip()

        if not nome:
            flash('O nome do paciente é obrigatório.', 'danger')
            return render_template('paciente_form.html', paciente=request.form, action_url=url_for('patients_bp.editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista, peis_disponiveis=peis_disponiveis)

        try:
            data_nascimento_dt = parse_date_input(data_nascimento)
            
            if data_nascimento and data_nascimento_dt is None:
                flash('Formato de data de nascimento inválido. Use AAAA-MM-DD ou DD/MM/YYYY.', 'danger')
                return render_template('paciente_form.html', paciente=request.form, action_url=url_for('patients_bp.adicionar_paciente'), convenios=convenios_lista, peis_disponiveis=peis_disponiveis)

            paciente_data_update = {
                'nome': nome,
                'data_nascimento': data_nascimento_dt,
                'cpf': cpf if cpf else None,
                'rg': rg if rg else None,
                'genero': genero if genero else None,
                'contato_telefone': telefone if telefone else None, 
                'contato_email': email if email else None,  
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
                'peis_associados': new_peis_associados_ids,
                'atualizado_em': db.SERVER_TIMESTAMP
            }
            
            batch = db.batch()
            batch.update(paciente_ref, paciente_data_update)

            # Synchronize the 'peis_individuais' subcollection
            existing_individual_peis_docs = paciente_ref.collection('peis_individuais').stream()
            existing_individual_pei_ids = {doc.id for doc in existing_individual_peis_docs}

            # Add new PEIs to subcollection if they don't exist
            for pei_template_id in new_peis_associados_ids:
                if pei_template_id not in existing_individual_pei_ids:
                    pei_template_doc = db.collection('clinicas').document(clinica_id).collection('peis').document(pei_template_id).get()
                    if pei_template_doc.exists:
                        pei_data = pei_template_doc.to_dict()
                        if pei_data is None:
                            print(f"Aviso: pei_template_doc.to_dict() retornou None para ID {pei_template_id}")
                            continue
                            
                        pei_data['pei_template_id'] = pei_template_id
                        
                        metas_list = pei_data.get('metas')
                        if not isinstance(metas_list, list):
                            print(f"Aviso: 'metas' para PEI {pei_template_id} não é uma lista. Forçando para lista vazia.")
                            metas_list = []

                        for meta in metas_list:
                            if 'status' not in meta:
                                meta['status'] = 'Não Iniciada'
                            if 'tempo_total_gasto' not in meta:
                                meta['tempo_total_gasto'] = 0
                            if 'cronometro_inicio' not in meta:
                                meta['cronometro_inicio'] = None
                        pei_data['metas'] = metas_list

                        individual_pei_ref = paciente_ref.collection('peis_individuais').document(pei_template_id)
                        batch.set(individual_pei_ref, pei_data)

            # Remove PEIs from subcollection if they are no longer associated
            for existing_pei_id in existing_individual_pei_ids:
                if existing_pei_id not in new_peis_associados_ids:
                    batch.delete(paciente_ref.collection('peis_individuais').document(existing_pei_id))

            batch.commit()
            
            flash('Paciente atualizado com sucesso!', 'success')
            return redirect(url_for('patients_bp.listar_pacientes'))
        except Exception as e:
            flash(f'Erro ao atualizar paciente: {e}', 'danger')
            print(f"Erro edit_patient (POST): {e}")  

   
    try:
        paciente_doc = paciente_ref.get()
        if paciente_doc.exists:
            paciente = paciente_doc.to_dict()
            if paciente:
                paciente['id'] = paciente_doc.id
                
                if paciente.get('data_nascimento') and isinstance(paciente.get('data_nascimento'), datetime.datetime):
                    paciente['data_nascimento'] = paciente['data_nascimento'].strftime('%Y-%m-%d')
                else:
                    paciente['data_nascimento'] = ''
 
                paciente['genero'] = paciente.get('genero', '')
                paciente['cpf'] = paciente.get('cpf', '')
                paciente['rg'] = paciente.get('rg', '')
                paciente['contato_telefone'] = paciente.get('contato_telefone', '')  
                paciente['contato_email'] = paciente.get('contato_email', '') 

                if 'endereco' not in paciente or not isinstance(paciente['endereco'], dict):
                    paciente['endereco'] = {}
                paciente['endereco']['cep'] = paciente['endereco'].get('cep', '')
                paciente['endereco']['logradouro'] = paciente['endereco'].get('logradouro', '')
                paciente['endereco']['numero'] = paciente['endereco'].get('numero', '')
                paciente['endereco']['complemento'] = paciente['endereco'].get('complemento', '')
                paciente['endereco']['bairro'] = paciente['endereco'].get('bairro', '')
                paciente['endereco']['cidade'] = paciente['endereco'].get('cidade', '')
                paciente['endereco']['estado'] = paciente['endereco'].get('estado', '')

                paciente['peis_associados_ids'] = paciente.get('peis_associados', [])  

                print("Dados do paciente carregados para edição:", paciente)
           
                return render_template('paciente_form.html', paciente=paciente, action_url=url_for('patients_bp.editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista, peis_disponiveis=peis_disponiveis)
        else:
            flash('Paciente não encontrado.', 'danger')
            return redirect(url_for('patients_bp.listar_pacientes'))
    except Exception as e:
        flash(f'Erro ao carregar paciente para edição: {e}', 'danger')
        print(f"Erro edit_patient (GET): {e}")  
        return redirect(url_for('patients_bp.listar_pacientes'))