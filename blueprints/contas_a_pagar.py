from flask import render_template, session, flash, redirect, url_for, request, jsonify
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore
import datetime
import pytz

# Importar utils
from utils import get_db, login_required, admin_required, SAO_PAULO_TZ, convert_doc_to_dict, parse_date_input

def register_contas_a_pagar_routes(app):
    @app.route('/contas_a_pagar', endpoint='listar_contas_a_pagar')
    @login_required
    @admin_required # Apenas administradores podem gerenciar contas a pagar
    def listar_contas_a_pagar():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        contas_ref = db_instance.collection('clinicas').document(clinica_id).collection('contas_a_pagar')
        contas_lista = []
        
        search_query = request.args.get('search', '').strip()
        filter_status = request.args.get('status', 'todas').strip() # 'todas', 'pendente', 'paga', 'vencida'
        
        query = contas_ref.order_by('data_vencimento', direction=firestore.Query.ASCENDING) # Ordena por vencimento

        # Aplica filtros de status
        if filter_status == 'pendente':
            query = query.where(filter=FieldFilter('status', '==', 'pendente'))
        elif filter_status == 'paga':
            query = query.where(filter=FieldFilter('status', '==', 'paga'))
        elif filter_status == 'vencida':
            # Para "vencida", precisamos comparar com a data atual
            # No Firestore, não podemos fazer query de "menor que" em campos não indexados sem um índice composto.
            # A forma mais robusta é filtrar por status 'pendente' e depois filtrar por data no Python.
            # Ou, se houver um índice composto para 'status' e 'data_vencimento', podemos usar:
            # query = query.where(filter=FieldFilter('status', '==', 'pendente')).where(filter=FieldFilter('data_vencimento', '<', datetime.datetime.now(SAO_PAULO_TZ)))
            # Por simplicidade e para evitar problemas de índice complexos, faremos o filtro final em Python.
            query = query.where(filter=FieldFilter('status', '==', 'pendente')) # Primeiro filtra pendentes
        
        try:
            docs = query.stream()
            hoje_dt = datetime.datetime.now(SAO_PAULO_TZ)

            for doc in docs:
                conta = doc.to_dict()
                if conta:
                    conta['id'] = doc.id
                    
                    # Formatar data de vencimento
                    if 'data_vencimento' in conta and isinstance(conta['data_vencimento'], datetime.datetime):
                        conta['data_vencimento_fmt'] = conta['data_vencimento'].strftime('%d/%m/%Y')
                        # Verifica se está vencida (apenas para status 'pendente')
                        if filter_status == 'vencida' and conta['status'] == 'pendente' and conta['data_vencimento'] < hoje_dt:
                            contas_lista.append(conta)
                        elif filter_status != 'vencida': # Adiciona se não for filtro de vencida
                            contas_lista.append(conta)
                    else:
                        conta['data_vencimento_fmt'] = 'N/A'
                        if filter_status != 'vencida': # Adiciona mesmo sem data de vencimento se não for filtro de vencida
                            contas_lista.append(conta)

            # Filtrar por search_query (nome do produto ou descrição)
            if search_query:
                contas_lista = [c for c in contas_lista if search_query.lower() in c.get('descricao', '').lower() or search_query.lower() in c.get('produto_nome', '').lower()]
            
            # Re-filtrar para "vencida" se não foi feito acima ou para garantir
            if filter_status == 'vencida':
                contas_lista = [c for c in contas_lista if c.get('status') == 'pendente' and c.get('data_vencimento') and c['data_vencimento'] < hoje_dt]
            
            # Ordenar a lista final (se necessário, já ordenado por data_vencimento na query)
            # contas_lista.sort(key=lambda x: x.get('data_vencimento', datetime.datetime.max.replace(tzinfo=SAO_PAULO_TZ))) # Garante que N/A vão para o final

        except Exception as e:
            flash(f'Erro ao listar contas a pagar: {e}. Verifique seus índices do Firestore.', 'danger')
            print(f"ERRO: [listar_contas_a_pagar] {e}")
        
        return render_template('contas_a_pagar.html', contas=contas_lista, search_query=search_query, filter_status=filter_status, now=hoje_dt)

    @app.route('/contas_a_pagar/nova', methods=['GET', 'POST'], endpoint='adicionar_conta_a_pagar')
    @login_required
    @admin_required
    def adicionar_conta_a_pagar():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        if request.method == 'POST':
            descricao = request.form['descricao'].strip()
            valor_str = request.form.get('valor', '0').strip()
            data_vencimento_str = request.form.get('data_vencimento', '').strip()
            status = request.form.get('status', 'pendente').strip()
            produto_id = request.form.get('produto_id', '').strip() # Pode vir de uma seleção manual

            if not descricao or not valor_str:
                flash('Descrição e Valor são obrigatórios.', 'danger')
                return render_template('conta_a_pagar_form.html', conta=request.form, action_url=url_for('adicionar_conta_a_pagar'))
            
            try:
                valor = float(valor_str.replace(',', '.'))
                
                data_vencimento_dt = None
                if data_vencimento_str:
                    parsed_date = datetime.datetime.strptime(data_vencimento_str, '%Y-%m-%d')
                    data_vencimento_dt = SAO_PAULO_TZ.localize(parsed_date)

                conta_data = {
                    'descricao': descricao,
                    'valor': valor,
                    'data_vencimento': data_vencimento_dt if data_vencimento_dt else None,
                    'status': status,
                    'data_lancamento': datetime.datetime.now(SAO_PAULO_TZ),
                    'usuario_responsavel': session.get('user_name', 'N/A')
                }
                if produto_id:
                    conta_data['produto_id'] = produto_id
                    # Opcional: buscar nome do produto se produto_id for fornecido
                    produto_doc = db_instance.collection('clinicas').document(clinica_id).collection('estoque_produtos').document(produto_id).get()
                    if produto_doc.exists:
                        conta_data['produto_nome'] = produto_doc.to_dict().get('nome')

                db_instance.collection('clinicas').document(clinica_id).collection('contas_a_pagar').add(conta_data)
                flash('Conta a pagar adicionada com sucesso!', 'success')
                return redirect(url_for('listar_contas_a_pagar'))
            except ValueError:
                flash('Valor deve ser um número válido.', 'danger')
            except Exception as e:
                flash(f'Erro ao adicionar conta a pagar: {e}', 'danger')
                print(f"ERRO: [adicionar_conta_a_pagar] {e}")
        
        # Carrega produtos ativos para o select no formulário (opcional)
        produtos_ativos = []
        try:
            docs = db_instance.collection('clinicas').document(clinica_id).collection('estoque_produtos').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()
            for doc in docs:
                p_data = doc.to_dict()
                if p_data:
                    produtos_ativos.append({'id': doc.id, 'nome': p_data.get('nome', doc.id)})
        except Exception as e:
            print(f"ERRO: [adicionar_conta_a_pagar GET] Erro ao carregar produtos ativos: {e}")
            flash('Erro ao carregar produtos para vincular.', 'warning')

        return render_template('conta_a_pagar_form.html', conta=None, action_url=url_for('adicionar_conta_a_pagar'), produtos_ativos=produtos_ativos)

    @app.route('/contas_a_pagar/editar/<string:conta_doc_id>', methods=['GET', 'POST'], endpoint='editar_conta_a_pagar')
    @login_required
    @admin_required
    def editar_conta_a_pagar(conta_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        conta_ref = db_instance.collection('clinicas').document(clinica_id).collection('contas_a_pagar').document(conta_doc_id)
        
        if request.method == 'POST':
            descricao = request.form['descricao'].strip()
            valor_str = request.form.get('valor', '0').strip()
            data_vencimento_str = request.form.get('data_vencimento', '').strip()
            status = request.form.get('status', 'pendente').strip()
            produto_id = request.form.get('produto_id', '').strip()

            if not descricao or not valor_str:
                flash('Descrição e Valor são obrigatórios.', 'danger')
                return render_template('conta_a_pagar_form.html', conta=request.form, action_url=url_for('editar_conta_a_pagar', conta_doc_id=conta_doc_id))
            
            try:
                valor = float(valor_str.replace(',', '.'))
                
                data_vencimento_dt = None
                if data_vencimento_str:
                    parsed_date = datetime.datetime.strptime(data_vencimento_str, '%Y-%m-%d')
                    data_vencimento_dt = SAO_PAULO_TZ.localize(parsed_date)
                
                update_data = {
                    'descricao': descricao,
                    'valor': valor,
                    'data_vencimento': data_vencimento_dt if data_vencimento_dt else None,
                    'status': status,
                    'atualizado_em': firestore.SERVER_TIMESTAMP
                }
                if produto_id:
                    update_data['produto_id'] = produto_id
                    produto_doc = db_instance.collection('clinicas').document(clinica_id).collection('estoque_produtos').document(produto_id).get()
                    if produto_doc.exists:
                        update_data['produto_nome'] = produto_doc.to_dict().get('nome')
                else:
                    update_data['produto_id'] = firestore.DELETE_FIELD
                    update_data['produto_nome'] = firestore.DELETE_FIELD


                conta_ref.update(update_data)
                flash('Conta a pagar atualizada com sucesso!', 'success')
                return redirect(url_for('listar_contas_a_pagar'))
            except ValueError:
                flash('Valor deve ser um número válido.', 'danger')
            except Exception as e:
                flash(f'Erro ao atualizar conta a pagar: {e}', 'danger')
                print(f"ERRO: [editar_conta_a_pagar POST] {e}")

        try:
            conta_doc = conta_ref.get()
            if conta_doc.exists:
                conta = conta_doc.to_dict()
                if conta:
                    conta['id'] = conta_doc.id
                    if 'data_vencimento' in conta and isinstance(conta['data_vencimento'], datetime.datetime):
                        conta['data_vencimento_input'] = conta['data_vencimento'].strftime('%Y-%m-%d')
                    else:
                        conta['data_vencimento_input'] = ''
                    
                    # Carrega produtos ativos para o select no formulário
                    produtos_ativos = []
                    try:
                        docs = db_instance.collection('clinicas').document(clinica_id).collection('estoque_produtos').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()
                        for doc in docs:
                            p_data = doc.to_dict()
                            if p_data:
                                produtos_ativos.append({'id': doc.id, 'nome': p_data.get('nome', doc.id)})
                    except Exception as e:
                        print(f"ERRO: [editar_conta_a_pagar GET] Erro ao carregar produtos ativos: {e}")
                        flash('Erro ao carregar produtos para vincular.', 'warning')

                    return render_template('conta_a_pagar_form.html', conta=conta, action_url=url_for('editar_conta_a_pagar', conta_doc_id=conta_doc_id), produtos_ativos=produtos_ativos)
            else:
                flash('Conta a pagar não encontrada.', 'danger')
                return redirect(url_for('listar_contas_a_pagar'))
        except Exception as e:
            flash(f'Erro ao carregar conta a pagar para edição: {e}', 'danger')
            print(f"ERRO: [editar_conta_a_pagar GET] {e}")
            return redirect(url_for('listar_contas_a_pagar'))

    @app.route('/contas_a_pagar/marcar_paga/<string:conta_doc_id>', methods=['POST'], endpoint='marcar_conta_paga')
    @login_required
    @admin_required
    def marcar_conta_paga(conta_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        conta_ref = db_instance.collection('clinicas').document(clinica_id).collection('contas_a_pagar').document(conta_doc_id)
        try:
            conta_ref.update({'status': 'paga', 'data_pagamento': datetime.datetime.now(SAO_PAULO_TZ), 'atualizado_em': firestore.SERVER_TIMESTAMP})
            flash('Conta a pagar marcada como paga com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao marcar conta como paga: {e}', 'danger')
            print(f"ERRO: [marcar_conta_paga] {e}")
        return redirect(url_for('listar_contas_a_pagar'))

    @app.route('/contas_a_pagar/excluir/<string:conta_doc_id>', methods=['POST'], endpoint='excluir_conta_a_pagar')
    @login_required
    @admin_required
    def excluir_conta_a_pagar(conta_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            db_instance.collection('clinicas').document(clinica_id).collection('contas_a_pagar').document(conta_doc_id).delete()
            flash('Conta a pagar excluída com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao excluir conta a pagar: {e}.', 'danger')
            print(f"ERRO: [excluir_conta_a_pagar] {e}")
        return redirect(url_for('listar_contas_a_pagar'))

