import datetime
import json
import os
from functools import wraps
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth_admin
from flask import Flask, flash, redirect, render_template, request, session, url_for, jsonify, render_template_string
import pytz

from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.field_path import FieldPath
from collections import Counter

app = Flask(__name__)
app.secret_key = os.urandom(24)
CORS(app)

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

SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')

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
        return SAO_PAULO_TZ.localize(datetime.datetime(parsed_date.year, parsed_date.month, parsed_date.day, 0, 0, 0))
    
    return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or 'clinica_id' not in session or 'user_uid' not in session:
            return redirect(url_for('login_page'))
        if not db:
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

@app.route('/login', methods=['GET'])
def login_page():
    if 'logged_in' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/session-login', methods=['POST'])
def session_login():
    if not db:
        return jsonify({"success": False, "message": "Erro crítico do servidor (DB não inicializado)."}), 500

    id_token = request.json.get('idToken')
    if not id_token:
        return jsonify({"success": False, "message": "ID Token não fornecido."}), 400

    try:
        decoded_token = firebase_auth_admin.verify_id_token(id_token)
        uid_from_token = decoded_token['uid']
        email = decoded_token.get('email', '')

        mapeamento_ref = db.collection('User').document(uid_from_token.strip())
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
    if not db: return "Firebase não inicializado", 500
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
                clinica_ref = db.collection('clinicas').document(clinica_id_associada)
                if not clinica_ref.get().exists:
                    clinica_ref.set({
                        'nome_oficial': nome_clinica_display,
                        'criada_em_dashboard_setup': firestore.SERVER_TIMESTAMP
                    })
                db.collection('User').document(user_uid).set({
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

@app.route('/')
@login_required
def index():
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
            user_doc = db.collection('User').document(user_uid).get()
            if user_doc.exists:
                profissional_id_logado = user_doc.to_dict().get('profissional_id')
            
            if not profissional_id_logado:
                flash("Sua conta de usuário não está corretamente associada a um perfil de profissional. Contate o administrador.", "warning")
        except Exception as e:
            flash(f"Erro ao buscar informações do profissional: {e}", "danger")
            return render_template('dashboard.html', kpi={}, proximos_agendamentos=[])

    agendamentos_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos')
    pacientes_ref = db.collection('clinicas').document(clinica_id).collection('pacientes')
    current_year = datetime.datetime.now(SAO_PAULO_TZ).year
    
    hoje_dt = datetime.datetime.now(SAO_PAULO_TZ)
    mes_atual_nome = hoje_dt.strftime('%B').capitalize()
    
    inicio_mes_atual_dt = hoje_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    fim_mes_anterior_dt = inicio_mes_atual_dt - datetime.timedelta(seconds=1)
    inicio_mes_anterior_dt = fim_mes_anterior_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    agendamentos_para_analise = []
    try:
        query_analise = agendamentos_ref.where(
            filter=FieldFilter('status', 'in', ['confirmado', 'concluido'])
        ).where(
            filter=FieldFilter('data_agendamento_ts', '>=', inicio_mes_anterior_dt)
        )

        if user_role != 'admin':
            if profissional_id_logado:
                query_analise = query_analise.where(
                    filter=FieldFilter('profissional_id', '==', profissional_id_logado)
                )
            else:
                query_analise = query_analise.where(
                    filter=FieldFilter('profissional_id', '==', 'ID_INVALIDO_PARA_NAO_RETORNAR_NADA')
                )

        docs_analise = query_analise.stream()
        for doc in docs_analise:
            ag_data = doc.to_dict()
            if ag_data:
                agendamentos_para_analise.append(ag_data)

    except Exception as e:
        print(f"Erro na consulta de agendamentos para o painel: {e}")
        flash("Erro ao calcular estatísticas do painel. Verifique seus índices do Firestore.", "danger")

    receita_mes_atual = 0.0
    atendimentos_mes_atual = 0
    receita_mes_anterior = 0.0
    atendimentos_mes_anterior = 0
    
    try:
        novos_pacientes_mes = pacientes_ref.where(
            filter=FieldFilter('data_cadastro', '>=', inicio_mes_atual_dt)
        ).count().get()[0][0].value
    except Exception:
        novos_pacientes_mes = 0

    for ag in agendamentos_para_analise:
        ag_timestamp = ag.get('data_agendamento_ts')
        preco = float(ag.get('servico_procedimento_preco', 0))
        
        if ag_timestamp and inicio_mes_atual_dt <= ag_timestamp:
            receita_mes_atual += preco
            atendimentos_mes_atual += 1
        elif ag_timestamp and inicio_mes_anterior_dt <= ag_timestamp < inicio_mes_atual_dt:
            receita_mes_anterior += preco
            atendimentos_mes_anterior += 1

    def calcular_variacao(atual, anterior):
        if anterior == 0:
            return 100.0 if atual > 0 else 0.0
        return ((atual - anterior) / anterior) * 100

    kpi_cards = {
        'receita_mes_atual': receita_mes_atual,
        'atendimentos_mes_atual': atendimentos_mes_atual,
        'variacao_receita': calcular_variacao(receita_mes_atual, receita_mes_anterior),
        'variacao_atendimentos': calcular_variacao(atendimentos_mes_atual, atendimentos_mes_anterior),
        'novos_pacientes_mes': novos_pacientes_mes,
    }

    atendimentos_por_dia = Counter()
    receita_por_dia = Counter()
    hoje_date = hoje_dt.date()
    for i in range(15):
        data = hoje_date - datetime.timedelta(days=i)
        atendimentos_por_dia[data.strftime('%d/%m')] = 0
        receita_por_dia[data.strftime('%d/%m')] = 0

    for ag in agendamentos_para_analise:
        ag_ts = ag.get('data_agendamento_ts')
        if ag_ts:
            ag_date = ag_ts.date()
            if (hoje_date - ag_date).days < 15:
                dia_str = ag_date.strftime('%d/%m')
                atendimentos_por_dia[dia_str] += 1
                receita_por_dia[dia_str] += float(ag.get('servico_procedimento_preco', 0))

    labels_atend_receita = sorted(atendimentos_por_dia.keys(), key=lambda x: datetime.datetime.strptime(x, '%d/%m'))
    dados_atendimento_vs_receita = {
        "labels": labels_atend_receita,
        "atendimentos": [atendimentos_por_dia[label] for label in labels_atend_receita],
        "receitas": [receita_por_dia[label] for label in labels_atend_receita]
    }

    receita_por_procedimento = Counter()
    for ag in agendamentos_para_analise:
        ag_ts = ag.get('data_agendamento_ts')
        if ag_ts and ag_ts >= inicio_mes_atual_dt:
            nome_proc = ag.get('servico_procedimento_nome', 'Desconhecido')
            receita_por_procedimento[nome_proc] += float(ag.get('servico_procedimento_preco', 0))
    
    top_5_procedimentos = receita_por_procedimento.most_common(5)
    dados_receita_procedimento = {
        "labels": [item[0] for item in top_5_procedimentos],
        "valores": [item[1] for item in top_5_procedimentos]
    }

    atendimentos_por_profissional = Counter()
    for ag in agendamentos_para_analise:
        ag_ts = ag.get('data_agendamento_ts')
        if ag_ts and ag_ts >= inicio_mes_atual_dt:
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
        dados_desempenho_profissional=json.dumps(dados_desempenho_profissional)
    )

@app.route('/usuarios')
@login_required
@admin_required
def listar_usuarios():
    clinica_id = session['clinica_id']
    usuarios_ref = db.collection('User')
    usuarios_lista = []
    try:
        docs = usuarios_ref.where(filter=FieldFilter('clinica_id', '==', clinica_id)).order_by('email').stream()
        for doc in docs:
            user_data = doc.to_dict()
            if user_data:
                user_data['uid'] = doc.id
                
                try:
                    firebase_user = firebase_auth_admin.get_user(doc.id)
                    user_data['disabled'] = firebase_user.disabled
                except firebase_auth_admin.UserNotFoundError:
                    user_data['disabled'] = True

                usuarios_lista.append(user_data)
    except Exception as e:
        flash(f'Erro ao listar usuários: {e}.', 'danger')
        print(f"Erro list_users: {e}")
        
    return render_template('usuarios.html', usuarios=usuarios_lista)

@app.route('/usuarios/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_usuario():
    clinica_id = session.get('clinica_id')
    if not clinica_id:
        flash('ID da clínica não encontrado na sessão.', 'danger')
        return redirect(url_for('index'))

    profissionais_disponiveis = []
    try:
        profissionais_docs = db.collection(f'clinicas/{clinica_id}/profissionais').stream()
        for doc in profissionais_docs:
            prof_data = doc.to_dict()
            profissionais_disponiveis.append({'id': doc.id, 'nome': prof_data.get('nome')})
        profissionais_disponiveis.sort(key=lambda x: x.get('nome', '').lower())
    except Exception as e:
        flash(f'Erro ao carregar a lista de profissionais: {e}', 'danger')

    if request.method == 'POST':
        email = request.form['email'].strip()
        password = request.form['password']
        role = request.form['role']
        nome_completo = request.form.get('nome_completo', '').strip()
        profissional_associado_id = request.form.get('profissional_associado_id')

        if not all([email, password, role]):
            flash('E-mail, senha e função são obrigatórios.', 'danger')
            return render_template('usuario_form.html', page_title="Adicionar Novo Utilizador", roles=['admin', 'medico'], profissionais=profissionais_disponiveis, user=request.form)

        try:
            user = firebase_auth_admin.create_user(
                email=email,
                password=password,
                display_name=nome_completo
            )
            
            batch = db.batch()

            user_data_firestore = {
                'email': email,
                'clinica_id': clinica_id,
                'nome_clinica_display': session.get('clinica_nome_display', 'Clínica On'),
                'role': role,
                'nome_completo': nome_completo,
                'associado_em': firestore.SERVER_TIMESTAMP
            }

            if role == 'medico' and profissional_associado_id:
                user_data_firestore['profissional_id'] = profissional_associado_id
                
                prof_ref = db.collection(f'clinicas/{clinica_id}/profissionais').document(profissional_associado_id)
                batch.update(prof_ref, {'user_uid': user.uid})

            user_ref = db.collection('User').document(user.uid)
            batch.set(user_ref, user_data_firestore)
            
            batch.commit()
            
            flash(f'Utilizador {email} ({role}) criado com sucesso!', 'success')
            return redirect(url_for('listar_usuarios'))
            
        except firebase_auth_admin.EmailAlreadyExistsError:
            flash('O e-mail fornecido já está em uso.', 'danger')
        except Exception as e:
            flash(f'Erro ao adicionar utilizador: {e}', 'danger')

    return render_template('usuario_form.html', page_title="Adicionar Novo Utilizador", action_url=url_for('adicionar_usuario'), roles=['admin', 'medico'], profissionais=profissionais_disponiveis)


@app.route('/usuarios/editar/<string:user_uid>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_usuario(user_uid):
    clinica_id = session.get('clinica_id')
    if not clinica_id:
        flash('ID da clínica não encontrado na sessão.', 'danger')
        return redirect(url_for('index'))
        
    user_ref = db.collection('User').document(user_uid)
    
    profissionais_disponiveis = []
    try:
        profissionais_docs = db.collection(f'clinicas/{clinica_id}/profissionais').stream()
        for doc in profissionais_docs:
            prof_data = doc.to_dict()
            profissionais_disponiveis.append({'id': doc.id, 'nome': prof_data.get('nome')})
        profissionais_disponiveis.sort(key=lambda x: x.get('nome', '').lower())
    except Exception as e:
        flash(f'Erro ao carregar a lista de profissionais: {e}', 'danger')

    try:
        user_doc = user_ref.get()
        if not user_doc.exists:
            flash('Utilizador não encontrado.', 'danger')
            return redirect(url_for('listar_usuarios'))
        user_data_original = user_doc.to_dict()
        old_profissional_id = user_data_original.get('profissional_id')
    except Exception as e:
        flash(f'Erro ao carregar dados do utilizador: {e}', 'danger')
        return redirect(url_for('listar_usuarios'))

    if request.method == 'POST':
        email = request.form['email'].strip()
        role = request.form['role']
        nome_completo = request.form.get('nome_completo', '').strip()
        new_profissional_id = request.form.get('profissional_associado_id')

        try:
            batch = db.batch()

            firebase_auth_admin.update_user(user_uid, email=email, display_name=nome_completo)
            
            user_data_update = {
                'email': email, 'role': role, 'nome_completo': nome_completo,
                'atualizado_em': firestore.SERVER_TIMESTAMP
            }
            
            if old_profissional_id != new_profissional_id:
                if old_profissional_id:
                    old_prof_ref = db.collection(f'clinicas/{clinica_id}/profissionais').document(old_profissional_id)
                    batch.update(old_prof_ref, {'user_uid': firestore.DELETE_FIELD})
                
                if role == 'medico' and new_profissional_id:
                    new_prof_ref = db.collection(f'clinicas/{clinica_id}/profissionais').document(new_profissional_id)
                    batch.update(new_prof_ref, {'user_uid': user_uid})
                    user_data_update['profissional_id'] = new_profissional_id
                else:
                    user_data_update['profissional_id'] = firestore.DELETE_FIELD
            
            batch.update(user_ref, user_data_update)
            batch.commit()
            
            flash(f'Utilizador {email} atualizado com sucesso!', 'success')
            return redirect(url_for('listar_usuarios'))
            
        except Exception as e:
            flash(f'Erro ao atualizar utilizador: {e}', 'danger')
            
    user_data_original['uid'] = user_uid
    return render_template(
        'usuario_form.html',    
        user=user_data_original,    
        page_title="Editar Utilizador",    
        action_url=url_for('editar_usuario', user_uid=user_uid),    
        roles=['admin', 'medico'],    
        profissionais=profissionais_disponiveis
    )

@app.route('/usuarios/ativar_desativar/<string:user_uid>', methods=['POST'])
@login_required
@admin_required
def ativar_desativar_usuario(user_uid):
    clinica_id = session['clinica_id']
    try:
        user_map_doc = db.collection('User').document(user_uid).get()
        if user_map_doc.exists:
            user_data = user_map_doc.to_dict()
            current_status_firebase = firebase_auth_admin.get_user(user_uid).disabled
            new_status_firebase = not current_status_firebase

            firebase_auth_admin.update_user(user_uid, disabled=new_status_firebase)
            
            if user_data.get('role') == 'medico' and user_data.get('profissional_id'):
                profissionais_ref = db.collection('clinicas').document(clinica_id).collection('profissionais')
                prof_doc_ref = profissionais_ref.document(user_data['profissional_id'])
                if prof_doc_ref.get().exists:
                    prof_doc_ref.update({
                        'ativo': not new_status_firebase
                    })

            flash(f'Usuário {user_data.get("email")} {"ativado" if not new_status_firebase else "desativado"} com sucesso!', 'success')
        else:
            flash('Usuário não encontrado no mapeamento.', 'danger')
    except firebase_admin.auth.UserNotFoundError:
        flash('Usuário não encontrado na Autenticação do Firebase.', 'danger')
    except Exception as e:
        flash(f'Erro ao alterar o status do usuário: {e}', 'danger')
        print(f"Erro in activate_deactivate_user: {e}")
    return redirect(url_for('listar_usuarios'))

@app.route('/profissionais')
@login_required
def listar_profissionais():
    clinica_id = session['clinica_id']
    profissionais_ref = db.collection('clinicas').document(clinica_id).collection('profissionais')
    profissionais_lista = []
    try:
        docs = profissionais_ref.order_by('nome').stream()
        for doc in docs:
            profissional = doc.to_dict()
            if profissional:
                profissional['id'] = doc.id
                profissionais_lista.append(profissional)
    except Exception as e:
        flash(f'Erro ao listar profissionais: {e}.', 'danger')
        print(f"Erro list_professionals: {e}")
    return render_template('profissionais.html', profissionais=profissionais_lista)

@app.route('/profissionais/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_profissional():
    clinica_id = session['clinica_id']
    if request.method == 'POST':
        nome = request.form['nome']
        telefone = request.form.get('telefone')
        email_profissional = request.form.get('email_profissional')
        crm_ou_registro = request.form.get('crm_ou_registro')
        ativo = 'ativo' in request.form
        try:
            if telefone and not telefone.isdigit():
                flash('O telefone deve conter apenas números.', 'warning')
                return render_template('profissional_form.html', profissional=request.form, action_url=url_for('adicionar_profissional'))

            db.collection('clinicas').document(clinica_id).collection('profissionais').add({
                'nome': nome,
                'telefone': telefone if telefone else None,
                'email': email_profissional if email_profissional else None,
                'crm_ou_registro': crm_ou_registro if crm_ou_registro else None,
                'ativo': ativo,
                'criado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Profissional adicionado com sucesso!', 'success')
            return redirect(url_for('listar_profissionais'))
        except Exception as e:
            flash(f'Erro ao adicionar profissional: {e}', 'danger')
            print(f"Erro add_professional: {e}")
    return render_template('profissional_form.html', profissional=None, action_url=url_for('adicionar_profissional'))

@app.route('/profissionais/editar/<string:profissional_doc_id>', methods=['GET', 'POST'])
@login_required
def editar_profissional(profissional_doc_id):
    clinica_id = session['clinica_id']
    profissional_ref = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id)
    
    if request.method == 'POST':
        nome = request.form['nome']
        telefone = request.form.get('telefone')
        email_profissional = request.form.get('email_profissional')
        crm_ou_registro = request.form.get('crm_ou_registro')
        ativo = 'ativo' in request.form
        try:
            if telefone and not telefone.isdigit():
                flash('O telefone deve conter apenas números.', 'warning')
            else:
                profissional_ref.update({
                    'nome': nome,
                    'telefone': telefone if telefone else None,
                    'email': email_profissional if email_profissional else None,
                    'crm_ou_registro': crm_ou_registro if crm_ou_registro else None,
                    'ativo': ativo,
                    'atualizado_em': firestore.SERVER_TIMESTAMP
                })
                flash('Profissional atualizado com sucesso!', 'success')
                return redirect(url_for('listar_profissionais'))
        except Exception as e:
            flash(f'Erro ao atualizar profissional: {e}', 'danger')
            print(f"Erro edit_professional (POST): {e}")

    try:
        profissional_doc = profissional_ref.get()
        if profissional_doc.exists:
            profissional = profissional_doc.to_dict()
            if profissional:
                profissional['id'] = profissional_doc.id
                return render_template('profissional_form.html', profissional=profissional, action_url=url_for('editar_profissional', profissional_doc_id=profissional_doc_id))
        else:
            flash('Profissional não encontrado.', 'danger')
            return redirect(url_for('listar_profissionais'))
    except Exception as e:
        flash(f'Erro ao carregar profissional para edição: {e}', 'danger')
        print(f"Erro edit_professional (GET): {e}")
        return redirect(url_for('listar_profissionais'))

@app.route('/profissionais/ativar_desativar/<string:profissional_doc_id>', methods=['POST'])
@login_required
@admin_required
def ativar_desativar_profissional(profissional_doc_id):
    clinica_id = session['clinica_id']
    profissional_ref = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id)
    try:
        profissional_doc = profissional_ref.get()
        if profissional_doc.exists:
            data = profissional_doc.to_dict()
            if data:
                current_status = data.get('ativo', False)   
                new_status = not current_status
                profissional_ref.update({'ativo': new_status, 'atualizado_em': firestore.SERVER_TIMESTAMP})
                flash(f'Profissional {"ativado" if new_status else "desativado"} com sucesso!', 'success')
        else:
            flash('Profissional não encontrado.', 'danger')
    except Exception as e:
        flash(f'Erro ao alterar o status do profissional: {e}', 'danger')
        print(f"Erro in activate_deactivate_professional: {e}")
    return redirect(url_for('listar_profissionais'))

@app.route('/pacientes')
@login_required
def listar_pacientes():
    clinica_id = session['clinica_id']
    pacientes_ref = db.collection('clinicas').document(clinica_id).collection('pacientes')
    convenios_ref = db.collection('clinicas').document(clinica_id).collection('convenios')
    pacientes_lista = []
    
    convenios_dict = {}
    try:
        convenios_docs = convenios_ref.stream()
        for doc in convenios_docs:
            convenios_dict[doc.id] = doc.to_dict().get('nome', 'Convênio Desconhecido')
    except Exception as e:
        print(f"Erro ao carregar convênios para pacientes: {e}")
        flash('Erro ao carregar informações de convênios.', 'danger')

    try:
        search_query = request.args.get('search', '').strip()
        
        query = pacientes_ref.order_by('nome')

        docs = query.stream()
        for doc in docs:
            paciente = doc.to_dict()
            if paciente:
                paciente['id'] = doc.id
                if paciente.get('convenio_id') and paciente['convenio_id'] in convenios_dict:
                    paciente['convenio_nome'] = convenios_dict[paciente['convenio_id']]
                else:
                    paciente['convenio_nome'] = 'Particular'
                
                # Simple client-side search simulation
                if search_query:
                    if search_query.lower() in paciente.get('nome', '').lower() or \
                       search_query in paciente.get('contato_telefone', ''):
                        pacientes_lista.append(paciente)
                else:
                    pacientes_lista.append(paciente)

    except Exception as e:
        flash(f'Erro ao listar pacientes: {e}.', 'danger')
        print(f"Erro list_patients: {e}")
    
    return render_template('pacientes.html', pacientes=pacientes_lista, search_query=search_query)

@app.route('/pacientes/novo', methods=['GET', 'POST'])
@login_required
def adicionar_paciente():
    clinica_id = session['clinica_id']
    
    convenios_lista = []
    try:
        convenios_docs = db.collection('clinicas').document(clinica_id).collection('convenios').order_by('nome').stream()
        for doc in convenios_docs:
            conv_data = doc.to_dict()
            if conv_data:
                convenios_lista.append({'id': doc.id, 'nome': conv_data.get('nome', doc.id)})
    except Exception as e:
        flash('Erro ao carregar convênios.', 'danger')
        print(f"Erro ao carregar convênios (add_patient GET): {e}")

    if request.method == 'POST':
        nome = request.form['nome'].strip()
        data_nascimento = request.form.get('data_nascimento', '').strip()
        cpf = request.form.get('cpf', '').strip()
        rg = request.form.get('rg', '').strip()
        genero = request.form.get('genero', '').strip()
        estado_civil = request.form.get('estado_civil', '').strip()
        telefone = request.form.get('telefone', '').strip()
        email = request.form.get('email', '').strip()
        indicacao = request.form.get('indicacao', '').strip()
        convenio_id = request.form.get('convenio_id', '').strip()
        observacoes = request.form.get('observacoes', '').strip()
        cep = request.form.get('cep', '').strip()
        logradouro = request.form.get('logradouro', '').strip()
        numero = request.form.get('numero', '').strip()
        complemento = request.form.get('complemento', '').strip()
        bairro = request.form.get('bairro', '').strip()
        cidade = request.form.get('cidade', '').strip()
        estado = request.form.get('estado', '').strip()

        if not nome:
            flash('O nome do paciente é obrigatório.', 'danger')
            return render_template('paciente_form.html', paciente=request.form, action_url=url_for('adicionar_paciente'), convenios=convenios_lista)

        try:
            data_nascimento_dt = parse_date_input(data_nascimento)
            
            if data_nascimento and data_nascimento_dt is None:
                flash('Formato de data de nascimento inválido. Use AAAA-MM-DD ou DD/MM/YYYY.', 'danger')
                return render_template('paciente_form.html', paciente=request.form, action_url=url_for('adicionar_paciente'), convenios=convenios_lista)

            paciente_data = {
                'nome': nome,
                'data_nascimento': data_nascimento_dt,
                'cpf': cpf if cpf else None,
                'rg': rg if rg else None,
                'genero': genero if genero else None,
                'estado_civil': estado_civil if estado_civil else None,
                'contato_telefone': telefone if telefone else None,
                'contato_email': email if email else None,
                'indicacao': indicacao if indicacao else None,
                'convenio_id': convenio_id if convenio_id else None,
                'observacoes': observacoes if observacoes else None,
                'endereco': {
                    'cep': cep if cep else None,
                    'logradouro': logradouro if logradouro else None,
                    'numero': numero if numero else None,
                    'complemento': complemento if complemento else None,
                    'bairro': bairro if bairro else None,
                    'cidade': cidade if cidade else None,
                    'estado': estado if estado else None,
                },
                'data_cadastro': firestore.SERVER_TIMESTAMP
            }
            
            db.collection('clinicas').document(clinica_id).collection('pacientes').add(paciente_data)
            flash('Paciente adicionado com sucesso!', 'success')
            return redirect(url_for('listar_pacientes'))
        except Exception as e:
            flash(f'Erro ao adicionar paciente: {e}', 'danger')
            print(f"Erro add_patient: {e}")
    
    return render_template('paciente_form.html', paciente=None, action_url=url_for('adicionar_paciente'), convenios=convenios_lista)

@app.route('/pacientes/editar/<string:paciente_doc_id>', methods=['GET', 'POST'])
@login_required
def editar_paciente(paciente_doc_id):
    clinica_id = session['clinica_id']
    paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
    
    convenios_lista = []
    peis_disponiveis = []
    try:
        convenios_docs = db.collection('clinicas').document(clinica_id).collection('convenios').order_by('nome').stream()
        for doc in convenios_docs:
            conv_data = doc.to_dict()
            if conv_data:
                convenios_lista.append({'id': doc.id, 'nome': conv_data.get('nome', doc.id)})

        peis_docs = db.collection('clinicas').document(clinica_id).collection('peis').order_by('identificacao_pei').stream()
        for doc in peis_docs:
            pei_data = doc.to_dict()
            if pei_data:
                peis_disponiveis.append({'id': doc.id, 'identificacao': pei_data.get('identificacao_pei', doc.id)})

    except Exception as e:
        flash('Erro ao carregar convênios ou PEIs.', 'danger')
        print(f"Erro ao carregar convênios/PEIs (edit_patient GET): {e}")

    if request.method == 'POST':
        nome = request.form['nome'].strip()
        data_nascimento = request.form.get('data_nascimento', '').strip()
        cpf = request.form.get('cpf', '').strip()
        rg = request.form.get('rg', '').strip()
        genero = request.form.get('genero', '').strip()
        estado_civil = request.form.get('estado_civil', '').strip()
        telefone = request.form.get('telefone', '').strip()
        email = request.form.get('email', '').strip()
        indicacao = request.form.get('indicacao', '').strip()
        convenio_id = request.form.get('convenio_id', '').strip()
        observacoes = request.form.get('observacoes', '').strip()
        
        peis_associados_ids = request.form.getlist('peis_associados')

        cep = request.form.get('cep', '').strip()
        logradouro = request.form.get('logradouro', '').strip()
        numero = request.form.get('numero', '').strip()
        complemento = request.form.get('complemento', '').strip()
        bairro = request.form.get('bairro', '').strip()
        cidade = request.form.get('cidade', '').strip()
        estado = request.form.get('estado', '').strip()

        if not nome:
            flash('O nome do paciente é obrigatório.', 'danger')
            return render_template('paciente_form.html', paciente=request.form, action_url=url_for('editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista, peis_disponiveis=peis_disponiveis)

        try:
            data_nascimento_dt = parse_date_input(data_nascimento)
            
            if data_nascimento and data_nascimento_dt is None:
                flash('Formato de data de nascimento inválido. Use AAAA-MM-DD ou DD/MM/YYYY.', 'danger')
                return render_template('paciente_form.html', paciente=request.form, action_url=url_for('editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista, peis_disponiveis=peis_disponiveis)

            paciente_data_update = {
                'nome': nome,
                'data_nascimento': data_nascimento_dt,
                'cpf': cpf if cpf else None,
                'rg': rg if rg else None,
                'genero': genero if genero else None,
                'estado_civil': estado_civil if estado_civil else None,
                'contato_telefone': telefone if telefone else None,
                'contato_email': email if email else None,
                'indicacao': indicacao if indicacao else None,
                'convenio_id': convenio_id if convenio_id else None,
                'observacoes': observacoes if observacoes else None,
                'endereco': {
                    'cep': cep if cep else None,
                    'logradouro': logradouro if logradouro else None,
                    'numero': numero if numero else None,
                    'complemento': complemento if complemento else None,
                    'bairro': bairro if bairro else None,
                    'cidade': cidade if cidade else None,
                    'estado': estado if estado else None,
                },
                'peis_associados': peis_associados_ids if peis_associados_ids else [],
                'atualizado_em': firestore.SERVER_TIMESTAMP
            }
            
            paciente_ref.update(paciente_data_update)
            flash('Paciente atualizado com sucesso!', 'success')
            return redirect(url_for('listar_pacientes'))
        except Exception as e:
            flash(f'Erro ao atualizar paciente: {e}', 'danger')
            print(f"Erro edit_patient (POST): {e}")

    try:
        paciente_doc = paciente_ref.get()
        if paciente_doc.exists:
            paciente = paciente_doc.to_dict()
            if paciente:
                paciente['id'] = paciente_doc.id
                if paciente.get('data_nascimento') and isinstance(paciente.get('data_nascimento'), datetime.datetime):
                    paciente['data_nascimento'] = paciente['data_nascimento'].strftime('%Y-%m-%d')
                else:
                    paciente['data_nascimento'] = ''

                paciente['peis_associados_ids'] = paciente.get('peis_associados', [])

                return render_template('paciente_form.html', paciente=paciente, action_url=url_for('editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista, peis_disponiveis=peis_disponiveis)
        else:
            flash('Paciente não encontrado.', 'danger')
            return redirect(url_for('listar_pacientes'))
    except Exception as e:
        flash(f'Erro ao carregar paciente para edição: {e}', 'danger')
        print(f"Erro edit_patient (GET): {e}")
        return redirect(url_for('listar_pacientes'))
    
@app.route('/servicos_procedimentos')
@login_required
def listar_servicos_procedimentos():
    clinica_id = session['clinica_id']
    servicos_procedimentos_ref = db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos')
    servicos_procedimentos_lista = []
    try:
        docs = servicos_procedimentos_ref.order_by('nome').stream()
        for doc in docs:
            servico = doc.to_dict()
            if servico:
                servico['id'] = doc.id
                servico['preco_fmt'] = "R$ {:.2f}".format(float(servico.get('preco_sugerido', 0))).replace('.', ',')
                servicos_procedimentos_lista.append(servico)
    except Exception as e:
        flash(f'Erro ao listar serviços/procedimentos: {e}.', 'danger')
        print(f"Erro list_services_procedures: {e}")
    return render_template('servicos_procedimentos.html', servicos=servicos_procedimentos_lista)

@app.route('/servicos_procedimentos/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_servico_procedimento():
    clinica_id = session['clinica_id']
    if request.method == 'POST':
        nome = request.form['nome']
        tipo = request.form['tipo']
        try:
            duracao_minutos = int(request.form['duracao_minutos'])
            preco_sugerido = float(request.form['preco'].replace(',', '.'))
            db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').add({
                'nome': nome,
                'tipo': tipo,
                'duracao_minutos': duracao_minutos,
                'preco_sugerido': preco_sugerido,
                'criado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Serviço/Procedimento adicionado com sucesso!', 'success')
            return redirect(url_for('listar_servicos_procedimentos'))
        except ValueError:
            flash('A duração e o preço devem ser números válidos.', 'danger')
        except Exception as e:
            flash(f'Erro ao adicionar serviço/procedimento: {e}', 'danger')
            print(f"Erro add_service_procedure: {e}")
    return render_template('servico_procedimento_form.html', servico=None, action_url=url_for('adicionar_servico_procedimento'))

@app.route('/servicos_procedimentos/editar/<string:servico_doc_id>', methods=['GET', 'POST'])
@login_required
def editar_servico_procedimento(servico_doc_id):
    clinica_id = session['clinica_id']
    servico_ref = db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_doc_id)
    if request.method == 'POST':
        nome = request.form['nome']
        tipo = request.form['tipo']
        try:
            duracao_minutos = int(request.form['duracao_minutos'])
            preco_sugerido = float(request.form['preco'].replace(',', '.'))
            servico_ref.update({
                'nome': nome,
                'tipo': tipo,
                'duracao_minutos': duracao_minutos,
                'preco_sugerido': preco_sugerido,
                'atualizado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Serviço/Procedimento atualizado com sucesso!', 'success')
            return redirect(url_for('listar_servicos_procedimentos'))
        except ValueError:
            flash('A duração e o preço devem ser números válidos.', 'danger')
        except Exception as e:
            flash(f'Erro ao atualizar serviço/procedimento: {e}', 'danger')
            print(f"Erro edit_service_procedure (POST): {e}")
    try:
        servico_doc = servico_ref.get()
        if servico_doc.exists:
            servico = servico_doc.to_dict()
            if servico:
                servico['id'] = servico_doc.id
                servico['preco_form'] = str(servico.get('preco_sugerido', '0.00')).replace('.', ',')
                return render_template('servico_procedimento_form.html', servico=servico, action_url=url_for('editar_servico_procedimento', servico_doc_id=servico_doc_id))
        flash('Serviço/Procedimento não encontrado.', 'danger')
        return redirect(url_for('listar_servicos_procedimentos'))
    except Exception as e:
        flash(f'Erro ao carregar serviço/procedimento para edição: {e}', 'danger')
        print(f"Erro edit_service_procedure (GET): {e}")
        return redirect(url_for('listar_servicos_procedimentos'))

@app.route('/servicos_procedimentos/excluir/<string:servico_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_servico_procedimento(servico_doc_id):
    clinica_id = session['clinica_id']
    try:
        agendamentos_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos')
        agendamentos_com_servico = agendamentos_ref.where(filter=FieldFilter('servico_procedimento_id', '==', servico_doc_id)).limit(1).get()
        if len(agendamentos_com_servico) > 0:
            flash('Este serviço/procedimento não pode ser excluído, pois está associado a um ou mais agendamentos.', 'danger')
            return redirect(url_for('listar_servicos_procedimentos'))

        db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_doc_id).delete()
        flash('Serviço/Procedimento excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir serviço/procedimento: {e}.', 'danger')
        print(f"Erro delete_service_procedure: {e}")
    return redirect(url_for('listar_servicos_procedimentos'))

@app.route('/convenios')
@login_required
def listar_convenios():
    clinica_id = session['clinica_id']
    convenios_ref = db.collection('clinicas').document(clinica_id).collection('convenios')
    convenios_lista = []
    try:
        docs = convenios_ref.order_by('nome').stream()
        for doc in docs:
            convenio = doc.to_dict()
            if convenio:
                convenio['id'] = doc.id
                convenios_lista.append(convenio)
    except Exception as e:
        flash(f'Erro ao listar convênios: {e}.', 'danger')
        print(f"Erro list_covenants: {e}")
    return render_template('convenios.html', convenios=convenios_lista)

@app.route('/convenios/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_convenio():
    clinica_id = session['clinica_id']
    if request.method == 'POST':
        nome = request.form['nome'].strip()
        registro_ans = request.form.get('registro_ans', '').strip()
        tipo_plano = request.form.get('tipo_plano', '').strip()

        if not nome:
            flash('O nome do convênio é obrigatório.', 'danger')
            return render_template('convenio_form.html', convenio=request.form, action_url=url_for('adicionar_convenio'))
        try:
            db.collection('clinicas').document(clinica_id).collection('convenios').add({
                'nome': nome,
                'registro_ans': registro_ans if registro_ans else None,
                'tipo_plano': tipo_plano if tipo_plano else None,
                'criado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Convênio adicionado com sucesso!', 'success')
            return redirect(url_for('listar_convenios'))
        except Exception as e:
            flash(f'Erro ao adicionar convênio: {e}', 'danger')
            print(f"Erro add_covenant: {e}")
    return render_template('convenio_form.html', convenio=None, action_url=url_for('adicionar_convenio'))

@app.route('/convenios/editar/<string:convenio_doc_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_convenio(convenio_doc_id):
    clinica_id = session['clinica_id']
    convenio_ref = db.collection('clinicas').document(clinica_id).collection('convenios').document(convenio_doc_id)
    
    if request.method == 'POST':
        nome = request.form['nome'].strip()
        registro_ans = request.form.get('registro_ans', '').strip()
        tipo_plano = request.form.get('tipo_plano', '').strip()

        if not nome:
            flash('O nome do convênio é obrigatório.', 'danger')
            return render_template('convenio_form.html', convenio=request.form, action_url=url_for('editar_convenio', convenio_doc_id=convenio_doc_id))
        try:
            convenio_ref.update({
                'nome': nome,
                'registro_ans': registro_ans if registro_ans else None,
                'tipo_plano': tipo_plano if tipo_plano else None,
                'atualizado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Convênio atualizado com sucesso!', 'success')
            return redirect(url_for('listar_convenios'))
        except Exception as e:
            flash(f'Erro ao atualizar convênio: {e}', 'danger')
            print(f"Erro edit_covenant (POST): {e}")

    try:
        convenio_doc = convenio_ref.get()
        if convenio_doc.exists:
            convenio = convenio_doc.to_dict()
            if convenio:
                convenio['id'] = convenio_doc.id
                return render_template('convenio_form.html', convenio=convenio, action_url=url_for('editar_convenio', convenio_doc_id=convenio_doc_id))
        else:
            flash('Convênio não encontrado.', 'danger')
            return redirect(url_for('listar_convenios'))
    except Exception as e:
        flash(f'Erro ao carregar convênio para edição: {e}', 'danger')
        print(f"Erro edit_covenant (GET): {e}")
        return redirect(url_for('listar_convenios'))

@app.route('/convenios/excluir/<string:convenio_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_convenio(convenio_doc_id):
    clinica_id = session['clinica_id']
    try:
        pacientes_ref = db.collection('clinicas').document(clinica_id).collection('pacientes')
        pacientes_com_convenio = pacientes_ref.where(filter=FieldFilter('convenio_id', '==', convenio_doc_id)).limit(1).get()
        if len(pacientes_com_convenio) > 0:
            flash('Este convênio não pode ser excluído, pois está associado a um ou mais pacientes.', 'danger')
            return redirect(url_for('listar_convenios'))
            
        db.collection('clinicas').document(clinica_id).collection('convenios').document(convenio_doc_id).delete()
        flash('Convênio excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir convênio: {e}.', 'danger')
        print(f"Erro delete_covenant: {e}")
    return redirect(url_for('listar_convenios'))

@app.route('/horarios')
@login_required
def listar_horarios():
    clinica_id = session['clinica_id']
    todos_horarios_formatados = []
    try:
        profissionais_main_ref = db.collection('clinicas').document(clinica_id).collection('profissionais')
        profissionais_docs_stream = profissionais_main_ref.where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()

        for p_doc in profissionais_docs_stream:
            profissional_info = p_doc.to_dict()
            profissional_id_atual = p_doc.id
            profissional_nome_atual = profissional_info.get('nome', f"ID: {profissional_id_atual}")

            horarios_disponiveis_ref = profissionais_main_ref.document(profissional_id_atual).collection('horarios_disponiveis')
            horarios_docs_para_profissional_stream = horarios_disponiveis_ref.order_by('dia_semana').order_by('hora_inicio').stream()

            for horario_doc in horarios_docs_para_profissional_stream:
                horario = horario_doc.to_dict()
                if horario:
                    horario['id'] = horario_doc.id
                    horario['profissional_id_fk'] = profissional_id_atual
                    horario['profissional_nome'] = profissional_nome_atual
                    
                    dias_semana_map = {0: 'Domingo', 1: 'Segunda-feira', 2: 'Terça-feira', 3: 'Quarta-feira', 4: 'Quinta-feira', 5: 'Sexta-feira', 6: 'Sábado'}
                    horario['dia_semana_nome'] = dias_semana_map.get(horario.get('dia_semana'), 'N/A')
                    
                    todos_horarios_formatados.append(horario)
    
    except Exception as e:
        flash(f'Erro ao listar horários: {e}.', 'danger')
        print(f"Erro list_schedules: {e}")
    
    return render_template('horarios.html', horarios=todos_horarios_formatados, current_year=datetime.datetime.now(SAO_PAULO_TZ).year)


@app.route('/horarios/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_horario():
    clinica_id = session['clinica_id']
    profissionais_ativos_lista = []
    try:
        profissionais_docs = db.collection('clinicas').document(clinica_id).collection('profissionais').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()
        for doc in profissionais_docs:
            p_data = doc.to_dict()
            if p_data: profissionais_ativos_lista.append({'id': doc.id, 'nome': p_data.get('nome', doc.id)})
    except Exception as e:
        flash('Erro ao carregar profissionais ativos.', 'danger')
        print(f"Erro ao carregar profissionais (add_schedule GET): {e}")

    dias_semana_map = {0: 'Domingo', 1: 'Segunda-feira', 2: 'Terça-feira', 3: 'Quarta-feira', 4: 'Quinta-feira', 5: 'Sexta-feira', 6: 'Sábado'}

    if request.method == 'POST':
        try:
            profissional_id_selecionado = request.form['profissional_id']
            dia_semana = int(request.form['dia_semana'])
            hora_inicio = request.form['hora_inicio']
            hora_fim = request.form['hora_fim']
            intervalo_minutos_str = request.form.get('intervalo_minutos')
            intervalo_minutos = int(intervalo_minutos_str) if intervalo_minutos_str and intervalo_minutos_str.isdigit() else None
            ativo = 'ativo' in request.form    

            if not profissional_id_selecionado:
                flash('Por favor, selecione um profissional.', 'warning')
            elif hora_inicio >= hora_fim:
                flash('A hora de início deve ser anterior à hora de término.', 'warning')
            else:
                horario_data = {
                    'dia_semana': dia_semana,
                    'hora_inicio': hora_inicio,
                    'hora_fim': hora_fim,
                    'ativo': ativo,    
                    'criado_em': firestore.SERVER_TIMESTAMP
                }
                if intervalo_minutos is not None:
                    horario_data['intervalo_minutos'] = intervalo_minutos

                db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_id_selecionado).collection('horarios_disponiveis').add(horario_data)
                flash('Horário adicionado com sucesso!', 'success')
                return redirect(url_for('listar_horarios'))
        except ValueError:
            flash('Valores numéricos inválidos para dia ou intervalo.', 'danger')
        except Exception as e:
            flash(f'Erro ao adicionar horário: {e}', 'danger')
            print(f"Erro add_schedule (POST): {e}")
            
    return render_template('horario_form.html',    
                           profissionais=profissionais_ativos_lista,
                           dias_semana=dias_semana_map,    
                           horario=None,    
                           action_url=url_for('adicionar_horario'),
                           page_title='Adicionar Novo Horário',
                           current_year=datetime.datetime.now(SAO_PAULO_TZ).year)


@app.route('/profissionais/<string:profissional_doc_id>/horarios/editar/<string:horario_doc_id>', methods=['GET', 'POST'])
@login_required
def editar_horario(profissional_doc_id, horario_doc_id):
    clinica_id = session['clinica_id']
    horario_ref = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).collection('horarios_disponiveis').document(horario_doc_id)
    
    profissionais_ativos_lista = []
    try:
        profissionais_docs = db.collection('clinicas').document(clinica_id).collection('profissionais').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()
        for doc in profissionais_docs:
            p_data = doc.to_dict()
            if p_data: profissionais_ativos_lista.append({'id': doc.id, 'nome': p_data.get('nome', doc.id)})
    except Exception as e:
        flash(f'Erro ao carregar profissionais ativos para o formulário.', 'danger')
        print(f"Erro ao carregar profissionais (edit_schedule GET): {e}")

    dias_semana_map = {0: 'Domingo', 1: 'Segunda-feira', 2: 'Terça-feira', 3: 'Quarta-feira', 4: 'Quinta-feira', 5: 'Sexta-feira', 6: 'Sábado'}

    if request.method == 'POST':
        try:
            dia_semana = int(request.form['dia_semana'])
            hora_inicio = request.form['hora_inicio']
            hora_fim = request.form['hora_fim']
            intervalo_minutos_str = request.form.get('intervalo_minutos')
            intervalo_minutos = int(intervalo_minutos_str) if intervalo_minutos_str and intervalo_minutos_str.isdigit() else None
            ativo = 'ativo' in request.form

            if hora_inicio >= hora_fim:
                flash('A hora de início deve ser anterior à hora de término.', 'warning')
            else:
                horario_data_update = {
                    'dia_semana': dia_semana,
                    'hora_inicio': hora_inicio,
                    'hora_fim': hora_fim,
                    'ativo': ativo,    
                    'atualizado_em': firestore.SERVER_TIMESTAMP
                }
                if intervalo_minutos is not None:
                    horario_data_update['intervalo_minutos'] = intervalo_minutos
                else:    
                    horario_data_update['intervalo_minutos'] = firestore.DELETE_FIELD

                horario_ref.update(horario_data_update)
                flash('Horário atualizado com sucesso!', 'success')
                return redirect(url_for('listar_horarios'))
        except ValueError:
            flash('Valores numéricos inválidos.', 'danger')
        except Exception as e:
            flash(f'Erro ao atualizar horário: {e}', 'danger')
            print(f"Erro edit_schedule (POST): {e}")
            
    try:
        horario_doc_snapshot = horario_ref.get()
        if horario_doc_snapshot.exists:
            horario_data_db = horario_doc_snapshot.to_dict()
            if horario_data_db:
                horario_data_db['id'] = horario_doc_snapshot.id    
                horario_data_db['profissional_id_fk'] = profissional_doc_id
                
                profissional_pai_doc = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).get()
                if profissional_pai_doc.exists:
                    profissional_pai_data = profissional_pai_doc.to_dict()
                    if profissional_pai_data:
                        horario_data_db['profissional_nome_atual'] = profissional_pai_data.get('nome', profissional_doc_id)
                
                return render_template('horario_form.html',    
                                       profissionais=profissionais_ativos_lista,
                                       dias_semana=dias_semana_map,    
                                       horario=horario_data_db,    
                                       action_url=url_for('editar_horario', profissional_doc_id=profissional_doc_id, horario_doc_id=horario_doc_id),
                                       page_title=f"Editar Horário para {horario_data_db.get('profissional_nome_atual', 'Profissional')}",
                                       current_year=datetime.datetime.now(SAO_PAULO_TZ).year)
        else:
            flash('Horário específico não encontrado.', 'danger')
            return redirect(url_for('listar_horarios'))
    except Exception as e:
        flash(f'Erro ao carregar horário para edição: {e}', 'danger')
        print(f"Erro edit_schedule (GET): {e}")
        return redirect(url_for('listar_horarios'))


@app.route('/profissionais/<string:profissional_doc_id>/horarios/excluir/<string:horario_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_horario(profissional_doc_id, horario_doc_id):
    clinica_id = session['clinica_id']
    try:
        db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).collection('horarios_disponiveis').document(horario_doc_id).delete()
        flash('Horário disponível excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir horário: {e}', 'danger')
        print(f"Erro delete_schedule: {e}")
    return redirect(url_for('listar_horarios'))

@app.route('/profissionais/<string:profissional_doc_id>/horarios/ativar_desativar/<string:horario_doc_id>', methods=['POST'])
@login_required
@admin_required
def ativar_desativar_horario(profissional_doc_id, horario_doc_id):
    clinica_id = session['clinica_id']
    horario_ref = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).collection('horarios_disponiveis').document(horario_doc_id)
    try:
        horario_doc = horario_ref.get()
        if horario_doc.exists:
            data = horario_doc.to_dict()
            if data:
                current_status = data.get('ativo', False)    
                new_status = not current_status
                horario_ref.update({'ativo': new_status, 'atualizado_em': firestore.SERVER_TIMESTAMP})
                flash(f'Horário {"ativado" if new_status else "desativado"} com sucesso!', 'success')
            else:
                flash('Dados de horário inválidos.', 'danger')
        else:
            flash('Horário não encontrado.', 'danger')
    except Exception as e:
        flash(f'Erro ao alterar o status do horário: {e}', 'danger')
        print(f"Erro in activate_deactivate_schedule: {e}")
    return redirect(url_for('listar_horarios'))

@app.route('/agendamentos')
@login_required
def listar_agendamentos():
    clinica_id = session['clinica_id']
    agendamentos_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos')
    agendamentos_lista = []
    
    profissionais_para_filtro = []
    servicos_procedimentos_ativos = []
    pacientes_para_filtro = []

    try:
        profissionais_docs = db.collection('clinicas').document(clinica_id).collection('profissionais').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()
        for doc in profissionais_docs:
            p_data = doc.to_dict()
            if p_data: profissionais_para_filtro.append({'id': doc.id, 'nome': p_data.get('nome', doc.id)})
        
        servicos_docs = db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').order_by('nome').stream()
        for doc in servicos_docs:
            s_data = doc.to_dict()
            if s_data: servicos_procedimentos_ativos.append({'id': doc.id, 'nome': s_data.get('nome', doc.id), 'preco': s_data.get('preco_sugerido', 0.0)})

        pacientes_docs = db.collection('clinicas').document(clinica_id).collection('pacientes').order_by('nome').stream()
        for doc in pacientes_docs:
            pac_data = doc.to_dict()
            if pac_data: pacientes_para_filtro.append({'id': doc.id, 'nome': pac_data.get('nome', doc.id), 'contato_telefone': pac_data.get('contato_telefone', '')})

    except Exception as e:
        flash('Erro ao carregar dados para filtros/modal.', 'warning')
        print(f"Erro ao carregar profissionais/serviços_procedimentos/pacientes para filtros: {e}")

    filtros_atuais = {
        'paciente_nome': request.args.get('paciente_nome', '').strip(),
        'profissional_id': request.args.get('profissional_id', '').strip(),
        'status': request.args.get('status', '').strip(),
        'data_inicio': request.args.get('data_inicio', '').strip(),
        'data_fim': request.args.get('data_fim', '').strip(),
    }

    if not filtros_atuais['data_inicio'] and not filtros_atuais['data_fim']:
        hoje = datetime.datetime.now(SAO_PAULO_TZ)
        inicio_mes = hoje.replace(day=1)
        
        if inicio_mes.month == 12:
            proximo_mes_inicio = inicio_mes.replace(year=inicio_mes.year + 1, month=1, day=1)
        else:
            proximo_mes_inicio = inicio_mes.replace(month=inicio_mes.month + 1, day=1)
        fim_mes = proximo_mes_inicio - datetime.timedelta(days=1)
        
        filtros_atuais['data_inicio'] = inicio_mes.strftime('%Y-%m-%d')
        filtros_atuais['data_fim'] = fim_mes.strftime('%Y-%m-%d')

    query = agendamentos_ref

    if filtros_atuais['paciente_nome']:
        query = query.where(filter=FieldFilter('paciente_nome', '>=', filtros_atuais['paciente_nome'])).where(filter=FieldFilter('paciente_nome', '<=', filtros_atuais['paciente_nome'] + '\uf8ff'))
    if filtros_atuais['profissional_id']:
        query = query.where(filter=FieldFilter('profissional_id', '==', filtros_atuais['profissional_id']))
    if filtros_atuais['status']:
        query = query.where(filter=FieldFilter('status', '==', filtros_atuais['status']))
    if filtros_atuais['data_inicio']:
        try:
            dt_inicio_utc = SAO_PAULO_TZ.localize(datetime.datetime.strptime(filtros_atuais['data_inicio'], '%Y-%m-%d')).astimezone(pytz.utc)
            query = query.where(filter=FieldFilter('data_agendamento_ts', '>=', dt_inicio_utc))
        except ValueError:
            flash('Data de início inválida. Use o formato AAAA-MM-DD.', 'warning')
    if filtros_atuais['data_fim']:
        try:
            dt_fim_utc = SAO_PAULO_TZ.localize(datetime.datetime.strptime(filtros_atuais['data_fim'], '%Y-%m-%d').replace(hour=23, minute=59, second=59)).astimezone(pytz.utc)
            query = query.where(filter=FieldFilter('data_agendamento_ts', '<=', dt_fim_utc))
        except ValueError:
            flash('Data de término inválida. Use o formato AAAA-MM-DD.', 'warning')

    try:
        docs_stream = query.order_by('data_agendamento_ts', direction=firestore.Query.DESCENDING).stream()

        for doc in docs_stream:
            ag = doc.to_dict()
            if ag:
                ag['id'] = doc.id
                if ag.get('data_agendamento'):
                    try: ag['data_agendamento_fmt'] = datetime.datetime.strptime(ag['data_agendamento'], '%Y-%m-%d').strftime('%d/%m/%Y')
                    except: ag['data_agendamento_fmt'] = ag['data_agendamento']
                else: ag['data_agendamento_fmt'] = "N/A"
                
                ag['preco_servico_fmt'] = "R$ {:.2f}".format(float(ag.get('servico_procedimento_preco', 0))).replace('.', ',')
                data_criacao_ts = ag.get('data_criacao')
                if isinstance(data_criacao_ts, datetime.datetime):
                    ag['data_criacao_fmt'] = data_criacao_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                else:
                    ag['data_criacao_fmt'] = "N/A"
                agendamentos_lista.append(ag)
    except Exception as e:
        flash(f'Erro ao listar agendamentos: {e}. Verifique seus índices do Firestore.', 'danger')
        print(f"Erro list_appointments: {e}")
    
    stats_cards = {
        'confirmado': {'count': 0, 'total_valor': 0.0},
        'concluido': {'count': 0, 'total_valor': 0.0},
        'cancelado': {'count': 0, 'total_valor': 0.0},
        'pendente': {'count': 0, 'total_valor': 0.0}
    }
    for agendamento in agendamentos_lista:
        status = agendamento.get('status', 'pendente').lower()
        preco = float(agendamento.get('servico_procedimento_preco', 0))
        if status in stats_cards:
            stats_cards[status]['count'] += 1
            stats_cards[status]['total_valor'] += preco

    return render_template('agendamentos.html',    
                           agendamentos=agendamentos_lista,
                           stats_cards=stats_cards,
                           profissionais_para_filtro=profissionais_para_filtro,
                           servicos_ativos=servicos_procedimentos_ativos,
                           pacientes_para_filtro=pacientes_para_filtro,
                           filtros_atuais=filtros_atuais,
                           current_year=datetime.datetime.now(SAO_PAULO_TZ).year)

@app.route('/agendamentos/registrar_manual', methods=['POST'])
@login_required
def registrar_atendimento_manual():
    clinica_id = session['clinica_id']
    try:
        paciente_nome = request.form.get('cliente_nome_manual')
        paciente_telefone = request.form.get('cliente_telefone_manual')
        profissional_id_manual = request.form.get('barbeiro_id_manual')
        servico_procedimento_id_manual = request.form.get('servico_id_manual')
        data_agendamento_str = request.form.get('data_agendamento_manual')
        hora_agendamento_str = request.form.get('hora_agendamento_manual')
        preco_str = request.form.get('preco_manual')
        status_manual = request.form.get('status_manual')

        if not all([paciente_nome, profissional_id_manual, servico_procedimento_id_manual, data_agendamento_str, hora_agendamento_str, preco_str, status_manual]):
            flash('Todos os campos obrigatórios devem ser preenchidos.', 'danger')
            return redirect(url_for('listar_agendamentos'))

        preco_servico = float(preco_str.replace(',', '.'))

        paciente_ref_query = db.collection('clinicas').document(clinica_id).collection('pacientes').where(filter=FieldFilter('nome', '==', paciente_nome)).limit(1).get()
        
        paciente_doc_id = None
        if paciente_ref_query:
            for doc in paciente_ref_query:
                paciente_doc_id = doc.id
                break
        
        if not paciente_doc_id:
            _, novo_paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').add({
                'nome': paciente_nome,
                'contato_telefone': paciente_telefone if paciente_telefone else None,
                'data_cadastro': firestore.SERVER_TIMESTAMP
            })
            paciente_doc_id = novo_paciente_ref.id

        profissional_doc = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_id_manual).get()
        servico_procedimento_doc = db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_procedimento_id_manual).get()

        profissional_nome = profissional_doc.to_dict().get('nome', 'N/A') if profissional_doc.exists else 'N/A'
        servico_procedimento_nome = servico_procedimento_doc.to_dict().get('nome', 'N/A') if servico_procedimento_doc.exists else 'N/A'
        
        dt_agendamento_naive = datetime.datetime.strptime(f"{data_agendamento_str} {hora_agendamento_str}", "%Y-%m-%d %H:%M")
        dt_agendamento_sp = SAO_PAULO_TZ.localize(dt_agendamento_naive)
        data_agendamento_ts_utc = dt_agendamento_sp.astimezone(pytz.utc)

        novo_agendamento_dados = {
            'paciente_id': paciente_doc_id,
            'paciente_nome': paciente_nome,
            'paciente_numero': paciente_telefone if paciente_telefone else None,
            'profissional_id': profissional_id_manual,
            'profissional_nome': profissional_nome,
            'servico_procedimento_id': servico_procedimento_id_manual,
            'servico_procedimento_nome': servico_procedimento_nome,
            'data_agendamento': data_agendamento_str,
            'hora_agendamento': hora_agendamento_str,
            'data_agendamento_ts': data_agendamento_ts_utc,
            'servico_procedimento_preco': preco_servico,
            'status': status_manual,
            'tipo_agendamento': 'manual_dashboard',
            'data_criacao': firestore.SERVER_TIMESTAMP,
            'atualizado_em': firestore.SERVER_TIMESTAMP
        }
        
        db.collection('clinicas').document(clinica_id).collection('agendamentos').add(novo_agendamento_dados)
        
        flash('Atendimento registrado manualmente com sucesso!', 'success')
    except ValueError as ve:
        flash(f'Erro de valor ao registrar atendimento: {ve}', 'danger')
    except Exception as e:
        flash(f'Erro ao registrar atendimento manual: {e}', 'danger')
    return redirect(url_for('listar_agendamentos'))


@app.route('/agendamentos/alterar_status/<string:agendamento_doc_id>', methods=['POST'])
@login_required
def alterar_status_agendamento(agendamento_doc_id):
    clinica_id = session['clinica_id']
    novo_status = request.form.get('status')
    if not novo_status:
        flash('Nenhum status foi fornecido.', 'warning')
        return redirect(url_for('listar_agendamentos'))
    try:
        db.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_doc_id).update({
            'status': novo_status,
            'atualizado_em': firestore.SERVER_TIMESTAMP
        })
        flash(f'Status atualizado para "{novo_status}" com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao alterar o status do agendamento: {e}', 'danger')
    return redirect(url_for('listar_agendamentos'))

@app.route('/agendamentos/editar', methods=['POST'])
@login_required
def editar_agendamento():
    clinica_id = session['clinica_id']
    agendamento_id = request.form.get('agendamento_id')

    if not agendamento_id:
        flash('ID do agendamento não fornecido para edição.', 'danger')
        return redirect(url_for('listar_agendamentos'))

    try:
        agendamento_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_id)
        
        paciente_nome = request.form.get('cliente_nome_manual')
        profissional_id_manual = request.form.get('barbeiro_id_manual')
        servico_procedimento_id_manual = request.form.get('servico_id_manual')
        data_agendamento_str = request.form.get('data_agendamento_manual')
        hora_agendamento_str = request.form.get('hora_agendamento_manual')
        preco_str = request.form.get('preco_manual')
        status_manual = request.form.get('status_manual')

        if not all([paciente_nome, profissional_id_manual, servico_procedimento_id_manual, data_agendamento_str, hora_agendamento_str, preco_str, status_manual]):
            flash('Todos os campos obrigatórios devem ser preenchidos para editar.', 'danger')
            return redirect(url_for('listar_agendamentos'))
        
        profissional_doc = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_id_manual).get()
        servico_procedimento_doc = db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_procedimento_id_manual).get()

        profissional_nome = profissional_doc.to_dict().get('nome', 'N/A') if profissional_doc.exists else 'N/A'
        servico_procedimento_nome = servico_procedimento_doc.to_dict().get('nome', 'N/A') if servico_procedimento_doc.exists else 'N/A'
        
        dt_agendamento_naive = datetime.datetime.strptime(f"{data_agendamento_str} {hora_agendamento_str}", "%Y-%m-%d %H:%M")
        dt_agendamento_sp = SAO_PAULO_TZ.localize(dt_agendamento_naive)
        data_agendamento_ts_utc = dt_agendamento_sp.astimezone(pytz.utc)

        update_data = {
            'paciente_nome': paciente_nome,
            'profissional_id': profissional_id_manual,
            'profissional_nome': profissional_nome,
            'servico_procedimento_id': servico_procedimento_id_manual,
            'servico_procedimento_nome': servico_procedimento_nome,
            'data_agendamento': data_agendamento_str,
            'hora_agendamento': hora_agendamento_str,
            'data_agendamento_ts': data_agendamento_ts_utc,
            'servico_procedimento_preco': float(preco_str.replace(',', '.')),
            'status': status_manual,
            'atualizado_em': firestore.SERVER_TIMESTAMP
        }

        agendamento_ref.update(update_data)
        flash('Agendamento atualizado com sucesso!', 'success')

    except Exception as e:
        flash(f'Erro ao atualizar agendamento: {e}', 'danger')
        print(f"Erro edit_appointment: {e}")
        
    return redirect(url_for('listar_agendamentos'))

@app.route('/agendamentos/apagar', methods=['POST'])
@login_required
def apagar_agendamento():
    clinica_id = session['clinica_id']
    agendamento_id = request.form.get('agendamento_id')
    if not agendamento_id:
        flash('ID do agendamento não fornecido para exclusão.', 'danger')
        return redirect(url_for('listar_agendamentos'))

    try:
        db.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_id).delete()
        flash('Agendamento apagado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao apagar agendamento: {e}', 'danger')
        print(f"Erro delete_appointment: {e}")
    return redirect(url_for('listar_agendamentos'))

@app.route('/prontuarios')
@login_required
def buscar_prontuario():
    clinica_id = session['clinica_id']
    pacientes_para_busca = []
    search_query = request.args.get('search_query', '').strip()

    try:
        pacientes_ref = db.collection('clinicas').document(clinica_id).collection('pacientes')
        query = pacientes_ref.order_by('nome')

        docs = query.stream()
        for doc in docs:
            paciente_data = doc.to_dict()
            if paciente_data:
                paciente_data['id'] = doc.id
                if not search_query or search_query.lower() in paciente_data.get('nome', '').lower() or search_query in paciente_data.get('cpf', ''):
                    pacientes_para_busca.append(paciente_data)
            
    except Exception as e:
        flash(f'Erro ao carregar lista de pacientes para busca: {e}.', 'danger')
        print(f"Erro search_patient_record: {e}")

    return render_template('prontuario_busca.html', pacientes_para_busca=pacientes_para_busca, search_query=search_query)

# Em app.py

@app.route('/prontuarios/<string:paciente_doc_id>')
@login_required
def ver_prontuario(paciente_doc_id):
    clinica_id = session['clinica_id']
    paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
    peis_ref = db.collection('clinicas').document(clinica_id).collection('peis')

    paciente_data = None
    registros_prontuario = []
    peis_do_paciente = []

    try:
        paciente_doc = paciente_ref.get()
        if not paciente_doc.exists:
            flash('Paciente não encontrado.', 'danger')
            return redirect(url_for('buscar_prontuario'))

        paciente_data = convert_doc_to_dict(paciente_doc)

        if paciente_data and paciente_data.get('convenio_id'):
            convenio_doc = db.collection('clinicas').document(clinica_id).collection('convenios').document(paciente_data['convenio_id']).get()
            if convenio_doc.exists:
                paciente_data['convenio_nome'] = convenio_doc.to_dict().get('nome', 'N/A')

        # Lógica aprimorada para buscar PEIs
        raw_pei_ids = paciente_doc.to_dict().get('peis_associados', [])
        
        # Garante que a lista contém apenas IDs de string válidos
        pei_ids_associados_limpos = [pid for pid in raw_pei_ids if pid and isinstance(pid, str)]
        
        if pei_ids_associados_limpos:
            print(f"Buscando {len(pei_ids_associados_limpos)} PEIs individualmente...")
            for pei_id in pei_ids_associados_limpos:
                try:
                    pei_doc = peis_ref.document(pei_id).get()
                    if pei_doc.exists:
                        # Adiciona o PEI convertido para a lista
                        peis_do_paciente.append(convert_doc_to_dict(pei_doc))
                    else:
                        print(f"Aviso: PEI com ID '{pei_id}' não foi encontrado na coleção 'peis'.")
                except Exception as pei_e:
                    print(f"!!! ERRO ao buscar PEI individual com ID '{pei_id}': {pei_e}")
                    flash(f"Não foi possível carregar o PEI com ID: {pei_id}. Causa: {pei_e}", 'warning')

        # Busca o histórico de prontuários
        try:
            prontuarios_docs = paciente_ref.collection('prontuarios').order_by('data_registro', direction=firestore.Query.DESCENDING).stream()
            for doc in prontuarios_docs:
                registros_prontuario.append(convert_doc_to_dict(doc))
        except Exception as records_e:
            flash(f"Erro ao carregar o histórico do prontuário: {records_e}.", 'danger')

    except Exception as e:
        flash(f'Erro fatal ao carregar prontuário: {e}.', 'danger')
        print(f"!!! ERRO FATAL em ver_prontuario: {e}")
        return redirect(url_for('buscar_prontuario'))

    return render_template('prontuario.html',
                           paciente=paciente_data,
                           registros=registros_prontuario,
                           peis_do_paciente=peis_do_paciente)

@app.route('/api/pacientes/<string:paciente_doc_id>/agendamentos-concluidos', methods=['GET'])
@login_required
def api_agendamentos_concluidos(paciente_doc_id):
    clinica_id = session['clinica_id']
    user_role = session.get('user_role')
    user_uid = session.get('user_uid')
    
    agendamentos_concluidos = []
    
    try:
        profissional_id_logado = None
        # Se não for admin, pega o ID do profissional associado ao usuário
        if user_role != 'admin':
            prof_doc = db.collection('User').document(user_uid).get()
            profissional_id_logado = prof_doc.to_dict().get('profissional_id') if prof_doc.exists else None
            if not profissional_id_logado:
                return jsonify({'success': False, 'message': 'Usuário não associado a um profissional.'}), 403

        # Constrói a query para buscar agendamentos concluídos do paciente
        query = db.collection('clinicas').document(clinica_id).collection('agendamentos') \
            .where(filter=FieldFilter('paciente_id', '==', paciente_doc_id)) \
            .where(filter=FieldFilter('status', '==', 'concluido')) \
            .order_by('data_agendamento_ts', direction=firestore.Query.DESCENDING) \
            .limit(10) # Limita aos últimos 10 para performance

        # Se não for admin, filtra apenas os agendamentos do profissional logado
        if user_role != 'admin':
            query = query.where(filter=FieldFilter('profissional_id', '==', profissional_id_logado))

        docs = query.stream()
        for doc in docs:
            ag = convert_doc_to_dict(doc)
            if ag:
                agendamentos_concluidos.append(ag)
        
        return jsonify({'success': True, 'agendamentos': agendamentos_concluidos})

    except Exception as e:
        print(f"Erro API ao carregar agendamentos concluídos para paciente {paciente_doc_id}: {e}")
        return jsonify({'success': False, 'message': f'Erro interno do servidor: {e}'}), 500

@app.route('/agendamentos/<string:agendamento_id>/evolucao', methods=['GET'])
@login_required
def ver_evolucao_agendamento(agendamento_id):
    clinica_id = session['clinica_id']
    agendamento_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_id)
    
    mensagens_evolucao = []

    try:
        agendamento_doc = agendamento_ref.get()
        if not agendamento_doc.exists:
            return jsonify({'success': False, 'message': 'Agendamento não encontrado.'}), 404
        
        agendamento_data = agendamento_doc.to_dict()
        if not agendamento_data:
            return jsonify({'success': False, 'message': 'Dados do agendamento ausentes.'}), 500

        # Verifica permissão: admin pode ver tudo, profissional só pode ver os seus
        if session.get('user_role') != 'admin':
            user_uid = session.get('user_uid')
            prof_doc = db.collection('User').document(user_uid).get()
            profissional_id_logado = prof_doc.to_dict().get('profissional_id') if prof_doc.exists else None

            if agendamento_data.get('profissional_id') != profissional_id_logado:
                return jsonify({'success': False, 'message': 'Acesso negado.'}), 403

        # Busca as mensagens de evolução na subcoleção
        evolucao_ref = agendamento_ref.collection('evolucao')
        evolucao_docs = evolucao_ref.order_by('data_registro').stream()
        
        for doc in evolucao_docs:
            msg = convert_doc_to_dict(doc)
            if msg:
                mensagens_evolucao.append(msg)

    except Exception as e:
        print(f"Erro ver_evolucao_agendamento: {e}")
        return jsonify({'success': False, 'message': f'Erro ao carregar evolução: {e}'}), 500
    
    return jsonify({
        'agendamento': convert_doc_to_dict(agendamento_doc),
        'mensagens': mensagens_evolucao,
        'success': True
    })

@app.route('/agendamentos/<string:agendamento_id>/evolucao/adicionar', methods=['POST'])
@login_required
def adicionar_evolucao_agendamento(agendamento_id):
    clinica_id = session['clinica_id']
    agendamento_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_id)
    
    conteudo_mensagem = request.json.get('conteudo', '').strip()
    
    if not conteudo_mensagem:
        return jsonify({'success': False, 'message': 'Mensagem vazia não pode ser registrada.'}), 400

    profissional_logado_uid = session.get('user_uid')
    profissional_nome = session.get('user_name', session.get('user_email', 'Desconhecido'))
    profissional_id = None
    
    try:
        user_doc = db.collection('User').document(profissional_logado_uid).get()
        if user_doc.exists:
            profissional_id = user_doc.to_dict().get('profissional_id')
        
        if not profissional_id:
            # Fallback caso o usuário não esteja linkado a um profissional (ex: um admin registrando)
            profissional_id = profissional_logado_uid
            print(f"Aviso: Usuário {profissional_logado_uid} não tem profissional_id associado. Usando UID para registro de evolução.")

        # Validação de permissão
        agendamento_doc = agendamento_ref.get()
        if not agendamento_doc.exists:
            return jsonify({'success': False, 'message': 'Agendamento não encontrado.'}), 404
        
        if session.get('user_role') != 'admin':
            if agendamento_doc.to_dict().get('profissional_id') != profissional_id:
                return jsonify({'success': False, 'message': 'Acesso negado.'}), 403

        evolucao_ref = agendamento_ref.collection('evolucao')
        
        nova_mensagem = {
            'conteudo': conteudo_mensagem,
            'data_registro': firestore.SERVER_TIMESTAMP,
            'registrado_por_uid': profissional_logado_uid,
            'registrado_por_nome': profissional_nome,
            'profissional_id': profissional_id
        }
        
        update_time, new_doc_ref = evolucao_ref.add(nova_mensagem)
        
        # Precisamos buscar o documento recém-criado para obter o timestamp gerado pelo servidor
        new_doc = new_doc_ref.get()
        final_message = convert_doc_to_dict(new_doc)

        return jsonify({'success': True, 'message': 'Evolução registrada!', 'nova_mensagem': final_message}), 201

    except Exception as e:
        print(f"Erro ao adicionar evolução: {e}")
        return jsonify({'success': False, 'message': f'Erro ao registrar evolução: {e}'}), 500
    
