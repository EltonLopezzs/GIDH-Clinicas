from flask import Blueprint, render_template, session, redirect, url_for, flash, request, jsonify
# Importar login_required e admin_required do utils
from utils import login_required, admin_required, get_db, convert_doc_to_dict, SAO_PAULO_TZ, parse_date_input, get_all_protocols_with_items, get_patient_evaluations, create_evaluation, add_protocol_to_evaluation, get_evaluation_details, save_evaluation_task_response, update_evaluation_status, delete_evaluation, get_protocol_by_id, delete_linked_protocol_and_tasks, save_evaluation_scoring_response
import datetime
import json
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.colors import black
import io
from google.cloud import firestore # Importar firestore aqui
from reportlab.lib.units import inch # Importar para espaçamento

evaluations_bp = Blueprint('evaluations', __name__)

@evaluations_bp.route('/avaliacoes', methods=['GET'])
@login_required
def list_patients_for_evaluation():
    """
    Lista todos os pacientes para que o usuário possa selecionar um para avaliação.
    """
    db = get_db()
    clinica_id = session['clinica_id']
    pacientes_lista = []
    try:
        pacientes_docs = db.collection('clinicas').document(clinica_id).collection('pacientes').order_by('nome').stream()
        for doc in pacientes_docs:
            paciente_data = convert_doc_to_dict(doc)
            if paciente_data:
                # Adiciona TODAS as avaliações recentes do paciente para que o frontend possa ordenar
                recent_evaluations = get_patient_evaluations(clinica_id, paciente_data['id'])
                paciente_data['avaliacoes_recentes'] = recent_evaluations if recent_evaluations else []
                pacientes_lista.append(paciente_data)
    except Exception as e:
        flash(f'Erro ao carregar pacientes para avaliação: {e}', 'danger')
        print(f"Erro em list_patients_for_evaluation: {e}")
    
    return render_template('avaliacoes.html', pacientes=pacientes_lista, now=datetime.datetime.now(SAO_PAULO_TZ))

@evaluations_bp.route('/avaliacoes/paciente/<patient_id>', methods=['GET'])
@login_required
def patient_evaluation_page(patient_id):
    """
    Exibe a página de avaliações de um paciente específico, listando avaliações existentes
    e permitindo criar novas. Esta página agora será o ponto de entrada para gerenciar
    múltiplas avaliações para um paciente.
    """
    db = get_db()
    clinica_id = session['clinica_id']
    
    patient_data = None
    try:
        patient_doc = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).get()
        if patient_doc.exists:
            patient_data = convert_doc_to_dict(patient_doc)
            # Formatar a data de nascimento para exibição
            if patient_data.get('data_nascimento') and isinstance(patient_data['data_nascimento'], datetime.datetime):
                patient_data['data_nascimento_fmt'] = patient_data['data_nascimento'].strftime('%d/%m/%Y')
            elif patient_data.get('data_nascimento') and isinstance(patient_data['data_nascimento'], datetime.date):
                patient_data['data_nascimento_fmt'] = patient_data['data_nascimento'].strftime('%d/%m/%Y')
            else:
                patient_data['data_nascimento_fmt'] = 'N/A'
        else:
            flash('Paciente não encontrado.', 'danger')
            return redirect(url_for('evaluations.list_patients_for_evaluation'))
    except Exception as e:
        flash(f'Erro ao carregar dados do paciente: {e}', 'danger')
        print(f"Erro ao carregar paciente {patient_id} para avaliação: {e}")
        return redirect(url_for('evaluations.list_patients_for_evaluation'))

    # Obter todas as avaliações do paciente
    evaluations = get_patient_evaluations(clinica_id, patient_id)
    
    # Obter todos os protocolos disponíveis para vincular (agora para o modal de criação)
    available_protocols = get_all_protocols_with_items(clinica_id)

    # Renderiza a página principal de avaliações do paciente (que lista as avaliações)
    return render_template(
        'avaliacao_paciente.html', # Este é o template que lista as avaliações do paciente
        patient=patient_data,
        evaluations=evaluations,
        available_protocols=available_protocols, # Pode ser útil para o modal de criação
        now=datetime.datetime.now(SAO_PAULO_TZ)
    )

