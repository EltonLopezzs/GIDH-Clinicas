import datetime
import json
import os
import re
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth_admin
from flask import Flask, session, flash, redirect, url_for, request, jsonify, render_template_string
import pytz

# Import Blueprints
from routes.appointments import appointments_bp
from routes.auth_users import auth_users_bp
from routes.anamnesis_templates import anamnesis_templates_bp
from routes.chat import chat_bp
from routes.covenants import covenants_bp
from routes.dashboard import dashboard_bp
from routes.medical_records import medical_records_bp
from routes.patients import patients_bp
from routes.peis import peis_bp
from routes.professionals import professionals_bp
from routes.schedules import schedules_bp
from routes.services_procedures import services_procedures_bp

# Initialize Flask App
app = Flask(__name__)
app.secret_key = os.urandom(24)
CORS(app)

# Initialize Firestore DB (Global within the app context)
db = None
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

# Pass db and firebase_auth_admin to blueprints if needed using app.config
app.config['DB'] = db
app.config['FIREBASE_AUTH_ADMIN'] = firebase_auth_admin

# Define time zone globally
SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')
app.config['SAO_PAULO_TZ'] = SAO_PAULO_TZ

# Common utility functions (moved to utils/firestore_utils.py)
from utils.firestore_utils import convert_doc_to_dict, parse_date_input, slugify_filter

# Register slugify filter in Jinja2 environment
app.jinja_env.filters['slugify'] = slugify_filter

# Routes for setup (kept here as it's a one-off admin setup route)
@app.route('/setup-mapeamento-admin', methods=['GET', 'POST'])
def setup_mapeamento_admin():
    if not app.config['DB']: return "Firebase não inicializado", 500
    if request.method == 'POST':
        user_uid = request.form['user_uid'].strip()
        email_para_referencia = request.form['email_para_referencia'].strip().lower()
        clinica_id_associada = request.form['clinica_id_associada'].strip()
        nome_clinica_display = request.form['nome_clinica_display'].strip()
        user_role = request.form.get('user_role', 'medico').strip()

        if not all([user_uid, email_para_referencia, clinica_id_associada, nome_clinica_display, user_role]):
            flash("Todos os campos são obrigatórios.", "danger")
        else:
            try:
                clinica_ref = app.config['DB'].collection('clinicas').document(clinica_id_associada)
                if not clinica_ref.get().exists:
                    clinica_ref.set({
                        'nome_oficial': nome_clinica_display,
                        'criada_em_dashboard_setup': firestore.SERVER_TIMESTAMP
                    })
                app.config['DB'].collection('User').document(user_uid).set({
                    'email': email_para_referencia,
                    'clinica_id': clinica_id_associada,
                    'nome_clinica_display': nome_clinica_display,
                    'role': user_role,
                    'associado_em': firestore.SERVER_TIMESTAMP
                })
                flash(f'UID do usuário {user_uid} ({user_role}) associado à clínica {nome_clinica_display} ({clinica_id_associada})! Agora você pode tentar <a href="{url_for("login_page")}">fazer login</a>.', 'success')
            except Exception as e:
                flash(f'Erro ao associar usuário: {e}', 'danger')
                print(f"Erro em setup_mapeamento_admin: {e}")
        return redirect(url_for('setup_mapeamento_admin'))
    
    return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Associar Administrador Firebase à Clínica</title>
            <style>
                body { font-family: sans-serif; padding: 20px; background-color: #f8f9fa; color: #333; }
                h2 { color: #a6683c; margin-bottom: 20px; }
                p { margin-bottom: 10px; line-height: 1.5; }
                form { background-color: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); max-width: 500px; margin: 20px auto; }
                input[type="text"], input[type="email"], select {
                    width: calc(100% - 22px);
                    padding: 10px;
                    margin-bottom: 15px;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                    font-size: 16px;
                }
                button[type="submit"] {
                    background-color: #a6683c;
                    color: white;
                    padding: 12px 20px;
                    border: none;
                    border-radius: 4px;
                    cursor: pointer;
                    font-size: 16px;
                    transition: background-color 0.3s ease;
                }
                button[type="submit"]:hover {
                    background-color: #c68642;
                }
                ul { list-style-type: none; padding: 0; margin-top: 20px; }
                .flash-message {
                    padding: 10px 15px;
                    margin-bottom: 10px;
                    border-radius: 5px;
                    font-weight: bold;
                    text-align: center;
                }
                .flash-message.success {
                    background-color: #d4edda;
                    color: #155724;
                    border: 1px solid #c3e6cb;
                }
                .flash-message.danger {
                    background-color: #f8d7da;
                    color: #721c24;
                    border: 1px solid #f5c6cb;
                }
                a { color: #a6683c; text-decoration: none; }
                a:hover { text-decoration: underline; }
            </style>
        </head>
        <body>
        <h2>Associar Usuário do Firebase Auth a uma Clínica</h2>
        <p><b>Passo 1:</b> Crie o usuário (com e-mail/senha) no console do Firebase > Autenticação.</p>
        <p><b>Passo 2:</b> Obtenha o UID do usuário (ex: na guia "Usuários" do Firebase Auth).</p>
        <p><b>Passo 3:</b> Preencha o formulário abaixo.</p>
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            <ul style="list-style-type: none; padding: 0;">
            {% for category, message in messages %}
              <li class="flash-message {{ category }}">{{ message | safe }}</li>
            {% endfor %}
            </ul>
          {% endif %}
        {% endwith %}
        <form method="post" action="{{ url_for('setup_mapeamento_admin') }}">
            UID do Usuário (Firebase Auth): <input type="text" name="user_uid" required size="40" value="{{ request.form.user_uid if 'user_uid' in request.form else '' }}"><br><br>
            E-mail do Usuário (para referência): <input type="email" name="email_para_referencia" required size="40" value="{{ request.form.email_para_referencia if 'email_para_referencia' in request.form else '' }}"><br><br>
            ID da Clínica (ex: clinicaSaoJudas): <input type="text" name="clinica_id_associada" required size="40" value="{{ request.form.clinica_id_associada if 'clinica_id_associada' in request.form else '' }}"><br><br>
            Nome de Exibição da Clínica: <input type="text" name="nome_clinica_display" required size="40" value="{{ request.form.nome_clinica_display if 'nome_clinica_display' in request.form else '' }}"><br><br>
            Função do Usuário: 
            <select name="user_role" required>
                <option value="admin" {% if request.form.user_role == 'admin' %}selected{% endif %}>Administrador</option>
                <option value="medico" {% if request.form.user_role == 'medico' %}selected{% endif %}>Médico</option>
            </select><br><br>
            <button type="submit">Associar Usuário à Clínica</button>
        </form>
        <p><a href="{{ url_for('login_page') }}">Ir para o Login</a></p>
        </body></html>
    """)

# Register Blueprints
app.register_blueprint(appointments_bp)
app.register_blueprint(auth_users_bp)
app.register_blueprint(anamnesis_templates_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(covenants_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(medical_records_bp)
app.register_blueprint(patients_bp)
app.register_blueprint(peis_bp)
app.register_blueprint(professionals_bp)
app.register_blueprint(schedules_bp)
app.register_blueprint(services_procedures_bp)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)), debug=True)