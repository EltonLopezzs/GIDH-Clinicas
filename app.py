import datetime # Alterado para importar datetime diretamente
import json
import os
from functools import wraps

import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth_admin
from flask import Flask, flash, redirect, render_template, request, session, url_for, jsonify, render_template_string
from flask_cors import CORS

from google.cloud.firestore_v1.base_query import FieldFilter
from collections import Counter, defaultdict # Importar defaultdict

# Importar utils e as funções de registro de rotas dos módulos de blueprint
# Assumindo que 'utils.py' existe e contém 'set_db', 'get_db', 'login_required', 'admin_required', 'SAO_PAULO_TZ', 'parse_date_input', 'convert_doc_to_dict'
from utils import set_db, get_db, login_required, admin_required, SAO_PAULO_TZ, parse_date_input, convert_doc_to_dict

# Importar as funções de registro de rotas de cada blueprint
from blueprints.users import register_users_routes
from blueprints.professionals import register_professionals_routes
from blueprints.patients import register_patients_routes
from blueprints.services import register_services_routes
from blueprints.covenants import register_covenants_routes
from blueprints.schedules import register_schedules_routes
from blueprints.appointments import register_appointments_routes
from blueprints.medical_records import register_medical_records_routes
from blueprints.estoque import register_estoque_routes # Importar o blueprint de Estoque
from blueprints.contas_a_pagar import register_contas_a_pagar_routes # Importar o blueprint de Contas a Pagar
from blueprints.peis import peis_bp # Importar o blueprint de PEIs (mantido como blueprint direto)
from blueprints.patrimonio import register_patrimonio_routes # NOVO: Importar o blueprint de Patrimônio

app = Flask(__name__)
app.secret_key = os.urandom(24) # Usando os.urandom para gerar uma chave secreta forte
CORS(app)

_db_client_instance = None
try:
    # Tenta obter a configuração do Firebase de uma variável de ambiente (usada no ambiente Canvas)
    firebase_config_str = os.environ.get('__firebase_config')
    if firebase_config_str:
        firebase_config_dict = json.loads(firebase_config_str)
        cred = credentials.Certificate(firebase_config_dict)
        if not firebase_admin._apps: # Verifica se o app Firebase já foi inicializado
            firebase_admin.initialize_app(cred)
            print("🔥 Firebase Admin SDK inicializado usando __firebase_config!")
        else:
            print("🔥 Firebase Admin SDK já foi inicializado.")
        _db_client_instance = firestore.client()
    else:
        # Fallback para serviceAccountKey.json para desenvolvimento local
        cred_path = os.path.join(os.path.dirname(__file__), 'serviceAccountKey.json')
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
                print("🔥 Firebase Admin SDK inicializado a partir de serviceAccountKey.json (desenvolvimento)!")
            else:
                print("🔥 Firebase Admin SDK já foi inicializado.")
            _db_client_instance = firestore.client()
        else:
            print("⚠️ Nenhuma credencial Firebase encontrada (__firebase_config ou serviceAccountKey.json). Firebase Admin SDK não inicializado.")
except Exception as e:
    print(f"🚨 ERRO CRÍTICO ao inicializar o Firebase Admin SDK: {e}")

# Define a instância do Firestore para ser acessível globalmente via utils
if _db_client_instance:
    set_db(_db_client_instance)

# --- Rotas de Autenticação ---

@app.route('/login', methods=['GET'])
def login_page():
    """Renderiza a página de login."""
    if 'logged_in' in session:
        return redirect(url_for('index')) # Redireciona para o dashboard se já logado
    return render_template('login.html')

