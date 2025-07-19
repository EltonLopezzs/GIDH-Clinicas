import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from google.cloud.firestore_v1.base_query import FieldFilter
from utils import get_db, login_required # Importar get_db e login_required
from datetime import datetime # Importar datetime para a data de inclusão

protocols_bp = Blueprint('protocols', __name__, template_folder='../templates')

@protocols_bp.route('/protocols')
@login_required
def list_protocols():
    """
    Rota para listar todos os protocolos ou buscar por termo, utilizando Firestore.
    """
    db = get_db()
    if not db:
        flash('Erro: Banco de dados não inicializado.', 'danger')
        return render_template('protocolos.html', protocols=[], search_term='')

    clinica_id = session.get('clinica_id')
    if not clinica_id:
        flash('Erro: ID da clínica não encontrado na sessão.', 'danger')
        return render_template('protocolos.html', protocols=[], search_term='')

    protocols_ref = db.collection('clinicas').document(clinica_id).collection('protocols')
    protocols_list = []
    search_term = request.args.get('search_term', '').lower()

    try:
        query = protocols_ref.order_by('nome') # Ordena por nome para exibição
        
        docs = query.stream()
        for doc in docs:
            protocol_data = doc.to_dict()
            protocol_data['id'] = doc.id
            
            # Carregar subcoleções para a lista de protocolos
            protocol_data['etapas'] = []
            for sub_doc in doc.reference.collection('etapas').stream():
                etapa_data = sub_doc.to_dict()
                etapa_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
                protocol_data['etapas'].append(etapa_data)

            protocol_data['niveis'] = []
            for sub_doc in doc.reference.collection('niveis').stream():
                nivel_data = sub_doc.to_dict()
                nivel_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
                protocol_data['niveis'].append(nivel_data)

            protocol_data['habilidades'] = []
            for sub_doc in doc.reference.collection('habilidades').stream():
                habilidade_data = sub_doc.to_dict()
                habilidade_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
                protocol_data['habilidades'].append(habilidade_data)

            protocol_data['pontuacao'] = []
            for sub_doc in doc.reference.collection('pontuacao').stream():
                pontuacao_data = sub_doc.to_dict()
                pontuacao_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
                protocol_data['pontuacao'].append(pontuacao_data)

            protocol_data['tarefas_testes'] = []
            for sub_doc in doc.reference.collection('tarefas_testes').stream():
                tarefa_data = sub_doc.to_dict()
                tarefa_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
                protocol_data['tarefas_testes'].append(tarefa_data)

            # Adiciona a data de inclusão, se existir
            # Firestore Timestamp objects need to be converted to Python datetime objects for display
            if 'data_inclusao' in protocol_data and protocol_data['data_inclusao']:
                # Convert Firestore Timestamp to Python datetime object
                protocol_data['data_inclusao'] = protocol_data['data_inclusao'].strftime('%d/%m/%Y %H:%M:%S')
            else:
                protocol_data['data_inclusao'] = 'N/A'


            # Filtragem em memória se houver termo de busca
            if search_term:
                if search_term in protocol_data.get('nome', '').lower() or \
                   search_term in protocol_data.get('descricao', '').lower():
                    protocols_list.append(protocol_data)
            else:
                protocols_list.append(protocol_data)

    except Exception as e:
        print(f"Erro ao buscar protocolos no Firestore: {e}")
        flash('Erro ao carregar protocolos. Tente novamente mais tarde.', 'danger')

    return render_template('protocolos.html', protocols=protocols_list, search_term=search_term)

@protocols_bp.route('/protocols/add')
@login_required
def add_protocol():
    """
    Rota para exibir o formulário de adição de novo protocolo.
    """
    return render_template('protocolo_form.html', protocol=None)

