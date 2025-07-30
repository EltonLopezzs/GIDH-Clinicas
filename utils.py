import datetime
from functools import wraps
from flask import session, redirect, url_for, flash
import pytz
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter # Importar FieldFilter
import uuid # Importar uuid para gerar IDs únicos para linked_protocol_instance_id

# Esta variável será inicializada por app.py
_db_instance = None 

# Garante que SAO_PAULO_TZ seja um objeto de fuso horário válido.
try:
    SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')
except pytz.UnknownTimeZoneError:
    print("ERRO: Fuso horário 'America/Sao_Paulo' não encontrado. Usando UTC como fallback.")
    SAO_PAULO_TZ = pytz.utc # Fallback para UTC se o fuso horário não for encontrado
except Exception as e:
    print(f"ERRO: Não foi possível inicializar o fuso horário: {e}. Usando UTC como fallback.")
    SAO_PAULO_TZ = pytz.utc # Fallback genérico

def set_db(db_client):
    """Define a instância do Firestore Client para ser acessível globalmente."""
    global _db_instance
    _db_instance = db_client

def get_db():
    """Retorna a instância do Firestore Client."""
    return _db_instance

def format_firestore_timestamp(timestamp):
    """
    Formata um timestamp do Firestore para uma string no fuso horário de São Paulo.
    Lida com datetimes com e sem tzinfo.
    """
    if isinstance(timestamp, datetime.datetime):
        # Se o datetime não tiver informações de fuso horário (naive), localize-o primeiro
        if timestamp.tzinfo is None:
            localized_timestamp = SAO_PAULO_TZ.localize(timestamp)
        else:
            # Se já tem tzinfo, converte diretamente para o fuso horário de São Paulo
            localized_timestamp = timestamp.astimezone(SAO_PAULO_TZ)
        return localized_timestamp.strftime('%Y-%m-%dT%H:%M:%S')
    return None

def convert_doc_to_dict(doc_snapshot):
    """
    Converte um snapshot de documento do Firestore em um dicionário Python.
    Objetos datetime.datetime são retornados como tal, sem formatação para string.
    """
    data = doc_snapshot.to_dict()
    if not data:
        return {}
    
    data['id'] = doc_snapshot.id

    def _convert_value(value):
        # Se o valor é um datetime.datetime, retorna-o diretamente.
        # A formatação para string será feita no blueprint ou no template.
        if isinstance(value, datetime.datetime):
            return value
        elif isinstance(value, dict):
            return {k: _convert_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_convert_value(item) for item in value]
        return value

    return {k: _convert_value(v) for k, v in data.items()}

