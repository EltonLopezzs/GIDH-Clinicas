import os
from flask import Flask, flash, redirect, render_template, request, session, url_for, jsonify, render_template_string
from datetime import timedelta # Importar timedelta para sessões permanentes

# --- Importações adicionais do seu app.py ---
import datetime
import json
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth_admin, storage
from flask_cors import CORS
from google.cloud.firestore_v1.base_query import FieldFilter
from collections import Counter, defaultdict

# Importar get_counts_for_navbar do utils
from utils import set_db, get_db, login_required, admin_required, SAO_PAULO_TZ, parse_date_input, convert_doc_to_dict, get_counts_for_navbar
from blueprints.users import register_users_routes
from blueprints.professionals import register_professionals_routes
from blueprints.patients import register_patients_routes
from blueprints.services import register_services_routes
from blueprints.covenants import register_covenants_routes
from blueprints.schedules import register_schedules_routes
from blueprints.appointments import register_appointments_routes
from blueprints.medical_records import register_medical_records_routes
from blueprints.estoque import register_estoque_routes
from blueprints.contas_a_pagar import register_contas_a_pagar_routes
from blueprints.peis import peis_bp
from blueprints.patrimonio import register_patrimonio_routes
from blueprints.protocols import protocols_bp
from blueprints.weekly_planning import weekly_planning_bp 
from blueprints.user_api import user_api_bp

# NOVO: Importações para IA
import google.generativeai as genai
from PyPDF2 import PdfReader
from dotenv import load_dotenv
import re # Para sanitização de JSON

# Carrega variáveis de ambiente do arquivo .env (para desenvolvimento local)
load_dotenv()

app = Flask(__name__)

# --- Configurações de Segurança e Sessão (CRUCIAL para ambientes de produção) ---

# SECRET_KEY: Essencial para assinar cookies de sessão.
# Use a variável de ambiente FLASK_SECRET_KEY. Se não for encontrada, use uma padrão.
# Esta chave DEVE ser CONSISTENTE entre os reinícios do Gunicorn.
app.secret_key = os.environ.get('FLASK_SECRET_KEY', '169f2ebd4e2dd3590ab847171e711086e2778a04570624da')

# Configurações de Proxy Reverso:
# Isso diz ao Flask para confiar nos cabeçalhos X-Forwarded-For e X-Forwarded-Proto do Nginx.
# Sem isso, Flask pode gerar URLs incorretas ou ter problemas de segurança com sessões.
# Ajuste o número de proxies conforme sua infraestrutura (1 para Nginx -> Gunicorn -> Flask)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Configurações de Cookie de Sessão:
# SESSION_COOKIE_SECURE: Define se o cookie de sessão só deve ser enviado via HTTPS.
app.config['SESSION_COOKIE_SECURE'] = False # Mude para True quando configurar HTTPS!

# SESSION_COOKIE_HTTPONLY: Impede que JavaScript acesse o cookie, aumentando a segurança.
app.config['SESSION_COOKIE_HTTPONLY'] = True

# SESSION_COOKIE_SAMESITE: Proteção contra CSRF. 'Lax' é um bom padrão.
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# PERMANENT_SESSION_LIFETIME: Tempo de vida da sessão (ex: 31 dias).
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=31)


CORS(app)

_db_client_instance = None

# Inicialização do Firebase Admin SDK
# Garante que o Firebase seja inicializado apenas uma vez e com o storageBucket
if not firebase_admin._apps:
    try:
        firebase_config_str = os.environ.get('__firebase_config')
        if firebase_config_str:
            firebase_config_dict = json.loads(firebase_config_str)
            cred = credentials.Certificate(firebase_config_dict)
            firebase_admin.initialize_app(cred, {
                'storageBucket': firebase_config_dict.get('storageBucket', os.environ.get('FIREBASE_STORAGE_BUCKET', 'gidh-e8968.appspot.com')) # Prioriza config, depois env, depois hardcoded default
            })
            print("🔥 Firebase Admin SDK inicializado usando __firebase_config!")
        else:
            cred_path = os.path.join(os.path.dirname(__file__), 'serviceAccountKey.json')
            if os.path.exists(cred_path):
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred, {
                    'storageBucket': os.environ.get('FIREBASE_STORAGE_BUCKET', 'gidh-e8968.appspot.com') # Use env var ou hardcode
                })
                print("🔥 Firebase Admin SDK inicializado a partir de serviceAccountKey.json (desenvolvimento)!")
            else:
                print("⚠️ Nenhuma credencial Firebase encontrada (__firebase_config ou serviceAccountKey.json). Firebase Admin SDK não inicializado.")
    except Exception as e:
        print(f"🚨 ERRO CRÍTICO ao inicializar o Firebase Admin SDK: {e}")