@protocols_bp.route('/protocols/edit/<protocol_id>')
@login_required
def edit_protocol(protocol_id):
    """
    Rota para exibir o formulário de edição de um protocolo existente, utilizando Firestore.
    Esta rota é para acesso direto, a modal usará a rota `get_protocol_form_content`.
    """
    db = get_db()
    if not db:
        flash('Erro: Banco de dados não inicializado.', 'danger')
        return redirect(url_for('protocols.list_protocols'))

    clinica_id = session.get('clinica_id')
    if not clinica_id:
        flash('Erro: ID da clínica não encontrado na sessão.', 'danger')
        return redirect(url_for('protocols.list_protocols'))

    protocol_ref = db.collection('clinicas').document(clinica_id).collection('protocols').document(protocol_id)
    
    try:
        protocol_doc = protocol_ref.get()
        if not protocol_doc.exists:
            flash('Protocolo não encontrado.', 'danger')
            return redirect(url_for('protocols.list_protocols'))
        
        protocol = protocol_doc.to_dict()
        protocol['id'] = protocol_doc.id # Adiciona o ID ao dicionário do protocolo
        
        # Carregar subcoleções
        protocol['etapas'] = []
        for sub_doc in protocol_ref.collection('etapas').stream():
            etapa_data = sub_doc.to_dict()
            etapa_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
            protocol['etapas'].append(etapa_data)

        protocol['niveis'] = []
        for sub_doc in protocol_ref.collection('niveis').stream():
            nivel_data = sub_doc.to_dict()
            nivel_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
            protocol['niveis'].append(nivel_data)

        protocol['habilidades'] = []
        for sub_doc in protocol_ref.collection('habilidades').stream():
            habilidade_data = sub_doc.to_dict()
            habilidade_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
            protocol['habilidades'].append(habilidade_data)

        protocol['pontuacao'] = []
        for sub_doc in protocol_ref.collection('pontuacao').stream():
            pontuacao_data = sub_doc.to_dict()
            pontuacao_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
            protocol['pontuacao'].append(pontuacao_data)

        protocol['tarefas_testes'] = []
        for sub_doc in protocol_ref.collection('tarefas_testes').stream():
            tarefa_data = sub_doc.to_dict()
            tarefa_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
            protocol['tarefas_testes'].append(tarefa_data)

        # Adiciona a data de inclusão, se existir
        if 'data_inclusao' in protocol and protocol['data_inclusao']:
            # Convert Firestore Timestamp to Python datetime object
            protocol['data_inclusao'] = protocol['data_inclusao'].strftime('%d/%m/%Y %H:%M:%S')
        else:
            protocol['data_inclusao'] = 'N/A'

        return render_template('protocolo_form.html', protocol=protocol)
    except Exception as e:
        print(f"Erro ao buscar protocolo para edição no Firestore: {e}")
        flash('Erro ao carregar protocolo para edição. Tente novamente mais tarde.', 'danger')
        return redirect(url_for('protocols.list_protocols'))

@protocols_bp.route('/protocols/get_form_content/<protocol_id>', methods=['GET'])
@login_required
def get_protocol_form_content(protocol_id):
    """
    Rota para retornar o conteúdo HTML do formulário de edição de um protocolo
    para ser carregado via AJAX em uma modal.
    """
    db = get_db()
    if not db:
        return jsonify(error='Erro: Banco de dados não inicializado.'), 500

    clinica_id = session.get('clinica_id')
    if not clinica_id:
        return jsonify(error='Erro: ID da clínica não encontrado na sessão.'), 403

    protocol = None
    if protocol_id != 'new': # 'new' indica que é um novo protocolo, não precisa buscar
        protocol_ref = db.collection('clinicas').document(clinica_id).collection('protocols').document(protocol_id)
        try:
            protocol_doc = protocol_ref.get()
            if protocol_doc.exists:
                protocol = protocol_doc.to_dict()
                protocol['id'] = protocol_doc.id
                # Carregar subcoleções para o modal também
                protocol['etapas'] = []
                for sub_doc in protocol_ref.collection('etapas').stream():
                    etapa_data = sub_doc.to_dict()
                    etapa_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
                    protocol['etapas'].append(etapa_data)

                protocol['niveis'] = []
                for sub_doc in protocol_ref.collection('niveis').stream():
                    nivel_data = sub_doc.to_dict()
                    nivel_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
                    protocol['niveis'].append(nivel_data)

                protocol['habilidades'] = []
                for sub_doc in protocol_ref.collection('habilidades').stream():
                    habilidade_data = sub_doc.to_dict()
                    habilidade_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
                    protocol['habilidades'].append(habilidade_data)

                protocol['pontuacao'] = []
                for sub_doc in protocol_ref.collection('pontuacao').stream():
                    pontuacao_data = sub_doc.to_dict()
                    pontuacao_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
                    protocol['pontuacao'].append(pontuacao_data)

                protocol['tarefas_testes'] = []
                for sub_doc in protocol_ref.collection('tarefas_testes').stream():
                    tarefa_data = sub_doc.to_dict()
                    tarefa_data['id'] = sub_doc.id # Adiciona o ID do subdocumento
                    protocol['tarefas_testes'].append(tarefa_data)
            else:
                return jsonify(error='Protocolo não encontrado.'), 404
        except Exception as e:
            print(f"Erro ao buscar protocolo para formulário da modal: {e}")
            return jsonify(error='Erro ao carregar protocolo para edição.'), 500

    # Renderiza o template do formulário e retorna como string
    # Usamos um template separado ou passamos um flag para ajustar o layout se necessário
    return render_template('protocolo_form_modal_content.html', protocol=protocol)


