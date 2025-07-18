import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from google.cloud.firestore_v1.base_query import FieldFilter
from utils import get_db, login_required # Importar get_db e login_required

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
        
        return render_template('protocolo_form.html', protocol=protocol)
    except Exception as e:
        print(f"Erro ao buscar protocolo para edição no Firestore: {e}")
        flash('Erro ao carregar protocolo para edição. Tente novamente mais tarde.', 'danger')
        return redirect(url_for('protocols.list_protocols'))

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
    
    # Campos da aba Geral
    tipo_protocolo = request.form.get('tipo_protocolo')
    nome = request.form['nome']
    descricao = request.form.get('descricao', '')
    duracao_estimada = request.form.get('duracao_estimada', type=int)
    ativo = 'ativo' in request.form

    # Coleta as etapas dinamicamente
    etapas_nomes = request.form.getlist('etapa_nome[]')
    etapas_descricoes = request.form.getlist('etapa_descricao[]')
    etapas = []
    for i in range(len(etapas_nomes)):
        if etapas_nomes[i].strip():
            etapas.append({
                'nome': etapas_nomes[i].strip(),
                'descricao': etapas_descricoes[i].strip() if i < len(etapas_descricoes) else ''
            })

    # Coleta os níveis dinamicamente
    niveis_ordem = request.form.getlist('nivel_ordem[]')
    niveis_valor = request.form.getlist('nivel_valor[]')
    niveis_faixa_etaria = request.form.getlist('nivel_faixa_etaria[]')
    niveis = []
    for i in range(len(niveis_ordem)):
        # Garante que 'ordem' e 'nivel' são preenchidos para considerar o nível
        if niveis_ordem[i].strip() and niveis_valor[i].strip():
            try:
                niveis.append({
                    'ordem': int(niveis_ordem[i].strip()),
                    'nivel': int(niveis_valor[i].strip()),
                    'faixa_etaria': niveis_faixa_etaria[i].strip() if i < len(niveis_faixa_etaria) else ''
                })
            except ValueError:
                flash('Erro: Ordem ou Nível da etapa inválido.', 'danger')
                return redirect(url_for('protocols.add_protocol') if not protocol_id else url_for('protocols.edit_protocol', protocol_id=protocol_id))


    # Coleta as habilidades dinamicamente
    habilidades_ordem = request.form.getlist('habilidade_ordem[]')
    habilidades_nome = request.form.getlist('habilidade_nome[]')
    habilidades = []
    for i in range(len(habilidades_ordem)):
        if habilidades_ordem[i].strip() and habilidades_nome[i].strip():
            try:
                habilidades.append({
                    'ordem': int(habilidades_ordem[i].strip()),
                    'nome': habilidades_nome[i].strip()
                })
            except ValueError:
                flash('Erro: Ordem da habilidade inválida.', 'danger')
                return redirect(url_for('protocols.add_protocol') if not protocol_id else url_for('protocols.edit_protocol', protocol_id=protocol_id))

    # Coleta as pontuações dinamicamente
    pontuacao_ordem = request.form.getlist('pontuacao_ordem[]')
    pontuacao_tipo = request.form.getlist('pontuacao_tipo[]')
    pontuacao_descricao = request.form.getlist('pontuacao_descricao[]')
    pontuacao_valor = request.form.getlist('pontuacao_valor[]')
    pontuacao = []
    for i in range(len(pontuacao_ordem)):
        if pontuacao_ordem[i].strip() and pontuacao_tipo[i].strip() and pontuacao_valor[i].strip():
            try:
                pontuacao.append({
                    'ordem': int(pontuacao_ordem[i].strip()),
                    'tipo': pontuacao_tipo[i].strip(),
                    'descricao': pontuacao_descricao[i].strip() if i < len(pontuacao_descricao) else '',
                    'valor': float(pontuacao_valor[i].strip())
                })
            except ValueError:
                flash('Erro: Ordem, Tipo ou Valor da pontuação inválido.', 'danger')
                return redirect(url_for('protocols.add_protocol') if not protocol_id else url_for('protocols.edit_protocol', protocol_id=protocol_id))

    # Coleta as tarefas/testes dinamicamente
    tarefa_nivel = request.form.getlist('tarefa_nivel[]')
    tarefa_ordem = request.form.getlist('tarefa_ordem[]')
    tarefa_item = request.form.getlist('tarefa_item[]')
    tarefa_nome = request.form.getlist('tarefa_nome[]')
    tarefa_habilidade = request.form.getlist('tarefa_habilidade[]')
    tarefa_marco = request.form.getlist('tarefa_marco[]')
    tarefas_testes = []
    for i in range(len(tarefa_nivel)):
        if tarefa_nivel[i].strip() and tarefa_ordem[i].strip() and tarefa_nome[i].strip():
            try:
                tarefas_testes.append({
                    'nivel': int(tarefa_nivel[i].strip()),
                    'ordem': int(tarefa_ordem[i].strip()),
                    'item': tarefa_item[i].strip() if i < len(tarefa_item) else '',
                    'nome': tarefa_nome[i].strip(),
                    'habilidade': tarefa_habilidade[i].strip() if i < len(tarefa_habilidade) else '',
                    'marco': tarefa_marco[i].strip() if i < len(tarefa_marco) else ''
                })
            except ValueError:
                flash('Erro: Nível, Ordem ou Nome da tarefa inválido.', 'danger')
                return redirect(url_for('protocols.add_protocol') if not protocol_id else url_for('protocols.edit_protocol', protocol_id=protocol_id))

    observacoes_gerais = request.form.get('observacoes_gerais', '')

    protocol_data = {
        'tipo_protocolo': tipo_protocolo,
        'nome': nome,
        'descricao': descricao,
        'duracao_estimada': duracao_estimada,
        'ativo': ativo,
        'etapas': etapas,
        'niveis': niveis,
        'habilidades': habilidades,
        'pontuacao': pontuacao,
        'tarefas_testes': tarefas_testes,
        'observacoes_gerais': observacoes_gerais
    }

    try:
        if protocol_id:
            # Atualizar protocolo existente
            protocol_ref = db.collection('clinicas').document(clinica_id).collection('protocols').document(protocol_id)
            protocol_ref.update(protocol_data)
            flash('Protocolo atualizado com sucesso!', 'success')
        else:
            # Adicionar novo protocolo
            db.collection('clinicas').document(clinica_id).collection('protocols').add(protocol_data)
            flash('Protocolo adicionado com sucesso!', 'success')
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
    db = get_db()
    if not db:
        return jsonify(success=False, message='Erro: Banco de dados não inicializado.'), 500

    clinica_id = session.get('clinica_id')
    if not clinica_id:
        return jsonify(success=False, message='Erro: ID da clínica não encontrado na sessão.'), 403

    protocol_ref = db.collection('clinicas').document(clinica_id).collection('protocols').document(protocol_id)
    
    try:
        protocol_ref.delete()
        flash('Protocolo excluído com sucesso!', 'success')
        return jsonify(success=True)
    except Exception as e:
        print(f"Erro ao excluir protocolo do Firestore: {e}")
        flash('Erro ao excluir protocolo. Tente novamente.', 'danger')
        return jsonify(success=False, message='Erro ao excluir protocolo.'), 500

