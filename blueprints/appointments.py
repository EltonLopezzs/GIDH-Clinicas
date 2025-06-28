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

            paciente_ref_query = db_instance.collection('clinicas').document(clinica_id).collection('pacientes')\
                                         .where(filter=FieldFilter('nome', '==', paciente_nome)).limit(1).get()
            
            paciente_doc_id = None
            if paciente_ref_query:
                for doc in paciente_ref_query:
                    paciente_doc_id = doc.id
                    break
            
            if not paciente_doc_id:
                novo_paciente_ref = db_instance.collection('clinicas').document(clinica_id).collection('pacientes').add({
                    'nome': paciente_nome,
                    'contato_telefone': paciente_telefone if paciente_telefone else None,
                    'data_cadastro': firestore.SERVER_TIMESTAMP
                })
                paciente_doc_id = novo_paciente_ref[1].id

            profissional_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_id_manual).get()
            servico_procedimento_doc = db_instance.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_procedimento_id_manual).get()

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
            
            db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').add(novo_agendamento_dados)
            
            flash('Atendimento registrado manualmente com sucesso!', 'success')
        except ValueError as ve:
            flash(f'Erro de valor ao registrar atendimento: {ve}', 'danger')
        except Exception as e:
            flash(f'Erro ao registrar atendimento manual: {e}', 'danger')
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
            db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_doc_id).update({
                'status': novo_status,
                'atualizado_em': firestore.SERVER_TIMESTAMP
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
            
            profissional_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_id_manual).get()
            servico_procedimento_doc = db_instance.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_procedimento_id_manual).get()

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
            db_instance.collection('clinicas').document(clinica_id).collection('agendamentos').document(agendamento_id).delete()
            flash('Agendamento apagado com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao apagar agendamento: {e}', 'danger')
            print(f"Erro delete_appointment: {e}")
        return redirect(url_for('listar_agendamentos'))