@app.route('/session-login', methods=['POST'])
def session_login():
    """
    Processa o ID Token enviado do frontend para estabelecer a sessão do servidor.
    Isso é uma abordagem mais segura para autenticação em Flask com Firebase.
    """
    db_instance = get_db()
    if not db_instance:
        return jsonify({"success": False, "message": "Erro crítico do servidor (DB não inicializado)."}), 500

    id_token = request.json.get('idToken')
    if not id_token:
        return jsonify({"success": False, "message": "ID Token não fornecido."}), 400

    try:
        # Verifica o ID Token com o Firebase Admin SDK
        decoded_token = firebase_auth_admin.verify_id_token(id_token)
        uid_from_token = decoded_token['uid']
        email = decoded_token.get('email', '')

        # Busca o documento do usuário na coleção 'User' para obter a role e clinica_id
        mapeamento_ref = db_instance.collection('User').document(uid_from_token.strip())
        mapeamento_doc = mapeamento_ref.get()

        if mapeamento_doc.exists:
            mapeamento_data = mapeamento_doc.to_dict()
            # Valida se os dados essenciais estão presentes
            if not mapeamento_data or 'clinica_id' not in mapeamento_data or 'role' not in mapeamento_data:
                return jsonify({"success": False, "message": "Configuração de usuário incompleta. Entre em contato com o administrador."}), 500

            # Define as variáveis de sessão
            session['logged_in'] = True
            session['user_uid'] = uid_from_token
            session['user_email'] = email
            session['clinica_id'] = mapeamento_data['clinica_id']
            session['clinica_nome_display'] = mapeamento_data.get('nome_clinica_display', 'Clínica On')
            session['user_role'] = mapeamento_data['role']
            session['user_name'] = mapeamento_data.get('nome_completo', email) # Pega o nome completo se existir, senão usa o email

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
    """
    Rota para configurar manualmente a associação de um UID de usuário do Firebase Auth
    a uma clínica e uma role no Firestore. Útil para o setup inicial de administradores.
    """
    db_instance = get_db()
    if not db_instance:
        return "Firebase não inicializado. Verifique a configuração do servidor.", 500

    if request.method == 'POST':
        user_uid = request.form['user_uid'].strip()
        email_para_referencia = request.form['email_para_referencia'].strip().lower()
        clinica_id_associada = request.form['clinica_id_associada'].strip()
        nome_clinica_display = request.form['nome_clinica_display'].strip()
        user_role = request.form.get('user_role', 'medico').strip() # Padrão para 'medico'

        if not all([user_uid, email_para_referencia, clinica_id_associada, nome_clinica_display, user_role]):
            flash("Todos os campos são obrigatórios.", "danger")
        else:
            try:
                # Cria ou verifica a existência da clínica
                clinica_ref = db_instance.collection('clinicas').document(clinica_id_associada)
                if not clinica_ref.get().exists:
                    clinica_ref.set({
                        'nome_oficial': nome_clinica_display,
                        'criada_em_dashboard_setup': firestore.SERVER_TIMESTAMP
                    })
                
                # Associa o UID do usuário à clínica e role
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

    # HTML inline para o formulário de setup
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
    """Limpa a sessão do usuário e retorna uma resposta JSON."""
    session.clear()
    return jsonify({"success": True, "message": "Sessão do servidor limpa."})

