from flask import render_template, session, flash, redirect, url_for, request, jsonify
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore
import datetime
import pytz

# Importar utils
from utils import get_db, login_required, admin_required, SAO_PAULO_TZ, convert_doc_to_dict

def register_estoque_routes(app):
    @app.route('/estoque', endpoint='listar_estoque')
    @login_required
    def listar_estoque():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        produtos_ref = db_instance.collection('clinicas').document(clinica_id).collection('estoque_produtos')
        produtos_lista = []
        
        search_query = request.args.get('search', '').strip()
        filter_type = request.args.get('filter', 'todos').strip() # 'todos', 'estoque_baixo', 'vencidos'

        query = produtos_ref.order_by('nome')

        if search_query:
            query = query.where(filter=FieldFilter('nome', '>=', search_query)).where(filter=FieldFilter('nome', '<=', search_query + '\uf8ff'))
        
        # Note: 'vencidos' filter would require storing expiration dates and checking them
        # For simplicity, I'll implement 'estoque_baixo' and 'todos' for now.
        # 'vencidos' would require a more complex query or client-side filtering after fetching.

        try:
            docs = query.stream()
            for doc in docs:
                produto = doc.to_dict()
                if produto:
                    produto['id'] = doc.id
                    
                    # Convert timestamps if necessary (e.g., for expiration dates if added later)
                    if 'data_vencimento' in produto and isinstance(produto['data_vencimento'], datetime.datetime):
                        produto['data_vencimento_fmt'] = produto['data_vencimento'].strftime('%d/%m/%Y')
                    
                    # Apply 'estoque_baixo' filter
                    if filter_type == 'estoque_baixo':
                        if produto.get('quantidade_atual', 0) <= produto.get('estoque_minimo', 0):
                            produtos_lista.append(produto)
                    else: # 'todos' or any other filter type
                        produtos_lista.append(produto)

            # For 'vencidos' tab, you'd filter here if not using a complex Firestore query
            # or add a specific index for it. For now, it's a placeholder.

        except Exception as e:
            flash(f'Erro ao listar produtos do estoque: {e}. Verifique seus índices do Firestore.', 'danger')
            print(f"Erro list_estoque: {e}")
        
        return render_template('estoque.html', produtos=produtos_lista, search_query=search_query, filter_type=filter_type)

    @app.route('/estoque/novo', methods=['GET', 'POST'], endpoint='adicionar_produto_estoque')
    @login_required
    @admin_required
    def adicionar_produto_estoque():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        if request.method == 'POST':
            nome = request.form['nome'].strip()
            estoque_minimo_str = request.form.get('estoque_minimo', '0').strip()
            unidade_medida = request.form.get('unidade_medida', '').strip()
            ativo = 'ativo' in request.form

            if not nome:
                flash('O nome do produto é obrigatório.', 'danger')
                return render_template('estoque_form.html', produto=request.form, action_url=url_for('adicionar_produto_estoque'))
            try:
                estoque_minimo = int(estoque_minimo_str)
                
                db_instance.collection('clinicas').document(clinica_id).collection('estoque_produtos').add({
                    'nome': nome,
                    'estoque_minimo': estoque_minimo,
                    'unidade_medida': unidade_medida if unidade_medida else None,
                    'quantidade_atual': 0, # Novo produto começa com 0 em estoque
                    'ativo': ativo,
                    'criado_em': firestore.SERVER_TIMESTAMP
                })
                flash('Produto adicionado ao estoque com sucesso!', 'success')
                return redirect(url_for('listar_estoque'))
            except ValueError:
                flash('Estoque mínimo deve ser um número válido.', 'danger')
            except Exception as e:
                flash(f'Erro ao adicionar produto ao estoque: {e}', 'danger')
                print(f"Erro add_produto_estoque: {e}")
        return render_template('estoque_form.html', produto=None, action_url=url_for('adicionar_produto_estoque'))

    @app.route('/estoque/editar/<string:produto_doc_id>', methods=['GET', 'POST'], endpoint='editar_produto_estoque')
    @login_required
    @admin_required
    def editar_produto_estoque(produto_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        produto_ref = db_instance.collection('clinicas').document(clinica_id).collection('estoque_produtos').document(produto_doc_id)
        
        if request.method == 'POST':
            nome = request.form['nome'].strip()
            estoque_minimo_str = request.form.get('estoque_minimo', '0').strip()
            unidade_medida = request.form.get('unidade_medida', '').strip()
            ativo = 'ativo' in request.form
            try:
                estoque_minimo = int(estoque_minimo_str)
                
                produto_ref.update({
                    'nome': nome,
                    'estoque_minimo': estoque_minimo,
                    'unidade_medida': unidade_medida if unidade_medida else None,
                    'ativo': ativo,
                    'atualizado_em': firestore.SERVER_TIMESTAMP
                })
                flash('Produto do estoque atualizado com sucesso!', 'success')
                return redirect(url_for('listar_estoque'))
            except ValueError:
                flash('Estoque mínimo deve ser um número válido.', 'danger')
            except Exception as e:
                flash(f'Erro ao atualizar produto do estoque: {e}', 'danger')
                print(f"Erro edit_produto_estoque (POST): {e}")

        try:
            produto_doc = produto_ref.get()
            if produto_doc.exists:
                produto = produto_doc.to_dict()
                if produto:
                    produto['id'] = produto_doc.id
                    return render_template('estoque_form.html', produto=produto, action_url=url_for('editar_produto_estoque', produto_doc_id=produto_doc_id))
            else:
                flash('Produto do estoque não encontrado.', 'danger')
                return redirect(url_for('listar_estoque'))
        except Exception as e:
            flash(f'Erro ao carregar produto do estoque para edição: {e}', 'danger')
            print(f"Erro edit_produto_estoque (GET): {e}")
            return redirect(url_for('listar_estoque'))

    @app.route('/estoque/ativar_desativar/<string:produto_doc_id>', methods=['POST'], endpoint='ativar_desativar_produto_estoque')
    @login_required
    @admin_required
    def ativar_desativar_produto_estoque(produto_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        produto_ref = db_instance.collection('clinicas').document(clinica_id).collection('estoque_produtos').document(produto_doc_id)
        try:
            produto_doc = produto_ref.get()
            if produto_doc.exists:
                data = produto_doc.to_dict()
                if data:
                    current_status = data.get('ativo', False)    
                    new_status = not current_status
                    produto_ref.update({'ativo': new_status, 'atualizado_em': firestore.SERVER_TIMESTAMP})
                    flash(f'Produto {"ativado" if new_status else "desativado"} com sucesso!', 'success')
                else:
                    flash('Dados do produto inválidos.', 'danger')
            else:
                flash('Produto não encontrado.', 'danger')
        except Exception as e:
            flash(f'Erro ao alterar o status do produto: {e}', 'danger')
            print(f"Erro in activate_deactivate_estoque_product: {e}")
        return redirect(url_for('listar_estoque'))

    @app.route('/estoque/excluir/<string:produto_doc_id>', methods=['POST'], endpoint='excluir_produto_estoque')
    @login_required
    @admin_required
    def excluir_produto_estoque(produto_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            # Check if there are any movements associated with this product
            movimentacoes_ref = db_instance.collection('clinicas').document(clinica_id).collection('estoque_movimentacoes')
            movimentacoes_com_produto = movimentacoes_ref.where(filter=FieldFilter('produto_id', '==', produto_doc_id)).limit(1).get()
            
            if len(movimentacoes_com_produto) > 0:
                flash('Este produto não pode ser excluído, pois possui movimentações de estoque registradas.', 'danger')
                return redirect(url_for('listar_estoque'))

            db_instance.collection('clinicas').document(clinica_id).collection('estoque_produtos').document(produto_doc_id).delete()
            flash('Produto do estoque excluído com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao excluir produto do estoque: {e}.', 'danger')
            print(f"Erro delete_estoque_product: {e}")
        return redirect(url_for('listar_estoque'))

    @app.route('/estoque/movimentar', methods=['GET', 'POST'], endpoint='movimentar_estoque')
    @login_required
    def movimentar_estoque():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        
        produtos_ativos_lista = []
        try:
            produtos_docs = db_instance.collection('clinicas').document(clinica_id).collection('estoque_produtos').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()
            for doc in produtos_docs:
                p_data = doc.to_dict()
                if p_data: produtos_ativos_lista.append({'id': doc.id, 'nome': p_data.get('nome', doc.id), 'quantidade_atual': p_data.get('quantidade_atual', 0)})
        except Exception as e:
            flash('Erro ao carregar produtos ativos para movimentação.', 'danger')
            print(f"Erro ao carregar produtos (movimentar_estoque GET): {e}")

        if request.method == 'POST':
            try:
                produto_id = request.form['produto_id']
                tipo_movimentacao = request.form['tipo_movimentacao']
                quantidade_str = request.form['quantidade'].strip()
                marca = request.form.get('marca', '').strip()
                preco_total_str = request.form.get('preco_total', '').strip()
                data_vencimento_str = request.form.get('data_vencimento', '').strip()
                criar_conta_pagar = 'criar_conta_pagar' in request.form

                if not all([produto_id, tipo_movimentacao, quantidade_str]):
                    flash('Produto, tipo de movimentação e quantidade são obrigatórios.', 'danger')
                    return redirect(url_for('listar_estoque'))

                quantidade = int(quantidade_str)
                if quantidade <= 0:
                    flash('A quantidade deve ser um número positivo.', 'danger')
                    return redirect(url_for('listar_estoque'))

                preco_total = float(preco_total_str.replace(',', '.')) if preco_total_str else 0.0
                
                data_vencimento = None
                if data_vencimento_str:
                    try:
                        data_vencimento = datetime.datetime.strptime(data_vencimento_str, '%Y-%m-%d').date()
                    except ValueError:
                        flash('Formato de data de vencimento inválido. Use AAAA-MM-DD.', 'danger')
                        return redirect(url_for('listar_estoque'))

                produto_ref = db_instance.collection('clinicas').document(clinica_id).collection('estoque_produtos').document(produto_id)
                produto_doc = produto_ref.get()
                if not produto_doc.exists:
                    flash('Produto não encontrado para movimentação.', 'danger')
                    return redirect(url_for('listar_estoque'))
                
                produto_data = produto_doc.to_dict()
                quantidade_atual = produto_data.get('quantidade_atual', 0)
                produto_nome = produto_data.get('nome', 'N/A')

                if tipo_movimentacao == 'entrada':
                    nova_quantidade = quantidade_atual + quantidade
                elif tipo_movimentacao == 'saida':
                    if quantidade_atual < quantidade:
                        flash('Quantidade em estoque insuficiente para esta saída.', 'danger')
                        return redirect(url_for('listar_estoque'))
                    nova_quantidade = quantidade_atual - quantidade
                else:
                    flash('Tipo de movimentação inválido.', 'danger')
                    return redirect(url_for('listar_estoque'))

                # Atualiza a quantidade atual do produto
                produto_ref.update({'quantidade_atual': nova_quantidade, 'atualizado_em': firestore.SERVER_TIMESTAMP})

                # Registra a movimentação
                db_instance.collection('clinicas').document(clinica_id).collection('estoque_movimentacoes').add({
                    'produto_id': produto_id,
                    'produto_nome': produto_nome,
                    'tipo_movimentacao': tipo_movimentacao,
                    'quantidade': quantidade,
                    'quantidade_apos_movimento': nova_quantidade,
                    'marca': marca if marca else None,
                    'preco_total': preco_total if preco_total else None,
                    'data_vencimento': data_vencimento if data_vencimento else None,
                    'criar_conta_pagar': criar_conta_pagar,
                    'data_movimentacao': datetime.datetime.now(SAO_PAULO_TZ),
                    'usuario_responsavel': session.get('user_name', 'N/A')
                })

                flash(f'Movimentação de estoque de {produto_nome} ({tipo_movimentacao}) registrada com sucesso!', 'success')
                return redirect(url_for('listar_estoque'))

            except ValueError:
                flash('Quantidade e preço devem ser números válidos.', 'danger')
            except Exception as e:
                flash(f'Erro ao movimentar estoque: {e}', 'danger')
                print(f"Erro movimentar_estoque: {e}")
        
        return render_template('movimentacao_estoque_form.html', produtos=produtos_ativos_lista, action_url=url_for('movimentar_estoque'))

    @app.route('/estoque/movimentacoes_historico', methods=['GET'], endpoint='historico_movimentacoes')
    @login_required
    def historico_movimentacoes():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        movimentacoes_lista = []
        
        search_query = request.args.get('search', '').strip()
        filter_type = request.args.get('type', '').strip() # 'entrada', 'saida'
        
        query = db_instance.collection('clinicas').document(clinica_id).collection('estoque_movimentacoes').order_by('data_movimentacao', direction=firestore.Query.DESCENDING)

        if search_query:
            query = query.where(filter=FieldFilter('produto_nome', '>=', search_query)).where(filter=FieldFilter('produto_nome', '<=', search_query + '\uf8ff'))
        if filter_type:
            query = query.where(filter=FieldFilter('tipo_movimentacao', '==', filter_type))

        try:
            docs = query.stream()
            for doc in docs:
                mov = doc.to_dict()
                if mov:
                    mov['id'] = doc.id
                    if 'data_movimentacao' in mov and isinstance(mov['data_movimentacao'], datetime.datetime):
                        mov['data_movimentacao_fmt'] = mov['data_movimentacao'].astimezone(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')
                    if 'data_vencimento' in mov and isinstance(mov['data_vencimento'], datetime.date):
                        mov['data_vencimento_fmt'] = mov['data_vencimento'].strftime('%d/%m/%Y')
                    movimentacoes_lista.append(mov)
        except Exception as e:
            flash(f'Erro ao listar histórico de movimentações: {e}.', 'danger')
            print(f"Erro historico_movimentacoes: {e}")
        
        return render_template('estoque_movimentacoes.html', movimentacoes=movimentacoes_lista, search_query=search_query, filter_type=filter_type)

# Adicione esta rota dentro da função register_estoque_routes(app):
    @app.route('/api/estoque/produtos_ativos', methods=['GET'], endpoint='api_produtos_ativos')
    @login_required
    def api_produtos_ativos():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        produtos_ativos = []
        try:
            docs = db_instance.collection('clinicas').document(clinica_id).collection('estoque_produtos').where(filter=FieldFilter('ativo', '==', True)).order_by('nome').stream()
            for doc in docs:
                p_data = doc.to_dict()
                if p_data:
                    produtos_ativos.append({
                        'id': doc.id,
                        'nome': p_data.get('nome', doc.id),
                        'quantidade_atual': p_data.get('quantidade_atual', 0),
                        'unidade_medida': p_data.get('unidade_medida', '')
                    })
        except Exception as e:
            print(f"Erro ao buscar produtos ativos para API: {e}")
            return jsonify({'error': 'Erro ao carregar produtos ativos.'}), 500
        return jsonify(produtos_ativos)