else:
    print("🔥 Firebase Admin SDK já foi inicializado.")

# Obtém a instância do cliente Firestore APÓS o Firebase app ser inicializado
try:
    _db_client_instance = firestore.client()
except Exception as e:
    print(f"🚨 ERRO CRÍTICO ao obter cliente Firestore: {e}")

if _db_client_instance:
    set_db(_db_client_instance)

# --- NOVO: Configura a API do Gemini ---
# A chave da API será carregada do ambiente (ou do .env se estiver em desenvolvimento local)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
if not os.getenv("GEMINI_API_KEY"):
    print("⚠️ VARIÁVEL DE AMBIENTE 'GEMINI_API_KEY' NÃO ENCONTRADA. A funcionalidade de IA pode não funcionar.")

# Context processor para injetar contagens na barra de navegação em todas as templates
@app.context_processor
def inject_navbar_counts():
    db_instance = get_db()
    if 'logged_in' in session and 'clinica_id' in session and db_instance:
        clinica_id = session['clinica_id']
        counts = get_counts_for_navbar(db_instance, clinica_id)
        return {'navbar_counts': counts}
    return {'navbar_counts': {}} # Retorna dicionário vazio se não estiver logado ou DB não estiver pronto


@app.route('/login', methods=['GET'])
def login_page():
    if 'logged_in' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/session-login', methods=['POST'])
def session_login():
    db_instance = get_db()
    if not db_instance:
        return jsonify({"success": False, "message": "Erro crítico do servidor (DB não inicializado)."}), 500

    id_token = request.json.get('idToken')
    if not id_token:
        return jsonify({"success": False, "message": "ID Token não fornecido."}), 400

    try:
        decoded_token = firebase_auth_admin.verify_id_token(id_token)
        uid_from_token = decoded_token['uid']
        email = decoded_token.get('email', '')

        mapeamento_ref = db_instance.collection('User').document(uid_from_token.strip())
        mapeamento_doc = mapeamento_ref.get()

        if mapeamento_doc.exists:
            mapeamento_data = mapeamento_doc.to_dict()
            if not mapeamento_data or 'clinica_id' not in mapeamento_data or 'role' not in mapeamento_data:
                return jsonify({"success": False, "message": "Configuração de usuário incompleta. Entre em contato com o administrador."}), 500

            session['logged_in'] = True
            session['user_uid'] = uid_from_token
            session['user_email'] = email
            session['clinica_id'] = mapeamento_data['clinica_id']
            session['clinica_nome_display'] = mapeamento_data.get('nome_clinica_display', 'Clínica On')
            session['user_role'] = mapeamento_data['role']
            session['user_name'] = mapeamento_data.get('nome_completo', email)
            # NOVO: Carrega a URL da foto do Firestore para a sessão
            session['user_photo_url'] = mapeamento_data.get('photo_url', '') 
            session.permanent = True

            print(f"Usuário {email} logado com sucesso. Função: {session['user_role']}")
            return jsonify({"success": True, "message": "Login bem-sucedido!"})
        else:
            return jsonify({"success": False, "message": "Usuário não autorizado ou não associado a uma clínica."}), 403

    except firebase_auth_admin.RevokedIdTokenError:
        return jsonify({"success": False, "message": "ID Token revogado. Faça login novamente."}), 401
    except firebase_auth_admin.UserDisabledError:
        return jsonify({"success": False, "message": "Sua conta de usuário foi desativada. Entre em contato com o administrador."}), 403
    except firebase_auth_admin.InvalidIdTokenError:
        return jsonify({"success": False, "message": "Credenciais inválidas. Verifique seu e-mail e senha."}), 401
    except Exception as e:
        print(f"Erro na verificação de token/mapeamento: {type(e).__name__} - {e}")
        return jsonify({"success": False, "message": f"Erro do servidor durante o login: {str(e)}"}), 500

