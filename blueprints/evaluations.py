from flask import Blueprint, render_template, session, redirect, url_for, flash, request, jsonify
# Importar login_required e admin_required do utils
from utils import login_required, admin_required, get_db, convert_doc_to_dict, SAO_PAULO_TZ, parse_date_input, get_all_protocols_with_items, get_patient_evaluations, create_evaluation, add_protocol_to_evaluation, get_evaluation_details, save_evaluation_task_response, update_evaluation_status, delete_evaluation, get_protocol_by_id
import datetime
import json
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.colors import black
import io

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
                # Adiciona as avaliações recentes do paciente para exibição
                paciente_data['avaliacoes_recentes'] = get_patient_evaluations(clinica_id, paciente_data['id'])
                pacientes_lista.append(paciente_data)
    except Exception as e:
        flash(f'Erro ao carregar pacientes para avaliação: {e}', 'danger')
        print(f"Erro em list_patients_for_evaluation: {e}")
    
    # Passa a data e hora atuais para o template
    return render_template('avaliacoes.html', pacientes=pacientes_lista, now=datetime.datetime.now(SAO_PAULO_TZ))

@evaluations_bp.route('/avaliacoes/paciente/<patient_id>', methods=['GET'])
@login_required
def patient_evaluation_page(patient_id):
    """
    Exibe a página de avaliação de um paciente específico, listando avaliações existentes
    e permitindo criar novas.
    """
    db = get_db()
    clinica_id = session['clinica_id']
    
    patient_data = None
    try:
        patient_doc = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).get()
        if patient_doc.exists:
            patient_data = convert_doc_to_dict(patient_doc)
        else:
            flash('Paciente não encontrado.', 'danger')
            return redirect(url_for('evaluations.list_patients_for_evaluation'))
    except Exception as e:
        flash(f'Erro ao carregar dados do paciente: {e}', 'danger')
        print(f"Erro ao carregar paciente {patient_id} para avaliação: {e}")
        return redirect(url_for('evaluations.list_patients_for_evaluation'))

    # Obter todas as avaliações do paciente
    evaluations = get_patient_evaluations(clinica_id, patient_id)
    
    # Obter todos os protocolos disponíveis para vincular
    available_protocols = get_all_protocols_with_items(clinica_id)

    # Passa a data e hora atuais para o template
    return render_template(
        'avaliacao_paciente.html',
        patient=patient_data,
        evaluations=evaluations,
        available_protocols=available_protocols,
        now=datetime.datetime.now(SAO_PAULO_TZ)
    )