@evaluations_bp.route('/avaliacoes/criar/<patient_id>', methods=['POST'])
@login_required
def create_new_evaluation(patient_id):
    """
    Cria uma nova avaliação para o paciente.
    Esta rota agora apenas cria a avaliação principal, sem vincular protocolos imediatamente.
    """
    clinica_id = session['clinica_id']
    professional_id = session.get('user_uid') # Ou o ID do profissional associado ao user_uid
    
    if not professional_id:
        flash('ID do profissional não encontrado na sessão.', 'danger')
        return redirect(url_for('evaluations.patient_evaluation_page', patient_id=patient_id))

    evaluation_date_str = request.form.get('evaluation_date')
    evaluation_date = parse_date_input(evaluation_date_str)
    if not evaluation_date:
        flash('Data da avaliação inválida.', 'danger')
        return redirect(url_for('evaluations.patient_evaluation_page', patient_id=patient_id))

    new_evaluation_id = create_evaluation(clinica_id, patient_id, professional_id, evaluation_date)

    if new_evaluation_id:
        flash('Nova avaliação criada com sucesso!', 'success')
        # Redireciona para a página de detalhes da avaliação recém-criada
        return redirect(url_for('evaluations.view_evaluation', patient_id=patient_id, evaluation_id=new_evaluation_id))
    else:
        flash('Erro ao criar nova avaliação.', 'danger')
        return redirect(url_for('evaluations.patient_evaluation_page', patient_id=patient_id))

@evaluations_bp.route('/avaliacoes/<patient_id>/<evaluation_id>', methods=['GET'])
@login_required
def view_evaluation(patient_id, evaluation_id):
    """
    Visualiza os detalhes de uma avaliação específica, exibindo os protocolos vinculados como cards.
    Esta rota agora renderiza 'avaliacao_detalhes.html'.
    """
    clinica_id = session['clinica_id']
    
    patient_data = None
    evaluation_details = None
    available_protocols = [] # Para o modal de vincular novo protocolo

    try:
        patient_doc = get_db().collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).get()
        if patient_doc.exists:
            patient_data = convert_doc_to_dict(patient_doc)
        else:
            flash('Paciente não encontrado.', 'danger')
            return redirect(url_for('evaluations.list_patients_for_evaluation'))

        evaluation_details = get_evaluation_details(clinica_id, patient_id, evaluation_id)
        if not evaluation_details:
            flash('Avaliação não encontrada.', 'danger')
            return redirect(url_for('evaluations.patient_evaluation_page', patient_id=patient_id))
        
        # Formatar a data da avaliação para exibição
        if evaluation_details.get('data_avaliacao') and isinstance(evaluation_details['data_avaliacao'], datetime.datetime):
            evaluation_details['data_avaliacao_fmt'] = evaluation_details['data_avaliacao'].strftime('%d/%m/%Y')
        else:
            evaluation_details['data_avaliacao_fmt'] = 'N/A'

        # Obter todos os protocolos disponíveis para vincular (para o modal)
        available_protocols = get_all_protocols_with_items(clinica_id)

    except Exception as e:
        flash(f'Erro ao carregar detalhes da avaliação: {e}', 'danger')
        print(f"Erro em view_evaluation para paciente {patient_id}, avaliação {evaluation_id}: {e}")
        return redirect(url_for('evaluations.patient_evaluation_page', patient_id=patient_id))

    # Renderiza o novo template de detalhes da avaliação
    return render_template(
        'avaliacao_detalhes.html', # NOVO TEMPLATE
        patient=patient_data,
        evaluation=evaluation_details,
        available_protocols=available_protocols,
        now=datetime.datetime.now(SAO_PAULO_TZ)
    )

