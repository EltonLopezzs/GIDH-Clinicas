import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth_admin
import datetime
import pytz
from collections import Counter
import ujson as json

from google.cloud.firestore_v1.base_query import FieldFilter


app = Flask(__name__)
app.secret_key = os.urandom(24)
CORS(app) # 2. INICIALIZAÇÃO DO CORS (ESSENCIAL PARA O LOGIN FUNCIONAR)

db = None
try:
    cred_path = os.path.join(os.path.dirname(__file__), 'serviceAccountKey.json')
    if not os.path.exists(cred_path):
        raise FileNotFoundError("Arquivo serviceAccountKey.json não encontrado na raiz do projeto.")

    cred = credentials.Certificate(cred_path)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
        print("🔥 Firebase Admin SDK inicializado pela primeira vez!")
    else:
        print("🔥 Firebase Admin SDK já estava inicializado.")
    db = firestore.client()
except Exception as e:
    print(f"🚨 ERRO CRÍTICO ao inicializar Firebase Admin SDK: {e}")

SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')


# Decorador personalizado para exigir que o utilizador esteja logado.
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Verifica se as chaves necessárias estão na sessão.
        if 'logged_in' not in session or 'clinica_id' not in session or 'user_uid' not in session:
            # Se não estiver logado, redireciona para a página de login.
            return redirect(url_for('login_page'))
        # Verifica se a conexão com o banco de dados está ativa.
        if not db:
            flash('Erro crítico: Conexão com o banco de dados falhou. Contate o suporte.', 'danger')
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# Decorador para exigir um papel de administrador.
def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Primeiro, garante que o utilizador esteja logado.
        if 'logged_in' not in session or 'clinica_id' not in session or 'user_uid' not in session:
            flash('Acesso não autorizado. Por favor, faça login.', 'danger')
            return redirect(url_for('login_page'))
        # Verifica se o papel do utilizador na sessão é 'admin'.
        if session.get('user_role') != 'admin':
            flash('Acesso negado: Você não tem permissões de administrador para esta ação.', 'danger')
            # Pode redirecionar para o dashboard ou uma página de erro.
            return redirect(url_for('index')) 
        return f(*args, **kwargs)
    return decorated_function

# --- ROTAS DE AUTENTICAÇÃO E SETUP ---
@app.route('/login', methods=['GET'])
def login_page():
    # Se o utilizador já estiver logado, redireciona para o dashboard.
    if 'logged_in' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/session-login', methods=['POST'])
def session_login():
    # Verifica se o banco de dados está inicializado.
    if not db:
        return jsonify({"success": False, "message": "Erro crítico no servidor (DB não inicializado)."}), 500

    # Obtém o ID Token do corpo da requisição JSON.
    id_token = request.json.get('idToken')
    if not id_token:
        return jsonify({"success": False, "message": "ID Token não fornecido."}), 400

    try:
        # Verifica o ID Token usando o Firebase Admin SDK.
        decoded_token = firebase_auth_admin.verify_id_token(id_token)
        uid_from_token = decoded_token['uid']
        email = decoded_token.get('email', '')

        # Busca o mapeamento do utilizador na coleção 'User'.
        mapeamento_ref = db.collection('User').document(uid_from_token.strip())
        mapeamento_doc = mapeamento_ref.get()

        if mapeamento_doc.exists:
            mapeamento_data = mapeamento_doc.to_dict()
            # Verifica se os dados essenciais estão presentes no mapeamento.
            if not mapeamento_data or 'clinica_id' not in mapeamento_data or 'role' not in mapeamento_data:
                return jsonify({"success": False, "message": "Configuração de utilizador incompleta. Contacte o administrador."}), 500

            # Define as variáveis de sessão para o utilizador logado.
            session['logged_in'] = True
            session['user_uid'] = uid_from_token
            session['user_email'] = email
            session['clinica_id'] = mapeamento_data['clinica_id']
            session['clinica_nome_display'] = mapeamento_data.get('nome_clinica_display', 'Clínica On')
            session['user_role'] = mapeamento_data['role'] # Armazena o papel do utilizador

            print(f"Utilizador {email} logado com sucesso. Papel: {session['user_role']}")
            return jsonify({"success": True, "message": "Login bem-sucedido!"})
        else:
            return jsonify({"success": False, "message": "Utilizador não autorizado ou não associado a uma clínica."}), 403

    except firebase_auth_admin.RevokedIdTokenError:
        return jsonify({"success": False, "message": "Token de ID revogado. Faça login novamente."}), 401
    except firebase_auth_admin.UserDisabledError:
        return jsonify({"success": False, "message": "Sua conta de utilizador foi desabilitada. Contacte o administrador."}), 403
    except firebase_auth_admin.InvalidIdTokenError:
        return jsonify({"success": False, "message": "Credenciais inválidas. Verifique seu e-mail e senha."}), 401
    except Exception as e:
        print(f"Erro na verificação do token/mapeamento: {type(e).__name__} - {e}")
        return jsonify({"success": False, "message": f"Erro no servidor durante o login: {str(e)}"}), 500