def parse_date_input(date_string):
    """
    Converte uma string de data (YYYY-MM-DD ou DD/MM/YYYY) para um objeto datetime.datetime
    localizado no fuso horário de São Paulo.
    """
    if not date_string:
        return None
    
    parsed_date = None
    try:
        # Tenta YYYY-MM-DD (formato de input type="date")
        parsed_date = datetime.datetime.strptime(date_string, '%Y-%m-%d')
    except ValueError:
        try:
            # Tenta DD/MM/YYYY
            parsed_date = datetime.datetime.strptime(date_string, '%d/%m/%Y')
        except ValueError:
            pass # Se nenhum formato funcionar, parsed_date permanece None
    
    if parsed_date:
        # Localiza o datetime para o fuso horário de São Paulo
        return SAO_PAULO_TZ.localize(parsed_date)
    
    return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or 'clinica_id' not in session or 'user_uid' not in session:
            return redirect(url_for('login_page'))
        if not get_db():
            flash('Erro crítico: A conexão com o banco de dados falhou. Entre em contato com o suporte.', 'danger')
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or 'clinica_id' not in session or 'user_uid' not in session:
            flash('Acesso não autorizado. Faça login.', 'danger')
            return redirect(url_for('login_page'))
        if session.get('user_role') != 'admin':
            flash('Acesso negado: Você não tem permissões de administrador para esta ação.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def get_counts_for_navbar(db_instance, clinica_id):
    """
    Obtém a contagem de documentos para várias coleções usadas na barra de navegação.
    """
    counts = {
        'pacientes': 0,
        'prontuarios': 0,
        'peis': 0,
        'agendamentos': 0,
        'servicos': 0,
        'convenios': 0,
        'protocolos': 0,
        'modelos_anamnese': 0,
        'profissionais': 0,
        'contas_a_pagar': 0,
        'estoque': 0,
        'patrimonio': 0,
        'horarios': 0,
        'utilizadores': 0, # Para usuários associados a esta clínica
        'avaliacoes': 0 # NOVO: Contagem para avaliações
    }

    if not db_instance or not clinica_id:
        print("Aviso: db_instance ou clinica_id não fornecidos para get_counts_for_navbar.")
        return counts

    try:
        # Pacientes
        counts['pacientes'] = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar pacientes: {e}")

    try:
        # Prontuários
        counts['prontuarios'] = db_instance.collection('clinicas').document(clinica_id).collection('prontuarios').count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar prontuários: {e}")
    
    try:
        # PEIs
        counts['peis'] = db_instance.collection('clinicas').document(clinica_id).collection('peis').count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar PEIs: {e}")

    try:
        # Agendamentos
        counts['agendamentos'] = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar agendamentos: {e}")

    try:
        # Serviços/Procedimentos
        counts['servicos'] = db_instance.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar serviços: {e}")

    try:
        # Convênios
        counts['convenios'] = db_instance.collection('clinicas').document(clinica_id).collection('convenios').count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar convênios: {e}")

    try:
        # Protocolos
        counts['protocolos'] = db_instance.collection('clinicas').document(clinica_id).collection('protocols').count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar protocolos: {e}")

    try:
        # Modelos Anamnese
        counts['modelos_anamnese'] = db_instance.collection('clinicas').document(clinica_id).collection('modelos_anamnese').count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar modelos de anamnese: {e}")

    try:
        # Profissionais
        counts['profissionais'] = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar profissionais: {e}")

    try:
        # Contas a Pagar
        counts['contas_a_pagar'] = db_instance.collection('clinicas').document(clinica_id).collection('contas_a_pagar').count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar contas a pagar: {e}")

    try:
        # Estoque
        counts['estoque'] = db_instance.collection('clinicas').document(clinica_id).collection('estoque').count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar estoque: {e}")

    try:
        # Patrimônio
        counts['patrimonio'] = db_instance.collection('clinicas').document(clinica_id).collection('patrimonio').count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar patrimônio: {e}")

    try:
        # Horários
        counts['horarios'] = db_instance.collection('clinicas').document(clinica_id).collection('horarios').count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar horários: {e}")

    try:
        # Utilizadores (filtrando por clinica_id na coleção 'User' global)
        # Note: 'User' collection is at the root, not under 'clinicas/{clinica_id}'
        counts['utilizadores'] = db_instance.collection('User').where(
            filter=FieldFilter('clinica_id', '==', clinica_id)
        ).count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar utilizadores: {e}")

    try:
        # NOVO: Avaliações (contagem total de avaliações para a clínica)
        total_avaliacoes = 0
        patients_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes')
        patients_docs = patients_ref.stream()
        for patient_doc in patients_docs:
            evaluations_ref = patient_doc.reference.collection('avaliacoes')
            total_avaliacoes += evaluations_ref.count().get()[0][0].value
        counts['avaliacoes'] = total_avaliacoes
    except Exception as e:
        print(f"Erro ao contar avaliações: {e}")

    return counts

# --- Funções para o Planejamento Semanal ---

def get_active_goals_for_patient(clinica_id, patient_id):
    """
    Retorna as metas ativas de um paciente.
    """
    db = get_db()
    goals_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('metas')
    active_goals = []
    try:
        # Filtra metas ativas
        docs = goals_ref.where(filter=FieldFilter('is_active', '==', True)).stream()
        for doc in docs:
            goal = doc.to_dict()
            if goal:
                goal['id'] = doc.id
                # Buscar os alvos para cada meta
                goal['alvos'] = get_goal_targets(clinica_id, patient_id, doc.id)
                active_goals.append(goal)
    except Exception as e:
        print(f"Erro ao buscar metas ativas para o paciente {patient_id}: {e}")
    return active_goals

def get_goal_targets(clinica_id, patient_id, goal_id):
    """
    Retorna os alvos de uma meta específica.
    """
    db = get_db()
    targets_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('metas').document(goal_id).collection('alvos')
    targets = []
    try:
        docs = targets_ref.order_by('created_at').stream()
        for doc in docs:
            target = doc.to_dict()
            if target:
                target['id'] = doc.id
                targets.append(target)
    except Exception as e:
        print(f"Erro ao buscar alvos para a meta {goal_id}: {e}")
    return targets

def add_goal(clinica_id, patient_id, description, professional_id):
    """
    Adiciona uma nova meta para um paciente.
    """
    db = get_db()
    goals_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('metas')
    try:
        goal_data = {
            'description': description,
            'is_active': True,
            'professional_id': professional_id,
            'created_at': firestore.SERVER_TIMESTAMP
        }
        _, doc_ref = goals_ref.add(goal_data)
        return doc_ref.id
    except Exception as e:
        print(f"Erro ao adicionar meta para o paciente {patient_id}: {e}")
        return None

def add_goal_target(clinica_id, patient_id, goal_id, description):
    """
    Adiciona um novo alvo para uma meta específica.
    """
    db = get_db()
    targets_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('metas').document(goal_id).collection('alvos')
    try:
        target_data = {
            'description': description,
            'completed': False,
            'created_at': firestore.SERVER_TIMESTAMP
        }
        _, doc_ref = targets_ref.add(target_data)
        return doc_ref.id
    except Exception as e:
        print(f"Erro ao adicionar alvo para a meta {goal_id}: {e}")
        return None

def update_goal_target_status(clinica_id, patient_id, goal_id, target_id, completed):
    """
    Atualiza o status de conclusão de um alvo.
    """
    db = get_db()
    target_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('metas').document(goal_id).collection('alvos').document(target_id)
    try:
        target_ref.update({'completed': completed})
        return True
    except Exception as e:
        print(f"Erro ao atualizar status do alvo {target_id}: {e}")
        return False

def get_weekly_appointments_for_patient(clinica_id, patient_id, start_date_str, end_date_str):
    """
    Retorna os agendamentos de um paciente para uma semana específica.
    start_date_str e end_date_str devem ser strings no formato 'YYYY-MM-DD'.
    """
    db = get_db()
    appointments_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos')
    weekly_appointments = []

    try:
        start_date = SAO_PAULO_TZ.localize(datetime.datetime.strptime(start_date_str, '%Y-%m-%d'))
        end_date = SAO_PAULO_TZ.localize(datetime.datetime.strptime(end_date_str, '%Y-%m-%d')) + datetime.timedelta(days=1, seconds=-1) # Inclui o final do dia

        # Busca agendamentos para o paciente dentro do período
        docs = appointments_ref.where(filter=FieldFilter('paciente_id', '==', patient_id))\
                               .where(filter=FieldFilter('data_hora_inicio', '>=', start_date))\
                               .where(filter=FieldFilter('data_hora_inicio', '<=', end_date))\
                               .order_by('data_hora_inicio')\
                               .stream()
        
        for doc in docs:
            appointment = doc.to_dict()
            if appointment:
                appointment['id'] = doc.id
                # Formata as datas para string para facilitar o uso no frontend
                if isinstance(appointment.get('data_hora_inicio'), datetime.datetime):
                    appointment['data_hora_inicio_str'] = format_firestore_timestamp(appointment['data_hora_inicio'])
                if isinstance(appointment.get('data_hora_fim'), datetime.datetime):
                    appointment['data_hora_fim_str'] = format_firestore_timestamp(appointment['data_hora_fim'])
                
                # Adiciona o nome do profissional ao agendamento, se disponível
                if appointment.get('profissional_id'):
                    prof_doc = db.collection('clinicas').document(clinica_id).collection('profissionais').document(appointment['profissional_id']).get()
                    if prof_doc.exists:
                        appointment['profissional_nome'] = prof_doc.to_dict().get('nome', 'Desconhecido')
                    else:
                        appointment['profissional_nome'] = 'Desconhecido'
                else:
                    appointment['profissional_nome'] = 'Não Atribuído'

                weekly_appointments.append(appointment)
    except Exception as e:
        print(f"Erro ao buscar agendamentos semanais para o paciente {patient_id}: {e}")
    return weekly_appointments

def save_weekly_plan_entry(clinica_id, patient_id, appointment_id, goal_id, professional_id, plan_date_str):
    """
    Salva uma entrada do planejamento semanal (associa uma meta a um agendamento).
    plan_date_str deve ser uma string no formato 'YYYY-MM-DD'.
    """
    db = get_db()
    weekly_plan_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('planejamento_semanal')
    try:
        plan_date = SAO_PAULO_TZ.localize(datetime.datetime.strptime(plan_date_str, '%Y-%m-%d'))
        
        plan_data = {
            'appointment_id': appointment_id,
            'goal_id': goal_id,
            'professional_id': professional_id,
            'plan_date': plan_date, # Armazena como datetime para consultas de data
            'created_at': firestore.SERVER_TIMESTAMP
        }
        _, doc_ref = weekly_plan_ref.add(plan_data)
        return doc_ref.id
    except Exception as e:
        print(f"Erro ao salvar entrada do planejamento semanal: {e}")
        return None

def delete_weekly_plan_entry(clinica_id, patient_id, entry_id):
    """
    Exclui uma entrada do planejamento semanal.
    """
    db = get_db()
    entry_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('planejamento_semanal').document(entry_id)
    try:
        entry_ref.delete()
        return True
    except Exception as e:
        print(f"Erro ao excluir entrada do planejamento semanal {entry_id}: {e}")
        return False

def get_weekly_plan_entries(clinica_id, patient_id, professional_id, start_date_str, end_date_str):
    """
    Retorna as entradas do planejamento semanal para um paciente e profissional em uma semana.
    """
    db = get_db()
    weekly_plan_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('planejamento_semanal')
    plan_entries = []

    try:
        start_date = SAO_PAULO_TZ.localize(datetime.datetime.strptime(start_date_str, '%Y-%m-%d'))
        end_date = SAO_PAULO_TZ.localize(datetime.datetime.strptime(end_date_str, '%Y-%m-%d')) + datetime.timedelta(days=1, seconds=-1) # Inclui o final do dia

        # Busca entradas para o paciente e profissional dentro do período
        docs = weekly_plan_ref.where(filter=FieldFilter('professional_id', '==', professional_id))\
                              .where(filter=FieldFilter('plan_date', '>=', start_date))\
                              .where(filter=FieldFilter('plan_date', '<=', end_date))\
                              .order_by('plan_date')\
                              .stream()
        
        for doc in docs:
            entry = doc.to_dict()
            if entry:
                entry['id'] = doc.id
                plan_entries.append(entry)
    except Exception as e:
        print(f"Erro ao buscar entradas do planejamento semanal para o paciente {patient_id}: {e}")
    return plan_entries

# --- NOVAS FUNÇÕES PARA AVALIAÇÕES ---

def get_all_protocols_with_items(clinica_id):
    """
    Retorna todos os protocolos e seus itens (incluindo níveis) para uma clínica.
    """
    db = get_db()
    protocols_list = []
    try:
        protocols_ref = db.collection('clinicas').document(clinica_id).collection('protocols')
        for protocol_doc in protocols_ref.stream():
            protocol_data = convert_doc_to_dict(protocol_doc)
            if protocol_data:
                # Carrega os níveis do protocolo
                protocol_data['niveis'] = []
                levels_ref = protocols_ref.document(protocol_doc.id).collection('niveis')
                for level_doc in levels_ref.order_by('nivel').stream():
                    level_data = convert_doc_to_dict(level_doc)
                    if level_data:
                        protocol_data['niveis'].append(level_data)
                
                # Carrega as habilidades do protocolo
                protocol_data['habilidades'] = []
                abilities_ref = protocols_ref.document(protocol_doc.id).collection('habilidades')
                for ability_doc in abilities_ref.order_by('nome').stream():
                    ability_data = convert_doc_to_dict(ability_doc)
                    if ability_data:
                        protocol_data['habilidades'].append(ability_data)

                protocols_list.append(protocol_data)
    except Exception as e:
        print(f"Erro ao buscar protocolos com itens e níveis para a clínica {clinica_id}: {e}")
    return protocols_list

def get_protocol_by_id(clinica_id, protocol_id):
    """
    Retorna um protocolo específico por ID, incluindo seus níveis, habilidades, pontuação e tarefas/testes.
    """
    db = get_db()
    try:
        protocol_doc = db.collection('clinicas').document(clinica_id).collection('protocols').document(protocol_id).get()
        if protocol_doc.exists:
            protocol_data = convert_doc_to_dict(protocol_doc)
            
            # Adicionar os níveis do protocolo
            protocol_data['niveis'] = []
            levels_ref = db.collection('clinicas').document(clinica_id).collection('protocols').document(protocol_id).collection('niveis')
            for level_doc in levels_ref.order_by('nivel').stream():
                level_data = convert_doc_to_dict(level_doc)
                if level_data:
                    protocol_data['niveis'].append(level_data)

            # Adicionar as habilidades do protocolo
            protocol_data['habilidades'] = []
            abilities_ref = db.collection('clinicas').document(clinica_id).collection('protocols').document(protocol_id).collection('habilidades')
            for ability_doc in abilities_ref.order_by('nome').stream():
                ability_data = convert_doc_to_dict(ability_doc)
                if ability_data:
                    protocol_data['habilidades'].append(ability_data)

            # Adicionar a pontuação do protocolo
            protocol_data['pontuacao'] = []
            scoring_ref = db.collection('clinicas').document(clinica_id).collection('protocols').document(protocol_id).collection('pontuacao')
            for score_doc in scoring_ref.order_by('ordem').stream():
                score_data = convert_doc_to_dict(score_doc)
                if score_data:
                    protocol_data['pontuacao'].append(score_data)

            # Adicionar os itens do protocolo (tarefas_testes)
            protocol_data['tarefas_testes'] = []
            items_ref = db.collection('clinicas').document(clinica_id).collection('protocols').document(protocol_id).collection('tarefas_testes')
            # Ordena por nivel e ordem para consistência
            for item_doc in items_ref.order_by('nivel').order_by('ordem').stream():
                item_data = convert_doc_to_dict(item_doc)
                if item_data:
                    protocol_data['tarefas_testes'].append(item_data)

            return protocol_data
    except Exception as e:
        print(f"Erro ao buscar protocolo {protocol_id} com níveis, habilidades, pontuação e itens: {e}")
    return None

def get_protocol_items_by_protocol_id(clinica_id, protocol_id):
    """
    Retorna todos os itens (tarefas_testes) de um protocolo específico.
    """
    db = get_db()
    items_list = []
    try:
        items_ref = db.collection('clinicas').document(clinica_id).collection('protocols').document(protocol_id).collection('tarefas_testes')
        # Ordena por nivel e ordem para garantir a sequência
        for item_doc in items_ref.order_by('nivel').order_by('ordem').stream():
            item_data = convert_doc_to_dict(item_doc)
            if item_data:
                items_list.append(item_data)
    except Exception as e:
        print(f"Erro ao buscar itens do protocolo {protocol_id}: {e}")
    return items_list

def get_patient_evaluations(clinica_id, patient_id):
    """
    Retorna todas as avaliações de um paciente.
    """
    db = get_db()
    evaluations_list = []
    try:
        evaluations_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('avaliacoes')
        for eval_doc in evaluations_ref.order_by('data_avaliacao', direction=firestore.Query.DESCENDING).stream():
            eval_data = convert_doc_to_dict(eval_doc)
            if eval_data:
                evaluations_list.append(eval_data)
    except Exception as e:
        print(f"Erro ao buscar avaliações para o paciente {patient_id}: {e}")
    return evaluations_list

def create_evaluation(clinica_id, patient_id, professional_id, evaluation_date):
    """
    Cria uma nova avaliação para um paciente.
    """
    db = get_db()
    evaluations_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('avaliacoes')
    try:
        eval_data = {
            'data_avaliacao': evaluation_date,
            'profissional_id': professional_id,
            'status': 'rascunho', # Pode ser 'rascunho', 'finalizado'
            'created_at': firestore.SERVER_TIMESTAMP
        }
        _, doc_ref = evaluations_ref.add(eval_data)
        return doc_ref.id
    except Exception as e:
        print(f"Erro ao criar avaliação para o paciente {patient_id}: {e}")
        return None

def add_protocol_to_evaluation(clinica_id, patient_id, evaluation_id, protocol_id, protocol_name):
    """
    Vincula um protocolo (inteiro, sem nível específico) a uma avaliação existente e copia todas as suas tarefas e pontuações.
    Um ID de instância único é gerado para o protocolo vinculado.
    """
    db = get_db()
    evaluation_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('avaliacoes').document(evaluation_id)
    
    try:
        # 1. Gerar um ID de instância único para este protocolo vinculado à avaliação
        linked_protocol_instance_id = str(uuid.uuid4())

        # 2. Obter o protocolo mestre COMPLETO (incluindo tarefas_testes e pontuacao)
        master_protocol_data = get_protocol_by_id(clinica_id, protocol_id)
        if not master_protocol_data:
            print(f"Erro: Protocolo mestre {protocol_id} não encontrado para vinculação.")
            return False

        # 3. Salvar os dados básicos do protocolo vinculado na subcoleção 'protocolos_vinculados'
        # Isso inclui o ID do protocolo mestre e o nome para referência
        protocol_link_data = {
            'protocol_id': protocol_id,
            'protocol_name': protocol_name,
            'data_vinculacao': firestore.SERVER_TIMESTAMP,
            'id': linked_protocol_instance_id, # Armazena o ID da instância gerado
            'niveis_snapshot': master_protocol_data.get('niveis', []) # Snapshot dos níveis
        }
        linked_protocol_doc_ref = evaluation_ref.collection('protocolos_vinculados').document(linked_protocol_instance_id)
        linked_protocol_doc_ref.set(protocol_link_data)

        # 4. Copiar TODAS as tarefas/testes do protocolo mestre para a subcoleção 'tarefas_snapshot'
        # sob o documento do protocolo vinculado na avaliação.
        tasks_snapshot_ref = linked_protocol_doc_ref.collection('tarefas_snapshot')
        for item in master_protocol_data.get('tarefas_testes', []):
            task_snapshot_data = {
                'protocol_item_id': item.get('id'), # ID do item original do protocolo
                'nivel': item.get('nivel'),
                'ordem': item.get('ordem'),
                'item_numero': item.get('item'),
                'nome_tarefa': item.get('nome'),
                'habilidade_marco': item.get('habilidade_marco'),
                'exemplo': item.get('exemplo', ''),
                'criterio': item.get('criterio', ''),
                'pergunta': item.get('pergunta', ''),
                'objetivo': item.get('objetivo', ''),
                'created_at': firestore.SERVER_TIMESTAMP # Timestamp de quando o snapshot foi criado
            }
            tasks_snapshot_ref.add(task_snapshot_data) # Firestore gera um novo ID para cada tarefa no snapshot

        # 5. Copiar os critérios de pontuação do protocolo mestre para a subcoleção 'pontuacao_snapshot'
        # sob o documento do protocolo vinculado na avaliação.
        scoring_snapshot_ref = linked_protocol_doc_ref.collection('pontuacao_snapshot')
        for score_item in master_protocol_data.get('pontuacao', []):
            scoring_snapshot_data = {
                'scoring_item_id': score_item.get('id'), # ID do item de pontuação original
                'ordem': score_item.get('ordem'),
                'descricao': score_item.get('descricao'),
                'valor': score_item.get('valor'),
                'created_at': firestore.SERVER_TIMESTAMP # Timestamp de quando o snapshot foi criado
            }
            scoring_snapshot_ref.add(scoring_snapshot_data)
            
        # 6. Inicializar as tarefas avaliadas na subcoleção 'tarefas_avaliadas' da avaliação
        # com base nas tarefas do snapshot. Isso é crucial para que o frontend possa carregar
        # as respostas salvas e o estado inicial.
        evaluation_tasks_ref = evaluation_ref.collection('tarefas_avaliadas')
        
        # Buscar as tarefas que acabaram de ser copiadas para o snapshot
        # Nota: Idealmente, usaríamos os IDs gerados no passo 4. Por simplicidade,
        # vamos re-consultar o snapshot ou passar os IDs gerados no passo 4.
        # Para evitar complexidade extra de IDs aninhados, vamos copiar os dados
        # novamente para 'tarefas_avaliadas', mas agora com o linked_protocol_instance_id.
        # O 'protocol_item_id' do snapshot será usado para vincular.
        
        # Pega as tarefas que foram adicionadas ao snapshot (re-consulta)
        snapshot_tasks = []
        for doc in tasks_snapshot_ref.stream():
            snapshot_tasks.append(convert_doc_to_dict(doc))

        for task_snap in snapshot_tasks:
            task_data_for_eval = {
                'linked_protocol_instance_id': linked_protocol_instance_id, # Link para a instância específica do protocolo vinculado
                'protocol_item_id': task_snap.get('protocol_item_id'), # ID do item original do protocolo
                'task_snapshot_id': task_snap.get('id'), # ID do documento da tarefa no snapshot
                'nivel': task_snap.get('nivel'), 
                'item_numero': task_snap.get('item_numero'),
                'nome_tarefa': task_snap.get('nome_tarefa'),
                'habilidade_marco': task_snap.get('habilidade_marco'),
                'exemplo': task_snap.get('exemplo', ''),
                'criterio': task_snap.get('criterio', ''),
                'pergunta': task_snap.get('pergunta', ''),
                'objetivo': task_snap.get('objetivo', ''),
                'response_value': '', # Valor inicial vazio
                'additional_info': '', # Info adicional vazia
                'data_resposta': None,
                'status': 'pendente', # 'pendente', 'respondida'
                'created_at': firestore.SERVER_TIMESTAMP # Timestamp de quando a tarefa avaliada foi inicializada
            }
            evaluation_tasks_ref.add(task_data_for_eval) # Firestore gera um novo ID para cada tarefa avaliada

        # 7. Inicializar as pontuações avaliadas na subcoleção 'pontuacoes_avaliadas' da avaliação
        evaluation_scoring_ref = evaluation_ref.collection('pontuacoes_avaliadas')
        
        # Pega as pontuações que foram adicionadas ao snapshot (re-consulta)
        snapshot_scoring_items = []
        for doc in scoring_snapshot_ref.stream():
            snapshot_scoring_items.append(convert_doc_to_dict(doc))

        for score_snap in snapshot_scoring_items:
            scoring_data_for_eval = {
                'linked_protocol_instance_id': linked_protocol_instance_id,
                'scoring_item_id': score_snap.get('scoring_item_id'), # ID do item de pontuação original
                'scoring_snapshot_id': score_snap.get('id'), # ID do documento da pontuação no snapshot
                'descricao': score_snap.get('descricao'),
                'valor': score_snap.get('valor'),
                'data_aplicacao': None,
                'aplicado': False, # Indica se o profissional "respondeu" a este critério
                'created_at': firestore.SERVER_TIMESTAMP
            }
            evaluation_scoring_ref.add(scoring_data_for_eval)

        return True
    except Exception as e:
        print(f"Erro ao vincular protocolo {protocol_id} à avaliação {evaluation_id} do paciente {patient_id}: {e}")
        return False

def get_evaluation_details(clinica_id, patient_id, evaluation_id):
    """
    Retorna os detalhes de uma avaliação específica, incluindo protocolos vinculados, tarefas e pontuações aplicadas.
    """
    db = get_db()
    evaluation_data = None
    try:
        eval_doc = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('avaliacoes').document(evaluation_id).get()
        if eval_doc.exists:
            evaluation_data = convert_doc_to_dict(eval_doc)
            
            # Buscar protocolos vinculados
            evaluation_data['protocolos_vinculados'] = []
            linked_protocols_ref = eval_doc.reference.collection('protocolos_vinculados')
            for linked_proto_doc in linked_protocols_ref.stream():
                linked_proto_data = convert_doc_to_dict(linked_proto_doc)
                if linked_proto_data:
                    evaluation_data['protocolos_vinculados'].append(linked_proto_data)
            
            # Buscar tarefas avaliadas
            evaluation_data['tarefas_avaliadas'] = []
            tasks_ref = eval_doc.reference.collection('tarefas_avaliadas')
            # Ordena por linked_protocol_instance_id, nivel, e item_numero para consistência
            for task_doc in tasks_ref.order_by('linked_protocol_instance_id').order_by('nivel').order_by('item_numero').stream(): 
                task_data = convert_doc_to_dict(task_doc)
                if task_data and task_data.get('data_resposta'):
                    task_data['data_resposta_fmt'] = format_firestore_timestamp(task_data['data_resposta'])
                evaluation_data['tarefas_avaliadas'].append(task_data)

            # Buscar pontuações avaliadas/aplicadas
            evaluation_data['pontuacoes_avaliadas'] = []
            scoring_applied_ref = eval_doc.reference.collection('pontuacoes_avaliadas')
            # Ordena por linked_protocol_instance_id e created_at para consistência
            for score_applied_doc in scoring_applied_ref.order_by('linked_protocol_instance_id').order_by('created_at').stream():
                score_applied_data = convert_doc_to_dict(score_applied_doc)
                if score_applied_data and score_applied_data.get('data_aplicacao'):
                    score_applied_data['data_aplicacao_fmt'] = format_firestore_timestamp(score_applied_data['data_aplicacao'])
                evaluation_data['pontuacoes_avaliadas'].append(score_applied_data)
                
    except Exception as e:
        print(f"Erro ao buscar detalhes da avaliação {evaluation_id} do paciente {patient_id}: {e}")
    return evaluation_data

def save_evaluation_task_response(clinica_id, patient_id, evaluation_id, task_id, response_value, additional_info):
    """
    Salva a resposta de uma tarefa específica dentro de uma avaliação.
    """
    db = get_db()
    task_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('avaliacoes').document(evaluation_id).collection('tarefas_avaliadas').document(task_id)
    try:
        task_ref.update({
            'response_value': response_value,
            'additional_info': additional_info,
            'data_resposta': firestore.SERVER_TIMESTAMP,
            'status': 'respondida'
        })
        return True
    except Exception as e:
        print(f"Erro ao salvar resposta da tarefa {task_id} na avaliação {evaluation_id}: {e}")
        return False

def save_evaluation_scoring_response(clinica_id, patient_id, evaluation_id, scoring_applied_id, applied_value):
    """
    Salva a resposta de um critério de pontuação específico dentro de uma avaliação.
    """
    db = get_db()
    scoring_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('avaliacoes').document(evaluation_id).collection('pontuacoes_avaliadas').document(scoring_applied_id)
    try:
        scoring_ref.update({
            'aplicado': applied_value, # True/False
            'data_aplicacao': firestore.SERVER_TIMESTAMP if applied_value else None
        })
        return True
    except Exception as e:
        print(f"Erro ao salvar resposta do critério de pontuação {scoring_applied_id} na avaliação {evaluation_id}: {e}")
        return False


def update_evaluation_status(clinica_id, patient_id, evaluation_id, status):
    """
    Atualiza o status de uma avaliação (ex: 'finalizado').
    """
    db = get_db()
    evaluation_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('avaliacoes').document(evaluation_id)
    try:
        evaluation_ref.update({'status': status})
        return True
    except Exception as e:
        print(f"Erro ao atualizar status da avaliação {evaluation_id}: {e}")
        return False

def delete_evaluation(clinica_id, patient_id, evaluation_id):
    """
    Exclui uma avaliação e suas subcoleções (protocolos vinculados e tarefas avaliadas).
    """
    db = get_db()
    evaluation_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('avaliacoes').document(evaluation_id)
    
    try:
        # Excluir subcoleção 'protocolos_vinculados' e suas subcoleções de snapshot
        linked_protocols_ref = evaluation_ref.collection('protocolos_vinculados')
        for linked_proto_doc in linked_protocols_ref.stream():
            # Excluir subcoleções de snapshot dentro do protocolo vinculado
            linked_proto_doc.reference.collection('tarefas_snapshot').map(lambda doc: doc.reference.delete())
            linked_proto_doc.reference.collection('pontuacao_snapshot').map(lambda doc: doc.reference.delete())
            linked_proto_doc.reference.delete() # Excluir o documento do protocolo vinculado

        # Excluir subcoleção 'tarefas_avaliadas'
        tasks_ref = evaluation_ref.collection('tarefas_avaliadas')
        for doc in tasks_ref.stream():
            doc.reference.delete()

        # Excluir subcoleção 'pontuacoes_avaliadas'
        scoring_ref = evaluation_ref.collection('pontuacoes_avaliadas')
        for doc in scoring_ref.stream():
            doc.reference.delete()

        # Excluir o documento da avaliação principal
        evaluation_ref.delete()
        return True
    except Exception as e:
        print(f"Erro ao excluir avaliação {evaluation_id} do paciente {patient_id}: {e}")
        return False

def delete_linked_protocol_and_tasks(clinica_id, patient_id, evaluation_id, linked_protocol_instance_id_to_remove):
    """
    Desvincula um protocolo de uma avaliação e remove APENAS as tarefas
    e pontuações associadas a ESSE protocolo vinculado (usando o linked_protocol_instance_id).
    Também remove os snapshots de tarefas e pontuações.
    """
    db = get_db()
    evaluation_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(patient_id).collection('avaliacoes').document(evaluation_id)
    
    try:
        # 1. Excluir o documento do protocolo vinculado na subcoleção 'protocolos_vinculados'
        linked_protocol_doc_ref = evaluation_ref.collection('protocolos_vinculados').document(linked_protocol_instance_id_to_remove)
        
        # Excluir subcoleções de snapshot dentro do protocolo vinculado antes de deletar o documento principal
        for doc in linked_protocol_doc_ref.collection('tarefas_snapshot').stream():
            doc.reference.delete()
        for doc in linked_protocol_doc_ref.collection('pontuacao_snapshot').stream():
            doc.reference.delete()
        
        linked_protocol_doc_ref.delete() # Excluir o documento do protocolo vinculado

        # 2. Excluir as tarefas avaliadas associadas a este protocolo na subcoleção 'tarefas_avaliadas'
        tasks_to_delete_query = evaluation_ref.collection('tarefas_avaliadas').where(
            filter=FieldFilter('linked_protocol_instance_id', '==', linked_protocol_instance_id_to_remove)
        )
        for task_doc in tasks_to_delete_query.stream():
            task_doc.reference.delete()
        
        # 3. Excluir as pontuações avaliadas associadas a este protocolo na subcoleção 'pontuacoes_avaliadas'
        scoring_to_delete_query = evaluation_ref.collection('pontuacoes_avaliadas').where(
            filter=FieldFilter('linked_protocol_instance_id', '==', linked_protocol_instance_id_to_remove)
        )
        for score_doc in scoring_to_delete_query.stream():
            score_doc.reference.delete()

        return True
    except Exception as e:
        print(f"Erro ao desvincular protocolo {linked_protocol_instance_id_to_remove} da avaliação {evaluation_id} do paciente {patient_id}: {e}")
        return False