@evaluations_bp.route('/avaliacoes/<patient_id>/<evaluation_id>/protocol/<linked_protocol_instance_id>', methods=['GET']) 
@login_required
def view_protocol_tasks(patient_id, evaluation_id, linked_protocol_instance_id): 
    """
    Exibe as tarefas de um protocolo específico vinculado a uma avaliação.
    Agora, busca as tarefas e pontuações do SNAPSHOT armazenado na avaliação,
    e os níveis do snapshot também.
    """
    db = get_db()
    clinica_id = session['clinica_id']

    patient_data = None
    evaluation_details = None
    protocol_data = None # Representa o snapshot do protocolo vinculado
    tasks = []
    protocol_levels = [] # Lista de níveis do snapshot do protocolo
    protocol_scoring = [] # Lista de pontuações do snapshot do protocolo

    try:
        # 1. Obter dados do paciente
        patient_doc = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).get()
        if patient_doc.exists:
            patient_data = convert_doc_to_dict(patient_doc)
        else:
            flash('Paciente não encontrado.', 'danger')
            return redirect(url_for('evaluations.list_patients_for_evaluation'))

        # 2. Obter detalhes da avaliação
        evaluation_details = get_evaluation_details(clinica_id, patient_id, evaluation_id)
        if not evaluation_details:
            flash('Avaliação não encontrada.', 'danger')
            return redirect(url_for('evaluations.patient_evaluation_page', patient_id=patient_id))
        
        # Formatar a data da avaliação para exibição
        if evaluation_details.get('data_avaliacao') and isinstance(evaluation_details['data_avaliacao'], datetime.datetime):
            evaluation_details['data_avaliacao_fmt'] = evaluation_details['data_avaliacao'].strftime('%d/%m/%Y')
        else:
            evaluation_details['data_avaliacao_fmt'] = 'N/A'

        # Encontrar a instância do protocolo vinculado para obter o master_protocol_id
        current_linked_protocol_instance = None
        for linked_proto in evaluation_details.get('protocolos_vinculados', []):
            if linked_proto.get('id') == linked_protocol_instance_id: # Match by instance ID
                current_linked_protocol_instance = linked_proto
                break
        
        if not current_linked_protocol_instance:
            flash('Protocolo vinculado não encontrado para esta avaliação.', 'danger')
            return redirect(url_for('evaluations.view_evaluation', patient_id=patient_id, evaluation_id=evaluation_id))

        # 3. Obter os dados do SNAPSHOT do protocolo vinculado
        # Isso inclui os níveis e as habilidades que foram copiados no momento da vinculação.
        # As tarefas e pontuações serão buscadas de subcoleções do snapshot.
        linked_protocol_doc_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('avaliacoes').document(evaluation_id).collection('protocolos_vinculados').document(linked_protocol_instance_id)
        
        protocol_data = convert_doc_to_dict(linked_protocol_doc_ref.get())
        if not protocol_data:
            flash('Erro: Snapshot do protocolo vinculado não encontrado.', 'danger')
            return redirect(url_for('evaluations.view_evaluation', patient_id=patient_id, evaluation_id=evaluation_id))

        # Extrair níveis do snapshot (armazenados diretamente no documento da instância vinculada)
        protocol_levels = sorted(protocol_data.get('niveis_snapshot', []), key=lambda x: x.get('nivel', 0))

        # Obter as tarefas do SNAPSHOT (subcoleção 'tarefas_snapshot')
        tasks_snapshot_ref = linked_protocol_doc_ref.collection('tarefas_snapshot')
        snapshot_tasks = []
        for task_snap_doc in tasks_snapshot_ref.order_by('nivel').order_by('ordem').stream():
            snapshot_tasks.append(convert_doc_to_dict(task_snap_doc))

        # Obter os critérios de pontuação do SNAPSHOT (subcoleção 'pontuacao_snapshot')
        scoring_snapshot_ref = linked_protocol_doc_ref.collection('pontuacao_snapshot')
        protocol_scoring = []
        for score_snap_doc in scoring_snapshot_ref.order_by('ordem').stream():
            protocol_scoring.append(convert_doc_to_dict(score_snap_doc))

        # 4. Obter as tarefas avaliadas para esta instância do protocolo
        # e mesclar com os dados do snapshot para ter todas as informações da tarefa.
        # A 'tarefas_avaliadas' contém a resposta e info adicional, mas não todos os detalhes da tarefa.
        # Os detalhes da tarefa vêm do snapshot.
        tasks_evaluated_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('avaliacoes').document(evaluation_id).collection('tarefas_avaliadas')
        
        # Query tasks by linked_protocol_instance_id
        tasks_query = tasks_evaluated_ref.where(
            filter=firestore.FieldFilter('linked_protocol_instance_id', '==', linked_protocol_instance_id)
        ).stream()

        # Mapear tarefas avaliadas por protocol_item_id para fácil lookup
        evaluated_tasks_map = {t.get('protocol_item_id'): t for t in evaluation_details.get('tarefas_avaliadas', []) if t.get('linked_protocol_instance_id') == linked_protocol_instance_id}
        
        # Construir a lista final de tarefas para o template, mesclando snapshot com respostas
        tasks = []
        for task_snap in snapshot_tasks:
            task_id_from_master = task_snap.get('protocol_item_id')
            evaluated_task = evaluated_tasks_map.get(task_id_from_master)
            
            merged_task = {
                'id': evaluated_task['id'] if evaluated_task else None, # ID do documento em 'tarefas_avaliadas'
                'protocol_item_id': task_snap.get('protocol_item_id'), # ID do item original do protocolo
                'nivel': task_snap.get('nivel'),
                'item_numero': task_snap.get('item_numero'),
                'nome_tarefa': task_snap.get('nome_tarefa'),
                'habilidade_marco': task_snap.get('habilidade_marco'),
                'exemplo': task_snap.get('exemplo', ''),
                'criterio': task_snap.get('criterio', ''),
                'pergunta': task_snap.get('pergunta', ''),
                'objetivo': task_snap.get('objetivo', ''),
                'response_value': evaluated_task.get('response_value', '') if evaluated_task else '',
                'additional_info': evaluated_task.get('additional_info', '') if evaluated_task else '',
                'data_resposta': evaluated_task.get('data_resposta') if evaluated_task else None,
                'status': evaluated_task.get('status', 'pendente') if evaluated_task else 'pendente'
            }
            tasks.append(merged_task)

        # Ordenar as tarefas mescladas para garantir a ordem correta no frontend
        tasks = sorted(tasks, key=lambda x: (x.get('nivel', 0), x.get('item_numero', '')))


    except Exception as e:
        flash(f'Erro ao carregar tarefas do protocolo: {e}', 'danger')
        print(f"Erro em view_protocol_tasks para paciente {patient_id}, avaliação {evaluation_id}, instância de protocolo {linked_protocol_instance_id}: {e}")
        return redirect(url_for('evaluations.view_evaluation', patient_id=patient_id, evaluation_id=evaluation_id))

    return render_template(
        'avaliacao_protocolo_tarefas.html',
        patient=patient_data,
        evaluation=evaluation_details,
        protocol=protocol_data, # Agora é o snapshot do protocolo vinculado
        tasks=tasks,
        protocol_levels=protocol_levels, # Níveis do snapshot
        protocol_scoring=protocol_scoring, # Pontuação do snapshot
        # applied_scoring_responses não é mais necessário aqui, pois a lógica de botões foi alterada
        now=datetime.datetime.now(SAO_PAULO_TZ)
    )

