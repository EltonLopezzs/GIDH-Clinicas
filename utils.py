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