@app.route('/prontuarios/<string:paciente_doc_id>/anamnese/novo', methods=['GET', 'POST'])
@login_required
def adicionar_anamnese(paciente_doc_id):
    clinica_id = session['clinica_id']
    profissional_logado_uid = session.get('user_uid')   

    profissional_doc_id = None
    try:
        user_doc = db.collection('User').document(profissional_logado_uid).get()
        if user_doc.exists:
            profissional_doc_id = user_doc.to_dict().get('profissional_id')
        if not profissional_doc_id:
            flash('Seu usuário não está associado a um profissional. Contate o administrador.', 'danger')
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
    except Exception as e:
        flash(f'Erro ao verificar profissional associado: {e}', 'danger')
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
    paciente_doc = paciente_ref.get()
    if not paciente_doc.exists:
        flash('Paciente não encontrado.', 'danger')
        return redirect(url_for('buscar_prontuario'))
    
    paciente_nome = paciente_doc.to_dict().get('nome', 'Paciente Desconhecido')

    modelos_anamnese = []
    try:
        modelos_docs = db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').order_by('identificacao').stream()
        for doc in modelos_docs:
            modelos_anamnese.append(convert_doc_to_dict(doc))
    except Exception as e:
        flash('Erro ao carregar modelos de anamnese.', 'warning')
        print(f"Erro ao carregar modelos de anamnese: {e}")

    if request.method == 'POST':
        conteudo = request.form.get('conteudo', '').strip()   
        modelo_base_id = request.form.get('modelo_base_id')
        
        try:
            db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').add({
                'profissional_id': profissional_doc_id,
                'data_registro': firestore.SERVER_TIMESTAMP,
                'tipo_registro': 'anamnese',
                'titulo': 'Anamnese',
                'conteudo': conteudo,
                'modelo_base_id': modelo_base_id if modelo_base_id else None
            })
            flash('Anamnese adicionada com sucesso!', 'success')
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
        except Exception as e:
            flash(f'Erro ao adicionar anamnese: {e}', 'danger')
            print(f"Erro add_anamnesis (POST): {e}")
    
    return render_template('anamnese_form.html',    
                           paciente_id=paciente_doc_id,    
                           paciente_nome=paciente_nome,    
                           modelos_anamnese=modelos_anamnese,    
                           action_url=url_for('adicionar_anamnese', paciente_doc_id=paciente_doc_id),
                           page_title=f"Registrar Anamnese para {paciente_nome}")