@evaluations_bp.route('/api/protocols/<protocol_id>/levels', methods=['GET'])
@login_required
def get_protocol_levels(protocol_id):
    """
    Retorna os níveis de um protocolo específico.
    Esta rota pode não ser mais necessária para o fluxo de vinculação,
    mas é mantida para compatibilidade ou outras funcionalidades.
    """
    db = get_db()
    clinica_id = session['clinica_id']
    levels_list = []
    try:
        # Busca o documento do protocolo para obter a subcoleção 'niveis'
        protocol_doc_ref = db.collection('clinicas').document(clinica_id).collection('protocols').document(protocol_id)
        
        # Acessa a subcoleção 'niveis'
        levels_ref = protocol_doc_ref.collection('niveis')
        
        # Itera sobre os documentos na subcoleção 'niveis'
        for level_doc in levels_ref.order_by('nivel').stream():
            level_data = convert_doc_to_dict(level_doc)
            if level_data:
                levels_list.append(level_data)
        
        if not levels_list:
            print(f"Nenhum nível encontrado para o protocolo {protocol_id} na clínica {clinica_id}.")
            return jsonify({'success': False, 'message': 'Nenhum nível encontrado para este protocolo.'}), 404

        return jsonify({'success': True, 'levels': levels_list})
    except Exception as e:
        print(f"Erro ao buscar níveis do protocolo {protocol_id}: {e}")
        return jsonify({'success': False, 'message': f'Erro interno: {str(e)}'}), 500