def delete_subcollection_docs(protocol_ref, subcollection_name):
    """
    Função auxiliar para deletar todos os documentos de uma subcoleção.
    """
    docs = protocol_ref.collection(subcollection_name).stream()
    for doc in docs:
        doc.reference.delete()

@protocols_bp.route('/protocols/save', methods=['POST'])
@login_required
def save_protocol():
    """
    Rota para salvar (adicionar ou atualizar) um protocolo no Firestore.
    """
    db = get_db()
    if not db:
        flash('Erro: Banco de dados não inicializado.', 'danger')
        return redirect(url_for('protocols.list_protocols'))

    clinica_id = session.get('clinica_id')
    if not clinica_id:
        flash('Erro: ID da clínica não encontrado na sessão.', 'danger')
        return redirect(url_for('protocols.list_protocols'))

    protocol_id = request.form.get('id')
    
    # Debug: Print all form data
    print(f"Dados do formulário recebidos: {request.form}")

    # Campos da aba Geral
    tipo_protocolo = request.form.get('tipo_protocolo')
    nome = request.form.get('nome') 
    descricao = request.form.get('descricao', '')
    
    ativo = 'ativo' in request.form

    # Dados principais do protocolo (sem as listas dinâmicas)
    main_protocol_data = {
        'tipo_protocolo': tipo_protocolo,
        'nome': nome,
        'descricao': descricao,
        'ativo': ativo,
        'observacoes_gerais': request.form.get('observacoes_gerais', '')
    }

    try:
        if protocol_id:
            # Atualizar protocolo existente
            protocol_ref = db.collection('clinicas').document(clinica_id).collection('protocols').document(protocol_id)
            protocol_ref.update(main_protocol_data)
            flash('Protocolo atualizado com sucesso!', 'success')
            
            # Ao atualizar, deleta as subcoleções existentes para recriá-las
            delete_subcollection_docs(protocol_ref, 'etapas')
            delete_subcollection_docs(protocol_ref, 'niveis')
            delete_subcollection_docs(protocol_ref, 'habilidades')
            delete_subcollection_docs(protocol_ref, 'pontuacao')
            delete_subcollection_docs(protocol_ref, 'tarefas_testes')

        else:
            # Adicionar novo protocolo e obter o ID gerado
            protocol_ref = db.collection('clinicas').document(clinica_id).collection('protocols').document()
            main_protocol_data['data_inclusao'] = datetime.now() # Adiciona a data de inclusão para novos protocolos
            protocol_ref.set(main_protocol_data)
            protocol_id = protocol_ref.id # Armazena o ID do novo protocolo
            flash('Protocolo adicionado com sucesso!', 'success')
        
        # Coleta e salva as etapas dinamicamente como subcoleção
        etapas_ids = request.form.getlist('etapa_id[]')
        etapas_nomes = request.form.getlist('etapa_nome[]')
        etapas_descricoes = request.form.getlist('etapa_descricao[]')
        etapas_ordem = request.form.getlist('etapa_ordem[]') # Coletar a ordem também
        for i in range(len(etapas_nomes)):
            if etapas_nomes[i].strip():
                protocol_ref.collection('etapas').add({
                    'id': etapas_ids[i].strip(), # Salvar o ID gerado
                    'nome': etapas_nomes[i].strip(),
                    'descricao': etapas_descricoes[i].strip() if i < len(etapas_descricoes) else '',
                    'ordem': int(etapas_ordem[i].strip()) if etapas_ordem[i].strip() else None # Salvar a ordem
                })

        # Coleta e salva os níveis dinamicamente como subcoleção
        niveis_ids = request.form.getlist('nivel_id[]')
        niveis_ordem = request.form.getlist('nivel_ordem[]')
        niveis_valor = request.form.getlist('nivel_valor[]')
        niveis_faixa_etaria = request.form.getlist('nivel_faixa_etaria[]')
        for i in range(len(niveis_ordem)):
            if niveis_ordem[i].strip() and niveis_valor[i].strip():
                try:
                    protocol_ref.collection('niveis').add({
                        'id': niveis_ids[i].strip(), # Salvar o ID gerado
                        'ordem': int(niveis_ordem[i].strip()),
                        'nivel': int(niveis_valor[i].strip()),
                        'faixa_etaria': niveis_faixa_etaria[i].strip() if i < len(niveis_faixa_etaria) else ''
                    })
                except ValueError:
                    flash('Erro: Ordem ou Nível da etapa inválido. Certifique-se de que são números inteiros.', 'danger')
                    continue

        # Coleta e salva as habilidades dinamicamente como subcoleção
        habilidades_ids = request.form.getlist('habilidade_id[]')
        habilidades_ordem = request.form.getlist('habilidade_ordem[]')
        habilidades_nome = request.form.getlist('habilidade_nome[]')
        for i in range(len(habilidades_ordem)):
            if habilidades_ordem[i].strip() and habilidades_nome[i].strip():
                try:
                    protocol_ref.collection('habilidades').add({
                        'id': habilidades_ids[i].strip(), # Salvar o ID gerado
                        'ordem': int(habilidades_ordem[i].strip()),
                        'nome': habilidades_nome[i].strip()
                    })
                except ValueError:
                    flash('Erro: Ordem da habilidade inválida. Certifique-se de que é um número inteiro.', 'danger')
                    continue

        # Coleta e salva as pontuações dinamicamente como subcoleção
        pontuacao_ids = request.form.getlist('pontuacao_id[]')
        pontuacao_ordem = request.form.getlist('pontuacao_ordem[]')
        pontuacao_tipo = request.form.getlist('pontuacao_tipo[]')
        pontuacao_descricao = request.form.getlist('pontuacao_descricao[]')
        pontuacao_valor = request.form.getlist('pontuacao_valor[]')
        for i in range(len(pontuacao_ordem)):
            if pontuacao_ordem[i].strip() and pontuacao_tipo[i].strip() and pontuacao_valor[i].strip():
                try:
                    protocol_ref.collection('pontuacao').add({
                        'id': pontuacao_ids[i].strip(), # Salvar o ID gerado
                        'ordem': int(pontuacao_ordem[i].strip()),
                        'tipo': pontuacao_tipo[i].strip(),
                        'descricao': pontuacao_descricao[i].strip() if i < len(pontuacao_descricao) else '',
                        'valor': float(pontuacao_valor[i].strip())
                    })
                except ValueError:
                    flash('Erro: Ordem, Tipo ou Valor da pontuação inválido. Certifique-se de que Ordem é inteiro e Valor é numérico.', 'danger')
                    continue

        # Coleta e salva as tarefas/testes dinamicamente como subcoleção
        tarefa_ids = request.form.getlist('tarefa_id[]')
        tarefa_nivel = request.form.getlist('tarefa_nivel[]')
        tarefa_ordem = request.form.getlist('tarefa_ordem[]')
        tarefa_item = request.form.getlist('tarefa_item[]')
        tarefa_nome = request.form.getlist('tarefa_nome[]')
        tarefa_habilidade_marco = request.form.getlist('tarefa_habilidade_marco[]')
        tarefa_resultado_observacao = request.form.getlist('tarefa_resultado_observacao[]')
        tarefa_pergunta = request.form.getlist('tarefa_pergunta[]')
        tarefa_exemplo = request.form.getlist('tarefa_exemplo[]')
        tarefa_criterio = request.form.getlist('tarefa_criterio[]')
        tarefa_objetivo = request.form.getlist('tarefa_objetivo[]')

        for i in range(len(tarefa_nivel)):
            if tarefa_nivel[i].strip() and tarefa_ordem[i].strip() and tarefa_nome[i].strip():
                try:
                    protocol_ref.collection('tarefas_testes').add({
                        'id': tarefa_ids[i].strip(), # Salvar o ID gerado
                        'nivel': int(tarefa_nivel[i].strip()),
                        'ordem': int(tarefa_ordem[i].strip()),
                        'item': tarefa_item[i].strip() if i < len(tarefa_item) else '',
                        'nome': tarefa_nome[i].strip(),
                        'habilidade_marco': tarefa_habilidade_marco[i].strip() if i < len(tarefa_habilidade_marco) else '',
                        'resultado_observacao': tarefa_resultado_observacao[i].strip() if i < len(tarefa_resultado_observacao) else '',
                        'pergunta': tarefa_pergunta[i].strip() if i < len(tarefa_pergunta) else '',
                        'exemplo': tarefa_exemplo[i].strip() if i < len(tarefa_exemplo) else '',
                        'criterio': tarefa_criterio[i].strip() if i < len(tarefa_criterio) else '',
                        'objetivo': tarefa_objetivo[i].strip() if i < len(tarefa_objetivo) else ''
                    })
                except ValueError:
                    flash('Erro: Nível, Ordem ou Nome da tarefa inválido. Certifique-se de que Nível e Ordem são números inteiros.', 'danger')
                    continue

    except Exception as e:
        print(f"Erro ao salvar protocolo no Firestore: {e}")
        flash('Erro ao salvar protocolo. Verifique os dados e tente novamente.', 'danger')

    return redirect(url_for('protocols.list_protocols'))