@app.route('/prontuarios/<string:paciente_doc_id>/anamnese/editar/<string:anamnese_doc_id>', methods=['GET', 'POST'])
@login_required
def editar_anamnese(paciente_doc_id, anamnese_doc_id):
    clinica_id = session['clinica_id']

    anamnese_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').document(anamnese_doc_id)
    paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
    
    paciente_doc = paciente_ref.get()
    if not paciente_doc.exists:
        flash('Paciente não encontrado.', 'danger')
        return redirect(url_for('buscar_prontuario'))
    
    paciente_nome = paciente_doc.to_dict().get('nome', 'Paciente Desconhecido')

    modelos_anamnese = []
    try:
        modelos_docs = db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').order_by('identificacao').stream()
        for doc in modelos_docs:
            modelos_anamnese.append(convert_doc_to_dict(doc))
    except Exception as e:
        flash('Erro ao carregar modelos de anamnese.', 'warning')
        print(f"Erro ao carregar modelos de anamnese (edit): {e}")

    if request.method == 'POST':
        conteudo = request.form.get('conteudo', '').strip()
        modelo_base_id = request.form.get('modelo_base_id')
        
        try:
            anamnese_ref.update({
                'conteudo': conteudo,
                'modelo_base_id': modelo_base_id if modelo_base_id else None,
                'atualizado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Anamnese atualizada com sucesso!', 'success')
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
        except Exception as e:
            flash(f'Erro ao atualizar anamnese: {e}', 'danger')
            print(f"Erro edit_anamnesis (POST): {e}")
    
    try:
        anamnese_doc = anamnese_ref.get()
        if anamnese_doc.exists and anamnese_doc.to_dict().get('tipo_registro') == 'anamnese':
            anamnese_data = convert_doc_to_dict(anamnese_doc)
            
            return render_template('anamnese_form.html',    
                                   paciente_id=paciente_doc_id,    
                                   paciente_nome=paciente_nome,    
                                   anamnese=anamnese_data,    
                                   modelos_anamnese=modelos_anamnese,
                                   action_url=url_for('editar_anamnese', paciente_doc_id=paciente_doc_id, anamnese_doc_id=anamnese_doc_id),
                                   page_title=f"Editar Anamnese para {paciente_nome}")
        else:
            flash('Anamnese não encontrada ou tipo de registro inválido.', 'danger')
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
    except Exception as e:
        flash(f'Erro ao carregar anamnese para edição: {e}', 'danger')
        print(f"Erro edit_anamnesis (GET): {e}")
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

@app.route('/prontuarios/<string:paciente_doc_id>/registrar_registro_generico', methods=['POST'])
@login_required
def registrar_registro_generico(paciente_doc_id):
    clinica_id = session['clinica_id']
    profissional_logado_uid = session.get('user_uid')

    profissional_doc_id = None
    profissional_nome = "Profissional Desconhecido"
    try:
        user_doc = db.collection('User').document(profissional_logado_uid).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            profissional_doc_id = user_data.get('profissional_id')
            profissional_nome = user_data.get('nome_completo', profissional_nome)

        if not profissional_doc_id:
            flash('Seu usuário não está associado a um profissional. Não foi possível registrar.', 'danger')
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
    except Exception as e:
        flash(f'Erro ao verificar profissional para registro: {e}', 'danger')
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    try:
        tipo_registro = request.form.get('tipo_registro')
        titulo = request.form.get('titulo', '').strip()
        conteudo = request.form.get('conteudo', '').strip()
        
        # --- CORREÇÃO APLICADA AQUI ---
        # Captura as referências do PEI do formulário
        referencia_pei_id = request.form.get('referencia_pei_id')
        referencia_meta_titulo = request.form.get('referencia_meta_titulo')

        if not all([tipo_registro, titulo, conteudo]):
            flash(f'Por favor, preencha o título e o conteúdo para o registro.', 'danger')
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

        novo_registro_data = {
            'profissional_id': profissional_doc_id,
            'profissional_nome': profissional_nome,
            'data_registro': firestore.SERVER_TIMESTAMP,
            'tipo_registro': tipo_registro,
            'titulo': titulo,
            'conteudo': conteudo,
            'atualizado_em': firestore.SERVER_TIMESTAMP
        }

        # Adiciona as referências do PEI ao documento se for uma evolução
        if tipo_registro == 'evolucao_pei' and referencia_pei_id and referencia_meta_titulo:
            novo_registro_data['referencia_pei_id'] = referencia_pei_id
            novo_registro_data['referencia_meta_titulo'] = referencia_meta_titulo

        db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').add(novo_registro_data)
        
        flash(f'Registro de {tipo_registro.replace("_", " ")} adicionado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao adicionar registro: {e}', 'danger')
        print(f"Erro registrar_registro_generico: {e}")
    
    return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

@app.route('/prontuarios/<string:paciente_doc_id>/editar_registro_generico/<string:registro_doc_id>', methods=['POST'])
@login_required
def editar_registro_generico(paciente_doc_id, registro_doc_id):
    clinica_id = session['clinica_id']
    registro_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').document(registro_doc_id)

    try:
        titulo = request.form.get('titulo', '').strip()
        conteudo = request.form.get('conteudo', '').strip()
        tipo_registro = request.form.get('tipo_registro_feedback', 'Registro') # Campo para feedback

        if not all([titulo, conteudo]):
            flash('Por favor, preencha o título e o conteúdo para o registro.', 'danger')
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

        registro_ref.update({
            'titulo': titulo,
            'conteudo': conteudo,
            'atualizado_em': firestore.SERVER_TIMESTAMP
        })
        flash(f'{tipo_registro.capitalize()} atualizado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao atualizar registro: {e}', 'danger')
        print(f"Erro editar_registro_generico: {e}")
    return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))


