import datetime
import json
from flask import Blueprint, render_template, session, flash, redirect, url_for, request, current_app
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.field_path import FieldPath
from collections import Counter
from decorators.auth_decorators import login_required
import pytz # Adicionado: Importar pytz para manipulação de fuso horário

dashboard_bp = Blueprint('dashboard_bp', __name__)

@dashboard_bp.route('/')
@login_required
def index():
    db = current_app.config['DB']
    SAO_PAULO_TZ = current_app.config['SAO_PAULO_TZ']

    try:
        clinica_id = session['clinica_id']
        user_role = session.get('user_role')
        user_uid = session.get('user_uid')
    except KeyError:
        flash("Sessão inválida ou expirada. Por favor, faça login novamente.", "danger")
        return redirect(url_for('auth_users_bp.login_page'))

    profissional_id_logado = None
    if user_role != 'admin':
        if not user_uid:
            flash("UID do usuário não encontrado na sessão. Faça login novamente.", "danger")
            return redirect(url_for('auth_users_bp.login_page'))
        try:
            user_doc = db.collection('User').document(user_uid).get()
            if user_doc.exists:
                profissional_id_logado = user_doc.to_dict().get('profissional_id')
            
            if not profissional_id_logado:
                flash("Sua conta de usuário não está corretamente associada a um perfil de profissional. Contate o administrador.", "warning")
        except Exception as e:
            flash(f"Erro ao buscar informações do profissional: {e}", "danger")
            return render_template('dashboard.html', kpi={}, proximos_agendamentos=[], now=datetime.datetime.now(SAO_PAULO_TZ), timedelta=datetime.timedelta)

    agendamentos_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos')
    pacientes_ref = db.collection('clinicas').document(clinica_id).collection('pacientes')
    convenios_ref = db.collection('clinicas').document(clinica_id).collection('convenios')
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

    # Pré-carregar nomes de convênios para pacientes
    convenios_dict = {}
    try:
        convenios_docs = convenios_ref.stream()
        for doc in convenios_docs:
            convenios_dict[doc.id] = doc.to_dict().get('nome', 'Convênio Desconhecido')
    except Exception as e:
        print(f"Erro ao carregar convênios para o dashboard: {e}")
        # Não flashear aqui, pois o dashboard deve carregar mesmo com erro de convênio

    # Mapear IDs de paciente para informações completas do paciente
    pacientes_info_map = {} # Corrigido: Variável nomeada corretamente
    try:
        pacientes_docs = pacientes_ref.stream()
        for doc in pacientes_docs:
            pacientes_info_map[doc.id] = doc.to_dict()
    except Exception as e:
        print(f"Erro ao carregar informações de pacientes para o dashboard: {e}")
    
    # Adicionado: Dados para os filtros do dashboard
    pacientes_para_filtro = []
    convenios_para_filtro = []

    try:
        pacientes_docs = db.collection('clinicas').document(clinica_id).collection('pacientes').order_by('nome').stream()
        for doc in pacientes_docs:
            p_data = doc.to_dict()
            if p_data: pacientes_para_filtro.append({'id': doc.id, 'nome': p_data.get('nome', doc.id)})
        
        convenios_docs = db.collection('clinicas').document(clinica_id).collection('convenios').order_by('nome').stream()
        for doc in convenios_docs:
            c_data = doc.to_dict()
            if c_data: convenios_para_filtro.append({'id': doc.id, 'nome': c_data.get('nome', doc.id)})
    except Exception as e:
        print(f"Erro ao carregar dados para os filtros do dashboard: {e}")

    # Filtros atuais do dashboard
    filtros_atuais_dashboard = {
        'paciente_id': request.args.get('paciente_id', '').strip(),
        'convenio_id': request.args.get('convenio_id', '').strip(),
        'data_inicio_dashboard': request.args.get('data_inicio_dashboard', '').strip(),
        'data_fim_dashboard': request.args.get('data_fim_dashboard', '').strip(),
    }

    proximos_agendamentos_lista = []
    try:
        query_proximos = agendamentos_ref.where(
            filter=FieldFilter('status', '==', 'confirmado')
        )
        
        # Aplicar filtros do dashboard se existirem
        if filtros_atuais_dashboard['paciente_id']:
            query_proximos = query_proximos.where(
                filter=FieldFilter('paciente_id', '==', filtros_atuais_dashboard['paciente_id'])
            )
        if filtros_atuais_dashboard['convenio_id']:
            # Para filtrar por convênio, precisamos primeiro filtrar os pacientes com esse convênio
            pacientes_com_convenio_ids = []
            pacientes_query = pacientes_ref.where(
                filter=FieldFilter('convenio_id', '==', filtros_atuais_dashboard['convenio_id'])
            ).stream()
            for p_doc in pacientes_query:
                pacientes_com_convenio_ids.append(p_doc.id)
            
            if pacientes_com_convenio_ids:
                query_proximos = query_proximos.where(
                    filter=FieldFilter('paciente_id', 'in', pacientes_com_convenio_ids)
                )
            else: # Se não houver pacientes com o convênio selecionado, retorne uma lista vazia
                # Passar as variáveis now e timedelta para o template, mesmo em caso de erro de filtro
                return render_template(
                    'dashboard.html',
                    current_year=current_year,
                    mes_atual_nome=mes_atual_nome,
                    kpi=kpi_cards,
                    proximos_agendamentos=[], # Lista vazia
                    dados_atendimento_vs_receita=json.dumps(dados_atendimento_vs_receita),
                    dados_receita_procedimento=json.dumps(dados_receita_procedimento),
                    dados_desempenho_profissional=json.dumps(dados_desempenho_profissional),
                    pacientes_para_filtro=pacientes_para_filtro,
                    convenios_para_filtro=convenios_para_filtro,
                    filtros_atuais_dashboard=filtros_atuais_dashboard,
                    now=datetime.datetime.now(SAO_PAULO_TZ),
                    timedelta=datetime.timedelta
                )
        
        if filtros_atuais_dashboard['data_inicio_dashboard']:
            try:
                # O fuso horário de São Paulo pode ser 'America/Sao_Paulo'
                dt_inicio_local = SAO_PAULO_TZ.localize(datetime.datetime.strptime(filtros_atuais_dashboard['data_inicio_dashboard'], '%Y-%m-%d'))
                dt_inicio_utc = dt_inicio_local.astimezone(pytz.utc)
                query_proximos = query_proximos.where(filter=FieldFilter('data_agendamento_ts', '>=', dt_inicio_utc))
            except ValueError:
                flash('Data de início inválida no filtro do dashboard. Use o formato AAAA-MM-DD.', 'warning')
        if filtros_atuais_dashboard['data_fim_dashboard']:
            try:
                # O fuso horário de São Paulo pode ser 'America/Sao_Paulo'
                dt_fim_local = SAO_PAULO_TZ.localize(datetime.datetime.strptime(filtros_atuais_dashboard['data_fim_dashboard'], '%Y-%m-%d').replace(hour=23, minute=59, second=59))
                dt_fim_utc = dt_fim_local.astimezone(pytz.utc)
                query_proximos = query_proximos.where(filter=FieldFilter('data_agendamento_ts', '<=', dt_fim_utc))
            except ValueError:
                flash('Data de término inválida no filtro do dashboard. Use o formato AAAA-MM-DD.', 'warning')

        # Filtro de profissional logado (já existente)
        if user_role != 'admin':
            if profissional_id_logado:
                query_proximos = query_proximos.where(
                    filter=FieldFilter('profissional_id', '==', profissional_id_logado)
                )
            else:
                proximos_agendamentos_lista = []  # Nenhuma agenda para este profissional
        
        # Somente execute a consulta se houver um profissional logado ou se for admin
        if user_role == 'admin' or profissional_id_logado:
            docs_proximos = query_proximos.order_by('data_agendamento_ts').limit(10).stream()
            for doc in docs_proximos:
                ag_data = doc.to_dict()
                if ag_data and ag_data.get('data_agendamento_ts'):
                    paciente_id = ag_data.get('paciente_id')
                    convenio_nome = 'Particular' # Default
                    # Corrigido: Usar pacientes_info_map
                    if paciente_id and paciente_id in pacientes_info_map:
                        paciente_data = pacientes_info_map[paciente_id]
                        convenio_id_paciente = paciente_data.get('convenio_id')
                        if convenio_id_paciente and convenio_id_paciente in convenios_dict:
                            convenio_nome = convenios_dict[convenio_id_paciente]

                    proximos_agendamentos_lista.append({
                        'id_agendamento': doc.id, # Adicionado ID do agendamento para futuras modificações
                        'id_profissional': ag_data.get('profissional_id'),
                        'data_agendamento': ag_data.get('data_agendamento_ts').strftime('%d/%m/%Y'),
                        'hora_agendamento': ag_data.get('hora_agendamento', "N/A"),
                        'cliente_nome': ag_data.get('paciente_nome', "N/A"),
                        'profissional_nome': ag_data.get('profissional_nome', "N/A"),
                        'servico_procedimento_nome': ag_data.get('servico_procedimento_nome', "N/A"),
                        'preco': float(ag_data.get('servico_procedimento_preco', 0.0)),
                        'convenio_nome': convenio_nome
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
        pacientes_para_filtro=pacientes_para_filtro, # Passar para o filtro do dashboard
        convenios_para_filtro=convenios_para_filtro, # Passar para o filtro do dashboard
        filtros_atuais_dashboard=filtros_atuais_dashboard, # Passar os filtros atuais
        now=hoje_dt, # Passar a variável now para o template
        timedelta=datetime.timedelta # Passar timedelta para o template
    )