@app.route('/setup-mapeamento-admin', methods=['GET', 'POST'])
def setup_mapeamento_admin():
    db_instance = get_db()
    if not db_instance: return "Firebase não inicializado", 500
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
                clinica_ref = db_instance.collection('clinicas').document(clinica_id_associada)
                if not clinica_ref.get().exists:
                    clinica_ref.set({
                        'nome_oficial': nome_clinica_display,
                        'criada_em_dashboard_setup': firestore.SERVER_TIMESTAMP
                    })
                db_instance.collection('User').document(user_uid).set({
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
              <li class="flash-message {{ category | safe }}">{{ message | safe }}</li>
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


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True, "message": "Sessão do servidor limpa."})

@app.route('/', endpoint='index')
@login_required
def index():
    db_instance = get_db()
    try:
        clinica_id = session['clinica_id']
        user_role = session.get('user_role')
        user_uid = session.get('user_uid')
    except KeyError:
        flash("Sessão inválida ou expirada. Por favor, faça login novamente.", "danger")
        return redirect(url_for('login_page'))

    profissional_id_logado = None
    if user_role != 'admin':
        if not user_uid:
            flash("UID do usuário não encontrado na sessão. Faça login novamente.", "danger")
            return redirect(url_for('login_page'))
        try:
            user_doc = db_instance.collection('User').document(user_uid).get()
            if user_doc.exists:
                profissional_id_logado = user_doc.to_dict().get('profissional_id')

            if not profissional_id_logado:
                flash("Sua conta de usuário não está corretamente associada a um perfil de profissional. Contate o administrador.", "warning")
        except Exception as e:
            flash(f"Erro ao buscar informações do profissional: {e}", "danger")
            return render_template('dashboard.html', kpi={}, proximos_agendamentos=[])

    # Referências das coleções
    agendamentos_ref = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos')
    pacientes_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes')
    peis_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis')

    current_year = datetime.datetime.now(SAO_PAULO_TZ).year
    hoje_dt = datetime.datetime.now(SAO_PAULO_TZ)
    mes_atual_nome = hoje_dt.strftime('%B').capitalize()

    total_peis = 0
    peis_finalizados = 0
    peis_em_progresso = 0
    total_atendimentos_concluidos = 0
    total_agendamentos = 0
    total_pacientes = 0

    try:
        total_pacientes = pacientes_ref.count().get()[0][0].value
    except Exception as e:
        print(f"Erro ao contar pacientes: {e}")
        flash("Erro ao carregar contagem de pacientes.", "danger")

    try:
        peis_docs = peis_ref.stream()
        for doc in peis_docs:
            total_peis += 1
            pei_data = doc.to_dict()
            if pei_data.get('status') == 'finalizado':
                peis_finalizados += 1
            elif pei_data.get('status') == 'ativo':
                peis_em_progresso += 1
    except Exception as e:
        print(f"Erro ao contar PEIs: {e}")
        flash("Erro ao carregar contagem de PEIs.", "danger")

    try:
        agendamentos_docs = agendamentos_ref.stream()
        for doc in agendamentos_docs:
            total_agendamentos += 1
            ag_data = doc.to_dict()
            if ag_data.get('status') == 'concluido':
                total_atendimentos_concluidos += 1
    except Exception as e:
        print(f"Erro ao contar agendamentos: {e}")
        flash("Erro ao carregar contagem de agendamentos.", "danger")


    kpi_cards = {
        'total_pacientes': total_pacientes,
        'total_peis': total_peis,
        'peis_finalizados': peis_finalizados,
        'peis_em_progresso': peis_em_progresso,
        'total_agendamentos': total_agendamentos,
        'total_atendimentos_concluidos': total_atendimentos_concluidos,
    }

    pacientes_pei_progress = []
    pacientes_pei_mental_map_data = {}

    try:
        all_patients_docs = pacientes_ref.order_by('nome').stream()
        for patient_doc in all_patients_docs:
            patient_id = patient_doc.id
            patient_name = patient_doc.to_dict().get('nome', 'N/A')

            total_targets_patient = 0
            completed_targets_patient = 0
            total_active_peis_patient = 0
            
            aids_attempts_by_type = defaultdict(int)
            aids_counts_by_type = defaultdict(int)

            patient_peis_query = peis_ref.where(
                filter=FieldFilter('paciente_id', '==', patient_id)
            ).where(
                filter=FieldFilter('status', '==', 'ativo')
            )

            if user_role != 'admin' and profissional_id_logado:
                patient_peis_query = patient_peis_query.where(
                    filter=FieldFilter('profissionais_ids', 'array_contains', profissional_id_logado)
                )
            elif user_role != 'admin' and not profissional_id_logado:
                patient_peis_query = peis_ref.where(filter=FieldFilter('paciente_id', '==', 'INVALID_ID_TO_RETURN_NONE'))


            for pei_doc in patient_peis_query.stream():
                total_active_peis_patient += 1
                metas_ref = peis_ref.document(pei_doc.id).collection('metas')
                metas_docs = metas_ref.stream()

                for meta_doc in metas_docs:
                    alvos_ref = metas_ref.document(meta_doc.id).collection('alvos')
                    alvos_docs = alvos_ref.stream()

                    for alvo_doc in alvos_docs:
                        total_targets_patient += 1
                        if alvo_doc.to_dict().get('status') == 'finalizada':
                            completed_targets_patient += 1
                        
                        ajudas_ref = alvos_ref.document(alvo_doc.id).collection('ajudas')
                        ajudas_docs = ajudas_ref.stream()
                        for ajuda_doc in ajudas_docs:
                            ajuda_data = ajuda_doc.to_dict()
                            sigla = ajuda_data.get('sigla')
                            attempts_count = ajuda_data.get('attempts_count', 0)
                            if sigla:
                                aids_attempts_by_type[sigla] += attempts_count
                                aids_counts_by_type[sigla] += 1
                    
            progress_percentage = 0
            if total_targets_patient > 0:
                progress_percentage = (completed_targets_patient / total_targets_patient) * 100

            mental_map_data_for_patient = {}
            for sigla, total_attempts in aids_attempts_by_type.items():
                count = aids_counts_by_type[sigla]
                mental_map_data_for_patient[sigla] = round(total_attempts / count, 1) if count > 0 else 0

            all_siglas = ['AFT', 'AFP', 'AG', 'AE', 'I']
            for sigla in all_siglas:
                if sigla not in mental_map_data_for_patient:
                    mental_map_data_for_patient[sigla] = 0

            pacientes_pei_progress.append({
                'id': patient_id,
                'nome': patient_name,
                'total_peis_ativos': total_active_peis_patient,
                'total_targets': total_targets_patient,
                'completed_targets': completed_targets_patient,
                'progress_percentage': round(progress_percentage, 1)
            })
            pacientes_pei_mental_map_data[patient_id] = mental_map_data_for_patient

    except Exception as e:
        print(f"Erro ao calcular progresso de PEIs por paciente: {e}")
        flash("Erro ao carregar progresso de PEIs por paciente.", "danger")


    agendamentos_para_analise = []
    try:
        query_analise = agendamentos_ref.where(
            filter=FieldFilter('status', 'in', ['confirmado', 'concluido'])
        ).where(
            filter=FieldFilter('data_agendamento_ts', '>=', hoje_dt - datetime.timedelta(days=15))
        )

        if user_role != 'admin':
            if profissional_id_logado:
                query_analise = query_analise.where(
                    filter=FieldFilter('profissional_id', '==', profissional_id_logado)
                )
            else:
                agendamentos_para_analise = []

        if user_role == 'admin' or profissional_id_logado:
            docs_analise = query_analise.stream()
            for doc in docs_analise:
                ag_data = doc.to_dict()
                if ag_data:
                    agendamentos_para_analise.append(ag_data)

    except Exception as e:
        print(f"Erro na consulta de agendamentos para o painel: {e}")
        flash("Erro ao calcular estatísticas do painel. Verifique seus índices do Firestore.", "danger")

    atendimentos_por_dia = Counter()
    hoje_date = hoje_dt.date()
    for i in range(15):
        data = hoje_date - datetime.timedelta(days=i)
        atendimentos_por_dia[data] = 0

    for ag in agendamentos_para_analise:
        ag_ts = ag.get('data_agendamento_ts')
        if ag_ts:
          ag_date = ag_ts.date()
          if (hoje_date - ag_date).days < 15:
              atendimentos_por_dia[ag_date] += 1

    labels_atend_receita = sorted(atendimentos_por_dia.keys())
    dados_atendimento_vs_receita = {
        "labels": [label.strftime('%d/%m') for label in labels_atend_receita],
        "atendimentos": [atendimentos_por_dia[label] for label in labels_atend_receita],
        "receitas": [0 for _ in labels_atend_receita]
    }

    contagem_procedimento = Counter()
    for ag in agendamentos_para_analise:
        ag_ts = ag.get('data_agendamento_ts')
        if ag_ts and ag_ts.month == hoje_dt.month and ag_ts.year == hoje_dt.year:
            nome_proc = ag.get('servico_procedimento_nome', 'Desconhecido')
            contagem_procedimento[nome_proc] += 1

    top_5_procedimentos = contagem_procedimento.most_common(5)
    dados_receita_procedimento = {
        "labels": [item[0] for item in top_5_procedimentos],
        "valores": [item[1] for item in top_5_procedimentos]
    }

    atendimentos_por_profissional = Counter()
    for ag in agendamentos_para_analise:
        ag_ts = ag.get('data_agendamento_ts')
        if ag_ts and ag_ts.month == hoje_dt.month and ag_ts.year == hoje_dt.year:
            nome_prof = ag.get('profissional_nome', 'Desconhecido')
            atendimentos_por_profissional[nome_prof] += 1

    top_5_profissionais = atendimentos_por_profissional.most_common(5)
    dados_desempenho_profissional = {
        "labels": [item[0] for item in top_5_profissionais],
        "valores": [item[1] for item in top_5_profissionais]
    }

    proximos_agendamentos_lista = []
    try:
        query_proximos = agendamentos_ref.where(
            filter=FieldFilter('status', '==', 'confirmado')
        ).where(
            filter=FieldFilter('data_agendamento_ts', '>=', hoje_dt.replace(hour=0, minute=0, second=0))
        )

        if user_role != 'admin':
            if profissional_id_logado:
                query_proximos = query_proximos.where(
                    filter=FieldFilter('profissional_id', '==', profissional_id_logado)
                )
            else:
                proximos_agendamentos_lista = []

        if user_role == 'admin' or profissional_id_logado:
            docs_proximos = query_proximos.order_by('data_agendamento_ts').limit(10).stream()
            for doc in docs_proximos:
                ag_data = doc.to_dict()
                if ag_data and ag_data.get('data_agendamento_ts'):
                    proximos_agendamentos_lista.append({
                        'id_profissional': ag_data.get('profissional_id'),
                        'data_agendamento': ag_data.get('data_agendamento_ts').strftime('%d/%m/%Y'),
                        'hora_agendamento': ag_data.get('hora_agendamento', "N/A"),
                        'cliente_nome': ag_data.get('paciente_nome', "N/A"),
                        'profissional_nome': ag_data.get('profissional_nome', "N/A"),
                        'servico_procedimento_nome': ag_data.get('servico_procedimento_nome', "N/A"),
                        'preco': float(ag_data.get('servico_procedimento_preco', 0.0))
                    })
    except Exception as e:
        print(f"ERRO ao buscar próximos agendamentos: {e}")
        flash("Erro ao carregar próximos agendamentos.", "danger")

    return render_template(
        'dashboard.html',
        current_year=current_year,
        mes_atual_nome=mes_atual_nome,
        kpi=kpi_cards,
        proximos_agendamentos=proximos_agendamentos_lista,
        dados_atendimento_vs_receita=json.dumps(dados_atendimento_vs_receita),
        dados_receita_procedimento=json.dumps(dados_receita_procedimento),
        dados_desempenho_profissional=json.dumps(dados_desempenho_profissional),
        pacientes_pei_progress=pacientes_pei_progress,
        pacientes_pei_mental_map_data=json.dumps(pacientes_pei_mental_map_data)
    )