@app.route('/prontuarios/<string:paciente_doc_id>/apagar_registro_generico', methods=['POST'])
@login_required
def apagar_registro_generico(paciente_doc_id):
    clinica_id = session['clinica_id']
    registro_id = request.form.get('registro_id')

    if not registro_id:
        flash('ID do registro não fornecido para exclusão.', 'danger')
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    try:
        db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').document(registro_id).delete()
        flash('Registro apagado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao apagar registro: {e}', 'danger')
        print(f"Erro apagar_registro_generico: {e}")
    return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

@app.route('/modelos_anamnese')
@login_required
@admin_required
def listar_modelos_anamnese():
    clinica_id = session['clinica_id']
    modelos_lista = []
    try:
        docs = db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').order_by('identificacao').stream()
        for doc in docs:
            modelo = convert_doc_to_dict(doc)
            modelos_lista.append(modelo)
    except Exception as e:
        flash(f'Erro ao listar modelos de anamnese: {e}.', 'danger')
        print(f"Erro list_anamnesis_templates: {e}")
    return render_template('modelos_anamnese.html', modelos=modelos_lista)

@app.route('/modelos_anamnese/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_modelo_anamnese():
    clinica_id = session['clinica_id']
    if request.method == 'POST':
        identificacao = request.form['identificacao'].strip()
        conteudo_modelo = request.form['conteudo_modelo']
        
        if not identificacao:
            flash('A identificação do modelo é obrigatória.', 'danger')
            return render_template('modelo_anamnese_form.html', modelo=request.form, action_url=url_for('adicionar_modelo_anamnese'))
        try:
            db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').add({
                'identificacao': identificacao,
                'conteudo_modelo': conteudo_modelo,
                'criado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Modelo de anamnese adicionado com sucesso!', 'success')
            return redirect(url_for('listar_modelos_anamnese'))
        except Exception as e:
            flash(f'Erro ao adicionar modelo de anamnese: {e}', 'danger')
            print(f"Erro add_anamnesis_template: {e}")
    return render_template('modelo_anamnese_form.html', modelo=None, action_url=url_for('adicionar_modelo_anamnese'))