@protocols_bp.route('/protocols/delete/<protocol_id>', methods=['POST'])
@login_required
def delete_protocol(protocol_id):
    """
    Rota para excluir um protocolo do Firestore.
    """
    print(f"DEBUG: Tentando excluir protocolo com ID: {protocol_id}") # Log de depuração

    db = get_db()
    if not db:
        print("DEBUG: Erro: Banco de dados não inicializado.")
        return jsonify(success=False, message='Erro: Banco de dados não inicializado.'), 500

    clinica_id = session.get('clinica_id')
    if not clinica_id:
        print("DEBUG: Erro: ID da clínica não encontrado na sessão.")
        return jsonify(success=False, message='Erro: ID da clínica não encontrado na sessão.'), 403

    protocol_ref = db.collection('clinicas').document(clinica_id).collection('protocols').document(protocol_id)
    
    try:
        # Verifica se o protocolo existe antes de tentar deletar
        if not protocol_ref.get().exists:
            print(f"DEBUG: Protocolo com ID {protocol_id} não encontrado.")
            # Não use flash para requisições AJAX que esperam JSON.
            return jsonify(success=False, message='Protocolo não encontrado.'), 404

        print(f"DEBUG: Deletando subcoleções para o protocolo ID: {protocol_id}")
        # Deleta todas as subcoleções antes de deletar o documento principal
        delete_subcollection_docs(protocol_ref, 'etapas')
        delete_subcollection_docs(protocol_ref, 'niveis')
        delete_subcollection_docs(protocol_ref, 'habilidades')
        delete_subcollection_docs(protocol_ref, 'pontuacao')
        delete_subcollection_docs(protocol_ref, 'tarefas_testes')
        print(f"DEBUG: Subcoleções deletadas para o protocolo ID: {protocol_id}")

        protocol_ref.delete()
        print(f"DEBUG: Protocolo principal deletado: {protocol_id}")
        # Não use flash para requisições AJAX que esperam JSON.
        return jsonify(success=True, message='Protocolo excluído com sucesso!')
    except Exception as e:
        print(f"DEBUG: Erro ao excluir protocolo do Firestore: {e}")
        # Não use flash para requisições AJAX que esperam JSON.
        return jsonify(success=False, message=f'Erro ao excluir protocolo: {str(e)}.'), 500