@evaluations_bp.route('/avaliacoes/criar/<patient_id>', methods=['POST'])
@login_required
def create_new_evaluation(patient_id):
    """
    Cria uma nova avaliação para o paciente.
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
        return redirect(url_for('evaluations.view_evaluation', patient_id=patient_id, evaluation_id=new_evaluation_id))
    else:
        flash('Erro ao criar nova avaliação.', 'danger')
        return redirect(url_for('evaluations.patient_evaluation_page', patient_id=patient_id))

@evaluations_bp.route('/avaliacoes/<patient_id>/<evaluation_id>', methods=['GET'])
@login_required
def view_evaluation(patient_id, evaluation_id):
    """
    Visualiza os detalhes de uma avaliação específica, exibindo os protocolos vinculados como cards.
    """
    clinica_id = session['clinica_id']
    
    patient_data = None
    evaluation_details = None
    available_protocols = []

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

        # Modificado para obter os níveis junto com os protocolos
        available_protocols = get_all_protocols_with_items(clinica_id)

    except Exception as e:
        flash(f'Erro ao carregar detalhes da avaliação: {e}', 'danger')
        print(f"Erro em view_evaluation para paciente {patient_id}, avaliação {evaluation_id}: {e}")
        return redirect(url_for('evaluations.patient_evaluation_page', patient_id=patient_id))

    # Passa a data e hora atuais para o template
    return render_template(
        'avaliacao_detalhes.html',
        patient=patient_data,
        evaluation=evaluation_details,
        available_protocols=available_protocols,
        now=datetime.datetime.now(SAO_PAULO_TZ)
    )

@evaluations_bp.route('/avaliacoes/<patient_id>/<evaluation_id>/protocol/<linked_protocol_id>', methods=['GET'])
@login_required
def view_protocol_tasks(patient_id, evaluation_id, linked_protocol_id):
    """
    Exibe as tarefas de um protocolo específico vinculado a uma avaliação.
    """
    db = get_db()
    clinica_id = session['clinica_id']

    patient_data = None
    evaluation_details = None
    protocol_data = None
    tasks = []
    linked_protocol_level = None

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

        # 3. Obter dados do protocolo (apenas o nome, etc., do protocolo mestre)
        protocol_data = get_protocol_by_id(clinica_id, linked_protocol_id)
        if not protocol_data:
            flash('Protocolo não encontrado.', 'danger')
            return redirect(url_for('evaluations.view_evaluation', patient_id=patient_id, evaluation_id=evaluation_id))

        # 4. Encontrar o nível do protocolo vinculado nesta avaliação
        for linked_proto in evaluation_details.get('protocolos_vinculados', []):
            if linked_proto.get('protocol_id') == linked_protocol_id:
                linked_protocol_level = linked_proto.get('protocol_level')
                break
        
        if linked_protocol_level is None:
            flash('Nível do protocolo vinculado não encontrado para esta avaliação.', 'danger')
            return redirect(url_for('evaluations.view_evaluation', patient_id=patient_id, evaluation_id=evaluation_id))

        # 5. Obter as tarefas específicas para este protocolo e nível dentro da avaliação
        # As tarefas já estão carregadas em evaluation_details['tarefas_avaliadas']
        # Precisamos filtrar por protocol_id e nivel
        
        # Adicionando logs de depuração
        print(f"DEBUG: linked_protocol_id: {linked_protocol_id} (type: {type(linked_protocol_id)})")
        print(f"DEBUG: linked_protocol_level: {linked_protocol_level} (type: {type(linked_protocol_level)})")
        print(f"DEBUG: Total tasks in evaluation_details: {len(evaluation_details.get('tarefas_avaliadas', []))}")

        tasks = []
        for task in evaluation_details.get('tarefas_avaliadas', []):
            task_protocol_id = task.get('protocol_id')
            task_nivel = task.get('nivel')
            
            print(f"DEBUG: Task ID: {task.get('id')}, Protocol ID: {task_protocol_id} (type: {type(task_protocol_id)}), Nivel: {task_nivel} (type: {type(task_nivel)})")

            # Garante que a comparação de nível seja entre inteiros
            try:
                if task_protocol_id == linked_protocol_id and int(task_nivel) == int(linked_protocol_level):
                    tasks.append(task)
            except (ValueError, TypeError) as e:
                print(f"DEBUG: Erro de conversão de tipo para nível na tarefa {task.get('id')}: {e}")
                continue # Pula esta tarefa se o nível não puder ser convertido para int
        
        print(f"DEBUG: Tasks filtered for display: {len(tasks)}")

    except Exception as e:
        flash(f'Erro ao carregar tarefas do protocolo: {e}', 'danger')
        print(f"Erro em view_protocol_tasks para paciente {patient_id}, avaliação {evaluation_id}, protocolo {linked_protocol_id}: {e}")
        return redirect(url_for('evaluations.view_evaluation', patient_id=patient_id, evaluation_id=evaluation_id))

    return render_template(
        'avaliacao_protocolo_tarefas.html',
        patient=patient_data,
        evaluation=evaluation_details,
        protocol=protocol_data, # O protocolo mestre
        linked_protocol_level=linked_protocol_level, # O nível específico vinculado
        tasks=tasks, # As tarefas filtradas
        now=datetime.datetime.now(SAO_PAULO_TZ)
    )

@evaluations_bp.route('/api/protocols/<protocol_id>/levels', methods=['GET'])
@login_required
def get_protocol_levels(protocol_id):
    """
    Retorna os níveis de um protocolo específico.
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
    Recebe patient_id, evaluation_id, protocol_id, protocol_name, protocol_level.
    """
    data = request.get_json()
    patient_id = data.get('patient_id')
    evaluation_id = data.get('evaluation_id')
    protocol_id = data.get('protocol_id')
    protocol_name = data.get('protocol_name')
    protocol_level = data.get('protocol_level')
    clinica_id = session['clinica_id']

    if not all([patient_id, evaluation_id, protocol_id, protocol_name, protocol_level]):
        return jsonify({'success': False, 'message': 'Dados incompletos para vincular protocolo.'}), 400

    success = add_protocol_to_evaluation(clinica_id, patient_id, evaluation_id, protocol_id, protocol_name, protocol_level)

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
    task_id = data.get('task_id')
    response_value = data.get('response_value')
    additional_info = data.get('additional_info', '')
    clinica_id = session['clinica_id']

    if not all([patient_id, evaluation_id, task_id, response_value]):
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
        return redirect(url_for('evaluations.view_evaluation', patient_id=patient_id, evaluation_id=evaluation_id))

    patient_data = get_db().collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).get()
    patient_name = patient_data.to_dict().get('nome', 'Paciente Desconhecido') if patient_data.exists else 'Paciente Desconhecido'

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()

    # Estilos personalizados
    styles.add(ParagraphStyle(name='TitleStyle', fontSize=24, leading=28, alignment=TA_CENTER, spaceAfter=20, fontName='Helvetica-Bold'))
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
    story.append(Paragraph(f"Data de Nascimento: {patient_data.to_dict().get('data_nascimento_fmt', 'N/A') if patient_data.exists else 'N/A'}", styles['Normal']))
    story.append(Paragraph(f"Status da Avaliação: {evaluation_details.get('status', 'N/A').capitalize()}", styles['Normal']))
    story.append(Spacer(1, 0.4 * inch))

    # Protocolos Vinculados
    story.append(Paragraph("Protocolos Vinculados:", styles['Heading1']))
    if evaluation_details.get('protocolos_vinculados'):
        for proto_link in evaluation_details['protocolos_vinculados']:
            story.append(Paragraph(f"• {proto_link.get('protocol_name', 'N/A')} (Nível: {proto_link.get('protocol_level', 'N/A')})", styles['Normal']))
    else:
        story.append(Paragraph("Nenhum protocolo vinculado a esta avaliação.", styles['Italic']))
    story.append(Spacer(1, 0.4 * inch))

    # Tarefas Avaliadas
    story.append(Paragraph("Tarefas Avaliadas:", styles['Heading1']))
    if evaluation_details.get('tarefas_avaliadas'):
        # Agrupar tarefas por protocolo e nível para melhor organização
        grouped_tasks = {}
        for task in evaluation_details['tarefas_avaliadas']:
            # Buscar o nome do protocolo a partir do ID do protocolo, se não estiver na tarefa
            protocol_name = task.get('protocol_name')
            if not protocol_name:
                protocol_doc = get_protocol_by_id(clinica_id, task.get('protocol_id'))
                protocol_name = protocol_doc.get('nome', 'Protocolo Desconhecido') if protocol_doc else 'Protocolo Desconhecido'
                task['protocol_name'] = protocol_name # Adiciona para uso futuro se necessário
            
            nivel = task.get('nivel', 'N/A')
            if protocol_name not in grouped_tasks:
                grouped_tasks[protocol_name] = {}
            if nivel not in grouped_tasks[protocol_name]:
                grouped_tasks[protocol_name][nivel] = []
            grouped_tasks[protocol_name][nivel].append(task)
        
        for protocol_name, levels in grouped_tasks.items():
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

    doc.build(story)
    buffer.seek(0)

    return buffer.getvalue(), 200, {
        'Content-Type': 'application/pdf',
        'Content-Disposition': f'attachment; filename=avaliacao_{patient_name.replace(" ", "_")}_{evaluation_id}.pdf'
    }