# NOVO: Rota para a página de busca de PEIs
@app.route('/busca_peis', endpoint='busca_peis')
@login_required
def busca_peis():
    db_instance = get_db()
    clinica_id = session['clinica_id']
    pacientes_lista = []

    try:
        pacientes_docs = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').order_by('nome').stream()
        for doc in pacientes_docs:
            paciente_data = doc.to_dict()
            if paciente_data:
                paciente_data['id'] = doc.id
                if paciente_data.get('data_nascimento') and isinstance(paciente_data['data_nascimento'], datetime.date):
                    paciente_data['data_nascimento_fmt'] = paciente_data['data_nascimento'].strftime('%d/%m/%Y')
                elif isinstance(paciente_data.get('data_nascimento'), datetime.datetime):
                    paciente_data['data_nascimento_fmt'] = paciente_data['data_nascimento'].date().strftime('%d/%m/%Y')
                else:
                    paciente_data['data_nascimento_fmt'] = 'N/A'
                pacientes_lista.append(paciente_data)
    except Exception as e:
        flash(f'Erro ao carregar pacientes para busca de PEIs: {e}', 'danger')
        print(f"Erro busca_peis: {e}")

    return render_template('busca_peis.html', pacientes=pacientes_lista, current_year=datetime.datetime.now(SAO_PAULO_TZ).year)