@app.route('/', endpoint='index')
@login_required
def index():
    """
    Rota principal do dashboard. Coleta e organiza os dados para exibição,
    com lógica de filtragem por profissional se o usuário não for admin.
    """
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
                # Retorna um dashboard vazio para evitar erros, mas com a mensagem de aviso
                return render_template('dashboard.html', kpi={}, proximos_agendamentos=[],
                                       dados_atendimento_vs_receita=json.dumps({'labels':[], 'atendimentos':[], 'receitas':[]}),
                                       dados_receita_procedimento=json.dumps({'labels':[], 'valores':[]}),
                                       dados_desempenho_profissional=json.dumps({'labels':[], 'valores':[]}),
                                       pacientes_pei_progress=[], pacientes_pei_mental_map_data=json.dumps({}))
        except Exception as e:
            flash(f"Erro ao buscar informações do profissional: {e}", "danger")
            return render_template('dashboard.html', kpi={}, proximos_agendamentos=[],
                                   dados_atendimento_vs_receita=json.dumps({'labels':[], 'atendimentos':[], 'receitas':[]}),
                                   dados_receita_procedimento=json.dumps({'labels':[], 'valores':[]}),
                                   dados_desempenho_profissional=json.dumps({'labels':[], 'valores':[]}),
                                   pacientes_pei_progress=[], pacientes_pei_mental_map_data=json.dumps({}))

    # Referências das coleções específicas da clínica
    agendamentos_ref = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos')
    pacientes_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes')
    peis_ref = db_instance.collection('clinicas').document(clinica_id).collection('peis')

    current_year = datetime.datetime.now(SAO_PAULO_TZ).year
    hoje_dt = datetime.datetime.now(SAO_PAULO_TZ)
    mes_atual_nome = hoje_dt.strftime('%B').capitalize()

    # --- Coleta de Dados para KPIs ---
    kpi_cards = {
        'total_pacientes': 0,
        'total_peis': 0,
        'peis_finalizados': 0,
        'peis_em_progresso': 0,
        'total_agendamentos': 0,
        'total_atendimentos_concluidos': 0,
    }

    try:
        # Contagem de Pacientes
        # Usando .count().get()[0][0].value para contagens agregadas
        total_pacientes = pacientes_ref.count().get()[0][0].value
        kpi_cards['total_pacientes'] = total_pacientes
    except Exception as e:
        print(f"Erro ao contar pacientes: {e}")
        flash("Erro ao carregar contagem de pacientes.", "danger")

    try:
        # Contagem de PEIs e seus status
        # Para contagens de status, ainda é necessário iterar ou usar agregações mais complexas se disponíveis
        peis_query = peis_ref
        if user_role != 'admin' and profissional_id_logado:
            peis_query = peis_query.where(filter=FieldFilter('profissionais_ids', 'array_contains', profissional_id_logado))

        peis_docs_kpi = peis_query.stream()
        for doc in peis_docs_kpi:
            kpi_cards['total_peis'] += 1
            pei_data = doc.to_dict()
            if pei_data.get('status') == 'finalizado':
                kpi_cards['peis_finalizados'] += 1
            elif pei_data.get('status') == 'ativo': # Assumindo 'ativo' como em progresso
                kpi_cards['peis_em_progresso'] += 1
    except Exception as e:
        print(f"Erro ao contar PEIs: {e}")
        flash("Erro ao carregar contagem de PEIs.", "danger")

    try:
        # Contagem de Agendamentos e Atendimentos Concluídos
        agendamentos_query_kpi = agendamentos_ref
        if user_role != 'admin' and profissional_id_logado:
            agendamentos_query_kpi = agendamentos_query_kpi.where(filter=FieldFilter('profissional_id', '==', profissional_id_logado))

        agendamentos_docs_kpi = agendamentos_query_kpi.stream()
        for doc in agendamentos_docs_kpi:
            kpi_cards['total_agendamentos'] += 1
            ag_data = doc.to_dict()
            if ag_data.get('status') == 'concluido':
                kpi_cards['total_atendimentos_concluidos'] += 1
    except Exception as e:
        print(f"Erro ao contar agendamentos: {e}")
        flash("Erro ao carregar contagem de agendamentos.", "danger")

    # --- Progresso dos Pacientes em PEIs e Dados para Mapa Mental ---
    pacientes_pei_progress = []
    pacientes_pei_mental_map_data = {} # Para armazenar dados do gráfico de radar

    try:
        # Fetch all patients (or a paginated subset if there are too many)
        all_patients_docs = pacientes_ref.order_by('nome').stream()
        for patient_doc in all_patients_docs:
            patient_id = patient_doc.id
            patient_name = patient_doc.to_dict().get('nome', 'N/A')

            total_targets_patient = 0
            completed_targets_patient = 0
            total_active_peis_patient = 0
            
            # Para o mapa mental, agregaremos as tentativas por tipo de ajuda
            aids_attempts_by_type = defaultdict(int)
            aids_counts_by_type = defaultdict(int) # Para calcular a média

            # Fetch active PEIs for this patient, filtered by professional if not admin
            patient_peis_query = peis_ref.where(
                filter=FieldFilter('paciente_id', '==', patient_id)
            ).where(
                filter=FieldFilter('status', '==', 'ativo') # Only active PEIs
            )

            if user_role != 'admin' and profissional_id_logado:
                patient_peis_query = patient_peis_query.where(
                    filter=FieldFilter('profissionais_ids', 'array_contains', profissional_id_logado)
                )
            elif user_role != 'admin' and not profissional_id_logado:
                # Se não é admin e não tem profissional_id associado, este profissional não deve ver nenhum PEI
                # Usamos uma condição que sempre retorna vazio para evitar erros
                patient_peis_query = peis_ref.where(filter=FieldFilter('paciente_id', '==', 'INVALID_ID_TO_RETURN_NONE'))


            for pei_doc in patient_peis_query.stream():
                total_active_peis_patient += 1
                
                # For each active PEI, fetch its metas
                metas_ref = peis_ref.document(pei_doc.id).collection('metas')
                metas_docs = metas_ref.stream()

                for meta_doc in metas_docs:
                    # For each meta, fetch its alvos (targets)
                    alvos_ref = metas_ref.document(meta_doc.id).collection('alvos')
                    alvos_docs = alvos_ref.stream()

                    for alvo_doc in alvos_docs:
                        total_targets_patient += 1
                        if alvo_doc.to_dict().get('status') == 'finalizada':
                            completed_targets_patient += 1
                        
                        # Coletar dados das ajudas para o mapa mental
                        ajudas_ref = alvos_ref.document(alvo_doc.id).collection('ajudas')
                        ajudas_docs = ajudas_ref.stream()
                        for ajuda_doc in ajudas_docs:
                            ajuda_data = ajuda_doc.to_dict()
                            sigla = ajuda_data.get('sigla')
                            attempts_count = ajuda_data.get('tentativas_necessarias', 0)
                            if sigla:
                                aids_attempts_by_type[sigla] += attempts_count
                                aids_counts_by_type[sigla] += 1
            
            progress_percentage = 0
            if total_targets_patient > 0:
                progress_percentage = (completed_targets_patient / total_targets_patient) * 100

            # Calcular a média de tentativas para cada tipo de ajuda
            mental_map_data_for_patient = {}
            all_siglas = ['AFT', 'AFP', 'AG', 'AE', 'I'] # Ordem desejada para o gráfico
            for sigla in all_siglas:
                total_attempts = aids_attempts_by_type[sigla]
                count = aids_counts_by_type[sigla]
                mental_map_data_for_patient[sigla] = round(total_attempts / count, 1) if count > 0 else 0

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


    # --- Lógica para gráficos (Atendimentos Diários, Top Procedimentos, Top Profissionais) ---
    agendamentos_para_analise = []
    try:
        # Busca agendamentos dos últimos 15 dias com status relevante
        query_analise = agendamentos_ref.where(
            filter=FieldFilter('status', 'in', ['confirmado', 'concluido'])
        ).where(
            filter=FieldFilter('data_agendamento_ts', '>=', hoje_dt - datetime.timedelta(days=15)) # Últimos 15 dias
        )

        if user_role != 'admin' and profissional_id_logado:
            query_analise = query_analise.where(
                filter=FieldFilter('profissional_id', '==', profissional_id_logado)
            )
        elif user_role != 'admin' and not profissional_id_logado:
            agendamentos_para_analise = [] # Se não tem profissional_id logado, não mostra nada

        if user_role == 'admin' or profissional_id_logado: # Garante que só executa a query se houver permissão
            docs_analise = query_analise.stream()
            for doc in docs_analise:
                ag_data = doc.to_dict()
                if ag_data:
                    agendamentos_para_analise.append(ag_data)

    except Exception as e:
        print(f"Erro na consulta de agendamentos para o painel: {e}")
        flash("Erro ao calcular estatísticas do painel. Verifique seus índices do Firestore.", "danger")

    # Atendimentos Diários (Últimos 15 dias)
    atendimentos_por_dia = Counter()
    receita_por_dia = defaultdict(float) # Para somar a receita por dia

    hoje_date = hoje_dt.date()
    # Inicializa os últimos 15 dias com 0 atendimentos e 0 receita
    for i in range(15):
        data = hoje_date - datetime.timedelta(days=i)
        atendimentos_por_dia[data] = 0
        receita_por_dia[data] = 0.0

    for ag in agendamentos_para_analise:
        ag_ts = ag.get('data_agendamento_ts')
        if ag_ts:
            ag_date = ag_ts.date()
            if (hoje_date - ag_date).days < 15:
                atendimentos_por_dia[ag_date] += 1
                receita_por_dia[ag_date] += float(ag.get('servico_procedimento_preco', 0.0))

    labels_atend_receita = sorted(atendimentos_por_dia.keys())
    dados_atendimento_vs_receita = {
        "labels": [label.strftime('%d/%m') for label in labels_atend_receita],
        "atendimentos": [atendimentos_por_dia[label] for label in labels_atend_receita],
        "receitas": [round(receita_por_dia[label], 2) for label in labels_atend_receita]
    }

    # Gráfico de Top Procedimentos por Atendimentos (Mês Atual)
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

    # Gráfico de Top Profissionais por Atendimentos (Mês Atual)
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

    # --- Próximos Agendamentos para Mobile Slider ---
    proximos_agendamentos_lista = []
    try:
        # Busca agendamentos futuros ou de hoje com status 'confirmado'
        query_proximos = agendamentos_ref.where(
            filter=FieldFilter('status', '==', 'confirmado')
        ).where(
            filter=FieldFilter('data_agendamento_ts', '>=', hoje_dt.replace(hour=0, minute=0, second=0, microsecond=0)) # A partir do início do dia atual
        )

        if user_role != 'admin':
            if profissional_id_logado:
                query_proximos = query_proximos.where(
                    filter=FieldFilter('profissional_id', '==', profissional_id_logado)
                )
            else:
                proximos_agendamentos_lista = [] # Se não tem profissional_id logado, não mostra nada

        if user_role == 'admin' or profissional_id_logado: # Garante que só executa a query se houver permissão
            docs_proximos = query_proximos.order_by('data_agendamento_ts').limit(10).stream() # Limita para mobile
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
    """
    Renderiza a página de busca de PEIs, carregando a lista de pacientes
    para preencher o dropdown de filtro.
    """
    db_instance = get_db()
    clinica_id = session['clinica_id']
    pacientes_lista = []

    try:
        # Busca todos os pacientes para preencher o dropdown
        pacientes_docs = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').order_by('nome').stream()
        for doc in pacientes_docs:
            paciente_data = doc.to_dict()
            if paciente_data:
                paciente_data['id'] = doc.id
                # Formata a data de nascimento para exibição na tabela
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

@app.route('/offline')
def offline():
    """Renderiza a página offline para PWA."""
    return render_template('offline.html')

# Chamar as funções para registrar as rotas de cada blueprint
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
app.register_blueprint(peis_bp) # PEIs é registrado como blueprint direto
register_patrimonio_routes(app) 

if __name__ == '__main__':
    # Define o host e a porta para a execução do Flask, usando variáveis de ambiente se disponíveis
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)), debug=True)
