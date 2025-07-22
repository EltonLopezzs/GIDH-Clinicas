import datetime
from functools import wraps
from flask import session, redirect, url_for, flash
import pytz
from google.cloud import firestore # Necessário para firestore.SERVER_TIMESTAMP e outros objetos

# Esta variável será inicializada por app.py
_db_instance = None 

# Garante que SAO_PAULO_TZ seja um objeto de fuso horário válido.
# Se houver qualquer problema com pytz.timezone, ele será capturado aqui.
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
            # Assumimos que datetimes sem tzinfo do Firestore são UTC, ou o fuso horário do servidor
            # É mais seguro localizá-los para o fuso horário de São Paulo se eles representam a hora de lá.
            # Se eles são UTC e você quer convertê-los, primeiro localize para UTC e depois converta.
            # Para este caso, vamos assumir que são "naive" e representam a hora de SP.
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