# --- NOVO: Rota para importação de protocolo com IA ---
@app.route('/protocols/import_from_ai', methods=['POST'])
@login_required
def import_protocol_from_ai():
    if 'pdf_file' not in request.files:
        return jsonify({'success': False, 'message': 'Nenhum arquivo PDF enviado.'}), 400

    pdf_file = request.files['pdf_file']
    if pdf_file.filename == '':
        return jsonify({'success': False, 'message': 'Nome de arquivo inválido.'}), 400

    if pdf_file and pdf_file.filename.endswith('.pdf'):
        try:
            reader = PdfReader(pdf_file)
            text_content = ""
            for page in reader.pages:
                text_content += page.extract_text() + "\n"

            if not text_content.strip():
                return jsonify({'success': False, 'message': 'Não foi possível extrair texto do PDF. O PDF pode estar vazio ou ser uma imagem.'}), 400

            prompt = f"""
            Você é um assistente especializado em extrair informações de documentos de protocolo clínico, como o "Guia Portage" ou "Protocolo TEA".
            Seu objetivo é ler o texto fornecido e preencher um formulário de protocolo com as seguintes seções e campos.
            Preencha todos os campos que puder encontrar no documento, mesmo que estejam em diferentes formatos (listas, texto corrido, tabelas).

            **Estrutura do Protocolo a ser preenchida:**

            1.  **Geral:**
                - `nome`: Nome principal do protocolo (ex: "Guia Portage", "Protocolo TEA").
                - `descricao`: Uma descrição geral do protocolo.
                - `tipo_protocolo`: Infera se o protocolo é para "Aquisicao de Habilidades" (se focar em desenvolvimento, marcos, aprendizado) ou "Reducao de Comportamentos" (se focar em manejo de comportamentos desafiadores, estereotipias). Se ambos ou não claro, priorize "Aquisicao de Habilidades".
                - `ativo`: Booleano, sempre `true` para protocolos importados.

            2.  **Etapas (seções ou fases do protocolo):**
                - `nome`: Nome da etapa (ex: "Socialização - 0 a 1 ano", "Fase I: Troca Física").
                - `descricao`: Descrição breve da etapa, se disponível.

            3.  **Níveis (faixas etárias ou níveis de complexidade):**
                - `nivel`: O número do nível, se aplicável (ex: 1, 2, 3).
                - `faixa_etaria`: A faixa etária associada a este nível (ex: "0 a 1 ano", "3 a 4 anos").

            4.  **Habilidades (listas de habilidades, marcos de desenvolvimento ou competências):**
                - `nome`: Nome da habilidade ou do marco (ex: "Observa uma pessoa movimentando-se em seu campo visual.", "Suga e deglute líquidos.").

            5.  **Pontuação (critérios de avaliação ou escalas):**
                - `tipo`: Tipo de pontuação (ex: "S-Sim", "N-Não", "AV-Às vezes", "Pontuação ATEC").
                - `descricao`: Descrição do critério ou o que ele representa (ex: "alcançou", "ainda não alcançou", "Parcialmente verdadeiro").
                - `valor`: Valor numérico associado, se houver (ex: 2 para "Sim", 1 para "Às vezes", 0 para "Não").

            6.  **Tarefas/Testes (itens específicos a serem avaliados ou aplicados):**
                - `nivel`: O nível ou faixa etária da tarefa (inteiro).
                - `item`: O número do item ou da questão (string, ex: "01", "15").
                - `nome`: O texto da tarefa ou da pergunta (ex: "Observa uma pessoa movimentando-se em seu campo visual.", "Seu filho gosta de se balançar, de pular no seu joelho, etc.?").
                - `habilidade_marco`: A área de desenvolvimento ou habilidade principal a que a tarefa se refere (ex: "Socialização", "Linguagem", "Cognição", "Desenvolvimento Motor", "Auto Cuidados").
                - `resultado_observacao`: Se houver um campo de resultado ou observação na tabela (ex: "Resultado").
                - `pergunta`: Se o item for uma pergunta explícita.
                - `exemplo`: Se houver um exemplo para a tarefa.
                - `criterio`: Se houver um critério de sucesso para a tarefa.
                - `objetivo`: Se a tarefa for um objetivo específico.

            7.  **Observações Gerais:**
                - `observacoes_gerais`: Quaisquer observações gerais, dicas, ou informações adicionais sobre o protocolo que não se encaixam nas categorias acima.

            **Instruções CRÍTICAS para Geração de JSON:**
            - **O resultado DEVE ser um objeto JSON VÁLIDO e COMPLETO. ABSOLUTAMENTE NADA MAIS.**
            - **Todas as strings DEVEM ser escapadas corretamente para JSON.** Isso é MANDATÓRIO para evitar erros de parsing.
                - Aspas duplas (") dentro de strings DEVEM ser escapadas como `\"`.
                - Quebras de linha (`\n`) dentro de strings DEVEM ser escapadas como `\\n`.
                - Retornos de carro (`\r`) dentro de strings DEVEM ser escapadas como `\\r`.
                - Barras invertidas (`\`) dentro de strings DEVEM ser escapadas como `\\\\`.
                - **Qualquer outro caractere que não seja JSON-safe (como caracteres de controle ou outros símbolos que possam quebrar o JSON) DEVE ser escapado ou removido.**
            - **Garanta que todos os elementos de arrays e pares chave-valor em objetos sejam separados por VÍRGULAS.**
            - **O objeto JSON deve começar com `{{` e terminar com `}}`. Sem prefixos ou sufixos de texto.**

            TEXTO DO PROTOCOLO:
            {text_content}
            """

            model = genai.GenerativeModel('gemini-2.0-flash')
            response = model.generate_content(
                prompt,
                generation_config={
                    "response_mime_type": "application/json",
                    "response_schema": response_schema
                }
            )

            print("Raw Gemini Response Text:")
            raw_gemini_response = response.text
            print(raw_gemini_response)

            parsed_data = {}

            def sanitize_json_string(text):
                if not isinstance(text, str):
                    return text
                
                text = text.replace('\\', '\\\\')
                text = text.replace('"', '\\"')
                text = text.replace('\n', '\\n')
                text = text.replace('\r', '\\r')
                text = text.replace('\t', '\\t')
                return text

            sanitized_response_text = raw_gemini_response
            try:
                parsed_data = json.loads(raw_gemini_response)
            except json.JSONDecodeError as e:
                print(f"JSONDecodeError: {e}. Tentando sanitizar e corrigir a resposta...")
                cleaned_text_for_json = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', raw_gemini_response)
                
                try:
                    parsed_data = json.loads(cleaned_text_for_json)
                    print("Resposta parseada com sucesso após limpeza de caracteres de controle.")
                except json.JSONDecodeError as e_cleaned:
                    print(f"Falha ao parsear mesmo após limpeza de caracteres de controle: {e_cleaned}")
                    return jsonify({
                        'success': False, 
                        'message': f'Erro ao interpretar a resposta da IA. Formato JSON inválido. Detalhes: {e_cleaned}. Verifique o log do servidor para a resposta bruta.'
                    }), 500

            return jsonify({'success': True, 'data': parsed_data}), 200

        except Exception as e:
            print(f"Erro ao processar PDF ou chamar Gemini: {e}")
            return jsonify({'success': False, 'message': f'Erro interno ao processar o arquivo: {str(e)}'}), 500
    else:
        return jsonify({'success': False, 'message': 'Formato de arquivo não suportado. Por favor, envie um PDF.'}), 400


register_users_routes(app)
register_professionals_routes(app)
register_patients_routes(app)
register_services_routes(app)
register_covenants_routes(app)
register_schedules_routes(app)
register_appointments_routes(app)
register_medical_records_routes(app)
register_estoque_routes(app)
register_contas_a_pagar_routes(app)
app.register_blueprint(peis_bp) 
register_patrimonio_routes(app) 
app.register_blueprint(protocols_bp)
app.register_blueprint(weekly_planning_bp) 
app.register_blueprint(user_api_bp)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)), debug=True)