@evaluations_bp.route('/api/avaliacoes/vincular_protocolo', methods=['POST'])
@login_required
def api_link_protocol_to_evaluation():
    """
    API para vincular um protocolo a uma avaliação.
    Recebe patient_id, evaluation_id, protocol_id, protocol_name.
    O nível não é mais selecionado aqui, o protocolo inteiro é vinculado.
    """
    data = request.get_json()
    patient_id = data.get('patient_id')
    evaluation_id = data.get('evaluation_id')
    protocol_id = data.get('protocol_id')
    protocol_name = data.get('protocol_name')
    # protocol_level = data.get('protocol_level') # REMOVIDO
    clinica_id = session['clinica_id']

    if not all([patient_id, evaluation_id, protocol_id, protocol_name]): # Removido protocol_level da validação
        return jsonify({'success': False, 'message': 'Dados incompletos para vincular protocolo.'}), 400

    # Chamada para add_protocol_to_evaluation sem protocol_level
    success = add_protocol_to_evaluation(clinica_id, patient_id, evaluation_id, protocol_id, protocol_name)

    if success:
        return jsonify({'success': True, 'message': 'Protocolo vinculado e tarefas adicionadas com sucesso!'})
    else:
        return jsonify({'success': False, 'message': 'Erro ao vincular protocolo.'}), 500

@evaluations_bp.route('/api/avaliacoes/salvar_resposta_tarefa', methods=['POST'])
@login_required
def api_save_task_response():
    """
    API para salvar a resposta de uma tarefa específica dentro de uma avaliação.
    Recebe patient_id, evaluation_id, task_id, response_value, additional_info.
    """
    data = request.get_json()
    patient_id = data.get('patient_id')
    evaluation_id = data.get('evaluation_id')
    task_id = data.get('task_id') # Este é o ID do documento em 'tarefas_avaliadas'
    response_value = data.get('response_value')
    additional_info = data.get('additional_info', '')
    clinica_id = session['clinica_id']

    # Alterado para permitir response_value vazio, mas ainda requer os outros campos
    if not all([patient_id, evaluation_id, task_id]):
        return jsonify({'success': False, 'message': 'Dados incompletos para salvar resposta da tarefa.'}), 400

    success = save_evaluation_task_response(clinica_id, patient_id, evaluation_id, task_id, response_value, additional_info)

    if success:
        return jsonify({'success': True, 'message': 'Resposta da tarefa salva com sucesso!'})
    else:
        return jsonify({'success': False, 'message': 'Erro ao salvar resposta da tarefa.'}), 500

