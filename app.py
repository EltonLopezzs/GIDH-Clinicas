import datetime
import json
import os

import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth_admin
from flask import Flask, flash, redirect, render_template, request, session, url_for, jsonify, render_template_string
from flask_cors import CORS

from google.cloud.firestore_v1.base_query import FieldFilter
from collections import Counter

# Importar utils e as funções de registro de rotas dos módulos de blueprint
from utils import set_db, get_db, login_required, admin_required, SAO_PAULO_TZ, parse_date_input, convert_doc_to_dict
from blueprints.users import register_users_routes
from blueprints.professionals import register_professionals_routes
from blueprints.patients import register_patients_routes
from blueprints.services import register_services_routes
from blueprints.covenants import register_covenants_routes
from blueprints.schedules import register_schedules_routes
from blueprints.appointments import register_appointments_routes
from blueprints.medical_records import register_medical_records_routes


app = Flask(__name__)
app.secret_key = os.urandom(24)
CORS(app)

_db_client_instance = None
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
        _db_client_instance = firestore.client()
    else:
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

if _db_client_instance:
    set_db(_db_client_instance)

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

    # Coletar filtros da URL
    filtros_atuais = {
        'status': request.args.get('status', '').strip(),
        'data_inicio': request.args.get('data_inicio', '').strip(),
        'data_fim': request.args.get('data_fim', '').strip(),
        'convenio_id': request.args.get('convenio_id', '').strip(),
        'profissional_id': request.args.get('profissional_id', '').strip() if user_role == 'admin' else ''
    }

    # Carregar dados para preencher os seletores de filtro
    profissionais_lista = []
    convenios_lista = []
    try:
        profissionais_docs = db_instance.collection(f'clinicas/{clinica_id}/profissionais').order_by('nome').stream()
        for doc in profissionais_docs:
            prof_data = doc.to_dict()
            profissionais_lista.append({'id': doc.id, 'nome': prof_data.get('nome')})
        
        convenios_docs = db_instance.collection(f'clinicas/{clinica_id}/convenios').order_by('nome').stream()
        for doc in convenios_docs:
            conv_data = doc.to_dict()
            convenios_lista.append({'id': doc.id, 'nome': conv_data.get('nome')})
    except Exception as e:
        flash(f"Erro ao carregar dados para os filtros: {e}", "danger")

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
            return render_template('dashboard.html', kpi={}, proximos_agendamentos=[], profissionais=[], convenios=[], filtros_atuais=filtros_atuais)

    agendamentos_ref = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos')
    pacientes_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes')
    current_year = datetime.datetime.now(SAO_PAULO_TZ).year
    
    hoje_dt = datetime.datetime.now(SAO_PAULO_TZ)
    mes_atual_nome = hoje_dt.strftime('%B').capitalize()
    
    # Define o período de análise padrão (mês anterior e atual) se não houver filtro de data
    if filtros_atuais['data_inicio']:
        inicio_mes_analise_dt = SAO_PAULO_TZ.localize(datetime.datetime.strptime(filtros_atuais['data_inicio'], '%Y-%m-%d'))
    else:
        inicio_mes_analise_dt = hoje_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    fim_mes_anterior_dt = inicio_mes_analise_dt - datetime.timedelta(seconds=1)
    inicio_mes_anterior_dt = fim_mes_anterior_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Construir a query base para agendamentos
    query_analise = agendamentos_ref

    # Aplicar filtros
    if filtros_atuais['status']:
        query_analise = query_analise.where(filter=FieldFilter('status', '==', filtros_atuais['status']))
    else:
        # Se nenhum status for selecionado, busca apenas os concluídos e confirmados para os KPIs
        query_analise = query_analise.where(filter=FieldFilter('status', 'in', ['confirmado', 'concluido']))
    
    if filtros_atuais['data_inicio']:
        query_analise = query_analise.where(filter=FieldFilter('data_agendamento_ts', '>=', inicio_mes_analise_dt.astimezone(pytz.utc)))
    
    if filtros_atuais['data_fim']:
        dt_fim = SAO_PAULO_TZ.localize(datetime.datetime.strptime(filtros_atuais['data_fim'], '%Y-%m-%d')).replace(hour=23, minute=59, second=59)
        query_analise = query_analise.where(filter=FieldFilter('data_agendamento_ts', '<=', dt_fim.astimezone(pytz.utc)))

    if user_role == 'admin' and filtros_atuais['profissional_id']:
        query_analise = query_analise.where(filter=FieldFilter('profissional_id', '==', filtros_atuais['profissional_id']))
    elif user_role != 'admin':
        if profissional_id_logado:
            query_analise = query_analise.where(filter=FieldFilter('profissional_id', '==', profissional_id_logado))
        else:
            query_analise = query_analise.where(filter=FieldFilter('profissional_id', '==', 'ID_INVALIDO_PARA_NAO_RETORNAR_NADA'))
            
    # Lógica de filtro por convênio (requer busca adicional de pacientes)
    pacientes_com_convenio = []
    if filtros_atuais['convenio_id']:
        try:
            pacientes_query = pacientes_ref.where(filter=FieldFilter('convenio_id', '==', filtros_atuais['convenio_id'])).stream()
            pacientes_com_convenio = [doc.id for doc in pacientes_query]
            if pacientes_com_convenio:
                 # Limitação: A consulta 'in' suporta no máximo 30 itens.
                query_analise = query_analise.where(filter=FieldFilter('paciente_id', 'in', pacientes_com_convenio[:30]))
            else:
                 # Se nenhum paciente tem o convênio, a query não retornará nada
                query_analise = query_analise.where(filter=FieldFilter('paciente_id', '==', 'ID_INVALIDO_PARA_NAO_RETORNAR_NADA'))
        except Exception as e:
            print(f"Erro ao filtrar por convênio: {e}")
            flash("Não foi possível aplicar o filtro por convênio.", "warning")


    # Executar a query
    agendamentos_para_analise = []
    try:
        docs_analise = query_analise.stream()
        for doc in docs_analise:
            ag_data = doc.to_dict()
            if ag_data:
                agendamentos_para_analise.append(ag_data)
    except Exception as e:
        print(f"Erro na consulta de agendamentos para o painel: {e}")
        flash("Erro ao calcular estatísticas do painel. Verifique seus índices do Firestore.", "danger")

    # --- Cálculo de KPIs com dados filtrados ---
    receita_mes_atual = sum(float(ag.get('servico_procedimento_preco', 0)) for ag in agendamentos_para_analise if ag.get('data_agendamento_ts') and ag['data_agendamento_ts'] >= inicio_mes_analise_dt)
    atendimentos_mes_atual = len([ag for ag in agendamentos_para_analise if ag.get('data_agendamento_ts') and ag['data_agendamento_ts'] >= inicio_mes_analise_dt])
    
    # Para comparação, precisamos de uma query separada para o mês anterior se o filtro de data estiver ativo
    receita_mes_anterior = 0.0
    atendimentos_mes_anterior = 0
    # (A lógica de comparação do mês anterior pode ser simplificada ou removida quando há filtros de data)

    novos_pacientes_mes = 0
    if filtros_atuais['data_inicio']:
        try:
            novos_pacientes_mes_query = pacientes_ref.where(filter=FieldFilter('data_cadastro', '>=', inicio_mes_analise_dt))
            if filtros_atuais['data_fim']:
                novos_pacientes_mes_query = novos_pacientes_mes_query.where(filter=FieldFilter('data_cadastro', '<=', dt_fim))
            novos_pacientes_mes = novos_pacientes_mes_query.count().get()[0][0].value
        except Exception as e:
            print(f"Erro ao contar novos pacientes: {e}")
            novos_pacientes_mes = 'N/A'

    kpi_cards = {
        'receita_mes_atual': receita_mes_atual,
        'atendimentos_mes_atual': atendimentos_mes_atual,
        'variacao_receita': 0, # Simplificado quando há filtro de data
        'variacao_atendimentos': 0, # Simplificado quando há filtro de data
        'novos_pacientes_mes': novos_pacientes_mes,
    }
    
    # --- Cálculo dos Gráficos com dados filtrados ---
    atendimentos_por_dia = Counter()
    receita_por_dia = Counter()
    
    data_loop_inicial = inicio_mes_analise_dt.date()
    data_loop_final = dt_fim.date() if filtros_atuais['data_fim'] else hoje_dt.date()
    dias_no_intervalo = (data_loop_final - data_loop_inicial).days + 1

    dias_labels = [(data_loop_inicial + datetime.timedelta(days=i)).strftime('%d/%m') for i in range(dias_no_intervalo)]
    for dia in dias_labels:
        atendimentos_por_dia[dia] = 0
        receita_por_dia[dia] = 0

    for ag in agendamentos_para_analise:
        ag_ts = ag.get('data_agendamento_ts')
        if ag_ts:
            dia_str = ag_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m')
            if dia_str in atendimentos_por_dia:
                atendimentos_por_dia[dia_str] += 1
                receita_por_dia[dia_str] += float(ag.get('servico_procedimento_preco', 0))

    dados_atendimento_vs_receita = {
        "labels": dias_labels,
        "atendimentos": [atendimentos_por_dia[label] for label in dias_labels],
        "receitas": [receita_por_dia[label] for label in dias_labels]
    }

    receita_por_procedimento = Counter(ag.get('servico_procedimento_nome', 'Desconhecido') for ag in agendamentos_para_analise)
    top_5_procedimentos = receita_por_procedimento.most_common(5)
    dados_receita_procedimento = {
        "labels": [item[0] for item in top_5_procedimentos],
        "valores": [item[1] for item in top_5_procedimentos]
    }

    atendimentos_por_profissional = Counter(ag.get('profissional_nome', 'Desconhecido') for ag in agendamentos_para_analise)
    top_5_profissionais = atendimentos_por_profissional.most_common(5)
    dados_desempenho_profissional = {
        "labels": [item[0] for item in top_5_profissionais],
        "valores": [item[1] for item in top_5_profissionais]
    }

    # Próximos agendamentos não são afetados pelos filtros do dashboard
    proximos_agendamentos_lista = []
    try:
        query_proximos = db_instance.collection(f'clinicas/{clinica_id}/agendamentos').where(
            filter=FieldFilter('status', '==', 'confirmado')
        ).where(
            filter=FieldFilter('data_agendamento_ts', '>=', hoje_dt.replace(hour=0, minute=0, second=0, microsecond=0))
        )
        
        if user_role != 'admin' and profissional_id_logado:
            query_proximos = query_proximos.where(
                filter=FieldFilter('profissional_id', '==', profissional_id_logado)
            )

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
        profissionais=profissionais_lista,
        convenios=convenios_lista,
        filtros_atuais=filtros_atuais
    )

# Chamar as funções para registrar as rotas diretamente no app
register_users_routes(app)
register_professionals_routes(app)
register_patients_routes(app)
register_services_routes(app)
register_covenants_routes(app)
register_schedules_routes(app)
register_appointments_routes(app)
register_medical_records_routes(app)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)), debug=True)