# Rota de configuração inicial para um super-admin associar um UID a uma clínica.
# Esta rota deve ser usada com extrema cautela e removida/protegida após o setup inicial.
@app.route('/setup-mapeamento-admin', methods=['GET', 'POST'])
def setup_mapeamento_admin():
    if not db: return "Firebase não inicializado", 500
    if request.method == 'POST':
        user_uid = request.form['user_uid'].strip()
        email_para_referencia = request.form['email_para_referencia'].strip().lower()
        clinica_id_associada = request.form['clinica_id_associada'].strip()
        nome_clinica_display = request.form['nome_clinica_display'].strip()
        user_role = request.form.get('user_role', 'medico').strip() # Permite definir o papel

        if not all([user_uid, email_para_referencia, clinica_id_associada, nome_clinica_display, user_role]):
            flash("Todos os campos são obrigatórios.", "danger")
        else:
            try:
                # Cria ou verifica a coleção da clínica.
                clinica_ref = db.collection('clinicas').document(clinica_id_associada)
                if not clinica_ref.get().exists:
                    clinica_ref.set({
                        'nome_oficial': nome_clinica_display,
                        'criada_em_dashboard_setup': firestore.SERVER_TIMESTAMP
                    })
                # Mapeia o utilizador ao papel e à clínica.
                db.collection('User').document(user_uid).set({
                    'email': email_para_referencia,
                    'clinica_id': clinica_id_associada,
                    'nome_clinica_display': nome_clinica_display,
                    'role': user_role, # Salva o papel do utilizador
                    'associado_em': firestore.SERVER_TIMESTAMP
                })
                flash(f'Utilizador UID {user_uid} ({user_role}) associado à clínica {nome_clinica_display} ({clinica_id_associada})! Você pode agora tentar <a href="{url_for("login_page")}">fazer login</a>.', 'success')
            except Exception as e:
                flash(f'Erro ao associar utilizador: {e}', 'danger')
                print(f"Erro em setup_mapeamento_admin: {e}")
        # Redireciona para a própria rota setup-mapeamento-admin para exibir a flash message
        return redirect(url_for('setup_mapeamento_admin'))
    
    # Este é o HTML que será renderizado no GET da rota /setup-mapeamento-admin
    # Usamos render_template_string para que o Jinja2 possa processar as variáveis como url_for
    return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Associar Admin Firebase a Clínica</title>
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
        <h2>Associar Utilizador Firebase Auth a uma Clínica</h2>
        <p><b>Passo 1:</b> Crie o utilizador (com e-mail/senha) na consola do Firebase > Authentication.</p>
        <p><b>Passo 2:</b> Obtenha o UID do utilizador criado (ex: no separador "Utilizadores" do Firebase Auth).</p>
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
            UID do Utilizador (Firebase Auth): <input type="text" name="user_uid" required size="40" value="{{ request.form.user_uid if 'user_uid' in request.form else '' }}"><br><br>
            E-mail do Utilizador (para referência): <input type="email" name="email_para_referencia" required size="40" value="{{ request.form.email_para_referencia if 'email_para_referencia' in request.form else '' }}"><br><br>
            ID da Clínica (Ex: clinicaSaoJudas): <input type="text" name="clinica_id_associada" required size="40" value="{{ request.form.clinica_id_associada if 'clinica_id_associada' in request.form else '' }}"><br><br>
            Nome de Exibição da Clínica: <input type="text" name="nome_clinica_display" required size="40" value="{{ request.form.nome_clinica_display if 'nome_clinica_display' in request.form else '' }}"><br><br>
            Papel do Utilizador: 
            <select name="user_role" required>
                <option value="admin" {% if request.form.user_role == 'admin' %}selected{% endif %}>Administrador</option>
                <option value="medico" {% if request.form.user_role == 'medico' %}selected{% endif %}>Médico</option>
            </select><br><br>
            <button type="submit">Associar Utilizador à Clínica</button>
        </form>
        <p><a href="{{ url_for('login_page') }}">Ir para Login</a></p>
        </body></html>
    """)


@app.route('/logout', methods=['POST'])
def logout():
    session.clear() # Limpa todas as variáveis de sessão.
    return jsonify({"success": True, "message": "Sessão do servidor limpa."})

# --- ROTA PRINCIPAL (DASHBOARD) ---
@app.route('/')
@login_required
def index():
    clinica_id = session['clinica_id']
    agendamentos_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos')
    servicos_procedimentos_ref = db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos')
    current_year = datetime.datetime.now(SAO_PAULO_TZ).year

    # Mapa para armazenar informações de serviços/procedimentos (nome e preço)
    servicos_procedimentos_map = {}
    try:
        servicos_docs_stream = servicos_procedimentos_ref.stream()
        for serv_doc in servicos_docs_stream:
            serv_data_dict = serv_doc.to_dict()
            if serv_data_dict and 'preco_sugerido' in serv_data_dict and 'nome' in serv_data_dict:
                servicos_procedimentos_map[serv_doc.id] = {
                    'nome': serv_data_dict.get('nome', 'Serviço/Procedimento Desconhecido'),
                    'preco': float(serv_data_dict.get('preco_sugerido', 0))
                }
    except Exception as e:
        print(f"Erro CRÍTICO ao buscar serviços/procedimentos para dashboard: {e}")
        flash("Erro crítico ao carregar dados de serviços/procedimentos. O dashboard pode não exibir totais corretos.", "danger")

    hoje_dt = datetime.datetime.now(SAO_PAULO_TZ)
    hoje_inicio_dt = hoje_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    
    count_hoje, receita_hoje = 0, 0.0
    count_semana, receita_semana = 0, 0.0
    count_mes, receita_mes = 0, 0.0

    # Calcula o início e fim da semana atual.
    inicio_semana_dt = hoje_inicio_dt - datetime.timedelta(days=hoje_dt.weekday())
    fim_semana_dt = inicio_semana_dt + datetime.timedelta(days=7)
    
    # Calcula o início e fim do mês atual.
    inicio_mes_dt = hoje_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if inicio_mes_dt.month == 12:
        fim_mes_dt = inicio_mes_dt.replace(year=inicio_mes_dt.year + 1, month=1, day=1)
    else:
        fim_mes_dt = inicio_mes_dt.replace(month=inicio_mes_dt.month + 1, day=1)

    agendamentos_para_analise = []
    try:
        # Consulta agendamentos confirmados ou concluídos no mês atual para análise.
        query_geral_mes = agendamentos_ref.where(filter=FieldFilter('status', 'in', ['confirmado', 'concluido'])) \
                                          .where(filter=FieldFilter('data_agendamento_ts', '>=', inicio_mes_dt)) \
                                          .where(filter=FieldFilter('data_agendamento_ts', '<', fim_mes_dt)).stream()
        
        for doc in query_geral_mes:
            ag_data = doc.to_dict()
            if not ag_data: continue

            ag_timestamp_firestore = ag_data.get('data_agendamento_ts')
            if isinstance(ag_timestamp_firestore, datetime.datetime):
                agendamentos_para_analise.append(ag_data)

    except Exception as e:
        print(f"Erro query geral para dashboard: {e}. Verifique os índices do Firestore.")
        flash("Erro ao calcular estatísticas do dashboard. Verifique seus índices do Firestore.", "danger")

    # Inicializa dados para os gráficos.
    agendamentos_por_dia_semana = [0] * 7 # 0=Segunda, ..., 6=Domingo (Python's weekday)
    labels_dias_semana = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    contagem_servicos_semana = Counter()

    for ag_data in agendamentos_para_analise:
        ag_timestamp_sp = ag_data.get('data_agendamento_ts').astimezone(SAO_PAULO_TZ)
        # Usa 'servico_procedimento_id' e 'servicos_procedimentos_map'
        preco_servico_atual = float(servicos_procedimentos_map.get(ag_data.get('servico_procedimento_id'), {}).get('preco', 0))
        
        count_mes += 1
        receita_mes += preco_servico_atual

        if inicio_semana_dt <= ag_timestamp_sp < fim_semana_dt:
            count_semana += 1
            receita_semana += preco_servico_atual
            
            dia_da_semana_num = ag_timestamp_sp.weekday()
            agendamentos_por_dia_semana[dia_da_semana_num] += 1

            servico_procedimento_id = ag_data.get('servico_procedimento_id')
            if servico_procedimento_id:
                nome_servico = servicos_procedimentos_map.get(servico_procedimento_id, {}).get('nome', 'Desconhecido')
                contagem_servicos_semana[nome_servico] += 1

        if ag_timestamp_sp.date() == hoje_dt.date():
            count_hoje +=1
            receita_hoje += preco_servico_atual

    hoje_data = {'count': count_hoje, 'receita': receita_hoje}
    semana_data = {'count': count_semana, 'receita': receita_semana}
    mes_data = {'count': count_mes, 'receita': receita_mes}

    dados_desempenho_semana = {
        'labels': labels_dias_semana,
        'valores': agendamentos_por_dia_semana
    }

    servicos_populares_comuns = contagem_servicos_semana.most_common(5)
    dados_servicos_populares = {
        'labels': [item[0] for item in servicos_populares_comuns] or ['Nenhum Agendamento na Semana'],
        'valores': [item[1] for item in servicos_populares_comuns] or [0]
    }

    proximos_agendamentos_lista = []
    try:
        # Consulta os próximos agendamentos confirmados (até 5).
        query_proximos = agendamentos_ref.where(filter=FieldFilter('status', '==', 'confirmado')) \
                                         .where(filter=FieldFilter('data_agendamento_ts', '>=', hoje_inicio_dt)) \
                                         .order_by('data_agendamento_ts') \
                                         .limit(5).stream()
        for doc in query_proximos:
            ag_data = doc.to_dict()
            if not ag_data: continue

            # Obtém informações do serviço/procedimento.
            servico_info = servicos_procedimentos_map.get(ag_data.get('servico_procedimento_id'), {'nome': 'N/A', 'preco': 0.0})
            data_fmt = "N/A"
            if ag_data.get('data_agendamento'):
                try:
                    data_fmt = datetime.datetime.strptime(ag_data['data_agendamento'], '%Y-%m-%d').strftime('%d/%m/%Y')
                except ValueError:
                    data_fmt = ag_data['data_agendamento']

            proximos_agendamentos_lista.append({
                'data_agendamento': data_fmt,
                'hora_agendamento': ag_data.get('hora_agendamento', "N/A"),
                'cliente_nome': ag_data.get('paciente_nome', "N/A"), # Alterado de 'cliente_nome' para 'paciente_nome'
                'profissional_nome': ag_data.get('profissional_nome', "N/A"), # Alterado de 'barbeiro_nome' para 'profissional_nome'
                'servico_procedimento_nome': servico_info.get('nome'), # Alterado para 'servico_procedimento_nome'
                'preco': float(servico_info.get('preco', 0)),
                'status': ag_data.get('status', "N/A")
            })
    except Exception as e:
        print(f"Erro CRÍTICO ao buscar próximos agendamentos: {e}. Verifique os índices.")
        flash("Erro ao carregar próximos agendamentos.", "danger")

    return render_template('dashboard.html', hoje_data=hoje_data, semana_data=semana_data,
                           mes_data=mes_data, proximos_agendamentos=proximos_agendamentos_lista,
                           nome_clinica=session.get('clinica_nome_display', 'Sua Clínica'), # Alterado para 'nome_clinica'
                           current_year=current_year,
                           dados_desempenho_semana=dados_desempenho_semana,
                           dados_servicos_populares=dados_servicos_populares)


# --- ROTAS DE UTILIZADORES (ADMINISTRADORES E MÉDICOS) ---
@app.route('/usuarios')
@login_required
@admin_required # Apenas admins podem gerenciar utilizadores
def listar_usuarios():
    clinica_id = session['clinica_id']
    usuarios_ref = db.collection('User')
    usuarios_lista = []
    try:
        # Filtra utilizadores pela clinica_id e ordena por email
        docs = usuarios_ref.where(filter=FieldFilter('clinica_id', '==', clinica_id)).order_by('email').stream()
        for doc in docs:
            user_data = doc.to_dict()
            if user_data:
                user_data['uid'] = doc.id # UID é o ID do documento
                usuarios_lista.append(user_data)
    except Exception as e:
        flash(f'Erro ao listar utilizadores: {e}.', 'danger')
        print(f"Erro listar_usuarios: {e}")
    return render_template('usuarios.html', usuarios=usuarios_lista)

@app.route('/usuarios/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_usuario():
    clinica_id = session['clinica_id']
    if request.method == 'POST':
        email = request.form['email'].strip()
        password = request.form['password']
        role = request.form['role']
        nome_completo = request.form.get('nome_completo', '').strip()
        crm_ou_registro = request.form.get('crm_ou_registro', '').strip()

        if not all([email, password, role]):
            flash('E-mail, senha e papel são obrigatórios.', 'danger')
            return render_template('usuario_form.html', action_url=url_for('adicionar_usuario'), page_title="Adicionar Novo Utilizador", roles=['admin', 'medico'])

        try:
            # Cria o utilizador no Firebase Authentication
            user = firebase_auth_admin.create_user(email=email, password=password)
            user_uid = user.uid

            # Salva o mapeamento na coleção 'User' no Firestore
            db.collection('User').document(user_uid).set({
                'email': email,
                'clinica_id': clinica_id,
                'nome_clinica_display': session.get('clinica_nome_display', 'Clínica On'),
                'role': role,
                'nome_completo': nome_completo if nome_completo else None,
                'crm_ou_registro': crm_ou_registro if crm_ou_registro else None,
                'associado_em': firestore.SERVER_TIMESTAMP
            })

            # Se o utilizador for um médico, pode ser interessante criar um registo em 'profissionais'
            if role == 'medico':
                db.collection('clinicas').document(clinica_id).collection('profissionais').add({
                    'nome': nome_completo,
                    'email': email,
                    'crm_ou_registro': crm_ou_registro,
                    'user_uid': user_uid, # Referência ao UID do utilizador criado
                    'ativo': True, # Por padrão, médicos criados estão ativos
                    'criado_em': firestore.SERVER_TIMESTAMP
                })

            flash(f'Utilizador {email} ({role}) adicionado com sucesso!', 'success')
            return redirect(url_for('listar_usuarios'))
        except firebase_admin.auth.EmailAlreadyExistsError:
            flash('O e-mail fornecido já está em uso por outro utilizador.', 'danger')
        except Exception as e:
            flash(f'Erro ao adicionar utilizador: {e}', 'danger')
            print(f"Erro adicionar_usuario: {e}")
    
    return render_template('usuario_form.html', action_url=url_for('adicionar_usuario'), page_title="Adicionar Novo Utilizador", roles=['admin', 'medico'])

@app.route('/usuarios/editar/<string:user_uid>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_usuario(user_uid):
    clinica_id = session['clinica_id']
    user_map_ref = db.collection('User').document(user_uid)

    if request.method == 'POST':
        email = request.form['email'].strip()
        role = request.form['role']
        nome_completo = request.form.get('nome_completo', '').strip()
        crm_ou_registro = request.form.get('crm_ou_registro', '').strip()
        
        try:
            # Atualiza o registo do utilizador no Firestore
            user_map_ref.update({
                'email': email,
                'role': role,
                'nome_completo': nome_completo if nome_completo else None,
                'crm_ou_registro': crm_ou_registro if crm_ou_registro else None,
                'atualizado_em': firestore.SERVER_TIMESTAMP
            })

            # Atualiza o email no Firebase Auth (se diferente)
            firebase_auth_user = firebase_auth_admin.get_user(user_uid)
            if firebase_auth_user.email != email:
                firebase_auth_admin.update_user(user_uid, email=email)
            
            # Se for um médico, atualiza o registo correspondente em 'profissionais'
            if role == 'medico':
                profissionais_ref = db.collection('clinicas').document(clinica_id).collection('profissionais')
                prof_query = profissionais_ref.where(filter=FieldFilter('user_uid', '==', user_uid)).limit(1).stream()
                prof_doc = next(prof_query, None)
                if prof_doc:
                    profissionais_ref.document(prof_doc.id).update({
                        'nome': nome_completo,
                        'email': email,
                        'crm_ou_registro': crm_ou_registro,
                        'atualizado_em': firestore.SERVER_TIMESTAMP
                    })
                else: # Se não encontrou, cria (caso tenha mudado de role ou sido criado sem associar)
                     profissionais_ref.add({
                        'nome': nome_completo,
                        'email': email,
                        'crm_ou_registro': crm_ou_registro,
                        'user_uid': user_uid,
                        'ativo': True,
                        'criado_em': firestore.SERVER_TIMESTAMP
                    })

            flash('Utilizador atualizado com sucesso!', 'success')
            return redirect(url_for('listar_usuarios'))
        except firebase_admin.auth.EmailAlreadyExistsError:
            flash('O e-mail fornecido já está em uso por outro utilizador.', 'danger')
        except Exception as e:
            flash(f'Erro ao atualizar utilizador: {e}', 'danger')
            print(f"Erro editar_usuario (POST): {e}")

    try:
        user_map_doc = user_map_ref.get()
        if user_map_doc.exists:
            user_data = user_map_doc.to_dict()
            user_data['uid'] = user_map_doc.id
            return render_template('usuario_form.html', user=user_data, action_url=url_for('editar_usuario', user_uid=user_uid), page_title=f"Editar Utilizador: {user_data.get('nome_completo') or user_data.get('email')}", roles=['admin', 'medico'])
        else:
            flash('Utilizador não encontrado.', 'danger')
            return redirect(url_for('listar_usuarios'))
    except Exception as e:
        flash(f'Erro ao carregar utilizador para edição: {e}', 'danger')
        print(f"Erro editar_usuario (GET): {e}")
        return redirect(url_for('listar_usuarios'))


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
            new_status_firebase = not current_status_firebase # Se está disabled, ativa; se não está, desativa.

            firebase_auth_admin.update_user(user_uid, disabled=new_status_firebase)
            
            # Se for um médico, também atualiza o status em 'profissionais'
            if user_data.get('role') == 'medico':
                profissionais_ref = db.collection('clinicas').document(clinica_id).collection('profissionais')
                prof_query = profissionais_ref.where(filter=FieldFilter('user_uid', '==', user_uid)).limit(1).stream()
                prof_doc = next(prof_query, None)
                if prof_doc:
                    profissionais_ref.document(prof_doc.id).update({
                        'ativo': not new_status_firebase, # Inverte, pois 'disabled' é o oposto de 'ativo'
                        'atualizado_em': firestore.SERVER_TIMESTAMP
                    })

            flash(f'Utilizador {user_data.get("email")} {"ativado" if not new_status_firebase else "desativado"} com sucesso!', 'success')
        else:
            flash('Utilizador não encontrado no mapeamento.', 'danger')
    except firebase_admin.auth.UserNotFoundError:
        flash('Utilizador não encontrado no Firebase Authentication.', 'danger')
    except Exception as e:
        flash(f'Erro ao alterar status do utilizador: {e}', 'danger')
        print(f"Erro ativar_desativar_usuario: {e}")
    return redirect(url_for('listar_usuarios'))

# --- ROTAS DE PROFISSIONAIS (ANTIGOS BARBEIROS) ---
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
        print(f"Erro listar_profissionais: {e}")
    return render_template('profissionais.html', profissionais=profissionais_lista) # Renomeado para profissionais.html

@app.route('/profissionais/novo', methods=['GET', 'POST'])
@login_required
@admin_required # Apenas admins podem adicionar profissionais diretamente
def adicionar_profissional():
    clinica_id = session['clinica_id']
    if request.method == 'POST':
        nome = request.form['nome']
        telefone = request.form.get('telefone')
        email_profissional = request.form.get('email_profissional') # Novo campo de email para o profissional
        crm_ou_registro = request.form.get('crm_ou_registro') # Novo campo
        ativo = 'ativo' in request.form
        try:
            if telefone and not telefone.isdigit():
                flash('Telefone deve conter apenas números.', 'warning')
                return render_template('profissional_form.html', profissional=request.form, action_url=url_for('adicionar_profissional'))

            db.collection('clinicas').document(clinica_id).collection('profissionais').add({
                'nome': nome,
                'telefone': telefone if telefone else None,
                'email': email_profissional if email_profissional else None, # Salva o email
                'crm_ou_registro': crm_ou_registro if crm_ou_registro else None, # Salva CRM/registro
                'ativo': ativo,
                'criado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Profissional adicionado com sucesso!', 'success')
            return redirect(url_for('listar_profissionais'))
        except Exception as e:
            flash(f'Erro ao adicionar profissional: {e}', 'danger')
            print(f"Erro adicionar_profissional: {e}")
    return render_template('profissional_form.html', profissional=None, action_url=url_for('adicionar_profissional')) # Renomeado para profissional_form.html


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
                flash('Telefone deve conter apenas números.', 'warning')
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
            print(f"Erro editar_profissional (POST): {e}")

    try:
        profissional_doc = profissional_ref.get()
        if profissional_doc.exists:
            profissional = profissional_doc.to_dict()
            profissional['id'] = profissional_doc.id
            return render_template('profissional_form.html', profissional=profissional, action_url=url_for('editar_profissional', profissional_doc_id=profissional_doc_id))
        else:
            flash('Profissional não encontrado.', 'danger')
            return redirect(url_for('listar_profissionais'))
    except Exception as e:
        flash(f'Erro ao carregar profissional para edição: {e}', 'danger')
        print(f"Erro editar_profissional (GET): {e}")
        return redirect(url_for('listar_profissionais'))

@app.route('/profissionais/ativar_desativar/<string:profissional_doc_id>', methods=['POST'])
@login_required
@admin_required # Apenas admins podem ativar/desativar profissionais
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
        flash(f'Erro ao alterar status do profissional: {e}', 'danger')
        print(f"Erro ativar_desativar_profissional: {e}")
    return redirect(url_for('listar_profissionais'))

# --- ROTAS DE PACIENTES (NOVO) ---
@app.route('/pacientes')
@login_required
def listar_pacientes():
    clinica_id = session['clinica_id']
    pacientes_ref = db.collection('clinicas').document(clinica_id).collection('pacientes')
    pacientes_lista = []
    try:
        # Permite buscar por nome ou telefone
        search_query = request.args.get('search', '').strip()
        query = pacientes_ref.order_by('nome')

        if search_query:
            # Firestore não suporta 'LIKE' diretamente, então fazemos um range search.
            # Para buscar por nome
            query_nome = query.where(filter=FieldFilter('nome', '>=', search_query))\
                                .where(filter=FieldFilter('nome', '<=', search_query + '\uf8ff'))
            # Para buscar por telefone (se for um campo de texto)
            query_telefone = pacientes_ref.order_by('contato_telefone')\
                                         .where(filter=FieldFilter('contato_telefone', '>=', search_query))\
                                         .where(filter=FieldFilter('contato_telefone', '<=', search_query + '\uf8ff'))
            
            # Executa ambas as queries e combina os resultados (removendo duplicados)
            pacientes_set = set()
            for doc in query_nome.stream():
                paciente_data = doc.to_dict()
                if paciente_data:
                    paciente_data['id'] = doc.id
                    pacientes_set.add(json.dumps(paciente_data, sort_keys=True))
            
            for doc in query_telefone.stream():
                paciente_data = doc.to_dict()
                if paciente_data:
                    paciente_data['id'] = doc.id
                    pacientes_set.add(json.dumps(paciente_data, sort_keys=True))
            
            pacientes_lista = [json.loads(p) for p in pacientes_set]
            pacientes_lista.sort(key=lambda x: x.get('nome', '')) # Garante ordem após combinar
        else:
            # Se não houver busca, lista todos
            docs = query.stream()
            for doc in docs:
                paciente = doc.to_dict()
                if paciente:
                    paciente['id'] = doc.id
                    pacientes_lista.append(paciente)

    except Exception as e:
        flash(f'Erro ao listar pacientes: {e}. Verifique seus índices do Firestore.', 'danger')
        print(f"Erro listar_pacientes: {e}")
    return render_template('pacientes.html', pacientes=pacientes_lista, search_query=search_query)

@app.route('/pacientes/novo', methods=['GET', 'POST'])
@login_required
def adicionar_paciente():
    clinica_id = session['clinica_id']
    
    # Carregar convênios para o formulário
    convenios_lista = []
    try:
        convenios_docs = db.collection('clinicas').document(clinica_id).collection('convenios').order_by('nome').stream()
        for doc in convenios_docs:
            conv_data = doc.to_dict()
            if conv_data:
                convenios_lista.append({'id': doc.id, 'nome': conv_data.get('nome', doc.id)})
    except Exception as e:
        flash('Erro ao carregar convênios.', 'danger')
        print(f"Erro carregar convênios (adicionar_paciente GET): {e}")

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

        # Endereço
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
            # Converte data de nascimento para objeto datetime se fornecida
            data_nascimento_dt = None
            if data_nascimento:
                try:
                    data_nascimento_dt = datetime.datetime.strptime(data_nascimento, '%Y-%m-%d').date()
                except ValueError:
                    flash('Formato de data de nascimento inválido. Use AAAA-MM-DD.', 'danger')
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
            print(f"Erro adicionar_paciente: {e}")
    
    return render_template('paciente_form.html', paciente=None, action_url=url_for('adicionar_paciente'), convenios=convenios_lista)

@app.route('/pacientes/editar/<string:paciente_doc_id>', methods=['GET', 'POST'])
@login_required
def editar_paciente(paciente_doc_id):
    clinica_id = session['clinica_id']
    paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
    
    # Carregar convênios para o formulário
    convenios_lista = []
    try:
        convenios_docs = db.collection('clinicas').document(clinica_id).collection('convenios').order_by('nome').stream()
        for doc in convenios_docs:
            conv_data = doc.to_dict()
            if conv_data:
                convenios_lista.append({'id': doc.id, 'nome': conv_data.get('nome', doc.id)})
    except Exception as e:
        flash('Erro ao carregar convênios.', 'danger')
        print(f"Erro carregar convênios (editar_paciente GET): {e}")

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

        # Endereço
        cep = request.form.get('cep', '').strip()
        logradouro = request.form.get('logradouro', '').strip()
        numero = request.form.get('numero', '').strip()
        complemento = request.form.get('complemento', '').strip()
        bairro = request.form.get('bairro', '').strip()
        cidade = request.form.get('cidade', '').strip()
        estado = request.form.get('estado', '').strip()

        if not nome:
            flash('O nome do paciente é obrigatório.', 'danger')
            return render_template('paciente_form.html', paciente=request.form, action_url=url_for('editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista)

        try:
            data_nascimento_dt = None
            if data_nascimento:
                try:
                    data_nascimento_dt = datetime.datetime.strptime(data_nascimento, '%Y-%m-%d').date()
                except ValueError:
                    flash('Formato de data de nascimento inválido. Use AAAA-MM-DD.', 'danger')
                    return render_template('paciente_form.html', paciente=request.form, action_url=url_for('editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista)

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
                'atualizado_em': firestore.SERVER_TIMESTAMP
            }
            
            paciente_ref.update(paciente_data_update)
            flash('Paciente atualizado com sucesso!', 'success')
            return redirect(url_for('listar_pacientes'))
        except Exception as e:
            flash(f'Erro ao atualizar paciente: {e}', 'danger')
            print(f"Erro editar_paciente (POST): {e}")

    try:
        paciente_doc = paciente_ref.get()
        if paciente_doc.exists:
            paciente = paciente_doc.to_dict()
            paciente['id'] = paciente_doc.id
            # Formata a data de nascimento para o campo input type="date"
            if paciente.get('data_nascimento') and isinstance(paciente['data_nascimento'], datetime.date):
                paciente['data_nascimento'] = paciente['data_nascimento'].strftime('%Y-%m-%d')
            else: # Se for um timestamp do Firestore, converte para datetime.date
                if isinstance(paciente.get('data_nascimento'), datetime.datetime):
                    paciente['data_nascimento'] = paciente['data_nascimento'].date().strftime('%Y-%m-%d')

            return render_template('paciente_form.html', paciente=paciente, action_url=url_for('editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista)
        else:
            flash('Paciente não encontrado.', 'danger')
            return redirect(url_for('listar_pacientes'))
    except Exception as e:
        flash(f'Erro ao carregar paciente para edição: {e}', 'danger')
        print(f"Erro editar_paciente (GET): {e}")
        return redirect(url_for('listar_pacientes'))

# --- ROTAS DE SERVIÇOS/PROCEDIMENTOS (ANTIGOS SERVIÇOS) ---
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
        print(f"Erro listar_servicos_procedimentos: {e}")
    return render_template('servicos_procedimentos.html', servicos=servicos_procedimentos_lista) # Renomeado

@app.route('/servicos_procedimentos/novo', methods=['GET', 'POST'])
@login_required
@admin_required # Admins e talvez médicos possam criar/editar serviços? Definir.
def adicionar_servico_procedimento():
    clinica_id = session['clinica_id']
    if request.method == 'POST':
        nome = request.form['nome']
        tipo = request.form['tipo'] # 'Serviço' ou 'Procedimento'
        try:
            duracao_minutos = int(request.form['duracao_minutos'])
            preco_sugerido = float(request.form['preco'].replace(',', '.'))
            db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').add({
                'nome': nome,
                'tipo': tipo,
                'duracao_minutos': duracao_minutos,
                'preco_sugerido': preco_sugerido, # Alterado para preco_sugerido
                'criado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Serviço/Procedimento adicionado com sucesso!', 'success')
            return redirect(url_for('listar_servicos_procedimentos'))
        except ValueError:
            flash('Duração e Preço devem ser números válidos.', 'danger')
        except Exception as e:
            flash(f'Erro ao adicionar serviço/procedimento: {e}', 'danger')
            print(f"Erro adicionar_servico_procedimento: {e}")
    return render_template('servico_procedimento_form.html', servico=None, action_url=url_for('adicionar_servico_procedimento')) # Renomeado


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
            flash('Duração e Preço devem ser números válidos.', 'danger')
        except Exception as e:
            flash(f'Erro ao atualizar serviço/procedimento: {e}', 'danger')
            print(f"Erro editar_servico_procedimento (POST): {e}")
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
        print(f"Erro editar_servico_procedimento (GET): {e}")
        return redirect(url_for('listar_servicos_procedimentos'))

@app.route('/servicos_procedimentos/excluir/<string:servico_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_servico_procedimento(servico_doc_id):
    clinica_id = session['clinica_id']
    try:
        agendamentos_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos')
        # Verifica se há agendamentos associados a este serviço/procedimento
        agendamentos_com_servico = agendamentos_ref.where(filter=FieldFilter('servico_procedimento_id', '==', servico_doc_id)).limit(1).get()
        if len(agendamentos_com_servico) > 0:
            flash('Este serviço/procedimento não pode ser excluído pois está associado a um ou mais agendamentos.', 'danger')
            return redirect(url_for('listar_servicos_procedimentos'))

        db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_doc_id).delete()
        flash('Serviço/Procedimento excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir serviço/procedimento: {e}.', 'danger')
        print(f"Erro excluir_servico_procedimento: {e}")
    return redirect(url_for('listar_servicos_procedimentos'))

# --- ROTAS DE CONVÊNIOS (NOVO) ---
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
        print(f"Erro listar_convenios: {e}")
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
            print(f"Erro adicionar_convenio: {e}")
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
            print(f"Erro editar_convenio (POST): {e}")

    try:
        convenio_doc = convenio_ref.get()
        if convenio_doc.exists:
            convenio = convenio_doc.to_dict()
            convenio['id'] = convenio_doc.id
            return render_template('convenio_form.html', convenio=convenio, action_url=url_for('editar_convenio', convenio_doc_id=convenio_doc_id))
        else:
            flash('Convênio não encontrado.', 'danger')
            return redirect(url_for('listar_convenios'))
    except Exception as e:
        flash(f'Erro ao carregar convênio para edição: {e}', 'danger')
        print(f"Erro editar_convenio (GET): {e}")
        return redirect(url_for('listar_convenios'))

@app.route('/convenios/excluir/<string:convenio_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_convenio(convenio_doc_id):
    clinica_id = session['clinica_id']
    try:
        pacientes_ref = db.collection('clinicas').document(clinica_id).collection('pacientes')
        # Verifica se há pacientes associados a este convênio
        pacientes_com_convenio = pacientes_ref.where(filter=FieldFilter('convenio_id', '==', convenio_doc_id)).limit(1).get()
        if len(pacientes_com_convenio) > 0:
            flash('Este convênio não pode ser excluído pois está associado a um ou mais pacientes.', 'danger')
            return redirect(url_for('listar_convenios'))
            
        db.collection('clinicas').document(clinica_id).collection('convenios').document(convenio_doc_id).delete()
        flash('Convênio excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir convênio: {e}.', 'danger')
        print(f"Erro excluir_convenio: {e}")
    return redirect(url_for('listar_convenios'))

# --- ROTAS DE HORÁRIOS ---
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
                    horario['profissional_id_fk'] = profissional_id_atual # Alterado
                    horario['profissional_nome'] = profissional_nome_atual # Alterado
                    
                    dias_semana_map = {0: 'Domingo', 1: 'Segunda', 2: 'Terça', 3: 'Quarta', 4: 'Quinta', 5: 'Sexta', 6: 'Sábado'}
                    horario['dia_semana_nome'] = dias_semana_map.get(horario.get('dia_semana'), 'N/A')
                    
                    todos_horarios_formatados.append(horario)
    
    except Exception as e:
        flash(f'Erro ao listar horários: {e}.', 'danger')
        print(f"Erro listar_horarios: {e}")
    
    return render_template('horarios.html', horarios=todos_horarios_formatados, current_year=datetime.datetime.now(SAO_PAULO_TZ).year)


@app.route('/horarios/novo', methods=['GET', 'POST'])
@login_required
@admin_required # Apenas admins podem adicionar horários diretamente (ou o próprio médico para si)
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
        print(f"Erro carregar profissionais (adicionar_horario GET): {e}")

    dias_semana_map = {0: 'Domingo', 1: 'Segunda', 2: 'Terça', 3: 'Quarta', 4: 'Quinta', 5: 'Sexta', 6: 'Sábado'}

    if request.method == 'POST':
        try:
            profissional_id_selecionado = request.form['profissional_id'] # Alterado
            dia_semana = int(request.form['dia_semana'])
            hora_inicio = request.form['hora_inicio']
            hora_fim = request.form['hora_fim']
            intervalo_minutos_str = request.form.get('intervalo_minutos')
            intervalo_minutos = int(intervalo_minutos_str) if intervalo_minutos_str and intervalo_minutos_str.isdigit() else None
            ativo = 'ativo' in request.form 

            if not profissional_id_selecionado:
                flash('Por favor, selecione um profissional.', 'warning')
            elif hora_inicio >= hora_fim:
                flash('Hora de início deve ser anterior à hora de fim.', 'warning')
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

                db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_id_selecionado).collection('horarios_disponiveis').add(horario_data) # Alterado
                flash('Horário adicionado com sucesso!', 'success')
                return redirect(url_for('listar_horarios'))
        except ValueError:
            flash('Valores numéricos inválidos para dia ou intervalo.', 'danger')
        except Exception as e:
            flash(f'Erro ao adicionar horário: {e}', 'danger')
            print(f"Erro adicionar_horario (POST): {e}")
            
    return render_template('horario_form.html', 
                           profissionais=profissionais_ativos_lista, # Alterado
                           dias_semana=dias_semana_map, 
                           horario=None, 
                           action_url=url_for('adicionar_horario'),
                           page_title='Adicionar Novo Horário',
                           current_year=datetime.datetime.now(SAO_PAULO_TZ).year)


@app.route('/profissionais/<string:profissional_doc_id>/horarios/editar/<string:horario_doc_id>', methods=['GET', 'POST']) # Alterado
@login_required
def editar_horario(profissional_doc_id, horario_doc_id): # Alterado
    clinica_id = session['clinica_id']
    horario_ref = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).collection('horarios_disponiveis').document(horario_doc_id) # Alterado
    
    profissionais_ativos_lista = [] # Alterado
    try:
        profissionais_docs = db.collection('clinicas').document(clinica_id).collection('profissionais').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream() # Alterado
        for doc in profissionais_docs: # Alterado
            p_data = doc.to_dict() # Alterado
            if p_data: profissionais_ativos_lista.append({'id': doc.id, 'nome': p_data.get('nome', doc.id)}) # Alterado
    except Exception as e:
        flash('Erro ao carregar profissionais ativos para o formulário.', 'danger') # Alterado
        print(f"Erro carregar profissionais (editar_horario GET): {e}") # Alterado

    dias_semana_map = {0: 'Domingo', 1: 'Segunda', 2: 'Terça', 3: 'Quarta', 4: 'Quinta', 5: 'Sexta', 6: 'Sábado'}

    if request.method == 'POST':
        try:
            dia_semana = int(request.form['dia_semana'])
            hora_inicio = request.form['hora_inicio']
            hora_fim = request.form['hora_fim']
            intervalo_minutos_str = request.form.get('intervalo_minutos')
            intervalo_minutos = int(intervalo_minutos_str) if intervalo_minutos_str and intervalo_minutos_str.isdigit() else None
            ativo = 'ativo' in request.form

            if hora_inicio >= hora_fim:
                flash('Hora de início deve ser anterior à hora de fim.', 'warning')
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
            print(f"Erro editar_horario (POST): {e}")
    
    try:
        horario_doc_snapshot = horario_ref.get()
        if horario_doc_snapshot.exists:
            horario_data_db = horario_doc_snapshot.to_dict()
            if horario_data_db:
                horario_data_db['id'] = horario_doc_snapshot.id 
                horario_data_db['profissional_id_fk'] = profissional_doc_id # Alterado
                
                # Obtém o nome do profissional (antigo barbeiro)
                profissional_pai_doc = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).get() # Alterado
                if profissional_pai_doc.exists: # Alterado
                    profissional_pai_data = profissional_pai_doc.to_dict() # Alterado
                    if profissional_pai_data: # Alterado
                         horario_data_db['profissional_nome_atual'] = profissional_pai_data.get('nome', profissional_doc_id) # Alterado
                
                return render_template('horario_form.html', 
                                       profissionais=profissionais_ativos_lista, # Alterado
                                       dias_semana=dias_semana_map, 
                                       horario=horario_data_db, 
                                       action_url=url_for('editar_horario', profissional_doc_id=profissional_doc_id, horario_doc_id=horario_doc_id), # Alterado
                                       page_title=f"Editar Horário de {horario_data_db.get('profissional_nome_atual', 'Profissional')}", # Alterado
                                       current_year=datetime.datetime.now(SAO_PAULO_TZ).year)
        else:
            flash('Horário específico não encontrado.', 'danger')
            return redirect(url_for('listar_horarios'))
    except Exception as e:
        flash(f'Erro ao carregar horário para edição: {e}', 'danger')
        print(f"Erro editar_horario (GET): {e}")
        return redirect(url_for('listar_horarios'))


@app.route('/profissionais/<string:profissional_doc_id>/horarios/excluir/<string:horario_doc_id>', methods=['POST']) # Alterado
@login_required
@admin_required # Apenas admins podem excluir horários (ou o próprio médico para si)
def excluir_horario(profissional_doc_id, horario_doc_id): # Alterado
    clinica_id = session['clinica_id']
    try:
        db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).collection('horarios_disponiveis').document(horario_doc_id).delete() # Alterado
        flash('Horário disponível excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir horário: {e}', 'danger')
        print(f"Erro excluir_horario: {e}")
    return redirect(url_for('listar_horarios'))

@app.route('/profissionais/<string:profissional_doc_id>/horarios/ativar_desativar/<string:horario_doc_id>', methods=['POST']) # Alterado
@login_required
@admin_required # Apenas admins podem ativar/desativar horários (ou o próprio médico para si)
def ativar_desativar_horario(profissional_doc_id, horario_doc_id): # Alterado
    clinica_id = session['clinica_id']
    horario_ref = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).collection('horarios_disponiveis').document(horario_doc_id) # Alterado
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
                flash('Dados do horário inválidos.', 'danger')
        else:
            flash('Horário não encontrado.', 'danger')
    except Exception as e:
        flash(f'Erro ao alterar status do horário: {e}', 'danger')
        print(f"Erro em ativar_desativar_horario: {e}")
    return redirect(url_for('listar_horarios'))

# --- ROTAS DE AGENDAMENTOS ---
@app.route('/agendamentos')
@login_required
def listar_agendamentos():
    clinica_id = session['clinica_id']
    agendamentos_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos')
    agendamentos_lista = []
    
    profissionais_para_filtro = [] # Alterado
    servicos_procedimentos_ativos = [] # Alterado
    pacientes_para_filtro = [] # Novo

    try:
        profissionais_docs = db.collection('clinicas').document(clinica_id).collection('profissionais').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream() # Alterado
        for doc in profissionais_docs: # Alterado
            p_data = doc.to_dict() # Alterado
            if p_data: profissionais_para_filtro.append({'id': doc.id, 'nome': p_data.get('nome', doc.id)}) # Alterado
        
        servicos_docs = db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').order_by('nome').stream() # Alterado
        for doc in servicos_docs: # Alterado
            s_data = doc.to_dict() # Alterado
            if s_data: servicos_procedimentos_ativos.append({'id': doc.id, 'nome': s_data.get('nome', doc.id), 'preco': s_data.get('preco_sugerido', 0.0)}) # Alterado

        pacientes_docs = db.collection('clinicas').document(clinica_id).collection('pacientes').order_by('nome').stream()
        for doc in pacientes_docs:
            pac_data = doc.to_dict()
            if pac_data: pacientes_para_filtro.append({'id': doc.id, 'nome': pac_data.get('nome', doc.id)})


    except Exception as e:
        flash('Erro ao carregar dados para filtros/modal.', 'warning')
        print(f"Erro ao carregar profissionais/servicos_procedimentos/pacientes para filtros: {e}") # Alterado

    filtros_atuais = {
        'paciente_nome': request.args.get('paciente_nome', '').strip(), # Alterado
        'profissional_id': request.args.get('profissional_id', '').strip(), # Alterado
        'status': request.args.get('status', '').strip(),
        'data_inicio': request.args.get('data_inicio', '').strip(),
        'data_fim': request.args.get('data_fim', '').strip(),
    }

    # LÓGICA ATUALIZADA: Define o filtro padrão para o mês atual se nenhuma data for fornecida
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

    if filtros_atuais['paciente_nome']: # Alterado
        query = query.where(filter=FieldFilter('paciente_nome', '>=', filtros_atuais['paciente_nome'])).where(filter=FieldFilter('paciente_nome', '<=', filtros_atuais['paciente_nome'] + '\uf8ff')) # Alterado
    if filtros_atuais['profissional_id']: # Alterado
        query = query.where(filter=FieldFilter('profissional_id', '==', filtros_atuais['profissional_id'])) # Alterado
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
            flash('Data de fim inválida. Use o formato AAAA-MM-DD.', 'warning')

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
                
                # Ajusta o nome do campo para o preço
                ag['preco_servico_fmt'] = "R$ {:.2f}".format(float(ag.get('servico_procedimento_preco', 0))).replace('.', ',') # Alterado
                data_criacao_ts = ag.get('data_criacao')
                if isinstance(data_criacao_ts, datetime.datetime):
                    ag['data_criacao_fmt'] = data_criacao_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                else:
                    ag['data_criacao_fmt'] = "N/A"
                agendamentos_lista.append(ag)
    except Exception as e:
        flash(f'Erro ao listar agendamentos: {e}. Verifique os índices no Firestore.', 'danger')
        print(f"Erro listar_agendamentos: {e}")
    
    stats_cards = {
        'confirmado': {'count': 0, 'total_valor': 0.0},
        'concluido': {'count': 0, 'total_valor': 0.0},
        'cancelado': {'count': 0, 'total_valor': 0.0},
        'pendente': {'count': 0, 'total_valor': 0.0}
    }
    for agendamento in agendamentos_lista:
        status = agendamento.get('status', 'pendente').lower()
        preco = float(agendamento.get('servico_procedimento_preco', 0)) # Alterado
        if status in stats_cards:
            stats_cards[status]['count'] += 1
            stats_cards[status]['total_valor'] += preco

    return render_template('agendamentos.html', 
                           agendamentos=agendamentos_lista,
                           stats_cards=stats_cards,
                           profissionais_para_filtro=profissionais_para_filtro, # Alterado
                           servicos_ativos=servicos_procedimentos_ativos, # Alterado
                           pacientes_para_filtro=pacientes_para_filtro, # Novo
                           filtros_atuais=filtros_atuais,
                           current_year=datetime.datetime.now(SAO_PAULO_TZ).year)

@app.route('/agendamentos/registrar_manual', methods=['POST'])
@login_required
def registrar_atendimento_manual():
    clinica_id = session['clinica_id']
    try:
        paciente_nome = request.form.get('cliente_nome_manual') # Alterado
        paciente_telefone = request.form.get('cliente_telefone_manual') # Alterado
        profissional_id_manual = request.form.get('barbeiro_id_manual') # Alterado
        servico_procedimento_id_manual = request.form.get('servico_id_manual') # Alterado
        data_agendamento_str = request.form.get('data_agendamento_manual')
        hora_agendamento_str = request.form.get('hora_agendamento_manual')
        preco_str = request.form.get('preco_manual')
        status_manual = request.form.get('status_manual')

        if not all([paciente_nome, profissional_id_manual, servico_procedimento_id_manual, data_agendamento_str, hora_agendamento_str, preco_str, status_manual]):
            flash('Todos os campos obrigatórios devem ser preenchidos.', 'danger')
            return redirect(url_for('listar_agendamentos'))

        preco_servico = float(preco_str.replace(',', '.'))

        # Busca o ID do paciente pelo nome, ou cria um novo paciente se não existir
        paciente_ref_query = db.collection('clinicas').document(clinica_id).collection('pacientes')\
                               .where(filter=FieldFilter('nome', '==', paciente_nome)).limit(1).get()
        
        paciente_doc_id = None
        if paciente_ref_query:
            for doc in paciente_ref_query:
                paciente_doc_id = doc.id
                break
        
        if not paciente_doc_id:
            # Cria um novo paciente se não encontrado
            novo_paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').add({
                'nome': paciente_nome,
                'contato_telefone': paciente_telefone if paciente_telefone else None,
                'data_cadastro': firestore.SERVER_TIMESTAMP
            })
            paciente_doc_id = novo_paciente_ref[1].id # Pega o ID do documento recém-criado

        profissional_doc = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_id_manual).get() # Alterado
        servico_procedimento_doc = db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_procedimento_id_manual).get() # Alterado

        profissional_nome = profissional_doc.to_dict().get('nome', 'N/A') if profissional_doc.exists else 'N/A' # Alterado
        servico_procedimento_nome = servico_procedimento_doc.to_dict().get('nome', 'N/A') if servico_procedimento_doc.exists else 'N/A' # Alterado
        
        dt_agendamento_naive = datetime.datetime.strptime(f"{data_agendamento_str} {hora_agendamento_str}", "%Y-%m-%d %H:%M")
        dt_agendamento_sp = SAO_PAULO_TZ.localize(dt_agendamento_naive)
        data_agendamento_ts_utc = dt_agendamento_sp.astimezone(pytz.utc)

        novo_agendamento_dados = {
            'paciente_id': paciente_doc_id, # Novo campo
            'paciente_nome': paciente_nome, # Alterado
            'paciente_numero': paciente_telefone if paciente_telefone else None, # Alterado
            'profissional_id': profissional_id_manual, # Alterado
            'profissional_nome': profissional_nome, # Alterado
            'servico_procedimento_id': servico_procedimento_id_manual, # Alterado
            'servico_procedimento_nome': servico_procedimento_nome, # Alterado
            'data_agendamento': data_agendamento_str,
            'hora_agendamento': hora_agendamento_str,
            'data_agendamento_ts': data_agendamento_ts_utc,
            'servico_procedimento_preco': preco_servico, # Alterado para refletir o preço no agendamento
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
        flash(f'Erro ao alterar status do agendamento: {e}', 'danger')
        print(f"Erro alterar_status_agendamento: {e}")
    return redirect(url_for('listar_agendamentos'))

# --- ROTAS DE PRONTUÁRIOS (NOVO) ---
@app.route('/prontuarios')
@login_required
def buscar_prontuario():
    clinica_id = session['clinica_id']
    pacientes_para_busca = []
    try:
        pacientes_docs = db.collection('clinicas').document(clinica_id).collection('pacientes').order_by('nome').stream()
        for doc in pacientes_docs:
            paciente_data = doc.to_dict()
            if paciente_data:
                pacientes_para_busca.append({'id': doc.id, 'nome': paciente_data.get('nome', doc.id)})
    except Exception as e:
        flash('Erro ao carregar lista de pacientes para busca.', 'danger')
        print(f"Erro buscar_prontuario: {e}")

    return render_template('prontuario_busca.html', pacientes_para_busca=pacientes_para_busca)

@app.route('/prontuarios/<string:paciente_doc_id>')
@login_required
def ver_prontuario(paciente_doc_id):
    clinica_id = session['clinica_id']
    paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
    prontuarios_ref = paciente_ref.collection('prontuarios')
    
    paciente_data = None
    registros_prontuario = []
    
    try:
        paciente_doc = paciente_ref.get()
        if paciente_doc.exists:
            paciente_data = paciente_doc.to_dict()
            paciente_data['id'] = paciente_doc.id

            # Adiciona informações de convênio se existir
            if paciente_data.get('convenio_id'):
                convenio_doc = db.collection('clinicas').document(clinica_id).collection('convenios').document(paciente_data['convenio_id']).get()
                if convenio_doc.exists:
                    paciente_data['convenio_nome'] = convenio_doc.to_dict().get('nome', 'N/A')
            
            # Busca todos os registos de prontuário para este paciente
            docs_stream = prontuarios_ref.order_by('data_registro', direction=firestore.Query.DESCENDING).stream()
            for doc in docs_stream:
                registro = doc.to_dict()
                if registro:
                    registro['id'] = doc.id
                    if registro.get('data_registro') and isinstance(registro['data_registro'], datetime.datetime):
                        registro['data_registro_fmt'] = registro['data_registro'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                    else:
                        registro['data_registro_fmt'] = "N/A"
                    
                    # Carrega nome do profissional que criou o registro
                    profissional_doc_id = registro.get('profissional_id')
                    if profissional_doc_id:
                        prof_doc = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).get()
                        if prof_doc.exists:
                            registro['profissional_nome'] = prof_doc.to_dict().get('nome', 'Desconhecido')
                        else:
                            registro['profissional_nome'] = 'Desconhecido'

                    registros_prontuario.append(registro)
        else:
            flash('Paciente não encontrado.', 'danger')
            return redirect(url_for('buscar_prontuario'))
    except Exception as e:
        flash(f'Erro ao carregar prontuário: {e}.', 'danger')
        print(f"Erro ver_prontuario: {e}")

    return render_template('prontuario.html', paciente=paciente_data, registros=registros_prontuario)

@app.route('/prontuarios/<string:paciente_doc_id>/anamnese/novo', methods=['GET', 'POST'])
@login_required
def adicionar_anamnese(paciente_doc_id):
    clinica_id = session['clinica_id']
    # O user_uid na sessão é o UID do utilizador logado no Firebase Auth
    profissional_logado_uid = session.get('user_uid') 

    # Busca o ID do profissional associado ao user_uid logado
    profissional_doc_id = None
    profissional_nome = "Profissional Desconhecido"
    try:
        prof_query = db.collection('clinicas').document(clinica_id).collection('profissionais')\
                       .where(filter=FieldFilter('user_uid', '==', profissional_logado_uid)).limit(1).get()
        for doc in prof_query:
            profissional_doc_id = doc.id
            profissional_nome = doc.to_dict().get('nome', profissional_nome)
            break
        if not profissional_doc_id:
             flash('Seu utilizador não está associado a um profissional. Contacte o administrador.', 'danger')
             return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    except Exception as e:
        flash(f'Erro ao verificar o profissional associado: {e}', 'danger')
        print(f"Erro adicionar_anamnese (GET - profissional check): {e}")
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
            modelo = doc.to_dict()
            if modelo:
                modelo['id'] = doc.id
                modelos_anamnese.append(modelo)
    except Exception as e:
        flash('Erro ao carregar modelos de anamnese.', 'warning')
        print(f"Erro ao carregar modelos de anamnese: {e}")

    if request.method == 'POST':
        conteudo = request.form['conteudo']
        modelo_base_id = request.form.get('modelo_base_id')
        
        try:
            db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').add({
                'profissional_id': profissional_doc_id, # Salva o ID do profissional que criou
                'data_registro': firestore.SERVER_TIMESTAMP,
                'tipo_registro': 'anamnese',
                'conteudo': conteudo,
                'modelo_base_id': modelo_base_id if modelo_base_id else None
            })
            flash('Anamnese adicionada com sucesso!', 'success')
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
        except Exception as e:
            flash(f'Erro ao adicionar anamnese: {e}', 'danger')
            print(f"Erro adicionar_anamnese (POST): {e}")
    
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
            modelo = doc.to_dict()
            if modelo:
                modelo['id'] = doc.id
                modelos_anamnese.append(modelo)
    except Exception as e:
        flash('Erro ao carregar modelos de anamnese.', 'warning')
        print(f"Erro ao carregar modelos de anamnese (editar): {e}")

    if request.method == 'POST':
        conteudo = request.form['conteudo']
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
            print(f"Erro editar_anamnese (POST): {e}")
    
    try:
        anamnese_doc = anamnese_ref.get()
        if anamnese_doc.exists and anamnese_doc.to_dict().get('tipo_registro') == 'anamnese':
            anamnese_data = anamnese_doc.to_dict()
            anamnese_data['id'] = anamnese_doc.id
            return render_template('anamnese_form.html', 
                                   paciente_id=paciente_doc_id, 
                                   paciente_nome=paciente_nome, 
                                   anamnese=anamnese_data, 
                                   modelos_anamnese=modelos_anamnese,
                                   action_url=url_for('editar_anamnese', paciente_doc_id=paciente_doc_id, anamnese_doc_id=anamnese_doc_id),
                                   page_title=f"Editar Anamnese para {paciente_nome}")
        else:
            flash('Anamnese não encontrada ou tipo de registo inválido.', 'danger')
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
    except Exception as e:
        flash(f'Erro ao carregar anamnese para edição: {e}', 'danger')
        print(f"Erro editar_anamnese (GET): {e}")
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))


# --- ROTAS DE MODELOS DE ANAMNESE (NOVO) ---
@app.route('/modelos_anamnese')
@login_required
@admin_required # Apenas admins podem gerenciar modelos de anamnese, ou talvez médicos?
def listar_modelos_anamnese():
    clinica_id = session['clinica_id']
    modelos_lista = []
    try:
        docs = db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').order_by('identificacao').stream()
        for doc in docs:
            modelo = doc.to_dict()
            if modelo:
                modelo['id'] = doc.id
                modelos_lista.append(modelo)
    except Exception as e:
        flash(f'Erro ao listar modelos de anamnese: {e}.', 'danger')
        print(f"Erro listar_modelos_anamnese: {e}")
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
            print(f"Erro adicionar_modelo_anamnese: {e}")
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
            print(f"Erro editar_modelo_anamnese (POST): {e}")

    try:
        modelo_doc = modelo_ref.get()
        if modelo_doc.exists:
            modelo = modelo_doc.to_dict()
            modelo['id'] = modelo_doc.id
            return render_template('modelo_anamnese_form.html', modelo=modelo, action_url=url_for('editar_modelo_anamnese', modelo_doc_id=modelo_doc_id))
        else:
            flash('Modelo de anamnese não encontrado.', 'danger')
            return redirect(url_for('listar_modelos_anamnese'))
    except Exception as e:
        flash(f'Erro ao carregar modelo de anamnese para edição: {e}', 'danger')
        print(f"Erro editar_modelo_anamnese (GET): {e}")
        return redirect(url_for('listar_modelos_anamnese'))

@app.route('/modelos_anamnese/excluir/<string:modelo_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_modelo_anamnese(modelo_doc_id):
    clinica_id = session['clinica_id']
    try:
        # TODO: Se houver prontuários que referenciam este modelo, pode ser necessário verificar antes de excluir.
        # Por simplicidade, por enquanto, a exclusão é direta.
        db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').document(modelo_doc_id).delete()
        flash('Modelo de anamnese excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir modelo de anamnese: {e}.', 'danger')
        print(f"Erro excluir_modelo_anamnese: {e}")
    return redirect(url_for('listar_modelos_anamnese'))

# --- EXECUÇÃO DO APP ---
if __name__ == '__main__':
    # Para execução local, use um .env para GOOGLE_SERVICE_ACCOUNT_KEY_JSON e PORT
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)), debug=True)

