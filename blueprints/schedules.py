from flask import render_template, session, flash, redirect, url_for, request
import datetime
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore


# Importar utils
from utils import get_db, login_required, admin_required, SAO_PAULO_TZ


def register_schedules_routes(app): # Agora é uma função que recebe o app
    @app.route('/horarios', endpoint='listar_horarios') # Registra diretamente no app
    @login_required
    def listar_horarios():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        todos_horarios_formatados = []
        try:
            profissionais_main_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais')
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


    @app.route('/horarios/novo', methods=['GET', 'POST'], endpoint='adicionar_horario')
    @login_required
    @admin_required
    def adicionar_horario():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        profissionais_ativos_lista = []
        try:
            profissionais_docs = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()
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

                    db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_id_selecionado).collection('horarios_disponiveis').add(horario_data)
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


    @app.route('/profissionais/<string:profissional_doc_id>/horarios/editar/<string:horario_doc_id>', methods=['GET', 'POST'], endpoint='editar_horario')
    @login_required
    def editar_horario(profissional_doc_id, horario_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        horario_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).collection('horarios_disponiveis').document(horario_doc_id)
        
        profissionais_ativos_lista = []
        try:
            profissionais_docs = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()
            for doc in profissionais_docs:
                p_data = doc.to_dict()
                if p_data: profissionais_ativos_lista.append({'id': doc.id, 'nome': p_data.get('nome', doc.id)})
        except Exception as e:
            flash('Erro ao carregar profissionais ativos para o formulário.', 'danger')
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
                    
                    profissional_pai_doc = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).get()
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


    @app.route('/profissionais/<string:profissional_doc_id>/horarios/excluir/<string:horario_doc_id>', methods=['POST'], endpoint='excluir_horario')
    @login_required
    @admin_required
    def excluir_horario(profissional_doc_id, horario_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).collection('horarios_disponiveis').document(horario_doc_id).delete()
            flash('Horário disponível excluído com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao excluir horário: {e}', 'danger')
            print(f"Erro delete_schedule: {e}")
        return redirect(url_for('listar_horarios'))

    @app.route('/profissionais/<string:profissional_doc_id>/horarios/ativar_desativar/<string:horario_doc_id>', methods=['POST'], endpoint='ativar_desativar_horario')
    @login_required
    @admin_required
    def ativar_desativar_horario(profissional_doc_id, horario_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        horario_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id).collection('horarios_disponiveis').document(horario_doc_id)
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