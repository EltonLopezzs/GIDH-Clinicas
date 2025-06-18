import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, render_template_string
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth_admin
import datetime
import pytz
from collections import Counter
import ujson as json

from google.cloud.firestore_v1.base_query import FieldFilter

app = Flask(__name__)
# A chave secreta será usada para segurança da sessão do Flask
app.secret_key = os.urandom(24)
CORS(app) # 2. INITIALIZATION OF CORS (ESSENTIAL FOR LOGIN TO WORK)

db = None
try:
    # Tries to initialize Firebase Admin SDK using a local service account key file.
    # This is useful for local development but for production, environment variables are recommended for security.
    cred_path = os.path.join(os.path.dirname(__file__), 'serviceAccountKey.json')
    if not os.path.exists(cred_path):
        raise FileNotFoundError("serviceAccountKey.json file not found in project root. It is required for Firebase connection.")

    cred = credentials.Certificate(cred_path)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
        print("🔥 Firebase Admin SDK initialized for the first time!")
    else:
        print("🔥 Firebase Admin SDK was already initialized.")
    db = firestore.client()
except Exception as e:
    print(f"🚨 CRITICAL ERROR initializing Firebase Admin SDK: {e}")

# Defines the São Paulo timezone for consistency in date and time operations.
SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')

# Helper function to format Firestore timestamps for JSON serialization
def format_firestore_timestamp(timestamp):
    if isinstance(timestamp, datetime.datetime):
        # Convert to local timezone before formatting for display
        return timestamp.astimezone(SAO_PAULO_TZ).strftime('%Y-%m-%dT%H:%M:%S') # ISO format for JS compatibility
    return None # Or handle other types if needed

# Helper function to recursively convert Firestore document data to a serializable dictionary
def convert_doc_to_dict(doc_snapshot):
    data = doc_snapshot.to_dict()
    if not data:
        return {}
    
    # Add the document ID
    data['id'] = doc_snapshot.id

    def _convert_value(value):
        if isinstance(value, datetime.datetime):
            return format_firestore_timestamp(value) # Use the existing formatter
        elif isinstance(value, dict):
            return {k: _convert_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_convert_value(item) for item in value]
        # Ensure any Jinja2 Undefined objects are converted to None
        if isinstance(value, type(app.jinja_env.undefined)): # Check if it's a Jinja2 Undefined object
            return None
        return value

    return {k: _convert_value(v) for k, v in data.items()}

# Helper function to parse date input with multiple formats and convert to datetime.datetime
def parse_date_input(date_string):
    if not date_string:
        return None
    
    parsed_date = None
    # Try YYYY-MM-DD first (expected from flatpickr's dateFormat)
    try:
        parsed_date = datetime.datetime.strptime(date_string, '%Y-%m-%d').date()
    except ValueError:
        pass # Fallback to next format

    # Try DD/MM/YYYY (common manual input or altFormat from flatpickr)
    if parsed_date is None:
        try:
            parsed_date = datetime.datetime.strptime(date_string, '%d/%m/%Y').date()
        except ValueError:
            pass # No match
    
    if parsed_date:
        # Convert datetime.date to datetime.datetime at start of day in SAO_PAULO_TZ
        return SAO_PAULO_TZ.localize(datetime.datetime(parsed_date.year, parsed_date.month, parsed_date.day, 0, 0, 0))
    
    return None # Return None if no valid format is found


# Custom decorator to require the user to be logged in.
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Checks if necessary keys are in the session.
        if 'logged_in' not in session or 'clinica_id' not in session or 'user_uid' not in session:
            # If not logged in, redirects to the login page.
            return redirect(url_for('login_page'))
        # Checks if the database connection is active.
        if not db:
            flash('Critical error: Database connection failed. Contact support.', 'danger')
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# Decorator to require an administrator role.
def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # First, ensure the user is logged in.
        if 'logged_in' not in session or 'clinica_id' not in session or 'user_uid' not in session:
            flash('Unauthorized access. Please log in.', 'danger')
            return redirect(url_for('login_page'))
        # Checks if the user's role in the session is 'admin'.
        if session.get('user_role') != 'admin':
            flash('Access denied: You do not have administrator permissions for this action.', 'danger')
            # Can redirect to the dashboard or an error page.
            return redirect(url_for('index')) 
        return f(*args, **kwargs)
    return decorated_function

# --- AUTHENTICATION AND SETUP ROUTES ---
@app.route('/login', methods=['GET'])
def login_page():
    # If the user is already logged in, redirects to the dashboard.
    if 'logged_in' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/session-login', methods=['POST'])
def session_login():
    # Checks if the database is initialized.
    if not db:
        return jsonify({"success": False, "message": "Critical server error (DB not initialized)."}), 500

    # Gets the ID Token from the JSON request body.
    id_token = request.json.get('idToken')
    if not id_token:
        return jsonify({"success": False, "message": "ID Token not provided."}), 400

    try:
        # Verifies the ID Token using the Firebase Admin SDK.
        decoded_token = firebase_auth_admin.verify_id_token(id_token)
        uid_from_token = decoded_token['uid']
        email = decoded_token.get('email', '')

        # Searches for the user mapping in the 'User' collection.
        mapeamento_ref = db.collection('User').document(uid_from_token.strip())
        mapeamento_doc = mapeamento_ref.get()

        if mapeamento_doc.exists:
            mapeamento_data = mapeamento_doc.to_dict()
            # Checks if essential data is present in the mapping.
            if not mapeamento_data or 'clinica_id' not in mapeamento_data or 'role' not in mapeamento_data:
                return jsonify({"success": False, "message": "Incomplete user configuration. Contact the administrator."}), 500

            # Sets session variables for the logged-in user.
            session['logged_in'] = True
            session['user_uid'] = uid_from_token
            session['user_email'] = email
            session['clinica_id'] = mapeamento_data['clinica_id']
            session['clinica_nome_display'] = mapeamento_data.get('nome_clinica_display', 'Clínica On')
            session['user_role'] = mapeamento_data['role'] # Stores the user's role

            print(f"User {email} successfully logged in. Role: {session['user_role']}")
            return jsonify({"success": True, "message": "Login successful!"})
        else:
            return jsonify({"success": False, "message": "Unauthorized user or not associated with a clinic."}), 403

    except firebase_auth_admin.RevokedIdTokenError:
        return jsonify({"success": False, "message": "ID Token revoked. Please log in again."}), 401
    except firebase_auth_admin.UserDisabledError:
        return jsonify({"success": False, "message": "Your user account has been disabled. Contact the administrator."}), 403
    except firebase_auth_admin.InvalidIdTokenError:
        return jsonify({"success": False, "message": "Invalid credentials. Check your email and password."}), 401
    except Exception as e:
        print(f"Error in token/mapping verification: {type(e).__name__} - {e}")
        return jsonify({"success": False, "message": f"Server error during login: {str(e)}"}), 500

