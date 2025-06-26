import datetime
import json
import os
import re
from functools import wraps

import firebase_admin
import pytz
from firebase_admin import credentials, firestore, auth as firebase_auth_admin
from flask import flash, redirect, session, url_for, request

# Inicialização do Firestore
db = None
SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')

try:
    firebase_config_str = os.environ.get('__firebase_config')
    if firebase_config_str:
        firebase_config_dict = json.loads(firebase_config_str)
        cred = credentials.Certificate(firebase_config_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
            print("🔥 Firebase Admin SDK inicializado usando __firebase_config!")
        else:
            print("🔥 Firebase Admin SDK já foi inicializado.")
        db = firestore.client()
    else:
        cred_path = os.path.join(os.path.dirname(__file__), 'serviceAccountKey.json')
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
                print("🔥 Firebase Admin SDK inicializado a partir de serviceAccountKey.json (desenvolvimento)!")
            else:
                print("🔥 Firebase Admin SDK já foi inicializado.")
            db = firestore.client()
        else:
            print("⚠️ Nenhuma credencial Firebase encontrada (__firebase_config ou serviceAccountKey.json). Firebase Admin SDK não inicializado.")
except Exception as e:
    print(f"🚨 ERRO CRÍTICO ao inicializar o Firebase Admin SDK: {e}")

# Funções utilitárias

def convert_doc_to_dict(doc_snapshot):
    """
    Converte um DocumentSnapshot do Firestore em um dicionário Python,
    formatando datas e adicionando o ID do documento.
    """
    if not doc_snapshot or not doc_snapshot.exists:
        return None

    data = doc_snapshot.to_dict()
    data['id'] = doc_snapshot.id

    def _convert_value(value):
        if isinstance(value, datetime.datetime):
            local_time = value.astimezone(SAO_PAULO_TZ)
            if local_time.hour == 0 and local_time.minute == 0:
                return local_time.strftime('%d/%m/%Y')
            return local_time.strftime('%d/%m/%Y %H:%M')
        elif isinstance(value, dict):
            return {k: _convert_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_convert_value(item) for item in value]
        return value

    return {key: _convert_value(val) for key, val in data.items()}


def parse_date_input(date_string):
    """
    Converte uma string de data para um objeto datetime localizado em SAO_PAULO_TZ.
    Suporta os formatos 'YYYY-MM-DD' e 'DD/MM/YYYY'.
    """
    if not date_string:
        return None
    
    parsed_date = None
    try:
        parsed_date = datetime.datetime.strptime(date_string, '%Y-%m-%d').date()
    except ValueError:
        pass

    if parsed_date is None:
        try:
            parsed_date = datetime.datetime.strptime(date_string, '%d/%m/%Y').date()
        except ValueError:
            pass
    
    if parsed_date:
        # Localiza a data no fuso horário de São Paulo, no início do dia
        return SAO_PAULO_TZ.localize(datetime.datetime(parsed_date.year, parsed_date.month, parsed_date.day, 0, 0, 0))
    
    return None

def slugify_filter(s):
    """
    Converte uma string em um slug URL-friendly.
    Remove caracteres não alfanuméricos, converte para minúsculas e substitui espaços por hífens.
    """
    s = s.lower().strip()
    s = re.sub(r'[^\w\s-]', '', s) # Remove caracteres não alfanuméricos, exceto espaços e hífens
    s = re.sub(r'[\s_-]+', '-', s)  # Substitui espaços e múltiplos hífens/underscores por um único hífen
    s = re.sub(r'^-+|-+$', '', s)  # Remove hífens do início e do fim
    return s

# Decoradores de autenticação e autorização

def login_required(f):
    """
    Decorador para exigir que o usuário esteja logado.
    Redireciona para a página de login se a sessão não contiver as chaves necessárias.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or 'clinica_id' not in session or 'user_uid' not in session:
            flash('Você precisa estar logado para acessar esta página.', 'danger')
            return redirect(url_for('auth.login_page')) # Usar blueprint 'auth'
        if not db:
            flash('Erro crítico: A conexão com o banco de dados falhou. Entre em contato com o suporte.', 'danger')
            return redirect(url_for('auth.login_page')) # Usar blueprint 'auth'
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """
    Decorador para exigir que o usuário tenha o papel de 'admin'.
    Redireciona e mostra uma mensagem de erro se o usuário não for admin.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or 'clinica_id' not in session or 'user_uid' not in session:
            flash('Acesso não autorizado. Faça login.', 'danger')
            return redirect(url_for('auth.login_page')) # Usar blueprint 'auth'
        if session.get('user_role') != 'admin':
            flash('Acesso negado: Você não tem permissões de administrador para esta ação.', 'danger')
            return redirect(url_for('dashboard.index')) # Usar blueprint 'dashboard'
        return f(*args, **kwargs)
    return decorated_function

