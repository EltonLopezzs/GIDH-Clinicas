from functools import wraps
from flask import session, redirect, url_for, flash, current_app

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or 'clinica_id' not in session or 'user_uid' not in session:
            flash("Sessão inválida ou expirada. Por favor, faça login novamente.", "danger")
            return redirect(url_for('auth_users_bp.login_page'))
        if not current_app.config['DB']: # Access DB from current_app.config
            flash('Erro crítico: A conexão com o banco de dados falhou. Entre em contato com o suporte.', 'danger')
            return redirect(url_for('auth_users_bp.login_page'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or 'clinica_id' not in session or 'user_uid' not in session:
            flash('Acesso não autorizado. Faça login.', 'danger')
            return redirect(url_for('auth_users_bp.login_page'))
        if session.get('user_role') != 'admin':
            flash('Acesso negado: Você não tem permissões de administrador para esta ação.', 'danger')
            return redirect(url_for('dashboard_bp.index')) # Redirect to dashboard or appropriate page for non-admins
        return f(*args, **kwargs)
    return decorated_function