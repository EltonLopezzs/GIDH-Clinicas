from flask import render_template, session, flash, redirect, url_for, request, jsonify
import datetime
import pytz
import json
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore # Importar no topo


# Importar utils
from utils import get_db, login_required, SAO_PAULO_TZ


def register_appointments_routes(app):
    @app.route('/agendamentos', endpoint='listar_agendamentos')
    @login_required
    def listar_agendamentos():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        agendamentos_ref = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos')
        agendamentos_lista = []
        
        profissionais_para_filtro = []
        servicos_procedimentos_ativos = []
        pacientes_para_filtro = []

        try:
            profissionais_docs = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()
            for doc in profissionais_docs:
                p_data = doc.to_dict()
                if p_data: profissionais_para_filtro.append({'id': doc.id, 'nome': p_data.get('nome', doc.id)})
            
            servicos_docs = db_instance.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').order_by('nome').stream()
            for doc in servicos_docs:
                s_data = doc.to_dict()
                if s_data: servicos_procedimentos_ativos.append({'id': doc.id, 'nome': s_data.get('nome', doc.id), 'preco': s_data.get('preco_sugerido', 0.0)})

            pacientes_docs = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').order_by('nome').stream()
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

    @app.route('/agendamentos/registrar_manual', methods=['POST'], endpoint='registrar_atendimento_manual')
    @login_required
    def registrar_atendimento_manual():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            paciente_id = request.form.get('paciente_id') # Novo: para paciente existente
            paciente_nome = request.form.get('cliente_nome_manual')
            paciente_telefone = request.form.get('cliente_telefone_manual')
            profissional_id_manual = request.form.get('barbeiro_id_manual')
            servico_procedimento_id_manual = request.form.get('servico_id_manual')
            data_agendamento_str = request.form.get('data_agendamento_manual')
            hora_agendamento_str = request.form.get('hora_agendamento_manual')
            preco_str = request.form.get('preco_manual')
            status_manual = request.form.get('status_manual')

            recorrente = request.form.get('recorrente_checkbox') == 'true'
            dias_semana = request.form.getlist('dias_semana') # Lista de strings (ex: ['1', '2'])
            data_fim_recorrencia_str = request.form.get('data_fim_recorrencia')

            if not all([paciente_nome, profissional_id_manual, servico_procedimento_id_manual, data_agendamento_str, hora_agendamento_str, preco_str, status_manual]):
                flash('Todos os campos obrigatórios devem ser preenchidos.', 'danger')
                return redirect(url_for('listar_agendamentos'))

            if recorrente and (not dias_semana or not data_fim_recorrencia_str):
                flash('Para agendamentos recorrentes, selecione os dias da semana e a data de fim da recorrência.', 'danger')
                return redirect(url_for('listar_agendamentos'))

            preco_servico = float(preco_str.replace(',', '.'))

            # Lógica para encontrar ou criar paciente
            paciente_doc_id = None
            if paciente_id: # Se um paciente existente foi selecionado
                paciente_doc = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_id).get()
                if paciente_doc.exists:
                    paciente_doc_id = paciente_doc.id
                    paciente_nome = paciente_doc.to_dict().get('nome', paciente_nome)
                    paciente_telefone = paciente_doc.to_dict().get('contato_telefone', paciente_telefone)
                else:
                    flash('Paciente selecionado não encontrado.', 'danger')
                    return redirect(url_for('listar_agendamentos'))
            else: # Se um novo paciente foi digitado
                paciente_ref_query = db_instance.collection('clinicas').document(clinica_id).collection('pacientes')\
                                                .where(filter=FieldFilter('nome', '==', paciente_nome)).limit(1).get()
                
                if paciente_ref_query:
                    for doc in paciente_ref_query:
                        paciente_doc_id = doc.id
                        break
                
                if not paciente_doc_id:
                    # Cria o novo paciente e obtém a referência do documento
                    _, novo_paciente_doc_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').add({
                        'nome': paciente_nome,
                        'contato_telefone': paciente_telefone if paciente_telefone else None,
                        'data_cadastro': firestore.SERVER_TIMESTAMP
                    })
                    # Atualiza o documento do novo paciente com o id_paciente
                    novo_paciente_doc_ref.update({'id_paciente': novo_paciente_doc_ref.id})
                    paciente_doc_id = novo_paciente_doc_ref.id

            profissional_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_id_manual).get()
            servico_procedimento_doc = db_instance.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_procedimento_id_manual).get()

            profissional_nome = profissional_doc.to_dict().get('nome', 'N/A') if profissional_doc.exists else 'N/A'
            servico_procedimento_nome = servico_procedimento_doc.to_dict().get('nome', 'N/A') if servico_procedimento_doc.exists else 'N/A'
            
            # Lista para armazenar os agendamentos a serem criados
            agendamentos_a_criar = []

            if recorrente:
                data_inicio_recorrencia = datetime.datetime.strptime(data_agendamento_str, '%Y-%m-%d').date()
                data_fim_recorrencia = datetime.datetime.strptime(data_fim_recorrencia_str, '%Y-%m-%d').date()
                
                # Converte os dias da semana selecionados para inteiros
                dias_semana_int = [int(d) for d in dias_semana]

                current_date = data_inicio_recorrencia
                while current_date <= data_fim_recorrencia:
                    python_weekday = current_date.weekday() # 0=Seg, 1=Ter, ..., 6=Dom
                    js_equivalent_weekday = (python_weekday + 1) % 7 # 0=Dom, 1=Seg, ..., 6=Sáb

                    if js_equivalent_weekday in dias_semana_int:
                        dt_agendamento_naive = datetime.datetime.strptime(f"{current_date.strftime('%Y-%m-%d')} {hora_agendamento_str}", "%Y-%m-%d %H:%M")
                        dt_agendamento_sp = SAO_PAULO_TZ.localize(dt_agendamento_naive)
                        data_agendamento_ts_utc = dt_agendamento_sp.astimezone(pytz.utc)

                        agendamentos_a_criar.append({
                            'paciente_id': paciente_doc_id,
                            'paciente_nome': paciente_nome,
                            'paciente_numero': paciente_telefone if paciente_telefone else None,
                            'profissional_id': profissional_id_manual,
                            'profissional_nome': profissional_nome,
                            'servico_procedimento_id': servico_procedimento_id_manual,
                            'servico_procedimento_nome': servico_procedimento_nome,
                            'data_agendamento': current_date.strftime('%Y-%m-%d'),
                            'hora_agendamento': hora_agendamento_str,
                            'data_agendamento_ts': data_agendamento_ts_utc,
                            'servico_procedimento_preco': preco_servico,
                            'status': status_manual,
                            'tipo_agendamento': 'recorrente',
                            'data_criacao': firestore.SERVER_TIMESTAMP,
                            'atualizado_em': firestore.SERVER_TIMESTAMP,
                            'notificacao_pendente': True, # NOVO: Marcar para notificação
                            'tipo_alteracao': 'novo_agendamento', # NOVO: Tipo de alteração
                            'detalhes_alteracao': f'Novo agendamento recorrente com {profissional_nome} para {servico_procedimento_nome} em {current_date.strftime("%d/%m/%Y")} às {hora_agendamento_str}.' # NOVO: Detalhes
                        })
                    current_date += datetime.timedelta(days=1)
                
                if agendamentos_a_criar:
                    batch = db_instance.batch()
                    for agendamento_data in agendamentos_a_criar:
                        new_doc_ref = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').document()
                        batch.set(new_doc_ref, agendamento_data)
                    batch.commit()
                    flash(f'{len(agendamentos_a_criar)} agendamentos recorrentes registrados com sucesso!', 'success')
                else:
                    flash('Nenhum agendamento recorrente foi gerado com os critérios fornecidos.', 'warning')

            else: # Agendamento único
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
                    'atualizado_em': firestore.SERVER_TIMESTAMP,
                    'notificacao_pendente': True, # NOVO: Marcar para notificação
                    'tipo_alteracao': 'novo_agendamento', # NOVO: Tipo de alteração
                    'detalhes_alteracao': f'Novo agendamento com {profissional_nome} para {servico_procedimento_nome} em {data_agendamento_str} às {hora_agendamento_str}.' # NOVO: Detalhes
                }
                
                db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').add(novo_agendamento_dados)
                flash('Atendimento registrado manualmente com sucesso!', 'success')

        except ValueError as ve:
            flash(f'Erro de valor ao registrar atendimento: {ve}', 'danger')
        except Exception as e:
            flash(f'Erro ao registrar atendimento manual: {e}', 'danger')
            print(f"Erro registrar_atendimento_manual: {e}") # Adicionado print para depuração
        return redirect(url_for('listar_agendamentos'))


    @app.route('/agendamentos/alterar_status/<string:agendamento_doc_id>', methods=['POST'], endpoint='alterar_status_agendamento')
    @login_required
    def alterar_status_agendamento(agendamento_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        novo_status = request.form.get('status')
        if not novo_status:
            flash('Nenhum status foi fornecido.', 'warning')
            return redirect(url_for('listar_agendamentos'))
        try:
            agendamento_doc_ref = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_doc_id)
            original_agendamento_data = agendamento_doc_ref.get().to_dict()

            if not original_agendamento_data:
                flash('Agendamento não encontrado.', 'danger')
                return redirect(url_for('listar_agendamentos'))

            old_status = original_agendamento_data.get('status', 'N/A')
            
            # Detalhes da alteração para a notificação
            detalhes_alteracao = f"Status alterado de '{old_status}' para '{novo_status}' para o agendamento de {original_agendamento_data.get('paciente_nome', 'N/A')} em {original_agendamento_data.get('data_agendamento', 'N/A')} às {original_agendamento_data.get('hora_agendamento', 'N/A')}."

            agendamento_doc_ref.update({
                'status': novo_status,
                'atualizado_em': firestore.SERVER_TIMESTAMP,
                'notificacao_pendente': True, # NOVO: Marcar para notificação
                'tipo_alteracao': 'status_alterado', # NOVO: Tipo de alteração
                'detalhes_alteracao': detalhes_alteracao # NOVO: Detalhes
            })
            flash(f'Status atualizado para "{novo_status}" com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao alterar o status do agendamento: {e}', 'danger')
        return redirect(url_for('listar_agendamentos'))

    @app.route('/agendamentos/editar', methods=['POST'], endpoint='editar_agendamento')
    @login_required
    def editar_agendamento():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        agendamento_id = request.form.get('agendamento_id')

        if not agendamento_id:
            flash('ID do agendamento não fornecido para edição.', 'danger')
            return redirect(url_for('listar_agendamentos'))

        try:
            agendamento_ref = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_id)
            original_agendamento_data = agendamento_ref.get().to_dict()

            if not original_agendamento_data:
                flash('Agendamento não encontrado para edição.', 'danger')
                return redirect(url_for('listar_agendamentos'))
            
            # Obter dados do formulário
            paciente_nome = request.form.get('cliente_nome_manual')
            profissional_id_manual = request.form.get('barbeiro_id_manual')
            servico_procedimento_id_manual = request.form.get('servico_id_manual')
            data_agendamento_str = request.form.get('data_agendamento_manual')
            hora_agendamento_str = request.form.get('hora_agendamento_manual')
            preco_str = request.form.get('preco_manual')
            status_manual = request.form.get('status_manual')

            # Validação básica
            if not all([paciente_nome, profissional_id_manual, servico_procedimento_id_manual, data_agendamento_str, hora_agendamento_str, preco_str, status_manual]):
                flash('Todos os campos obrigatórios devem ser preenchidos para editar.', 'danger')
                return redirect(url_for('listar_agendamentos'))
            
            # Obter nomes completos para notificação
            profissional_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_id_manual).get()
            servico_procedimento_doc = db_instance.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_procedimento_id_manual).get()

            profissional_nome = profissional_doc.to_dict().get('nome', 'N/A') if profissional_doc.exists else 'N/A'
            servico_procedimento_nome = servico_procedimento_doc.to_dict().get('nome', 'N/A') if servico_procedimento_doc.exists else 'N/A'
            
            # Converter data e hora para timestamp UTC
            dt_agendamento_naive = datetime.datetime.strptime(f"{data_agendamento_str} {hora_agendamento_str}", "%Y-%m-%d %H:%M")
            dt_agendamento_sp = SAO_PAULO_TZ.localize(dt_agendamento_naive)
            data_agendamento_ts_utc = dt_agendamento_sp.astimezone(pytz.utc)

            # Lógica para determinar tipo_alteracao e detalhes_alteracao
            tipo_alteracao = 'atualizado'
            detalhes_alteracao = 'Agendamento atualizado.'

            # Comparar com os dados originais para determinar o tipo de alteração
            if status_manual == 'cancelado' and original_agendamento_data.get('status') != 'cancelado':
                tipo_alteracao = 'cancelado'
                detalhes_alteracao = f"Agendamento de {original_agendamento_data.get('paciente_nome', 'N/A')} para {original_agendamento_data.get('data_agendamento', 'N/A')} às {original_agendamento_data.get('hora_agendamento', 'N/A')} foi CANCELADO."
            elif (data_agendamento_str != original_agendamento_data.get('data_agendamento') or \
                  hora_agendamento_str != original_agendamento_data.get('hora_agendamento')):
                tipo_alteracao = 'reagendado'
                detalhes_alteracao = f"Agendamento de {original_agendamento_data.get('paciente_nome', 'N/A')} reagendado de {original_agendamento_data.get('data_agendamento', 'N/A')} às {original_agendamento_data.get('hora_agendamento', 'N/A')} para {data_agendamento_str} às {hora_agendamento_str}."
            elif profissional_id_manual != original_agendamento_data.get('profissional_id'):
                tipo_alteracao = 'profissional_alterado'
                detalhes_alteracao = f"Profissional do agendamento de {original_agendamento_data.get('paciente_nome', 'N/A')} alterado de {original_agendamento_data.get('profissional_nome', 'N/A')} para {profissional_nome}."
            # Você pode adicionar mais condições aqui para outros tipos de alteração (ex: serviço, preço)
            
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
                'atualizado_em': firestore.SERVER_TIMESTAMP,
                'notificacao_pendente': True, # NOVO: Marcar para notificação
                'tipo_alteracao': tipo_alteracao, # NOVO: Tipo de alteração
                'detalhes_alteracao': detalhes_alteracao # NOVO: Detalhes
            }

            agendamento_ref.update(update_data)
            flash('Agendamento atualizado com sucesso!', 'success')

        except Exception as e:
            flash(f'Erro ao atualizar agendamento: {e}', 'danger')
            print(f"Erro edit_appointment: {e}")
            
        return redirect(url_for('listar_agendamentos'))

    @app.route('/agendamentos/apagar', methods=['POST'], endpoint='apagar_agendamento')
    @login_required
    def apagar_agendamento():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        agendamento_id = request.form.get('agendamento_id')
        if not agendamento_id:
            flash('ID do agendamento não fornecido para exclusão.', 'danger')
            return redirect(url_for('listar_agendamentos'))

        try:
            agendamento_doc_ref = db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_id)
            original_agendamento_data = agendamento_doc_ref.get().to_dict()

            if not original_agendamento_data:
                flash('Agendamento não encontrado para exclusão.', 'danger')
                return redirect(url_for('listar_agendamentos'))

            # Em vez de apagar o documento, vamos marcá-lo como "excluído" logicamente
            # e definir a notificação pendente.
            detalhes_alteracao = f"Agendamento de {original_agendamento_data.get('paciente_nome', 'N/A')} para {original_agendamento_data.get('data_agendamento', 'N/A')} às {original_agendamento_data.get('hora_agendamento', 'N/A')} foi APAGADO (excluído logicamente) do sistema."
            
            agendamento_doc_ref.update({
                'status': 'excluido', # Novo status para exclusão lógica
                'atualizado_em': firestore.SERVER_TIMESTAMP,
                'notificacao_pendente': True, # NOVO: Marcar para notificação
                'tipo_alteracao': 'agendamento_excluido', # NOVO: Tipo de alteração
                'detalhes_alteracao': detalhes_alteracao # NOVO: Detalhes
            })
            flash('Agendamento apagado (logicamente) com sucesso e notificação pendente!', 'success')
        except Exception as e:
            flash(f'Erro ao apagar agendamento: {e}', 'danger')
            print(f"Erro delete_appointment: {e}")
        return redirect(url_for('listar_agendamentos'))