# Initial configuration route for a super-admin to associate a UID with a clinic.
# This route should be used with extreme caution and disabled/highly protected in production.
@app.route('/setup-mapeamento-admin', methods=['GET', 'POST'])
def setup_mapeamento_admin():
    if not db: return "Firebase not initialized", 500
    if request.method == 'POST':
        user_uid = request.form['user_uid'].strip()
        email_para_referencia = request.form['email_para_referencia'].strip().lower()
        clinica_id_associada = request.form['clinica_id_associada'].strip()
        nome_clinica_display = request.form['nome_clinica_display'].strip()
        user_role = request.form.get('user_role', 'medico').strip() # Allows setting the role

        if not all([user_uid, email_para_referencia, clinica_id_associada, nome_clinica_display, user_role]):
            flash("All fields are mandatory.", "danger")
        else:
            try:
                # Creates or verifies the clinic collection.
                clinica_ref = db.collection('clinicas').document(clinica_id_associada)
                if not clinica_ref.get().exists:
                    clinica_ref.set({
                        'nome_oficial': nome_clinica_display,
                        'criada_em_dashboard_setup': firestore.SERVER_TIMESTAMP
                    })
                # Maps the user to the role and the clinic.
                db.collection('User').document(user_uid).set({
                    'email': email_para_referencia,
                    'clinica_id': clinica_id_associada,
                    'nome_clinica_display': nome_clinica_display,
                    'role': user_role, # Saves the user's role
                    'associado_em': firestore.SERVER_TIMESTAMP
                })
                flash(f'User UID {user_uid} ({user_role}) associated with clinic {nome_clinica_display} ({clinica_id_associada})! You can now try to <a href="{url_for("login_page")}">log in</a>.', 'success')
            except Exception as e:
                flash(f'Error associating user: {e}', 'danger')
                print(f"Error in setup_mapeamento_admin: {e}")
        # Redirects to the setup-mapeamento-admin route itself to display the flash message
        return redirect(url_for('setup_mapeamento_admin'))
    
    # This is the HTML that will be rendered on the GET of the /setup-mapeamento-admin route
    # We use render_template_string so that Jinja2 can process variables like url_for
    return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Associate Firebase Admin to Clinic</title>
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
        <h2>Associate Firebase Auth User to a Clinic</h2>
        <p><b>Step 1:</b> Create the user (with email/password) in the Firebase console > Authentication.</p>
        <p><b>Step 2:</b> Get the user's UID (e.g., from the "Users" tab in Firebase Auth).</p>
        <p><b>Step 3:</b> Fill out the form below.</p>
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
            User UID (Firebase Auth): <input type="text" name="user_uid" required size="40" value="{{ request.form.user_uid if 'user_uid' in request.form else '' }}"><br><br>
            User Email (for reference): <input type="email" name="email_para_referencia" required size="40" value="{{ request.form.email_para_referencia if 'email_para_referencia' in request.form else '' }}"><br><br>
            Clinic ID (e.g., clinicaSaoJudas): <input type="text" name="clinica_id_associada" required size="40" value="{{ request.form.clinica_id_associada if 'clinica_id_associada' in request.form else '' }}"><br><br>
            Clinic Display Name: <input type="text" name="nome_clinica_display" required size="40" value="{{ request.form.nome_clinica_display if 'nome_clinica_display' in request.form else '' }}"><br><br>
            User Role: 
            <select name="user_role" required>
                <option value="admin" {% if request.form.user_role == 'admin' %}selected{% endif %}>Administrator</option>
                <option value="medico" {% if request.form.user_role == 'medico' %}selected{% endif %}>Doctor</option>
            </select><br><br>
            <button type="submit">Associate User to Clinic</button>
        </form>
        <p><a href="{{ url_for('login_page') }}">Go to Login</a></p>
        </body></html>
    """)


@app.route('/logout', methods=['POST'])
def logout():
    session.clear() # Clears all session variables.
    return jsonify({"success": True, "message": "Server session cleared."})

# --- MAIN ROUTE (DASHBOARD) ---
@app.route('/')
@login_required
def index():
    clinica_id = session['clinica_id']
    agendamentos_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos')
    servicos_procedimentos_ref = db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos')
    current_year = datetime.datetime.now(SAO_PAULO_TZ).year

    # Map to store service/procedure information (name and price)
    servicos_procedimentos_map = {}
    try:
        servicos_docs_stream = servicos_procedimentos_ref.stream()
        for serv_doc in servicos_docs_stream:
            serv_data_dict = serv_doc.to_dict()
            if serv_data_dict and 'preco_sugerido' in serv_data_dict and 'nome' in serv_data_dict:
                servicos_procedimentos_map[serv_doc.id] = {
                    'nome': serv_data_dict.get('nome', 'Unknown Service/Procedure'),
                    'preco': float(serv_data_dict.get('preco_sugerido', 0))
                }
    except Exception as e:
        print(f"CRITICAL ERROR fetching services/procedures for dashboard: {e}")
        flash("Critical error loading service/procedure data. The dashboard may not display correct totals.", "danger")

    hoje_dt = datetime.datetime.now(SAO_PAULO_TZ)
    hoje_inicio_dt = hoje_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    
    count_hoje, receita_hoje = 0, 0.0
    count_semana, receita_semana = 0, 0.0
    count_mes, receita_mes = 0, 0.0

    # Calculates the start and end of the current week.
    inicio_semana_dt = hoje_inicio_dt - datetime.timedelta(days=hoje_dt.weekday())
    fim_semana_dt = inicio_semana_dt + datetime.timedelta(days=7)
    
    # Calculates the start and end of the current month.
    inicio_mes_dt = hoje_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if inicio_mes_dt.month == 12:
        fim_mes_dt = inicio_mes_dt.replace(year=inicio_mes_dt.year + 1, month=1, day=1)
    else:
        fim_mes_dt = inicio_mes_dt.replace(month=inicio_mes_dt.month + 1, day=1)

    agendamentos_para_analise = []
    try:
        # Queries confirmed or completed appointments in the current month for analysis.
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
        print(f"Error general query for dashboard: {e}. Check your Firestore indexes.")
        flash("Error calculating dashboard statistics. Check your Firestore indexes.", "danger")

    # Initializes data for the charts.
    agendamentos_por_dia_semana = [0] * 7 # 0=Monday, ..., 6=Sunday (Python's weekday)
    labels_dias_semana = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    contagem_servicos_semana = Counter()

    for ag_data in agendamentos_para_analise:
        ag_timestamp_sp = ag_data.get('data_agendamento_ts').astimezone(SAO_PAULO_TZ)
        # Uses 'servico_procedimento_id' and 'servicos_procedimentos_map'
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
                nome_servico = servicos_procedimentos_map.get(servico_procedimento_id, {}).get('nome', 'Unknown')
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
        'labels': [item[0] for item in servicos_populares_comuns] or ['No Appointments this Week'],
        'valores': [item[1] for item in servicos_populares_comuns] or [0]
    }

    proximos_agendamentos_lista = []
    try:
        # Queries next confirmed appointments (up to 5).
        query_proximos = agendamentos_ref.where(filter=FieldFilter('status', '==', 'confirmado')) \
                                         .where(filter=FieldFilter('data_agendamento_ts', '>=', hoje_inicio_dt)) \
                                         .order_by('data_agendamento_ts') \
                                         .limit(5).stream()
        for doc in query_proximos:
            ag_data = doc.to_dict()
            if not ag_data: continue

            # Gets service/procedure information.
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
                'cliente_nome': ag_data.get('paciente_nome', "N/A"), # Changed from 'cliente_name' to 'paciente_name'
                'profissional_nome': ag_data.get('profissional_nome', "N/A"), # Changed from 'barber_name' to 'professional_name'
                'servico_procedimento_nome': servico_info.get('nome'), # Changed to 'service_procedure_name'
                'preco': float(servico_info.get('preco', 0)),
                'status': ag_data.get('status', "N/A")
            })
    except Exception as e:
        print(f"CRITICAL ERROR fetching next appointments: {e}. Check indexes.")
        flash("Error loading next appointments.", "danger")

    return render_template('dashboard.html', hoje_data=hoje_data, semana_data=semana_data,
                           mes_data=mes_data, proximos_agendamentos=proximos_agendamentos_lista,
                           nome_clinica=session.get('clinica_nome_display', 'Your Clinic'), # Changed to 'clinic_name'
                           current_year=current_year,
                           dados_desempenho_semana=dados_desempenho_semana,
                           dados_servicos_populares=dados_servicos_populares)


# --- USER ROUTES (ADMINISTRATORS AND DOCTORS) ---
@app.route('/usuarios')
@login_required
@admin_required # Only admins can manage users
def listar_usuarios():
    clinica_id = session['clinica_id']
    usuarios_ref = db.collection('User')
    usuarios_lista = []
    try:
        # Filters users by clinica_id and orders by email
        docs = usuarios_ref.where(filter=FieldFilter('clinica_id', '==', clinica_id)).order_by('email').stream()
        for doc in docs:
            user_data = doc.to_dict()
            if user_data:
                user_data['uid'] = doc.id # UID is the document ID
                usuarios_lista.append(user_data)
    except Exception as e:
        flash(f'Error listing users: {e}.', 'danger')
        print(f"Error list_users: {e}")
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
            flash('Email, password, and role are mandatory.', 'danger')
            return render_template('usuario_form.html', action_url=url_for('adicionar_usuario'), page_title="Add New User", roles=['admin', 'medico'])

        try:
            # Creates the user in Firebase Authentication
            user = firebase_auth_admin.create_user(email=email, password=password)
            user_uid = user.uid

            # Saves the mapping in the 'User' collection in Firestore
            db.collection('User').document(user_uid).set({
                'email': email,
                'clinica_id': clinica_id,
                'nome_clinica_display': session.get('clinica_nome_display', 'Clínica On'),
                'role': role,
                'nome_completo': nome_completo if nome_completo else None,
                'crm_ou_registro': crm_ou_registro if crm_ou_registro else None,
                'associado_em': firestore.SERVER_TIMESTAMP
            })

            # If the user is a doctor, it might be interesting to create a record in 'professionals'
            if role == 'medico':
                db.collection('clinicas').document(clinica_id).collection('profissionais').add({
                    'nome': nome_completo,
                    'email': email,
                    'crm_ou_registro': crm_ou_registro,
                    'user_uid': user_uid, # Reference to the created user's UID
                    'ativo': True, # By default, created doctors are active
                    'criado_em': firestore.SERVER_TIMESTAMP
                })

            flash(f'User {email} ({role}) added successfully!', 'success')
            return redirect(url_for('listar_usuarios'))
        except firebase_admin.auth.EmailAlreadyExistsError:
            flash('The provided email is already in use by another user.', 'danger')
        except Exception as e:
            flash(f'Error adding user: {e}', 'danger')
            print(f"Error add_user: {e}")
    
    return render_template('usuario_form.html', action_url=url_for('adicionar_usuario'), page_title="Add New User", roles=['admin', 'medico'])

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
            # Updates the user record in Firestore
            user_map_ref.update({
                'email': email,
                'role': role,
                'nome_completo': nome_completo if nome_completo else None,
                'crm_ou_registro': crm_ou_registro if crm_ou_registro else None,
                'atualizado_em': firestore.SERVER_TIMESTAMP
            })

            # Updates the email in Firebase Auth (if different)
            firebase_auth_user = firebase_auth_admin.get_user(user_uid)
            if firebase_auth_user.email != email:
                firebase_auth_admin.update_user(user_uid, email=email)
            
            # If it's a doctor, updates the corresponding record in 'professionals'
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
                else: # If not found, creates it (in case it changed role or was created without association)
                     profissionais_ref.add({
                        'nome': nome_completo,
                        'email': email,
                        'crm_ou_registro': crm_ou_registro,
                        'user_uid': user_uid,
                        'ativo': True,
                        'criado_em': firestore.SERVER_TIMESTAMP
                    })

            flash(f'User {email} ({role}) added successfully!', 'success')
            return redirect(url_for('listar_usuarios'))
        except firebase_admin.auth.EmailAlreadyExistsError:
            flash('The provided email is already in use by another user.', 'danger')
        except Exception as e:
            flash(f'Error updating user: {e}', 'danger')
            print(f"Error edit_user (POST): {e}")

    try:
        user_map_doc = user_map_ref.get()
        if user_map_doc.exists:
            user_data = user_map_doc.to_dict()
            user_data['uid'] = user_map_doc.id
            return render_template('usuario_form.html', user=user_data, action_url=url_for('editar_usuario', user_uid=user_uid), page_title=f"Edit User: {user_data.get('nome_completo') or user_data.get('email')}", roles=['admin', 'medico'])
        else:
            flash('User not found.', 'danger')
            return redirect(url_for('listar_usuarios'))
    except Exception as e:
        flash(f'Error loading user for editing: {e}', 'danger')
        print(f"Error edit_user (GET): {e}")
        return redirect(url_for('listar_usuarios'))


@app.route('/usuarios/ativar_desativar/<string:user_uid>', methods=['POST'])
@login_required
@admin_required # Only admins can activate/deactivate professionals
def ativar_desativar_usuario(user_uid):
    clinica_id = session['clinica_id']
    try:
        user_map_doc = db.collection('User').document(user_uid).get()
        if user_map_doc.exists:
            user_data = user_map_doc.to_dict()
            current_status_firebase = firebase_auth_admin.get_user(user_uid).disabled
            new_status_firebase = not current_status_firebase # If it's disabled, enable; if not, disable.

            firebase_auth_admin.update_user(user_uid, disabled=new_status_firebase)
            
            # If it's a doctor, also updates the status in 'professionals'
            if user_data.get('role') == 'medico':
                profissionais_ref = db.collection('clinicas').document(clinica_id).collection('profissionais')
                prof_query = profissionais_ref.where(filter=FieldFilter('user_uid', '==', user_uid)).limit(1).stream()
                prof_doc = next(prof_query, None)
                if prof_doc:
                    profissionais_ref.document(prof_doc.id).update({
                        'ativo': not new_status_firebase, # Inverts, as 'disabled' is the opposite of 'active'
                        'atualizado_em': firestore.SERVER_TIMESTAMP
                    })

            flash(f'User {user_data.get("email")} {"enabled" if not new_status_firebase else "disabled"} successfully!', 'success')
        else:
            flash('User not found in mapping.', 'danger')
    except firebase_admin.auth.UserNotFoundError:
        flash('User not found in Firebase Authentication.', 'danger')
    except Exception as e:
        flash(f'Error changing user status: {e}', 'danger')
        print(f"Error activate_deactivate_user: {e}")
    return redirect(url_for('listar_usuarios'))

# --- PROFESSIONALS ROUTES (FORMER BARBERS) ---
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
        flash(f'Error listing professionals: {e}.', 'danger')
        print(f"Error list_professionals: {e}")
    return render_template('profissionais.html', profissionais=profissionais_lista) # Renamed to professionals.html

@app.route('/profissionais/novo', methods=['GET', 'POST'])
@login_required
@admin_required # Only admins can add professionals directly
def adicionar_profissional():
    clinica_id = session['clinica_id']
    if request.method == 'POST':
        nome = request.form['nome']
        telefone = request.form.get('telefone')
        email_profissional = request.form.get('email_profissional') # New email field for the professional
        crm_ou_registro = request.form.get('crm_ou_registro') # New field
        ativo = 'ativo' in request.form
        try:
            if telefone and not telefone.isdigit():
                flash('Phone must contain only numbers.', 'warning')
                return render_template('profissional_form.html', profissional=request.form, action_url=url_for('adicionar_profissional'))

            db.collection('clinicas').document(clinica_id).collection('profissionais').add({
                'nome': nome,
                'telefone': telefone if telefone else None,
                'email': email_profissional if email_profissional else None, # Saves the email
                'crm_ou_registro': crm_ou_registro if crm_ou_registro else None, # Saves CRM/registration
                'ativo': ativo,
                'criado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Professional added successfully!', 'success')
            return redirect(url_for('listar_profissionais'))
        except Exception as e:
            flash(f'Error adding professional: {e}', 'danger')
            print(f"Error add_professional: {e}")
    return render_template('profissional_form.html', profissional=None, action_url=url_for('adicionar_profissional')) # Renamed to profissional_form.html


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
                flash('Phone must contain only numbers.', 'warning')
            else:
                profissional_ref.update({
                    'nome': nome,
                    'telefone': telefone if telefone else None,
                    'email': email_profissional if email_profissional else None,
                    'crm_ou_registro': crm_ou_registro if crm_ou_registro else None,
                    'ativo': ativo,
                    'atualizado_em': firestore.SERVER_TIMESTAMP
                })
                flash('Professional updated successfully!', 'success')
                return redirect(url_for('listar_profissionais'))
        except Exception as e:
            flash(f'Error updating professional: {e}', 'danger')
            print(f"Error edit_professional (POST): {e}")

    try:
        profissional_doc = profissional_ref.get()
        if profissional_doc.exists:
            profissional = profissional_doc.to_dict()
            profissional['id'] = profissional_doc.id
            return render_template('profissional_form.html', profissional=profissional, action_url=url_for('editar_profissional', profissional_doc_id=profissional_doc_id))
        else:
            flash('Professional not found.', 'danger')
            return redirect(url_for('listar_profissionais'))
    except Exception as e:
        flash(f'Error loading professional for editing: {e}', 'danger')
        print(f"Error edit_professional (GET): {e}")
        return redirect(url_for('listar_profissionais'))

@app.route('/profissionais/ativar_desativar/<string:profissional_doc_id>', methods=['POST'])
@login_required
@admin_required # Only admins can activate/deactivate professionals
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
                flash(f'Professional {"enabled" if new_status else "disabled"} successfully!', 'success')
        else:
            flash('Professional not found in mapping.', 'danger')
    except firebase_admin.auth.UserNotFoundError:
        flash('Professional not found in Firebase Authentication.', 'danger')
    except Exception as e:
        flash(f'Error changing professional status: {e}', 'danger')
        print(f"Error activate_deactivate_user: {e}")
    return redirect(url_for('listar_usuarios'))

# --- PATIENTS ROUTES (NEW) ---
@app.route('/pacientes')
@login_required
def listar_pacientes():
    clinica_id = session['clinica_id']
    pacientes_ref = db.collection('clinicas').document(clinica_id).collection('pacientes')
    pacientes_lista = []
    try:
        # Allows searching by name or phone
        search_query = request.args.get('search', '').strip()
        query = pacientes_ref.order_by('nome')

        if search_query:
            # Firestore does not directly support 'LIKE', so we do a range search.
            # To search by name
            query_nome = query.where(filter=FieldFilter('nome', '>=', search_query))\
                                .where(filter=FieldFilter('nome', '<=', search_query + '\uf8ff'))
            # To search by phone (if it's a text field)
            query_telefone = pacientes_ref.order_by('contato_telefone')\
                                         .where(filter=FieldFilter('contato_telefone', '>=', search_query))\
                                         .where(filter=FieldFilter('contato_telefone', '<=', search_query + '\uf8ff'))
            
            # Executes both queries and combines the results (removing duplicates)
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
            pacientes_lista.sort(key=lambda x: x.get('nome', '')) # Ensures order after combining
        else:
            # If no search, lists all
            docs = query.stream()
            for doc in docs:
                paciente = doc.to_dict()
                if paciente:
                    paciente['id'] = doc.id
                    pacientes_lista.append(paciente)

    except Exception as e:
        flash(f'Error listing patients: {e}. Check your Firestore indexes.', 'danger')
        print(f"Error list_patients: {e}")
    return render_template('pacientes.html', pacientes=pacientes_lista, search_query=search_query)

@app.route('/pacientes/novo', methods=['GET', 'POST'])
@login_required
def adicionar_paciente():
    clinica_id = session['clinica_id']
    
    # Loads covenants for the form
    convenios_lista = []
    try:
        convenios_docs = db.collection('clinicas').document(clinica_id).collection('convenios').order_by('nome').stream()
        for doc in convenios_docs:
            conv_data = doc.to_dict()
            if conv_data:
                convenios_lista.append({'id': doc.id, 'nome': conv_data.get('nome', doc.id)})
    except Exception as e:
        flash('Error loading covenants.', 'danger')
        print(f"Error loading covenants (add_patient GET): {e}")

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

        # Address
        cep = request.form.get('cep', '').strip()
        logradouro = request.form.get('logradouro', '').strip()
        numero = request.form.get('numero', '').strip()
        complemento = request.form.get('complemento', '').strip()
        bairro = request.form.get('bairro', '').strip()
        cidade = request.form.get('cidade', '').strip()
        estado = request.form.get('estado', '').strip()

        if not nome:
            flash('Patient name is mandatory.', 'danger')
            return render_template('paciente_form.html', paciente=request.form, action_url=url_for('adicionar_paciente'), convenios=convenios_lista)

        try:
            # Converts date of birth to a datetime object if provided
            data_nascimento_dt = parse_date_input(data_nascimento) # Use the new parser
            
            if data_nascimento and data_nascimento_dt is None: # Check if input was provided but parsing failed
                flash('Invalid date of birth format. Please use YYYY-MM-DD or DD/MM/YYYY.', 'danger') # Corrected message
                return render_template('paciente_form.html', paciente=request.form, action_url=url_for('adicionar_paciente'), convenios=convenios_lista)

            paciente_data = {
                'nome': nome,
                'data_nascimento': data_nascimento_dt, # Use the parsed datetime.datetime object
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
            flash('Patient added successfully!', 'success')
            return redirect(url_for('listar_pacientes'))
        except Exception as e:
            flash(f'Error adding patient: {e}', 'danger')
            print(f"Error add_patient: {e}")
    
    return render_template('paciente_form.html', paciente=None, action_url=url_for('adicionar_paciente'), convenios=convenios_lista)

@app.route('/pacientes/editar/<string:paciente_doc_id>', methods=['GET', 'POST'])
@login_required
def editar_paciente(paciente_doc_id):
    clinica_id = session['clinica_id']
    paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
    
    # Loads covenants for the form
    convenios_lista = []
    try:
        convenios_docs = db.collection('clinicas').document(clinica_id).collection('convenios').order_by('nome').stream()
        for doc in convenios_docs:
            conv_data = doc.to_dict()
            if conv_data:
                convenios_lista.append({'id': doc.id, 'nome': conv_data.get('nome', doc.id)})
    except Exception as e:
        flash('Error loading covenants.', 'danger')
        print(f"Error loading covenants (edit_patient GET): {e}")

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

        # Address
        cep = request.form.get('cep', '').strip()
        logradouro = request.form.get('logradouro', '').strip()
        numero = request.form.get('numero', '').strip()
        complemento = request.form.get('complemento', '').strip()
        bairro = request.form.get('bairro', '').strip()
        cidade = request.form.get('cidade', '').strip()
        estado = request.form.get('estado', '').strip()

        if not nome:
            flash('Patient name is mandatory.', 'danger')
            return render_template('paciente_form.html', paciente=request.form, action_url=url_for('editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista)

        try:
            data_nascimento_dt = parse_date_input(data_nascimento) # Use the new parser
            
            if data_nascimento and data_nascimento_dt is None: # Check if input was provided but parsing failed
                flash('Invalid date of birth format. Please use YYYY-MM-DD or DD/MM/YYYY.', 'danger') # Corrected message
                return render_template('paciente_form.html', paciente=request.form, action_url=url_for('editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista)

            paciente_data_update = {
                'nome': nome,
                'data_nascimento': data_nascimento_dt, # Use the parsed datetime.datetime object
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
            flash('Patient updated successfully!', 'success')
            return redirect(url_for('listar_pacientes'))
        except Exception as e:
            flash(f'Error updating patient: {e}', 'danger')
            print(f"Error edit_patient (POST): {e}")

    try:
        paciente_doc = paciente_ref.get()
        if paciente_doc.exists:
            paciente = paciente_doc.to_dict()
            paciente['id'] = paciente_doc.id
            # Formats the date of birth for the input type="date" field
            if paciente.get('data_nascimento') and isinstance(paciente['data_nascimento'], datetime.date):
                paciente['data_nascimento'] = paciente['data_nascimento'].strftime('%Y-%m-%d')
            # If it's a Firestore timestamp, converts to datetime.date
            elif isinstance(paciente.get('data_nascimento'), datetime.datetime):
                paciente['data_nascimento'] = paciente['data_nascimento'].date().strftime('%Y-%m-%d')
            # Handle cases where data_nascimento might be None or an empty string, to avoid errors in the form
            else:
                paciente['data_nascimento'] = '' # Ensure it's an empty string if invalid/None

            return render_template('paciente_form.html', paciente=paciente, action_url=url_for('editar_paciente', paciente_doc_id=paciente_doc_id), convenios=convenios_lista)
        else:
            flash('Patient not found.', 'danger')
            return redirect(url_for('listar_pacientes'))
    except Exception as e:
        flash(f'Error loading patient for editing: {e}', 'danger')
        print(f"Error edit_patient (GET): {e}")
        return redirect(url_for('listar_pacientes'))

# --- SERVICES/PROCEDURES ROUTES (FORMER SERVICES) ---
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
        flash(f'Error listing services/procedures: {e}.', 'danger')
        print(f"Error list_services_procedures: {e}")
    return render_template('servicos_procedimentos.html', servicos=servicos_procedimentos_lista) # Renamed

@app.route('/servicos_procedimentos/novo', methods=['GET', 'POST'])
@login_required
@admin_required # Admins and possibly doctors can create/edit services? Define.
def adicionar_servico_procedimento():
    clinica_id = session['clinica_id']
    if request.method == 'POST':
        nome = request.form['nome']
        tipo = request.form['tipo'] # 'Service' or 'Procedure'
        try:
            duracao_minutos = int(request.form['duracao_minutos'])
            preco_sugerido = float(request.form['preco'].replace(',', '.'))
            db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').add({
                'nome': nome,
                'tipo': tipo,
                'duracao_minutos': duracao_minutos,
                'preco_sugerido': preco_sugerido, # Changed to preco_sugerido
                'criado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Service/Procedure added successfully!', 'success')
            return redirect(url_for('listar_servicos_procedimentos'))
        except ValueError:
            flash('Duration and Price must be valid numbers.', 'danger')
        except Exception as e:
            flash(f'Error adding service/procedure: {e}', 'danger')
            print(f"Error add_service_procedure: {e}")
    return render_template('servico_procedimento_form.html', servico=None, action_url=url_for('adicionar_servico_procedimento')) # Renamed


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
            flash('Service/Procedure updated successfully!', 'success')
            return redirect(url_for('listar_servicos_procedimentos'))
        except ValueError:
            flash('Duration and Price must be valid numbers.', 'danger')
        except Exception as e:
            flash(f'Error updating service/procedure: {e}', 'danger')
            print(f"Error edit_service_procedure (POST): {e}")
    try:
        servico_doc = servico_ref.get()
        if servico_doc.exists:
            servico = servico.to_dict()
            if servico:
                servico['id'] = servico_doc.id
                servico['preco_form'] = str(servico.get('preco_sugerido', '0.00')).replace('.', ',')
                return render_template('servico_procedimento_form.html', servico=servico, action_url=url_for('editar_servico_procedimento', servico_doc_id=servico_doc_id))
        flash('Service/Procedure not found.', 'danger')
        return redirect(url_for('listar_servicos_procedimentos'))
    except Exception as e:
        flash(f'Error loading service/procedure for editing: {e}', 'danger')
        print(f"Error edit_service_procedure (GET): {e}")
        return redirect(url_for('listar_servicos_procedimentos'))

@app.route('/servicos_procedimentos/excluir/<string:servico_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_servico_procedimento(servico_doc_id):
    clinica_id = session['clinica_id']
    try:
        agendamentos_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos')
        # Checks if there are appointments associated with this service/procedure
        agendamentos_com_servico = agendamentos_ref.where(filter=FieldFilter('servico_procedimento_id', '==', servico_doc_id)).limit(1).get()
        if len(agendamentos_com_servico) > 0:
            flash('This service/procedure cannot be deleted as it is associated with one or more appointments.', 'danger')
            return redirect(url_for('listar_servicos_procedimentos'))

        db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_doc_id).delete()
        flash('Service/Procedure deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting service/procedure: {e}.', 'danger')
        print(f"Error delete_service_procedure: {e}")
    return redirect(url_for('listar_servicos_procedimentos'))

# --- COVENANTS ROUTES (NEW) ---
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
        flash(f'Error listing covenants: {e}.', 'danger')
        print(f"Error list_covenants: {e}")
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
            flash('Covenant name is mandatory.', 'danger')
            return render_template('convenio_form.html', convenio=request.form, action_url=url_for('adicionar_convenio'))
        try:
            db.collection('clinicas').document(clinica_id).collection('convenios').add({
                'nome': nome,
                'registro_ans': registro_ans if registro_ans else None,
                'tipo_plano': tipo_plano if tipo_plano else None,
                'criado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Covenant added successfully!', 'success')
            return redirect(url_for('listar_convenios'))
        except Exception as e:
            flash(f'Error adding covenant: {e}', 'danger')
            print(f"Error add_covenant: {e}")
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
            flash('Covenant name is mandatory.', 'danger')
            return render_template('convenio_form.html', convenio=request.form, action_url=url_for('editar_convenio', convenio_doc_id=convenio_doc_id))
        try:
            convenio_ref.update({
                'nome': nome,
                'registro_ans': registro_ans if registro_ans else None,
                'tipo_plano': tipo_plano if tipo_plano else None,
                'atualizado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Covenant updated successfully!', 'success')
            return redirect(url_for('listar_convenios'))
        except Exception as e:
            flash(f'Error updating covenant: {e}', 'danger')
            print(f"Error edit_covenant (POST): {e}")

    try:
        convenio_doc = convenio_ref.get()
        if convenio_doc.exists:
            convenio = convenio_doc.to_dict()
            convenio['id'] = convenio_doc.id
            return render_template('convenio_form.html', convenio=convenio, action_url=url_for('editar_convenio', convenio_doc_id=convenio_doc_id))
        else:
            flash('Covenant not found.', 'danger')
            return redirect(url_for('listar_convenios'))
    except Exception as e:
        flash(f'Error loading covenant for editing: {e}', 'danger')
        print(f"Error edit_covenant (GET): {e}")
        return redirect(url_for('listar_convenios'))

@app.route('/convenios/excluir/<string:convenio_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_convenio(convenio_doc_id):
    clinica_id = session['clinica_id']
    try:
        pacientes_ref = db.collection('clinicas').document(clinica_id).collection('pacientes')
        # Checks if there are patients associated with this covenant
        pacientes_com_convenio = pacientes_ref.where(filter=FieldFilter('convenio_id', '==', convenio_doc_id)).limit(1).get()
        if len(pacientes_com_convenio) > 0:
            flash('This covenant cannot be deleted as it is associated with one or more patients.', 'danger')
            return redirect(url_for('listar_convenios'))
            
        db.collection('clinicas').document(clinica_id).collection('convenios').document(convenio_doc_id).delete()
        flash('Covenant deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting covenant: {e}.', 'danger')
        print(f"Error delete_covenant: {e}")
    return redirect(url_for('listar_convenios'))

# --- SCHEDULES ROUTES ---
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
                    horario['profissional_id_fk'] = profissional_id_atual # Changed
                    horario['profissional_nome'] = profissional_nome_atual # Changed
                    
                    dias_semana_map = {0: 'Sunday', 1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 4: 'Thursday', 5: 'Friday', 6: 'Saturday'}
                    horario['dia_semana_nome'] = dias_semana_map.get(horario.get('dia_semana'), 'N/A')
                    
                    todos_horarios_formatados.append(horario)
    
    except Exception as e:
        flash(f'Error listing schedules: {e}.', 'danger')
        print(f"Error list_schedules: {e}")
    
    return render_template('horarios.html', horarios=todos_horarios_formatados, current_year=datetime.datetime.now(SAO_PAULO_TZ).year)


@app.route('/horarios/novo', methods=['GET', 'POST'])
@login_required
@admin_required # Only admins can add schedules directly (or the doctor themselves)
def adicionar_horario():
    clinica_id = session['clinica_id']
    profissionais_ativos_lista = []
    try:
        profissionais_docs = db.collection('clinicas').document(clinica_id).collection('profissionais').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()
        for doc in profissionais_docs:
            p_data = doc.to_dict()
            if p_data: profissionais_ativos_lista.append({'id': doc.id, 'nome': p_data.get('nome', doc.id)})
    except Exception as e:
        flash('Error loading active professionals.', 'danger')
        print(f"Error loading professionals (add_schedule GET): {e}")

    dias_semana_map = {0: 'Sunday', 1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 4: 'Thursday', 5: 'Friday', 6: 'Saturday'}

    if request.method == 'POST':
        try:
            profissional_id_selecionado = request.form['profissional_id'] # Changed
            dia_semana = int(request.form['dia_semana'])
            hora_inicio = request.form['hora_inicio']
            hora_fim = request.form['hora_fim']
            intervalo_minutos_str = request.form.get('intervalo_minutos')
            intervalo_minutos = int(intervalo_minutos_str) if intervalo_minutos_str and intervalo_minutos_str.isdigit() else None
            ativo = 'ativo' in request.form 

            if not profissional_id_selecionado:
                flash('Please select a professional.', 'warning')
            elif hora_inicio >= hora_fim:
                flash('Start time must be before end time.', 'warning')
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

                db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_id_selecionado).collection('horarios_disponiveis').add(horario_data) # Changed
                flash('Schedule added successfully!', 'success')
                return redirect(url_for('listar_horarios'))
        except ValueError:
            flash('Invalid numeric values for day or interval.', 'danger')
        except Exception as e:
            flash(f'Error adding schedule: {e}', 'danger')
            print(f"Error add_schedule (POST): {e}")
            
    return render_template('horario_form.html', 
                           profissionais=profissionais_ativos_lista, # Changed
                           dias_semana=dias_semana_map, 
                           horario=None, 
                           action_url=url_for('adicionar_horario'),
                           page_title='Add New Schedule',
                           current_year=datetime.datetime.now(SAO_PAULO_TZ).year)


@app.route('/profissionais/<string:profissional_doc_id>/horarios/editar/<string:horario_doc_id>', methods=['GET', 'POST']) # Changed
@login_required
def editar_horario(profissional_doc_id, horario_doc_id): # Changed
    clinica_id = session['clinica_id']
    horario_ref = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).collection('horarios_disponiveis').document(horario_doc_id) # Changed
    
    profissionais_ativos_lista = [] # Changed
    try:
        profissionais_docs = db.collection('clinicas').document(clinica_id).collection('profissionais').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream() # Changed
        for doc in profissionais_docs: # Changed
            p_data = doc.to_dict() # Changed
            if p_data: profissionais_ativos_lista.append({'id': doc.id, 'nome': p_data.get('nome', doc.id)}) # Changed
    except Exception as e:
        flash('Error loading active professionals for the form.', 'danger') # Changed
        print(f"Error loading professionals (edit_schedule GET): {e}") # Changed

    dias_semana_map = {0: 'Sunday', 1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 4: 'Thursday', 5: 'Friday', 6: 'Saturday'}

    if request.method == 'POST':
        try:
            dia_semana = int(request.form['dia_semana'])
            hora_inicio = request.form['hora_inicio']
            hora_fim = request.form['hora_fim']
            intervalo_minutos_str = request.form.get('intervalo_minutos')
            intervalo_minutos = int(intervalo_minutos_str) if intervalo_minutos_str and intervalo_minutos_str.isdigit() else None
            ativo = 'ativo' in request.form

            if hora_inicio >= hora_fim:
                flash('Start time must be before end time.', 'warning')
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
                flash('Schedule updated successfully!', 'success')
                return redirect(url_for('listar_horarios'))
        except ValueError:
            flash('Invalid numeric values.', 'danger')
        except Exception as e:
            flash(f'Error updating schedule: {e}', 'danger')
            print(f"Error edit_schedule (POST): {e}")
    
    try:
        horario_doc_snapshot = horario_ref.get()
        if horario_doc_snapshot.exists:
            horario_data_db = horario_doc_snapshot.to_dict()
            if horario_data_db:
                horario_data_db['id'] = horario_doc_snapshot.id 
                horario_data_db['profissional_id_fk'] = profissional_doc_id # Changed
                
                # Gets the professional's name (former barber)
                profissional_pai_doc = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).get() # Changed
                if profissional_pai_doc.exists: # Changed
                    profissional_pai_data = profissional_pai_doc.to_dict() # Changed
                    if profissional_pai_data: # Changed
                         horario_data_db['profissional_nome_atual'] = profissional_pai_data.get('nome', profissional_doc_id) # Changed
                
                return render_template('horario_form.html', 
                                       profissionais=profissionais_ativos_lista, # Changed
                                       dias_semana=dias_semana_map, 
                                       horario=horario_data_db, 
                                       action_url=url_for('editar_horario', profissional_doc_id=profissional_doc_id, horario_doc_id=horario_doc_id), # Changed
                                       page_title=f"Edit Schedule for {horario_data_db.get('profissional_nome_atual', 'Professional')}", # Changed
                                       current_year=datetime.datetime.now(SAO_PAULO_TZ).year)
        else:
            flash('Specific schedule not found.', 'danger')
            return redirect(url_for('listar_horarios'))
    except Exception as e:
        flash(f'Error loading schedule for editing: {e}', 'danger')
        print(f"Error edit_schedule (GET): {e}")
        return redirect(url_for('listar_horarios'))


@app.route('/profissionais/<string:profissional_doc_id>/horarios/excluir/<string:horario_doc_id>', methods=['POST']) # Changed
@login_required
@admin_required # Only admins can delete schedules (or the doctor themselves)
def excluir_horario(profissional_doc_id, horario_doc_id): # Changed
    clinica_id = session['clinica_id']
    try:
        db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).collection('horarios_disponiveis').document(horario_doc_id).delete() # Changed
        flash('Available schedule deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting schedule: {e}', 'danger')
        print(f"Error delete_schedule: {e}")
    return redirect(url_for('listar_horarios'))

@app.route('/profissionais/<string:profissional_doc_id>/horarios/ativar_desativar/<string:horario_doc_id>', methods=['POST']) # Changed
@login_required
@admin_required # Only admins can activate/deactivate schedules (or the doctor themselves)
def ativar_desativar_horario(profissional_doc_id, horario_doc_id): # Changed
    clinica_id = session['clinica_id']
    horario_ref = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).collection('horarios_disponiveis').document(horario_doc_id) # Changed
    try:
        horario_doc = horario_ref.get()
        if horario_doc.exists:
            data = horario_doc.to_dict()
            if data:
                current_status = data.get('ativo', False) 
                new_status = not current_status
                horario_ref.update({'ativo': new_status, 'atualizado_em': firestore.SERVER_TIMESTAMP})
                flash(f'Schedule {"enabled" if new_status else "disabled"} successfully!', 'success')
            else:
                flash('Invalid schedule data.', 'danger')
        else:
            flash('Schedule not found.', 'danger')
    except Exception as e:
        flash(f'Error changing schedule status: {e}', 'danger')
        print(f"Error in activate_deactivate_schedule: {e}")
    return redirect(url_for('listar_horarios'))

# --- APPOINTMENTS ROUTES ---
@app.route('/agendamentos')
@login_required
def listar_agendamentos():
    clinica_id = session['clinica_id']
    agendamentos_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos')
    agendamentos_lista = []
    
    profissionais_para_filtro = [] # Changed
    servicos_procedimentos_ativos = [] # Changed
    pacientes_para_filtro = [] # New

    try:
        profissionais_docs = db.collection('clinicas').document(clinica_id).collection('profissionais').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream() # Changed
        for doc in profissionais_docs: # Changed
            p_data = doc.to_dict() # Changed
            if p_data: profissionais_para_filtro.append({'id': doc.id, 'nome': p_data.get('nome', doc.id)}) # Changed
        
        servicos_docs = db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').order_by('nome').stream() # Changed
        for doc in servicos_docs: # Changed
            s_data = doc.to_dict() # Changed
            if s_data: servicos_procedimentos_ativos.append({'id': doc.id, 'nome': s_data.get('nome', doc.id), 'preco': s_data.get('preco_sugerido', 0.0)}) # Changed

        pacientes_docs = db.collection('clinicas').document(clinica_id).collection('pacientes').order_by('nome').stream()
        for doc in pacientes_docs:
            pac_data = doc.to_dict()
            if pac_data: pacientes_para_filtro.append({'id': doc.id, 'nome': pac_data.get('nome', doc.id)})


    except Exception as e:
        flash('Error loading data for filters/modal.', 'warning')
        print(f"Error loading professionals/services_procedures/patients for filters: {e}") # Changed

    filtros_atuais = {
        'paciente_nome': request.args.get('paciente_nome', '').strip(), # Changed
        'profissional_id': request.args.get('profissional_id', '').strip(), # Changed
        'status': request.args.get('status', '').strip(),
        'data_inicio': request.args.get('data_inicio', '').strip(),
        'data_fim': request.args.get('data_fim', '').strip(),
    }

    # UPDATED LOGIC: Sets default filter to current month if no dates are provided
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

    if filtros_atuais['paciente_nome']: # Changed
        query = query.where(filter=FieldFilter('paciente_nome', '>=', filtros_atuais['paciente_nome'])).where(filter=FieldFilter('paciente_nome', '<=', filtros_atuais['paciente_nome'] + '\uf8ff')) # Changed
    if filtros_atuais['profissional_id']: # Changed
        query = query.where(filter=FieldFilter('profissional_id', '==', filtros_atuais['profissional_id'])) # Changed
    if filtros_atuais['status']:
        query = query.where(filter=FieldFilter('status', '==', filtros_atuais['status']))
    if filtros_atuais['data_inicio']:
        try:
            dt_inicio_utc = SAO_PAULO_TZ.localize(datetime.datetime.strptime(filtros_atuais['data_inicio'], '%Y-%m-%d')).astimezone(pytz.utc)
            query = query.where(filter=FieldFilter('data_agendamento_ts', '>=', dt_inicio_utc))
        except ValueError:
            flash('Invalid start date. Use YYYY-MM-DD format.', 'warning')
    if filtros_atuais['data_fim']:
        try:
            dt_fim_utc = SAO_PAULO_TZ.localize(datetime.datetime.strptime(filtros_atuais['data_fim'], '%Y-%m-%d').replace(hour=23, minute=59, second=59)).astimezone(pytz.utc)
            query = query.where(filter=FieldFilter('data_agendamento_ts', '<=', dt_fim_utc))
        except ValueError:
            flash('Invalid end date. Use YYYY-MM-DD format.', 'warning')

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
                
                # Adjusts the field name for price
                ag['preco_servico_fmt'] = "R$ {:.2f}".format(float(ag.get('servico_procedimento_preco', 0))).replace('.', ',') # Changed
                data_criacao_ts = ag.get('data_criacao')
                if isinstance(data_criacao_ts, datetime.datetime):
                    ag['data_criacao_fmt'] = data_criacao_ts.astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                else:
                    ag['data_criacao_fmt'] = "N/A"
                agendamentos_lista.append(ag)
    except Exception as e:
        flash(f'Error listing appointments: {e}. Check your Firestore indexes.', 'danger')
        print(f"Error list_appointments: {e}")
    
    stats_cards = {
        'confirmado': {'count': 0, 'total_valor': 0.0},
        'concluido': {'count': 0, 'total_valor': 0.0},
        'cancelado': {'count': 0, 'total_valor': 0.0},
        'pendente': {'count': 0, 'total_valor': 0.0}
    }
    for agendamento in agendamentos_lista:
        status = agendamento.get('status', 'pendente').lower()
        preco = float(agendamento.get('servico_procedimento_preco', 0)) # Changed
        if status in stats_cards:
            stats_cards[status]['count'] += 1
            stats_cards[status]['total_valor'] += preco

    return render_template('agendamentos.html', 
                           agendamentos=agendamentos_lista,
                           stats_cards=stats_cards,
                           profissionais_para_filtro=profissionais_para_filtro, # Changed
                           servicos_ativos=servicos_procedimentos_ativos, # Changed
                           pacientes_para_filtro=pacientes_para_filtro, # New
                           filtros_atuais=filtros_atuais,
                           current_year=datetime.datetime.now(SAO_PAULO_TZ).year)

@app.route('/agendamentos/registrar_manual', methods=['POST'])
@login_required
def registrar_atendimento_manual():
    clinica_id = session['clinica_id']
    try:
        paciente_nome = request.form.get('cliente_nome_manual') # Changed
        paciente_telefone = request.form.get('cliente_telefone_manual') # Changed
        profissional_id_manual = request.form.get('barbeiro_id_manual') # Changed
        servico_procedimento_id_manual = request.form.get('servico_id_manual') # Changed
        data_agendamento_str = request.form.get('data_agendamento_manual')
        hora_agendamento_str = request.form.get('hora_agendamento_manual')
        preco_str = request.form.get('preco_manual')
        status_manual = request.form.get('status_manual')

        if not all([paciente_nome, profissional_id_manual, servico_procedimento_id_manual, data_agendamento_str, hora_agendamento_str, preco_str, status_manual]):
            flash('All mandatory fields must be filled.', 'danger')
            return redirect(url_for('listar_agendamentos'))

        preco_servico = float(preco_str.replace(',', '.'))

        # Searches for the patient ID by name, or creates a new patient if it doesn't exist
        paciente_ref_query = db.collection('clinicas').document(clinica_id).collection('pacientes')\
                               .where(filter=FieldFilter('nome', '==', paciente_nome)).limit(1).get()
        
        paciente_doc_id = None
        if paciente_ref_query:
            for doc in paciente_ref_query:
                paciente_doc_id = doc.id
                break
        
        if not paciente_doc_id:
            # Creates a new patient if not found
            novo_paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').add({
                'nome': paciente_nome,
                'contato_telefone': paciente_telefone if paciente_telefone else None,
                'data_cadastro': firestore.SERVER_TIMESTAMP
            })
            paciente_doc_id = novo_paciente_ref[1].id # Gets the ID of the newly created document

        profissional_doc = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_id_manual).get() # Changed
        servico_procedimento_doc = db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_procedimento_id_manual).get() # Changed

        profissional_nome = profissional_doc.to_dict().get('nome', 'N/A') if profissional_doc.exists else 'N/A' # Changed
        servico_procedimento_nome = servico_procedimento_doc.to_dict().get('nome', 'N/A') if servico_procedimento_doc.exists else 'N/A' # Changed
        
        dt_agendamento_naive = datetime.datetime.strptime(f"{data_agendamento_str} {hora_agendamento_str}", "%Y-%m-%d %H:%M")
        dt_agendamento_sp = SAO_PAULO_TZ.localize(dt_agendamento_naive)
        data_agendamento_ts_utc = dt_agendamento_sp.astimezone(pytz.utc)

        novo_agendamento_dados = {
            'paciente_id': paciente_doc_id, # New field
            'paciente_nome': paciente_nome, # Changed
            'paciente_numero': paciente_telefone if paciente_telefone else None, # Changed
            'profissional_id': profissional_id_manual, # Changed
            'profissional_nome': profissional_nome, # Changed
            'servico_procedimento_id': servico_procedimento_id_manual, # Changed
            'servico_procedimento_nome': servico_procedimento_nome, # Changed
            'data_agendamento': data_agendamento_str,
            'hora_agendamento': hora_agendamento_str,
            'data_agendamento_ts': data_agendamento_ts_utc,
            'servico_procedimento_preco': preco_servico, # Changed to reflect price in appointment
            'status': status_manual,
            'tipo_agendamento': 'manual_dashboard',
            'data_criacao': firestore.SERVER_TIMESTAMP,
            'atualizado_em': firestore.SERVER_TIMESTAMP
        }
        
        db.collection('clinicas').document(clinica_id).collection('agendamentos').add(novo_agendamento_dados)
        
        flash('Attendance registered manually successfully!', 'success')
    except ValueError as ve:
        flash(f'Value error registering attendance: {ve}', 'danger')
    except Exception as e:
        flash(f'Error registering manual attendance: {e}', 'danger')
    return redirect(url_for('listar_agendamentos'))


@app.route('/agendamentos/alterar_status/<string:agendamento_doc_id>', methods=['POST'])
@login_required
def alterar_status_agendamento(agendamento_doc_id):
    clinica_id = session['clinica_id']
    novo_status = request.form.get('status')
    if not novo_status:
        flash('No status was provided.', 'warning')
        return redirect(url_for('listar_agendamentos'))
    try:
        db.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_doc_id).update({
            'status': novo_status,
            'atualizado_em': firestore.SERVER_TIMESTAMP
        })
        flash(f'Status updated to "{novo_status}" successfully!', 'success')
    except Exception as e:
        flash(f'Error changing appointment status: {e}', 'danger')
        print(f"Error change_appointment_status: {e}")
    return redirect(url_for('listar_agendamentos'))

# --- PATIENT RECORDS ROUTES (NEW) ---
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
        flash('Error loading patient list for search.', 'danger')
        print(f"Error search_patient_record: {e}")

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

            # Adds covenant information if it exists
            if paciente_data.get('convenio_id'):
                convenio_doc = db.collection('clinicas').document(clinica_id).collection('convenios').document(paciente_data['convenio_id']).get()
                if convenio_doc.exists:
                    paciente_data['convenio_nome'] = convenio_doc.to_dict().get('nome', 'N/A')
            
            # Searches all patient record entries
            docs_stream = prontuarios_ref.order_by('data_registro', direction=firestore.Query.DESCENDING).stream()
            for doc in docs_stream:
                registro = doc.to_dict()
                if registro:
                    registro['id'] = doc.id
                    if registro.get('data_registro') and isinstance(registro['data_registro'], datetime.datetime):
                        registro['data_registro_fmt'] = registro['data_registro'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                    else:
                        registro['data_registro_fmt'] = "N/A"
                    
                    # Loads the name of the professional who created the record
                    profissional_doc_id = registro.get('profissional_id')
                    if profissional_doc_id:
                        prof_doc = db.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).get()
                        if prof_doc.exists:
                            registro['profissional_nome'] = prof_doc.to_dict().get('nome', 'Unknown')
                        else:
                            registro['profissional_nome'] = 'Unknown'

                    registros_prontuario.append(registro)
        else:
            flash('Patient not found.', 'danger')
            return redirect(url_for('buscar_prontuario'))
    except Exception as e:
        flash(f'Error loading patient record: {e}.', 'danger')
        print(f"Error view_patient_record: {e}")

    return render_template('prontuario.html', paciente=paciente_data, registros=registros_prontuario)

@app.route('/prontuarios/<string:paciente_doc_id>/anamnese/novo', methods=['GET', 'POST'])
@login_required
def adicionar_anamnese(paciente_doc_id):
    clinica_id = session['clinica_id']
    # The user_uid in the session is the UID of the logged-in Firebase Auth user
    profissional_logado_uid = session.get('user_uid') 

    # Searches for the professional ID associated with the logged-in user_uid
    profissional_doc_id = None
    profissional_nome = "Unknown Professional"
    try:
        prof_query = db.collection('clinicas').document(clinica_id).collection('profissionais')\
                       .where(filter=FieldFilter('user_uid', '==', profissional_logado_uid)).limit(1).get()
        for doc in prof_query:
            profissional_doc_id = doc.id
            profissional_nome = doc.to_dict().get('nome', profissional_nome)
            break
        if not profissional_doc_id:
             flash('Your user is not associated with a professional. Contact the administrator.', 'danger')
             return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    except Exception as e:
        flash(f'Error verifying associated professional: {e}', 'danger')
        print(f"Error add_anamnesis (GET - professional check): {e}")
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))

    paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
    paciente_doc = paciente_ref.get()
    if not paciente_doc.exists:
        flash('Patient not found.', 'danger')
        return redirect(url_for('buscar_prontuario'))
    
    paciente_nome = paciente_doc.to_dict().get('nome', 'Unknown Patient')

    modelos_anamnese = []
    try:
        modelos_docs = db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').order_by('identificacao').stream()
        for doc in modelos_docs:
            modelo = convert_doc_to_dict(doc) # Use the new converter
            modelos_anamnese.append(modelo)
    except Exception as e:
        flash('Error loading anamnesis templates.', 'warning')
        print(f"Error loading anamnesis templates: {e}")

    if request.method == 'POST':
        conteudo = request.form['conteudo']
        modelo_base_id = request.form.get('modelo_base_id')
        print(f"DEBUG (add_anamnese): Conteúdo recebido: {conteudo[:100]}...") # Log first 100 chars
        print(f"DEBUG (add_anamnese): Todos os dados do formulário: {request.form}") # NOVO LOG
        
        try:
            db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').add({
                'profissional_id': profissional_doc_id, # Saves the ID of the professional who created it
                'data_registro': firestore.SERVER_TIMESTAMP,
                'tipo_registro': 'anamnese',
                'conteudo': conteudo,
                'modelo_base_id': modelo_base_id if modelo_base_id else None
            })
            flash('Anamnesis added successfully!', 'success')
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
        except Exception as e:
            flash(f'Error adding anamnesis: {e}', 'danger')
            print(f"Error add_anamnesis (POST): {e}")
    
    return render_template('anamnese_form.html', 
                           paciente_id=paciente_doc_id, 
                           paciente_nome=paciente_nome, 
                           modelos_anamnese=modelos_anamnese, 
                           action_url=url_for('adicionar_anamnese', paciente_doc_id=paciente_doc_id),
                           page_title=f"Register Anamnesis for {paciente_nome}")

@app.route('/prontuarios/<string:paciente_doc_id>/anamnese/editar/<string:anamnese_doc_id>', methods=['GET', 'POST'])
@login_required
def editar_anamnese(paciente_doc_id, anamnese_doc_id):
    clinica_id = session['clinica_id']
    anamnese_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id).collection('prontuarios').document(anamnese_doc_id)
    paciente_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_doc_id)
    
    paciente_doc = paciente_ref.get()
    if not paciente_doc.exists:
        flash('Patient not found.', 'danger')
        return redirect(url_for('buscar_prontuario'))
    
    paciente_nome = paciente_doc.to_dict().get('nome', 'Unknown Patient')

    modelos_anamnese = []
    try:
        modelos_docs = db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').order_by('identificacao').stream()
        for doc in modelos_docs:
            modelo = convert_doc_to_dict(doc) # Use the new converter
            modelos_anamnese.append(modelo)
    except Exception as e:
        flash('Error loading anamnesis templates.', 'warning')
        print(f"Error loading anamnesis templates (edit): {e}")

    if request.method == 'POST':
        conteudo = request.form['conteudo']
        modelo_base_id = request.form.get('modelo_base_id')
        print(f"DEBUG (edit_anamnese): Conteúdo recebido: {conteudo[:100]}...") # Log first 100 chars
        print(f"DEBUG (edit_anamnese): Todos os dados do formulário: {request.form}") # NOVO LOG
        
        try:
            anamnese_ref.update({
                'conteudo': conteudo,
                'modelo_base_id': modelo_base_id if modelo_base_id else None,
                'atualizado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Anamnesis updated successfully!', 'success')
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
        except Exception as e:
            flash(f'Error updating anamnesis: {e}', 'danger')
            print(f"Error edit_anamnesis (POST): {e}")
    
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
                                   page_title=f"Edit Anamnesis for {paciente_nome}")
        else:
            flash('Anamnesis not found or invalid record type.', 'danger')
            return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))
    except Exception as e:
        flash(f'Error loading anamnesis for editing: {e}', 'danger')
        print(f"Error edit_anamnesis (GET): {e}")
        return redirect(url_for('ver_prontuario', paciente_doc_id=paciente_doc_id))


# --- ANAMNESIS TEMPLATES ROUTES (NEW) ---
@app.route('/modelos_anamnese')
@login_required
@admin_required
def listar_modelos_anamnese():
    clinica_id = session['clinica_id']
    modelos_lista = []
    try:
        docs = db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').order_by('identificacao').stream()
        for doc in docs:
            modelo = convert_doc_to_dict(doc) # Use the new converter
            modelos_lista.append(modelo)
    except Exception as e:
        flash(f'Error listing anamnesis templates: {e}.', 'danger')
        print(f"Error list_anamnesis_templates: {e}")
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
            flash('Template identification is mandatory.', 'danger')
            return render_template('modelo_anamnese_form.html', modelo=request.form, action_url=url_for('adicionar_modelo_anamnese'))
        try:
            db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').add({
                'identificacao': identificacao,
                'conteudo_modelo': conteudo_modelo,
                'criado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Anamnesis template added successfully!', 'success')
            return redirect(url_for('listar_modelos_anamnese'))
        except Exception as e:
            flash(f'Error adding anamnesis template: {e}', 'danger')
            print(f"Error add_anamnesis_template: {e}")
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
            flash('Template identification is mandatory.', 'danger')
            return render_template('modelo_anamnese_form.html', modelo=request.form, action_url=url_for('editar_modelo_anamnese', modelo_doc_id=modelo_doc_id))
        try:
            modelo_ref.update({
                'identificacao': identificacao,
                'conteudo_modelo': conteudo_modelo,
                'atualizado_em': firestore.SERVER_TIMESTAMP
            })
            flash('Anamnesis template updated successfully!', 'success')
            return redirect(url_for('listar_modelos_anamnese'))
        except Exception as e:
            flash(f'Error updating anamnesis template: {e}', 'danger')
            print(f"Error edit_anamnesis_template (POST): {e}")

    try:
        modelo_doc = modelo_ref.get()
        if modelo_doc.exists:
            modelo = convert_doc_to_dict(modelo_doc) # Use the new converter
            return render_template('modelo_anamnese_form.html', modelo=modelo, action_url=url_for('editar_modelo_anamnese', modelo_doc_id=modelo_doc_id))
        else:
            flash('Anamnesis template not found.', 'danger')
            return redirect(url_for('listar_modelos_anamnese'))
    except Exception as e:
        flash(f'Error loading anamnesis template for editing: {e}', 'danger')
        print(f"Error edit_anamnesis_template (GET): {e}")
        return redirect(url_for('listar_modelos_anamnese'))

@app.route('/modelos_anamnese/excluir/<string:modelo_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_modelo_anamnese(modelo_doc_id):
    clinica_id = session['clinica_id']
    try:
        # TODO: If there are records referencing this template, it might be necessary to check before deleting.
        # For simplicity, for now, deletion is direct.
        db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').document(modelo_doc_id).delete()
        flash('Anamnesis template deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting anamnesis template: {e}.', 'danger')
        print(f"Error delete_anamnesis_template: {e}")
    return redirect(url_for('listar_modelos_anamnese'))

# --- APP EXECUTION ---
if __name__ == '__main__':
    # For local execution, use a .env for GOOGLE_SERVICE_ACCOUNT_KEY_JSON and PORT
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)), debug=True)