@evaluations_bp.route('/api/avaliacoes/finalizar/<patient_id>/<evaluation_id>', methods=['POST'])
@login_required
def api_finalize_evaluation(patient_id, evaluation_id):
    """
    API para finalizar uma avaliação.
    """
    clinica_id = session['clinica_id']
    success = update_evaluation_status(clinica_id, patient_id, evaluation_id, 'finalizado')

    if success:
        return jsonify({'success': True, 'message': 'Avaliação finalizada com sucesso!'})
    else:
        return jsonify({'success': False, 'message': 'Erro ao finalizar avaliação.'}), 500

@evaluations_bp.route('/api/avaliacoes/excluir/<patient_id>/<evaluation_id>', methods=['DELETE'])
@login_required
@admin_required # Apenas administradores podem excluir avaliações completas
def api_delete_evaluation(patient_id, evaluation_id):
    """
    API para excluir uma avaliação.
    """
    clinica_id = session['clinica_id']
    success = delete_evaluation(clinica_id, patient_id, evaluation_id)

    if success:
        return jsonify({'success': True, 'message': 'Avaliação excluída com sucesso!'})
    else:
        return jsonify({'success': False, 'message': 'Erro ao excluir avaliação.'}), 500

@evaluations_bp.route('/api/avaliacoes/desvincular_protocolo/<patient_id>/<evaluation_id>/<linked_protocol_instance_id>', methods=['DELETE']) 
@login_required
def api_remove_linked_protocol(patient_id, evaluation_id, linked_protocol_instance_id): 
    """
    API para desvincular um protocolo de uma avaliação e remover suas tarefas associadas.
    """
    clinica_id = session['clinica_id']
    success = delete_linked_protocol_and_tasks(clinica_id, patient_id, evaluation_id, linked_protocol_instance_id) 

    if success:
        return jsonify({'success': True, 'message': 'Protocolo desvinculado e tarefas removidas com sucesso!'})
    else:
        return jsonify({'success': False, 'message': 'Erro ao desvincular protocolo.'}), 500

@evaluations_bp.route('/api/avaliacoes/salvar_pontuacao_criterio', methods=['POST'])
@login_required
def api_save_scoring_response():
    """
    API para salvar a resposta de um critério de pontuação específico dentro de uma avaliação.
    """
    data = request.get_json()
    patient_id = data.get('patient_id')
    evaluation_id = data.get('evaluation_id')
    scoring_applied_id = data.get('scoring_applied_id') # Este é o ID do documento em 'pontuacoes_avaliadas'
    applied_value = data.get('applied_value')
    clinica_id = session['clinica_id']

    if not all([patient_id, evaluation_id, scoring_applied_id is not None, applied_value is not None]):
        return jsonify({'success': False, 'message': 'Dados incompletos para salvar resposta do critério de pontuação.'}), 400

    success = save_evaluation_scoring_response(clinica_id, patient_id, evaluation_id, scoring_applied_id, applied_value)

    if success:
        return jsonify({'success': True, 'message': 'Critério de pontuação salvo com sucesso!'})
    else:
        return jsonify({'success': False, 'message': 'Erro ao salvar critério de pontuação.'}), 500


