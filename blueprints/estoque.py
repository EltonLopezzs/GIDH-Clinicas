from flask import render_template, session, flash, redirect, url_for, request, jsonify
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore
import datetime
import pytz

# Importar utils
from utils import get_db, login_required, admin_required, SAO_PAULO_TZ, convert_doc_to_dict, parse_date_input

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

        print(f"DEBUG: [listar_estoque] Iniciando listagem de estoque para clinica_id: {clinica_id}")
        print(f"DEBUG: [listar_estoque] Search Query: '{search_query}', Filter Type: '{filter_type}'")

        try:
            # A consulta inicial pode ser mais ampla e o filtro aplicado em Python
            # para lidar com 'vencidos' que compara com a data atual.
            # Não adicione order_by aqui se não for estritamente necessário para evitar índices extras
            # O order_by('nome') já está no final para a lista Python
            docs = produtos_ref.stream() 
            
            # Obtenha a data atual uma vez, já no fuso horário correto
            hoje_data = datetime.datetime.now(SAO_PAULO_TZ).date() 

            for doc in docs:
                produto = doc.to_dict()
                if produto:
                    produto['id'] = doc.id
                    
                    # Formatar data de validade para exibição no frontend
                    if 'data_validade' in produto and isinstance(produto['data_validade'], datetime.datetime):
                        produto['data_validade_fmt'] = produto['data_validade'].strftime('%d/%m/%Y')
                        produto['data_validade_obj'] = produto['data_validade'].date() # Para comparação no Jinja
                    else:
                        produto['data_validade_fmt'] = 'N/A' # Se não houver data ou formato inválido
                        produto['data_validade_obj'] = None


                    # Aplicar filtros
                    if filter_type == 'estoque_baixo':
                        if produto.get('quantidade_atual', 0) <= produto.get('estoque_minimo', 0):
                            produtos_lista.append(produto)
                    elif filter_type == 'vencidos':
                        # Verifica se a data de validade existe e é anterior à data atual
                        if produto.get('data_validade_obj') and produto['data_validade_obj'] < hoje_data:
                            produtos_lista.append(produto)
                    else: # 'todos' ou qualquer outro filtro padrão
                        produtos_lista.append(produto)
                else:
                    print(f"DEBUG: [listar_estoque] Documento vazio encontrado: {doc.id}")

            # Se houver uma query de busca, filtre a lista final (após os filtros de aba)
            if search_query:
                produtos_lista = [p for p in produtos_lista if search_query.lower() in p.get('nome', '').lower()]
            
            # Ordenar a lista final por nome para consistência
            produtos_lista.sort(key=lambda x: x.get('nome', '').lower())

            print(f"DEBUG: [listar_estoque] Produtos encontrados após filtros e busca: {len(produtos_lista)}")

        except Exception as e:
            flash(f'Erro ao listar produtos do estoque: {e}. Verifique seus índices do Firestore.', 'danger')
            print(f"ERRO: [listar_estoque] {e}") # Log mais detalhado
        
        # Passar a data atual para o template para a lógica de "Vencidos" no frontend
        return render_template('estoque.html', produtos=produtos_lista, search_query=search_query, filter_type=filter_type, now=hoje_data)

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
            data_validade_str = request.form.get('data_validade', '').strip() # Novo campo
            ativo = 'ativo' in request.form

            if not nome:
                flash('O nome do produto é obrigatório.', 'danger')
                return render_template('estoque_form.html', produto=request.form, action_url=url_for('adicionar_produto_estoque'))
            try:
                estoque_minimo = int(estoque_minimo_str)
                
                data_validade_dt = None
                if data_validade_str:
                    # Parse a data e localize-a para datetime.datetime
                    parsed_date = datetime.datetime.strptime(data_validade_str, '%Y-%m-%d')
                    data_validade_dt = SAO_PAULO_TZ.localize(parsed_date)

                db_instance.collection('clinicas').document(clinica_id).collection('estoque_produtos').add({
                    'nome': nome,
                    'estoque_minimo': estoque_minimo,
                    'unidade_medida': unidade_medida if unidade_medida else None,
                    'quantidade_atual': 0, # Novo produto começa com 0 em estoque
                    'data_validade': data_validade_dt if data_validade_dt else None, # Armazena como datetime.datetime
                    'ativo': ativo,
                    'criado_em': firestore.SERVER_TIMESTAMP
                })
                flash('Produto adicionado ao estoque com sucesso!', 'success')
                return redirect(url_for('listar_estoque'))
            except ValueError:
                flash('Estoque mínimo deve ser um número válido.', 'danger')
            except Exception as e:
                flash(f'Erro ao adicionar produto ao estoque: {e}', 'danger')
                print(f"ERRO: [adicionar_produto_estoque] {e}")
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
            data_validade_str = request.form.get('data_validade', '').strip() # Novo campo
            ativo = 'ativo' in request.form
            try:
                estoque_minimo = int(estoque_minimo_str)
                
                data_validade_dt = None
                if data_validade_str:
                    # Parse a data e localize-a para datetime.datetime
                    parsed_date = datetime.datetime.strptime(data_validade_str, '%Y-%m-%d')
                    data_validade_dt = SAO_PAULO_TZ.localize(parsed_date)
                
                update_data = {
                    'nome': nome,
                    'estoque_minimo': estoque_minimo,
                    'unidade_medida': unidade_medida if unidade_medida else None,
                    'ativo': ativo,
                    'atualizado_em': firestore.SERVER_TIMESTAMP
                }
                # Atualiza ou remove o campo data_validade
                if data_validade_dt:
                    update_data['data_validade'] = data_validade_dt
                else:
                    update_data['data_validade'] = firestore.DELETE_FIELD # Remove o campo se estiver vazio

                produto_ref.update(update_data)
                flash('Produto do estoque atualizado com sucesso!', 'success')
                return redirect(url_for('listar_estoque'))
            except ValueError:
                flash('Estoque mínimo deve ser um número válido.', 'danger')
            except Exception as e:
                flash(f'Erro ao atualizar produto do estoque: {e}', 'danger')
                print(f"ERRO: [editar_produto_estoque POST] {e}")

        try:
            produto_doc = produto_ref.get()
            if produto_doc.exists:
                produto = produto_doc.to_dict()
                if produto:
                    produto['id'] = produto_doc.id
                    # Formata a data de validade para o campo input type="date"
                    if 'data_validade' in produto and isinstance(produto['data_validade'], datetime.datetime):
                        produto['data_validade_input'] = produto['data_validade'].strftime('%Y-%m-%d')
                    else:
                        produto['data_validade_input'] = '' # Garante que o campo esteja vazio se não houver data
                    return render_template('estoque_form.html', produto=produto, action_url=url_for('editar_produto_estoque', produto_doc_id=produto_doc_id))
            else:
                flash('Produto do estoque não encontrado.', 'danger')
                return redirect(url_for('listar_estoque'))
        except Exception as e:
            flash(f'Erro ao carregar produto do estoque para edição: {e}', 'danger')
            print(f"ERRO: [editar_produto_estoque GET] {e}")
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
            print(f"ERRO: [ativar_desativar_produto_estoque] {e}")
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
            print(f"ERRO: [excluir_produto_estoque] {e}")
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
            print(f"ERRO: [movimentar_estoque GET] ao carregar produtos: {e}")

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
                    return redirect(url_for('listar_estoque') + '#movimentacaoEstoqueModal') # Redireciona para o modal
                
                # Certifica que preco_total é um float, mesmo que vazio
                preco_total = float(preco_total_str.replace(',', '.')) if preco_total_str else 0.0
                
                data_vencimento = None
                if data_vencimento_str:
                    try:
                        # Armazena como datetime.datetime para consistência com outros campos de data
                        data_vencimento = datetime.datetime.strptime(data_vencimento_str, '%Y-%m-%d')
                        # Localiza a data para o fuso horário correto antes de salvar
                        data_vencimento = SAO_PAULO_TZ.localize(data_vencimento)
                    except ValueError:
                        flash('Formato de data de vencimento inválido. Use AAAA-MM-DD.', 'danger')
                        return redirect(url_for('listar_estoque') + '#movimentacaoEstoqueModal') # Redireciona para o modal

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
                        return redirect(url_for('listar_estoque') + '#movimentacaoEstoqueModal') # Redireciona para o modal
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
                    'data_vencimento': data_vencimento if data_vencimento else None, # Armazenado como datetime.datetime
                    'criar_conta_pagar': criar_conta_pagar,
                    'data_movimentacao': datetime.datetime.now(SAO_PAULO_TZ),
                    'usuario_responsavel': session.get('user_name', 'N/A')
                })

                # Lógica para criar Conta a Pagar
                if tipo_movimentacao == 'entrada' and criar_conta_pagar:
                    if preco_total <= 0 and not data_vencimento:
                        flash('Para criar uma Conta a Pagar, o Preço Total ou a Data de Vencimento são obrigatórios.', 'warning')
                        # Não impede a movimentação de estoque, apenas avisa sobre a conta a pagar
                    else:
                        try:
                            db_instance.collection('clinicas').document(clinica_id).collection('contas_a_pagar').add({
                                'descricao': f'Compra de estoque: {produto_nome} (Qtd: {quantidade})',
                                'produto_id': produto_id,
                                'produto_nome': produto_nome,
                                'valor': preco_total,
                                'data_vencimento': data_vencimento if data_vencimento else None,
                                'status': 'pendente', # Ou 'aberto', 'a_pagar'
                                'data_lancamento': datetime.datetime.now(SAO_PAULO_TZ),
                                'usuario_responsavel': session.get('user_name', 'N/A')
                            })
                            flash('Conta a Pagar criada com sucesso!', 'info')
                        except Exception as e:
                            flash(f'Erro ao criar Conta a Pagar: {e}', 'danger')
                            print(f"ERRO: [movimentar_estoque POST] Erro ao criar Conta a Pagar: {e}")


                flash(f'Movimentação de estoque de {produto_nome} ({tipo_movimentacao}) registrada com sucesso!', 'success')
                return redirect(url_for('listar_estoque'))

            except ValueError:
                flash('Quantidade e preço devem ser números válidos.', 'danger')
                return redirect(url_for('listar_estoque') + '#movimentacaoEstoqueModal') # Redireciona para o modal
            except Exception as e:
                flash(f'Erro ao movimentar estoque: {e}', 'danger')
                print(f"ERRO: [movimentar_estoque POST] {e}")
                return redirect(url_for('listar_estoque') + '#movimentacaoEstoqueModal') # Redireciona para o modal
        
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
                    if 'data_vencimento' in mov and isinstance(mov['data_vencimento'], datetime.datetime): # Alterado para datetime.datetime
                        mov['data_vencimento_fmt'] = mov['data_vencimento'].strftime('%d/%m/%Y')
                    else:
                        mov['data_vencimento_fmt'] = 'N/A' # Garante 'N/A' se não for datetime.datetime
                    movimentacoes_lista.append(mov)
        except Exception as e:
            flash(f'Erro ao listar histórico de movimentações: {e}.', 'danger')
            print(f"ERRO: [historico_movimentacoes] {e}")
        
        return render_template('estoque_movimentacoes.html', movimentacoes=movimentacoes_lista, search_query=search_query, filter_type=filter_type)

    # NOVO ENDPOINT DE API PARA PRODUTOS ATIVOS (para o modal de movimentação)
    @app.route('/api/estoque/produtos_ativos', methods=['GET'], endpoint='api_produtos_ativos')
    @login_required
    def api_produtos_ativos():
        db_instance = get_db()
        clinica_id = session.get('clinica_id') # Use .get() para evitar KeyError se não existir

        if not clinica_id:
            print("ERRO: [api_produtos_ativos] clinica_id não encontrado na sessão.")
            return jsonify({'error': 'ID da clínica não encontrado na sessão. Faça login novamente.'}), 401

        produtos_ativos = []
        try:
            print(f"DEBUG: [api_produtos_ativos] Buscando produtos ativos para clinica_id: {clinica_id}")
            # Esta é a consulta que provavelmente requer um índice composto
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
            print(f"DEBUG: [api_produtos_ativos] {len(produtos_ativos)} produtos ativos encontrados.")
            return jsonify(produtos_ativos)
        except Exception as e:
            # Imprime o erro completo no console do servidor
            print(f"ERRO CRÍTICO: [api_produtos_ativos] Erro ao buscar produtos ativos: {e}")
            # Retorna uma mensagem de erro genérica para o frontend
            return jsonify({'error': 'Erro ao carregar produtos ativos. Consulte os logs do servidor para mais detalhes.'}), 500

