from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from functools import wraps

from utils import get_db, admin_required, login_required, get_all_endpoints, get_counts_for_navbar

cargos_bp = Blueprint('cargos', __name__, url_prefix='/cargos', template_folder='../templates')

# Dicionário de tradução dos endpoints para português
ENDPOINT_DESCRIPTIONS = {
    'index': 'Dashboard',
    'listar_pacientes': 'Listar Pacientes',
    'buscar_prontuario': 'Buscar Prontuários',
    'busca_peis': 'Buscar PEIs',
    'evaluations.list_patients_for_evaluation': 'Avaliações',
    'listar_agendamentos': 'Listar Agendamentos',
    'listar_servicos_procedimentos': 'Listar Serviços/Procedimentos',
    'listar_convenios': 'Listar Convênios',
    'protocols.list_protocols': 'Listar Protocolos',
    'listar_modelos_anamnese': 'Listar Modelos de Anamnese',
    'listar_profissionais': 'Listar Profissionais',
    'listar_contas_a_pagar': 'Listar Contas a Pagar',
    'listar_estoque': 'Listar Estoque',
    'patrimonio.listar_patrimonio': 'Listar Patrimônio',
    'listar_horarios': 'Listar Horários',
    'listar_usuarios': 'Listar Utilizadores',
    'cargos.listar_cargos': 'Listar Cargos',
    'cargos.novo_cargo': 'Adicionar Novo Cargo',
    'cargos.editar_cargo': 'Editar Cargo',
    'cargos.excluir_cargo': 'Excluir Cargo',
    'adicionar_profissional': 'Adicionar Profissional',
    'editar_profissional': 'Editar Profissional',
    'ativar_desativar_profissional': 'Ativar/Desativar Profissional',
    'add_goal': 'Adicionar Meta',
    'add_pei': 'Adicionar PEI',
    'add_pei_activity': 'Adicionar Atividade do PEI',
    'add_target_to_goal': 'Adicionar Alvo à Meta',
    'adicionar_anamnese': 'Adicionar Anamnese',
    'adicionar_conta_a_pagar': 'Adicionar Conta a Pagar',
    'adicionar_convenio': 'Adicionar Convênio',
    'adicionar_horario': 'Adicionar Horário',
    'adicionar_modelo_anamnese': 'Adicionar Modelo de Anamnese',
    'adicionar_paciente': 'Adicionar Paciente',
    'adicionar_produto_estoque': 'Adicionar Produto no Estoque',
    'adicionar_servico_procedimento': 'Adicionar Serviço/Procedimento',
    'adicionar_usuario': 'Adicionar Utilizador',
    'apagar_agendamento': 'Apagar Agendamento',
    'apagar_registro_movimentacao': 'Apagar Movimentação de Estoque',
    'api_produtos_ativos': 'API Produtos Ativos',
    'ativar_desativar_horario': 'Ativar/Desativar Horário',
    'ativar_desativar_produto': 'Ativar/Desativar Produto',
    'ativar_desativar_usuario': 'Ativar/Desativar Utilizador',
    'delete_documento_pdf': 'Excluir Documento PDF',
    'delete_goal': 'Excluir Meta',
    'delete_pei': 'Excluir PEI',
}

@cargos_bp.route('/', endpoint='listar_cargos')
@login_required
@admin_required
def listar_cargos():
    db = get_db()
    clinica_id = session.get('clinica_id')
    cargos_ref = db.collection('clinicas').document(clinica_id).collection('cargos')
    cargos_lista = []
    try:
        docs = cargos_ref.order_by('nome').stream()
        for doc in docs:
            cargo = doc.to_dict()
            cargo['id'] = doc.id
            cargos_lista.append(cargo)
    except Exception as e:
        flash(f'Erro ao listar cargos: {e}', 'danger')
        print(f"Erro listar_cargos: {e}")
    return render_template('cargos.html', cargos=cargos_lista)

