from flask import render_template, session, flash, redirect, url_for, request, jsonify, Blueprint
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore
import datetime
import pytz

# Importar utils
from utils import get_db, login_required, admin_required, SAO_PAULO_TZ, convert_doc_to_dict, parse_date_input

patrimonio_bp = Blueprint('patrimonio', __name__)

def register_patrimonio_routes(app):
    """
    Registra as rotas relacionadas ao gerenciamento de patrimônio no aplicativo Flask.
    """

    @patrimonio_bp.route('/patrimonio', endpoint='listar_patrimonio')
    @login_required
    @admin_required # Apenas administradores podem gerenciar o patrimônio
    def listar_patrimonio():
        """
        Exibe a lista de todos os itens de patrimônio.
        Permite buscar por nome, código, tipo ou local de armazenamento.
        """
        db_instance = get_db()
        clinica_id = session['clinica_id']
        patrimonio_ref = db_instance.collection('clinicas').document(clinica_id).collection('patrimonio')
        patrimonio_lista = []

        search_query = request.args.get('search', '').strip()

        query = patrimonio_ref.order_by('nome', direction=firestore.Query.ASCENDING)

        try:
            docs = query.stream()
            for doc in docs:
                item = doc.to_dict()
                if item:
                    item['id'] = doc.id
                    # Formatar data de aquisição para exibição
                    if 'data_aquisicao' in item and isinstance(item['data_aquisicao'], datetime.datetime):
                        item['data_aquisicao_fmt'] = item['data_aquisicao'].strftime('%d/%m/%Y')
                    else:
                        item['data_aquisicao_fmt'] = 'N/A'
                    
                    # Adicionar à lista se corresponder à busca
                    if search_query:
                        if (search_query.lower() in item.get('nome', '').lower() or
                            search_query.lower() in item.get('codigo', '').lower() or
                            search_query.lower() in item.get('tipo', '').lower() or
                            search_query.lower() in item.get('local_armazenamento', '').lower()):
                            patrimonio_lista.append(item)
                    else:
                        patrimonio_lista.append(item)

        except Exception as e:
            flash(f'Erro ao listar patrimônio: {e}. Verifique seus índices do Firestore.', 'danger')
            print(f"ERRO: [listar_patrimonio] {e}")

        return render_template('patrimonio.html', patrimonio_itens=patrimonio_lista, search_query=search_query)

    @patrimonio_bp.route('/patrimonio/novo', methods=['GET', 'POST'], endpoint='adicionar_patrimonio')
    @login_required
    @admin_required
    def adicionar_patrimonio():
        """
        Permite adicionar um novo item de patrimônio.
        """
        db_instance = get_db()
        clinica_id = session['clinica_id']

        if request.method == 'POST':
            nome = request.form['nome'].strip()
            codigo = request.form.get('codigo', '').strip()
            tipo = request.form.get('tipo', '').strip()
            data_aquisicao_str = request.form.get('data_aquisicao', '').strip()
            local_armazenamento = request.form.get('local_armazenamento', '').strip()
            valor_str = request.form.get('valor', '0').strip()
            observacao = request.form.get('observacao', '').strip()
            # Verifica se o checkbox foi marcado
            criar_conta_pagar = 'criar_conta_pagar' in request.form 

            if not nome:
                flash('O nome do patrimônio é obrigatório.', 'danger')
                return render_template('patrimonio_form.html', item=request.form, action_url=url_for('patrimonio.adicionar_patrimonio'))
            
            try:
                # Converte o valor para float, tratando a vírgula como separador decimal
                valor = float(valor_str.replace(',', '.')) if valor_str else 0.0
                
                data_aquisicao_dt = None
                if data_aquisicao_str:
                    parsed_date = datetime.datetime.strptime(data_aquisicao_str, '%Y-%m-%d')
                    data_aquisicao_dt = SAO_PAULO_TZ.localize(parsed_date)

                patrimonio_data = {
                    'nome': nome,
                    'codigo': codigo,
                    'tipo': tipo,
                    'data_aquisicao': data_aquisicao_dt,
                    'local_armazenamento': local_armazenamento,
                    'valor': valor,
                    'observacao': observacao,
                    'data_cadastro': datetime.datetime.now(SAO_PAULO_TZ),
                    'usuario_cadastro': session.get('user_name', 'N/A')
                }

                # Adiciona o item de patrimônio e obtém sua referência
                new_patrimonio_ref = db_instance.collection('clinicas').document(clinica_id).collection('patrimonio').add(patrimonio_data)[1]
                
                # Se a opção de criar conta a pagar foi marcada e o valor é maior que zero, cria a conta
                if criar_conta_pagar and valor > 0:
                    contas_a_pagar_data = {
                        'descricao': f"Aquisição de Patrimônio: {nome}",
                        'valor': valor,
                        'data_vencimento': data_aquisicao_dt if data_aquisicao_dt else datetime.datetime.now(SAO_PAULO_TZ),
                        'status': 'pendente', # Assume pendente ao criar
                        'data_lancamento': datetime.datetime.now(SAO_PAULO_TZ),
                        'usuario_responsavel': session.get('user_name', 'N/A'),
                        'patrimonio_id': new_patrimonio_ref.id, # Vincula ao ID do patrimônio recém-criado
                        'patrimonio_nome': nome # Salva o nome para fácil referência
                    }
                    db_instance.collection('clinicas').document(clinica_id).collection('contas_a_pagar').add(contas_a_pagar_data)
                    flash('Item de patrimônio e conta a pagar adicionados com sucesso!', 'success')
                else:
                    flash('Item de patrimônio adicionado com sucesso!', 'success')

                return redirect(url_for('patrimonio.listar_patrimonio'))
            except ValueError:
                flash('Valor deve ser um número válido.', 'danger')
            except Exception as e:
                flash(f'Erro ao adicionar item de patrimônio: {e}', 'danger')
                print(f"ERRO: [adicionar_patrimonio] {e}")
        
        return render_template('patrimonio_form.html', item=None, action_url=url_for('patrimonio.adicionar_patrimonio'))

    @patrimonio_bp.route('/patrimonio/editar/<string:item_doc_id>', methods=['GET', 'POST'], endpoint='editar_patrimonio')
    @login_required
    @admin_required
    def editar_patrimonio(item_doc_id):
        """
        Permite editar um item de patrimônio existente.
        """
        db_instance = get_db()
        clinica_id = session['clinica_id']
        item_ref = db_instance.collection('clinicas').document(clinica_id).collection('patrimonio').document(item_doc_id)
        
        if request.method == 'POST':
            nome = request.form['nome'].strip()
            codigo = request.form.get('codigo', '').strip()
            tipo = request.form.get('tipo', '').strip()
            data_aquisicao_str = request.form.get('data_aquisicao', '').strip()
            local_armazenamento = request.form.get('local_armazenamento', '').strip()
            valor_str = request.form.get('valor', '0').strip()
            observacao = request.form.get('observacao', '').strip()
            # Verifica se o checkbox foi marcado
            criar_conta_pagar = 'criar_conta_pagar' in request.form 

            if not nome:
                flash('O nome do patrimônio é obrigatório.', 'danger')
                return render_template('patrimonio_form.html', item=request.form, action_url=url_for('patrimonio.editar_patrimonio', item_doc_id=item_doc_id))
            
            try:
                # Converte o valor para float, tratando a vírgula como separador decimal
                valor = float(valor_str.replace(',', '.')) if valor_str else 0.0
                
                data_aquisicao_dt = None
                if data_aquisicao_str:
                    parsed_date = datetime.datetime.strptime(data_aquisicao_str, '%Y-%m-%d')
                    data_aquisicao_dt = SAO_PAULO_TZ.localize(parsed_date)
                
                update_data = {
                    'nome': nome,
                    'codigo': codigo,
                    'tipo': tipo,
                    'data_aquisicao': data_aquisicao_dt,
                    'local_armazenamento': local_armazenamento,
                    'valor': valor,
                    'observacao': observacao,
                    'atualizado_em': firestore.SERVER_TIMESTAMP
                }

                item_ref.update(update_data)

                # Lógica para criar/atualizar conta a pagar vinculada
                if criar_conta_pagar and valor > 0:
                    # Tenta encontrar uma conta a pagar existente para este patrimônio
                    contas_existentes_query = db_instance.collection('clinicas').document(clinica_id).collection('contas_a_pagar').where(filter=FieldFilter('patrimonio_id', '==', item_doc_id)).limit(1).stream()
                    contas_existentes = list(contas_existentes_query)

                    contas_a_pagar_data = {
                        'descricao': f"Aquisição de Patrimônio: {nome}",
                        'valor': valor,
                        'data_vencimento': data_aquisicao_dt if data_aquisicao_dt else datetime.datetime.now(SAO_PAULO_TZ),
                        'status': 'pendente', # Assume pendente ao editar, pode ser ajustado
                        'data_lancamento': datetime.datetime.now(SAO_PAULO_TZ), # Pode manter o original se existir
                        'usuario_responsavel': session.get('user_name', 'N/A'),
                        'patrimonio_id': item_doc_id,
                        'patrimonio_nome': nome
                    }

                    if contas_existentes:
                        # Atualiza a conta existente
                        conta_existente_ref = contas_existentes[0].reference
                        conta_existente_ref.update(contas_a_pagar_data)
                        flash('Item de patrimônio e conta a pagar vinculada atualizados com sucesso!', 'success')
                    else:
                        # Cria uma nova conta se não existir
                        db_instance.collection('clinicas').document(clinica_id).collection('contas_a_pagar').add(contas_a_pagar_data)
                        flash('Item de patrimônio atualizado e nova conta a pagar criada!', 'success')
                else:
                    # Se o checkbox não foi marcado, mas existia uma conta vinculada, você pode optar por removê-la ou não fazer nada.
                    # Por enquanto, não faremos nada se o checkbox não estiver marcado.
                    flash('Item de patrimônio atualizado com sucesso!', 'success')


                return redirect(url_for('patrimonio.listar_patrimonio'))
            except ValueError:
                flash('Valor deve ser um número válido.', 'danger')
            except Exception as e:
                flash(f'Erro ao atualizar item de patrimônio: {e}', 'danger')
                print(f"ERRO: [editar_patrimonio POST] {e}")

        try:
            item_doc = item_ref.get()
            if item_doc.exists:
                item = item_doc.to_dict()
                if item:
                    item['id'] = item_doc.id
                    if 'data_aquisicao' in item and isinstance(item['data_aquisicao'], datetime.datetime):
                        item['data_aquisicao_input'] = item['data_aquisicao'].strftime('%Y-%m-%d')
                    else:
                        item['data_aquisicao_input'] = ''
                    
                    # Verifica se já existe uma conta a pagar para este patrimônio para pré-marcar o checkbox
                    contas_existentes_query = db_instance.collection('clinicas').document(clinica_id).collection('contas_a_pagar').where(filter=FieldFilter('patrimonio_id', '==', item_doc_id)).limit(1).stream()
                    item['criar_conta_pagar'] = bool(list(contas_existentes_query)) # True se encontrar uma conta, False caso contrário

                    return render_template('patrimonio_form.html', item=item, action_url=url_for('patrimonio.editar_patrimonio', item_doc_id=item_doc_id))
            else:
                flash('Item de patrimônio não encontrado.', 'danger')
                return redirect(url_for('patrimonio.listar_patrimonio'))
        except Exception as e:
            flash(f'Erro ao carregar item de patrimônio para edição: {e}', 'danger')
            print(f"ERRO: [editar_patrimonio GET] {e}")
            return redirect(url_for('patrimonio.listar_patrimonio'))

    @patrimonio_bp.route('/patrimonio/excluir/<string:item_doc_id>', methods=['POST'], endpoint='excluir_patrimonio')
    @login_required
    @admin_required
    def excluir_patrimonio(item_doc_id):
        """
        Exclui um item de patrimônio.
        """
        db_instance = get_db()
        clinica_id = session['clinica_id']
        try:
            # Opcional: Remover contas a pagar vinculadas ao patrimônio
            contas_vinculadas_query = db_instance.collection('clinicas').document(clinica_id).collection('contas_a_pagar').where(filter=FieldFilter('patrimonio_id', '==', item_doc_id)).stream()
            for conta_doc in contas_vinculadas_query:
                conta_doc.reference.delete()
                print(f"Conta a pagar vinculada {conta_doc.id} excluída.")

            db_instance.collection('clinicas').document(clinica_id).collection('patrimonio').document(item_doc_id).delete()
            flash('Item de patrimônio e contas a pagar vinculadas (se houver) excluídos com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao excluir item de patrimônio: {e}.', 'danger')
            print(f"ERRO: [excluir_patrimonio] {e}")
        return redirect(url_for('patrimonio.listar_patrimonio'))

    app.register_blueprint(patrimonio_bp)

