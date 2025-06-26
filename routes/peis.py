import datetime
import json
from flask import Blueprint, render_template, session, flash, redirect, url_for, request, jsonify, current_app
from google.cloud.firestore_v1.base_query import FieldFilter
from decorators.auth_decorators import login_required, admin_required # Import decorators
from utils.firestore_utils import convert_doc_to_dict, parse_date_input # Import utility functions

peis_bp = Blueprint('peis_bp', __name__)

@peis_bp.route('/peis')
@login_required
@admin_required
def listar_peis():
    db = current_app.config['DB']

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

@peis_bp.route('/peis/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_pei():
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    profissionais_lista = []
    try:
        profissionais_docs = db.collection(f'clinicas/{clinica_id}/profissionais').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()
        for doc in profissionais_docs:
            profissionais_lista.append(convert_doc_to_dict(doc))
    except Exception as e:
        flash('Erro ao carregar a lista de profissionais.', 'danger')

    if request.method == 'POST':
        identificacao_pei = request.form['identificacao_pei'].strip()
        descricao_pei = request.form.get('descricao_pei', '').strip()
        data_inicio_str = request.form.get('data_inicio', '').strip()
        data_fim_str = request.form.get('data_fim', '').strip()
        metas_json = request.form.get('metas_json', '[]')
        profissional_responsavel_id = request.form.get('profissional_responsavel_id')

        profissional_responsavel_nome = None
        if profissional_responsavel_id:
            try:
                prof_doc = db.collection(f'clinicas/{clinica_id}/profissionais').document(profissional_responsavel_id).get()
                if prof_doc.exists:
                    profissional_responsavel_nome = prof_doc.to_dict().get('nome')
            except Exception as e:
                flash(f'Erro ao buscar nome do profissional: {e}', 'warning')
        
        try:
            data_inicio_dt = parse_date_input(data_inicio_str) if data_inicio_str else None
            data_fim_dt = parse_date_input(data_fim_str) if data_fim_str else None
            metas_data = json.loads(metas_json)

            db.collection('clinicas').document(clinica_id).collection('peis').add({
                'identificacao_pei': identificacao_pei,
                'descricao_pei': descricao_pei,
                'data_inicio': data_inicio_dt,
                'data_fim': data_fim_dt,
                'profissional_responsavel_id': professional_responsavel_id,
                'profissional_responsavel_nome': professional_responsavel_nome,
                'metas': metas_data,
                'criado_em': db.SERVER_TIMESTAMP,
                'criado_por_uid': session.get('user_uid'),
                'criado_por_nome': session.get('user_name', session.get('user_email'))
            })
            flash('PEI adicionado com sucesso!', 'success')
            return redirect(url_for('peis_bp.listar_peis'))
        except Exception as e:
            flash(f'Erro ao adicionar PEI: {e}', 'danger')
    
    return render_template('pei_form.html', pei=None, action_url=url_for('peis_bp.adicionar_pei'), page_title='Adicionar Novo PEI', profissionais=profissionais_lista)


@peis_bp.route('/peis/editar/<string:pei_doc_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_pei(pei_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    pei_ref = db.collection('clinicas').document(clinica_id).collection('peis').document(pei_doc_id)
    
    profissionais_lista = []
    try:
        profissionais_docs = db.collection(f'clinicas/{clinica_id}/profissionais').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()
        for doc in profissionais_docs:
            profissionais_lista.append(convert_doc_to_dict(doc))
    except Exception as e:
        flash('Erro ao carregar a lista de profissionais.', 'danger')

    if request.method == 'POST':
        identificacao_pei = request.form['identificacao_pei'].strip()
        descricao_pei = request.form.get('descricao_pei', '').strip()
        data_inicio_str = request.form.get('data_inicio', '').strip()
        data_fim_str = request.form.get('data_fim', '').strip()
        metas_json = request.form.get('metas_json', '[]')
        profissional_responsavel_id = request.form.get('profissional_responsavel_id')

        profissional_responsavel_nome = None
        if profissional_responsavel_id:
            try:
                prof_doc = db.collection(f'clinicas/{clinica_id}/profissionais').document(profissional_responsavel_id).get()
                if prof_doc.exists:
                    profissional_responsavel_nome = prof_doc.to_dict().get('nome')
            except Exception as e:
                flash(f'Erro ao buscar nome do profissional: {e}', 'warning')
        
        try:
            data_inicio_dt = parse_date_input(data_inicio_str) if data_inicio_str else None
            data_fim_dt = parse_date_input(data_fim_str) if data_fim_str else None
            metas_data = json.loads(metas_json)

            pei_ref.update({
                'identificacao_pei': identificacao_pei,
                'descricao_pei': descricao_pei,
                'data_inicio': data_inicio_dt,
                'data_fim': data_fim_dt,
                'profissional_responsavel_id': professional_responsavel_id,
                'profissional_responsavel_nome': professional_responsavel_nome,
                'metas': metas_data,
                'atualizado_em': db.SERVER_TIMESTAMP,
                'atualizado_por_uid': session.get('user_uid'),
                'atualizado_por_nome': session.get('user_name', session.get('user_email'))
            })
            flash('PEI atualizado com sucesso!', 'success')
            return redirect(url_for('peis_bp.listar_peis'))
        except Exception as e:
            flash(f'Erro ao atualizar PEI: {e}', 'danger')

    try:
        pei_doc = pei_ref.get()
        if pei_doc.exists:
            pei = pei_doc.to_dict()
            if pei:
                pei['id'] = pei_doc.id
                if pei.get('data_inicio') and isinstance(pei['data_inicio'], datetime.datetime):
                    pei['data_inicio'] = pei['data_inicio'].strftime('%Y-%m-%d')
                else:
                    pei['data_inicio'] = ''

                if pei.get('data_fim') and isinstance(pei['data_fim'], datetime.datetime):
                    pei['data_fim'] = pei['data_fim'].strftime('%Y-%m-%d')
                else:
                    pei['data_fim'] = ''
                
                if 'metas' in pei and isinstance(pei['metas'], list):
                    pei['metas_json'] = json.dumps(pei['metas'], indent=2)
                else:
                    pei['metas_json'] = '[]'
                
                return render_template('pei_form.html', pei=pei, action_url=url_for('peis_bp.editar_pei', pei_doc_id=pei_doc_id), page_title=f"Editar PEI: {pei.get('identificacao_pei', 'N/A')}", profissionais=profissionais_lista)
        else:
            flash('PEI não encontrado.', 'danger')
            return redirect(url_for('peis_bp.listar_peis'))
    except Exception as e:
        flash(f'Erro ao carregar PEI para edição: {e}', 'danger')
        return redirect(url_for('peis_bp.listar_peis'))

@peis_bp.route('/peis/excluir/<string:pei_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_pei(pei_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    try:
        db.collection('clinicas').document(clinica_id).collection('peis').document(pei_doc_id).delete()
        flash('PEI excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir PEI: {e}.', 'danger')
        print(f"Erro excluir_pei: {e}")
    return redirect(url_for('peis_bp.listar_peis'))

@peis_bp.route('/api/pacientes/<string:paciente_id>/peis/<string:pei_individual_id>/meta/update', methods=['POST'])
@login_required
def update_pei_meta(paciente_id, pei_individual_id):
    db = current_app.config['DB']
    SAO_PAULO_TZ = current_app.config['SAO_PAULO_TZ']

    clinica_id = session['clinica_id']
    data = request.json
    meta_titulo = data.get('meta_titulo')
    action = data.get('action')

    if not all([meta_titulo, action]):
        return jsonify({'success': False, 'message': 'Dados incompletos.'}), 400

    try:
        pei_ref = db.collection('clinicas').document(clinica_id).collection('pacientes').document(paciente_id).collection('peis_individuais').document(pei_individual_id)
        pei_doc = pei_ref.get()

        if not pei_doc.exists:
            return jsonify({'success': False, 'message': 'PEI individual não encontrado para este paciente.'}), 404
        
        pei_data = pei_doc.to_dict()
        metas = pei_data.get('metas', [])
        
        if not isinstance(metas, list): # Safety check for 'metas' being a list
            print(f"Aviso: 'metas' para PEI individual {pei_individual_id} não é uma lista. Reformatando.")
            metas = []

        meta_encontrada = False
        target_meta_index = -1

        for i, meta in enumerate(metas):
            if meta.get('titulo') == meta_titulo:
               
                meta_encontrada = True
                target_meta_index = i
                
                if 'status' not in meta: meta['status'] = 'Não Iniciada'
                if 'tempo_total_gasto' not in meta: meta['tempo_total_gasto'] = 0
                if 'cronometro_inicio' not in meta: meta['cronometro_inicio'] = None

                if action == 'start_timer':
                    if meta['cronometro_inicio'] is None:
                        meta['cronometro_inicio'] = datetime.datetime.now(pytz.utc)
                        if meta['status'] == 'Não Iniciada':
                            meta['status'] = 'Em Andamento'
                
                elif action == 'stop_timer':
                    if meta.get('cronometro_inicio'):
                        inicio_ts = meta['cronometro_inicio']
                        inicio = inicio_ts if isinstance(inicio_ts, datetime.datetime) else pytz.utc.localize(datetime.datetime.fromisoformat(inicio_ts.replace('Z', '+00:00')))
                        fim = datetime.datetime.now(pytz.utc)
                        segundos_decorridos = (fim - inicio).total_seconds()
                        meta['tempo_total_gasto'] += round(segundos_decorridos)
                        meta['cronometro_inicio'] = None

                elif action == 'concluir':
                    if meta.get('cronometro_inicio'):
                        inicio_ts = meta['cronometro_inicio']
                        inicio = inicio_ts if isinstance(inicio_ts, datetime.datetime) else pytz.utc.localize(datetime.datetime.fromisoformat(inicio_ts.replace('Z', '+00:00')))
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