@cargos_bp.route('/novo', methods=['GET', 'POST'], endpoint='novo_cargo')
@login_required
@admin_required
def novo_cargo():
    db = get_db()
    clinica_id = session.get('clinica_id')
    endpoints = get_all_endpoints()
    
    if request.method == 'POST':
        nome = request.form.get('nome')
        permissions = request.form.getlist('permissions')
        
        if not nome:
            flash('O nome do cargo é obrigatório.', 'danger')
            return redirect(url_for('cargos.novo_cargo'))

        try:
            existing_cargo = db.collection('clinicas').document(clinica_id).collection('cargos').where(filter=FieldFilter('nome', '==', nome)).limit(1).get()
            if len(existing_cargo) > 0:
                flash('Já existe um cargo com este nome.', 'warning')
                return redirect(url_for('cargos.novo_cargo'))

            cargo_data = {
                'nome': nome,
                'permissions': permissions,
                'created_at': firestore.SERVER_TIMESTAMP
            }
            db.collection('clinicas').document(clinica_id).collection('cargos').add(cargo_data)
            flash('Cargo adicionado com sucesso!', 'success')
            return redirect(url_for('cargos.listar_cargos'))
        except Exception as e:
            flash(f'Erro ao adicionar cargo: {e}', 'danger')
            print(f"Erro novo_cargo (POST): {e}")
            return redirect(url_for('cargos.novo_cargo'))

    return render_template('cargo_form.html', endpoints=endpoints, cargo=None, descriptions=ENDPOINT_DESCRIPTIONS)

@cargos_bp.route('/editar/<string:cargo_id>', methods=['GET', 'POST'], endpoint='editar_cargo')
@login_required
@admin_required
def editar_cargo(cargo_id):
    db = get_db()
    clinica_id = session.get('clinica_id')
    endpoints = get_all_endpoints()
    cargo_ref = db.collection('clinicas').document(clinica_id).collection('cargos').document(cargo_id)
    
    if request.method == 'POST':
        nome = request.form.get('nome')
        permissions = request.form.getlist('permissions')
        
        if not nome:
            flash('O nome do cargo é obrigatório.', 'danger')
            return redirect(url_for('cargos.editar_cargo', cargo_id=cargo_id))

        try:
            cargo_ref.update({
                'nome': nome,
                'permissions': permissions,
                'updated_at': firestore.SERVER_TIMESTAMP
            })
            flash('Cargo atualizado com sucesso!', 'success')
            return redirect(url_for('cargos.listar_cargos'))
        except Exception as e:
            flash(f'Erro ao atualizar cargo: {e}', 'danger')
            print(f"Erro editar_cargo (POST): {e}")
            return redirect(url_for('cargos.editar_cargo', cargo_id=cargo_id))

    try:
        cargo_doc = cargo_ref.get()
        if not cargo_doc.exists:
            flash('Cargo não encontrado.', 'danger')
            return redirect(url_for('cargos.listar_cargos'))
        cargo = cargo_doc.to_dict()
        cargo['id'] = cargo_doc.id
        cargo['permissions_set'] = set(cargo.get('permissions', []))
    except Exception as e:
        flash(f'Erro ao carregar cargo: {e}', 'danger')
        print(f"Erro editar_cargo (GET): {e}")
        return redirect(url_for('cargos.listar_cargos'))

    return render_template('cargo_form.html', endpoints=endpoints, cargo=cargo, descriptions=ENDPOINT_DESCRIPTIONS)

@cargos_bp.route('/excluir/<string:cargo_id>', methods=['POST'], endpoint='excluir_cargo')
@login_required
@admin_required
def excluir_cargo(cargo_id):
    db = get_db()
    clinica_id = session.get('clinica_id')
    cargo_ref = db.collection('clinicas').document(clinica_id).collection('cargos').document(cargo_id)
    
    try:
        profissionais_com_cargo = db.collection('clinicas').document(clinica_id).collection('profissionais').where(filter=FieldFilter('cargo_id', '==', cargo_id)).limit(1).get()
        if len(profissionais_com_cargo) > 0:
            flash('Não é possível excluir o cargo. Existem profissionais vinculados a ele.', 'danger')
        else:
            cargo_ref.delete()
            flash('Cargo excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir cargo: {e}', 'danger')
        print(f"Erro excluir_cargo: {e}")
    
    return redirect(url_for('cargos.listar_cargos'))

