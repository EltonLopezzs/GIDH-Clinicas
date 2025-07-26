import datetime
from functools import wraps
from flask import session, redirect, url_for, flash
import pytz
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter # Importar FieldFilter

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
        'utilizadores': 0 # Para usuários associados a esta clínica
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