@evaluations_bp.route('/avaliacoes/gerar_pdf/<patient_id>/<evaluation_id>', methods=['GET'])
@login_required
def generate_evaluation_pdf(patient_id, evaluation_id):
    """
    Gera um PDF da avaliação.
    """
    clinica_id = session['clinica_id']
    evaluation_details = get_evaluation_details(clinica_id, patient_id, evaluation_id)
    
    if not evaluation_details:
        flash('Avaliação não encontrada para gerar PDF.', 'danger')
        return redirect(url_for('evaluations.view_evaluation', patient_id=patient_id, evaluation_id=evaluation_id)) # Corrigido para patient_id e evaluation_id

    patient_data = get_db().collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).get()
    patient_name = patient_data.to_dict().get('nome', 'Paciente Desconhecido') if patient_data.exists else 'Paciente Desconhecido'

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()

    # Estilos personalizados
    styles.add(ParagraphStyle(name='TitleStyle', fontSize=24, leading=28, alignment=TA_CENTER, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='Heading1', fontSize=18, leading=22, spaceAfter=14, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='Heading2', fontSize=14, leading=18, spaceAfter=10, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='Normal', fontSize=10, leading=12, spaceAfter=6))
    styles.add(ParagraphStyle(name='Italic', fontSize=10, leading=12, spaceAfter=6, fontName='Helvetica-Oblique'))
    styles.add(ParagraphStyle(name='TaskQuestion', fontSize=12, leading=14, spaceAfter=6, fontName='Helvetica-Bold', textColor=black))
    styles.add(ParagraphStyle(name='TaskAnswer', fontSize=10, leading=12, spaceAfter=4, leftIndent=10))

    story = []

    # Título
    story.append(Paragraph(f"Relatório de Avaliação - {patient_name}", styles['TitleStyle']))
    story.append(Paragraph(f"Data da Avaliação: {evaluation_details.get('data_avaliacao_fmt', 'N/A')}", styles['Heading2']))
    story.append(Spacer(1, 0.2 * inch))

    # Informações do Paciente
    story.append(Paragraph("Informações do Paciente:", styles['Heading1']))
    story.append(Paragraph(f"Nome: {patient_name}", styles['Normal']))
    patient_dob = patient_data.to_dict().get('data_nascimento')
    if patient_dob and isinstance(patient_dob, datetime.datetime):
        story.append(Paragraph(f"Data de Nascimento: {patient_dob.strftime('%d/%m/%Y')}", styles['Normal']))
    else:
        story.append(Paragraph("Data de Nascimento: N/A", styles['Normal']))
    story.append(Paragraph(f"Status da Avaliação: {evaluation_details.get('status', 'N/A').capitalize()}", styles['Normal']))
    story.append(Spacer(1, 0.4 * inch))

    # Protocolos Vinculados
    story.append(Paragraph("Protocolos Vinculados:", styles['Heading1']))
    if evaluation_details.get('protocolos_vinculados'):
        for proto_link in evaluation_details['protocolos_vinculados']:
            # Removido a exibição do nível aqui, pois o protocolo inteiro é vinculado
            story.append(Paragraph(f"• {proto_link.get('protocol_name', 'N/A')}", styles['Normal']))
    else:
        story.append(Paragraph("Nenhum protocolo vinculado a esta avaliação.", styles['Italic']))
    story.append(Spacer(1, 0.4 * inch))

    # Tarefas Avaliadas
    story.append(Paragraph("Tarefas Avaliadas:", styles['Heading1']))
    if evaluation_details.get('tarefas_avaliadas'):
        # Agrupar tarefas por linked_protocol_instance_id, nome do protocolo e nível para melhor organização
        grouped_tasks = {}
        for task in evaluation_details['tarefas_avaliadas']:
            linked_proto_instance_id = task.get('linked_protocol_instance_id')
            protocol_name = None # Será buscado do linked_protocol_instance
            nivel = task.get('nivel', 'N/A')

            # Buscar o nome do protocolo a partir do linked_protocol_instance_id
            for linked_proto in evaluation_details.get('protocolos_vinculados', []):
                if linked_proto.get('id') == linked_proto_instance_id:
                    protocol_name = linked_proto.get('protocol_name', 'Protocolo Desconhecido')
                    break
            
            if not protocol_name: # Fallback se não encontrar
                protocol_name = 'Protocolo Desconhecido'
            
            # Use uma tupla para agrupar por instância de protocolo e nome do protocolo
            group_key = (linked_proto_instance_id, protocol_name) 

            if group_key not in grouped_tasks:
                grouped_tasks[group_key] = {}
            if nivel not in grouped_tasks[group_key]:
                grouped_tasks[group_key][nivel] = []
            grouped_tasks[group_key][nivel].append(task)
        
        # Ordenar os grupos para exibição consistente
        sorted_group_keys = sorted(grouped_tasks.keys(), key=lambda x: x[1]) # Ordena pelo nome do protocolo

        for group_key in sorted_group_keys:
            linked_proto_instance_id, protocol_name = group_key
            levels = grouped_tasks[group_key]
            
            story.append(Spacer(1, 0.2 * inch))
            story.append(Paragraph(f"Protocolo: {protocol_name}", styles['Heading2']))
            
            for nivel in sorted(levels.keys()):
                story.append(Paragraph(f"Nível: {nivel}", styles['Heading2']))
                for task in levels[nivel]:
                    task_text = f"Item {task.get('item_numero', 'N/A')}: {task.get('nome_tarefa', 'N/A')}"
                    story.append(Paragraph(task_text, styles['TaskQuestion']))
                    story.append(Paragraph(f"Resposta: {task.get('response_value', 'Não Respondido')}", styles['TaskAnswer']))
                    if task.get('additional_info'):
                        story.append(Paragraph(f"Observações: {task.get('additional_info')}", styles['TaskAnswer']))
                    story.append(Spacer(1, 0.1 * inch))
    else:
        story.append(Paragraph("Nenhuma tarefa avaliada nesta avaliação.", styles['Italic']))

    # Pontuação Aplicada
    story.append(PageBreak()) # Nova página para a seção de pontuação
    story.append(Paragraph("Pontuação Aplicada:", styles['Heading1']))
    if evaluation_details.get('pontuacoes_avaliadas'):
        # Agrupar pontuações por linked_protocol_instance_id e nome do protocolo
        grouped_scoring = {}
        for score_entry in evaluation_details['pontuacoes_avaliadas']:
            linked_proto_instance_id = score_entry.get('linked_protocol_instance_id')
            protocol_name = None

            for linked_proto in evaluation_details.get('protocolos_vinculados', []):
                if linked_proto.get('id') == linked_proto_instance_id:
                    protocol_name = linked_proto.get('protocol_name', 'Protocolo Desconhecido')
                    break
            
            if not protocol_name:
                protocol_name = 'Protocolo Desconhecido'
            
            group_key = (linked_proto_instance_id, protocol_name)

            if group_key not in grouped_scoring:
                grouped_scoring[group_key] = []
            grouped_scoring[group_key].append(score_entry)
        
        sorted_scoring_keys = sorted(grouped_scoring.keys(), key=lambda x: x[1])

        for group_key in sorted_scoring_keys:
            linked_proto_instance_id, protocol_name = group_key
            scoring_entries = grouped_scoring[group_key]

            story.append(Spacer(1, 0.2 * inch))
            story.append(Paragraph(f"Protocolo: {protocol_name}", styles['Heading2']))
            
            for entry in scoring_entries:
                status = "Aplicado" if entry.get('aplicado') else "Não Aplicado"
                data_aplicacao = entry.get('data_aplicacao_fmt', 'N/A')
                story.append(Paragraph(f"• {entry.get('descricao', 'N/A')} (Valor: {entry.get('valor', 'N/A')}) - Status: {status} (Data: {data_aplicacao})", styles['Normal']))
    else:
        story.append(Paragraph("Nenhum critério de pontuação aplicado nesta avaliação.", styles['Italic']))


    doc.build(story)
    buffer.seek(0)

    return buffer.getvalue(), 200, {
        'Content-Type': 'application/pdf',
        'Content-Disposition': f'attachment; filename=avaliacao_{patient_name.replace(" ", "_")}_{evaluation_id}.pdf'
    }