@app.route('/modelos_anamnese/editar/<string:modelo_doc_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_modelo_anamnese(modelo_doc_id):
    clinica_id = session['clinica_id']
    modelo_ref = db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').document(modelo_doc_id)
    
    if request.method == 'POST':
        identificacao = request.form['identificacao'].strip()
        conteudo_modelo = request.form['conteudo_modelo']
        
        if not identificacao:
            flash('A identificação do modelo é obrigatória.', 'danger')
            return render_template('modelo_anamnese_form.html', modelo=request.form, action_url=url_for('editar_modelo_anamnese', modelo_doc_id=modelo_doc_id))
        try:
            modelo_ref.update({
                'identificacao': identificacao,
                'conteudo_modelo': conteudo_modelo,
                'atualizado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Modelo de anamnese atualizado com sucesso!', 'success')
            return redirect(url_for('listar_modelos_anamnese'))
        except Exception as e:
            flash(f'Erro ao atualizar modelo de anamnese: {e}', 'danger')
            print(f"Erro edit_anamnesis_template (POST): {e}")

    try:
        modelo_doc = modelo_ref.get()
        if modelo_doc.exists:
            modelo = convert_doc_to_dict(modelo_doc)
            return render_template('modelo_anamnese_form.html', modelo=modelo, action_url=url_for('editar_modelo_anamnese', modelo_doc_id=modelo_doc_id))
        else:
            flash('Modelo de anamnese não encontrado.', 'danger')
            return redirect(url_for('listar_modelos_anamnese'))
    except Exception as e:
        flash(f'Erro ao carregar modelo de anamnese para edição: {e}', 'danger')
        print(f"Erro edit_anamnesis_template (GET): {e}")
        return redirect(url_for('listar_modelos_anamnese'))

@app.route('/modelos_anamnese/excluir/<string:modelo_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_modelo_anamnese(modelo_doc_id):
    clinica_id = session['clinica_id']
    try:
        db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').document(modelo_doc_id).delete()
        flash('Modelo de anamnese excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir modelo de anamnese: {e}.', 'danger')
        print(f"Erro delete_anamnesis_template: {e}")
    return redirect(url_for('listar_modelos_anamnese'))

# ----------------------------------------------------------------------------------------------------------------------
# ROTAS DE GESTÃO DE PLANOS EDUCACIONAIS INDIVIDUALIZADOS (PEIs)
# ----------------------------------------------------------------------------------------------------------------------

@app.route('/peis')
@login_required
@admin_required
def listar_peis():
    clinica_id = session['clinica_id']
    peis_lista = []
    try:
        docs = db.collection('clinicas').document(clinica_id).collection('peis').order_by('identificacao_pei').stream()
        for doc in docs:
            pei = convert_doc_to_dict(doc)
            if pei:
                peis_lista.append(pei)
    except Exception as e:
        flash(f'Erro ao listar PEIs: {e}.', 'danger')
        print(f"Erro listar_peis: {e}")
    return render_template('peis.html', peis=peis_lista)

@app.route('/peis/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_pei():
    clinica_id = session['clinica_id']
    
    if request.method == 'POST':
        identificacao_pei = request.form['identificacao_pei'].strip()
        descricao_pei = request.form.get('descricao_pei', '').strip()
        data_inicio_str = request.form.get('data_inicio', '').strip()
        data_fim_str = request.form.get('data_fim', '').strip()
        metas_json = request.form.get('metas_json', '[]')
        
        if not identificacao_pei:
            flash('A identificação do PEI é obrigatória.', 'danger')
            return render_template('pei_form.html', pei=request.form, action_url=url_for('adicionar_pei'))

        try:
            data_inicio_dt = parse_date_input(data_inicio_str) if data_inicio_str else None
            data_fim_dt = parse_date_input(data_fim_str) if data_fim_str else None

            if data_inicio_str and not data_inicio_dt:
                flash('Formato de Data de Início inválido. Use AAAA-MM-DD ou DD/MM/YYYY.', 'danger')
                return render_template('pei_form.html', pei=request.form, action_url=url_for('adicionar_pei'))
            if data_fim_str and not data_fim_dt:
                flash('Formato de Data de Fim inválido. Use AAAA-MM-DD ou DD/MM/YYYY.', 'danger')
                return render_template('pei_form.html', pei=request.form, action_url=url_for('adicionar_pei'))
            
            try:
                metas_data = json.loads(metas_json)
                if not isinstance(metas_data, list):
                    raise ValueError("Metas deve ser uma lista JSON.")
            except (json.JSONDecodeError, ValueError) as e:
                flash(f'Formato inválido para as metas: {e}', 'danger')
                return render_template('pei_form.html', pei=request.form, action_url=url_for('adicionar_pei'))

            db.collection('clinicas').document(clinica_id).collection('peis').add({
                'identificacao_pei': identificacao_pei,
                'descricao_pei': descricao_pei,
                'data_inicio': data_inicio_dt,
                'data_fim': data_fim_dt,
                'metas': metas_data,
                'criado_em': firestore.SERVER_TIMESTAMP,
                'criado_por_uid': session.get('user_uid'),
                'criado_por_nome': session.get('user_name', session.get('user_email'))
            })
            flash('PEI adicionado com sucesso!', 'success')
            return redirect(url_for('listar_peis'))
        except Exception as e:
            flash(f'Erro ao adicionar PEI: {e}', 'danger')
            print(f"Erro adicionar_pei: {e}")
    
    return render_template('pei_form.html', pei=None, action_url=url_for('adicionar_pei'), page_title='Adicionar Novo PEI')



@app.route('/peis/editar/<string:pei_doc_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_pei(pei_doc_id):
    clinica_id = session['clinica_id']
    pei_ref = db.collection('clinicas').document(clinica_id).collection('peis').document(pei_doc_id)
    
    if request.method == 'POST':
        identificacao_pei = request.form['identificacao_pei'].strip()
        descricao_pei = request.form.get('descricao_pei', '').strip()
        data_inicio_str = request.form.get('data_inicio', '').strip()
        data_fim_str = request.form.get('data_fim', '').strip()
        metas_json = request.form.get('metas_json', '[]')

        if not identificacao_pei:
            flash('A identificação do PEI é obrigatória.', 'danger')
            return render_template('pei_form.html', pei=request.form, action_url=url_for('editar_pei', pei_doc_id=pei_doc_id))

        try:
            data_inicio_dt = parse_date_input(data_inicio_str) if data_inicio_str else None
            data_fim_dt = parse_date_input(data_fim_str) if data_fim_str else None

            if data_inicio_str and not data_inicio_dt:
                flash('Formato de Data de Início inválido. Use AAAA-MM-DD ou DD/MM/YYYY.', 'danger')
                return render_template('pei_form.html', pei=request.form, action_url=url_for('editar_pei', pei_doc_id=pei_doc_id))
            if data_fim_str and not data_fim_dt:
                flash('Formato de Data de Fim inválido. Use AAAA-MM-DD ou DD/MM/YYYY.', 'danger')
                return render_template('pei_form.html', pei=request.form, action_url=url_for('editar_pei', pei_doc_id=pei_doc_id))

            try:
                metas_data = json.loads(metas_json)
                if not isinstance(metas_data, list):
                    raise ValueError("Metas deve ser uma lista JSON.")
            except (json.JSONDecodeError, ValueError) as e:
                flash(f'Formato inválido para as metas: {e}', 'danger')
                return render_template('pei_form.html', pei=request.form, action_url=url_for('editar_pei', pei_doc_id=pei_doc_id))

            pei_ref.update({
                'identificacao_pei': identificacao_pei,
                'descricao_pei': descricao_pei,
                'data_inicio': data_inicio_dt,
                'data_fim': data_fim_dt,
                'metas': metas_data,
                'atualizado_em': firestore.SERVER_TIMESTAMP,
                'atualizado_por_uid': session.get('user_uid'),
                'atualizado_por_nome': session.get('user_name', session.get('user_email'))
            })
            flash('PEI atualizado com sucesso!', 'success')
            return redirect(url_for('listar_peis'))
        except Exception as e:
            flash(f'Erro ao atualizar PEI: {e}', 'danger')
            print(f"Erro editar_pei (POST): {e}")

    try:
        pei_doc = pei_ref.get()
        if pei_doc.exists:
            pei = pei_doc.to_dict()
            if pei:
                pei['id'] = pei_doc.id
                # CORREÇÃO APLICADA AQUI
                if pei.get('data_inicio') and isinstance(pei['data_inicio'], datetime.datetime):
                    pei['data_inicio'] = pei['data_inicio'].strftime('%Y-%m-%d')
                else:
                    pei['data_inicio'] = '' # Garante que o campo exista como string vazia

                if pei.get('data_fim') and isinstance(pei['data_fim'], datetime.datetime):
                    pei['data_fim'] = pei['data_fim'].strftime('%Y-%m-%d')
                else:
                    pei['data_fim'] = '' # Garante que o campo exista como string vazia
                
                if 'metas' in pei and isinstance(pei['metas'], list):
                    pei['metas_json'] = json.dumps(pei['metas'], indent=2)
                else:
                    pei['metas_json'] = '[]'
                
                return render_template('pei_form.html', pei=pei, action_url=url_for('editar_pei', pei_doc_id=pei_doc_id), page_title=f"Editar PEI: {pei.get('identificacao_pei', 'N/A')}")
        else:
            flash('PEI não encontrado.', 'danger')
            return redirect(url_for('listar_peis'))
    except Exception as e:
        flash(f'Erro ao carregar PEI para edição: {e}', 'danger')
        print(f"Erro editar_pei (GET): {e}")
        return redirect(url_for('listar_peis'))

@app.route('/peis/excluir/<string:pei_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_pei(pei_doc_id):
    clinica_id = session['clinica_id']
    try:
        # TODO: Check if this PEI is associated with any patient before deletion.
        db.collection('clinicas').document(clinica_id).collection('peis').document(pei_doc_id).delete()
        flash('PEI excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir PEI: {e}.', 'danger')
        print(f"Erro excluir_pei: {e}")
    return redirect(url_for('listar_peis'))

# Adicione esta nova rota ao final do seu arquivo app.py

@app.route('/api/peis/<string:pei_id>/meta/update', methods=['POST'])
@login_required
def update_pei_meta(pei_id):
    clinica_id = session['clinica_id']
    data = request.json
    meta_titulo = data.get('meta_titulo')
    action = data.get('action')

    if not all([meta_titulo, action]):
        return jsonify({'success': False, 'message': 'Dados incompletos para a atualização da meta.'}), 400

    try:
        pei_ref = db.collection('clinicas').document(clinica_id).collection('peis').document(pei_id)
        pei_doc = pei_ref.get()

        if not pei_doc.exists:
            return jsonify({'success': False, 'message': 'PEI não encontrado.'}), 404

        pei_data = pei_doc.to_dict()
        metas = pei_data.get('metas', [])
        meta_encontrada = False
        target_meta_index = -1

        for i, meta in enumerate(metas):
            if meta.get('titulo') == meta_titulo:
                meta_encontrada = True
                target_meta_index = i
                
                # Inicializa campos se não existirem para evitar erros
                if 'status' not in meta: meta['status'] = 'Não Iniciada'
                if 'tempo_total_gasto' not in meta: meta['tempo_total_gasto'] = 0
                if 'cronometro_inicio' not in meta: meta['cronometro_inicio'] = None

                if action == 'start_timer':
                    if meta['cronometro_inicio'] is None:
                        meta['cronometro_inicio'] = datetime.datetime.now(pytz.utc)
                        meta['status'] = 'Em Andamento'
                
                elif action == 'stop_timer':
                    if meta.get('cronometro_inicio'):
                        # Garante que o timestamp é um datetime antes de subtrair
                        inicio_ts = meta['cronometro_inicio']
                        if isinstance(inicio_ts, datetime.datetime):
                            inicio = inicio_ts
                        else: # Fallback para strings (embora o ideal seja sempre datetime)
                            inicio = pytz.utc.localize(datetime.datetime.fromisoformat(inicio_ts.replace('Z', '+00:00')))

                        fim = datetime.datetime.now(pytz.utc)
                        segundos_decorridos = (fim - inicio).total_seconds()
                        meta['tempo_total_gasto'] += round(segundos_decorridos)
                        meta['cronometro_inicio'] = None

                elif action == 'concluir':
                    if meta.get('cronometro_inicio'):
                        inicio_ts = meta['cronometro_inicio']
                        if isinstance(inicio_ts, datetime.datetime):
                           inicio = inicio_ts
                        else:
                           inicio = pytz.utc.localize(datetime.datetime.fromisoformat(inicio_ts.replace('Z', '+00:00')))
                        
                        fim = datetime.datetime.now(pytz.utc)
                        segundos_decorridos = (fim - inicio).total_seconds()
                        meta['tempo_total_gasto'] += round(segundos_decorridos)
                        meta['cronometro_inicio'] = None
                    
                    meta['status'] = 'Concluída'
                    if 'observacao' in data and data['observacao']:
                        meta['observacao_conclusao'] = data['observacao']

                elif action == 'reset':
                     meta['status'] = 'Não Iniciada'
                     meta['tempo_total_gasto'] = 0
                     meta['cronometro_inicio'] = None
                     if 'observacao_conclusao' in meta:
                         del meta['observacao_conclusao']


                metas[i] = meta
                break

        if not meta_encontrada:
            return jsonify({'success': False, 'message': 'Meta não encontrada.'}), 404

        pei_ref.update({'metas': metas})
        
      
        return jsonify({'success': True, 'updated_meta': metas[target_meta_index]})

    except Exception as e:
        print(f"Erro em update_pei_meta: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)), debug=True)