from flask import Blueprint, render_template, session, flash, redirect, url_for, request, current_app
from google.cloud.firestore_v1.base_query import FieldFilter
from decorators.auth_decorators import login_required, admin_required # Import decorators
from utils.firestore_utils import convert_doc_to_dict # Import utility functions

anamnesis_templates_bp = Blueprint('anamnesis_templates_bp', __name__)

@anamnesis_templates_bp.route('/modelos_anamnese')
@login_required
@admin_required
def listar_modelos_anamnese():
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    modelos_lista = []
    try:
        docs = db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').order_by('identificacao').stream()
        for doc in docs:
            modelo = convert_doc_to_dict(doc)
            modelos_lista.append(modelo)
    except Exception as e:
        flash(f'Erro ao listar modelos de anamnese: {e}.', 'danger')
        print(f"Erro list_anamnesis_templates: {e}")
    return render_template('modelos_anamnese.html', modelos=modelos_lista)

@anamnesis_templates_bp.route('/modelos_anamnese/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_modelo_anamnese():
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    if request.method == 'POST':
        identificacao = request.form['identificacao'].strip()
        conteudo_modelo = request.form['conteudo_modelo']
        
        if not identificacao:
            flash('A identificação do modelo é obrigatória.', 'danger')
            return render_template('modelo_anamnese_form.html', modelo=request.form, action_url=url_for('anamnesis_templates_bp.adicionar_modelo_anamnese'))
        try:
            db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').add({
                'identificacao': identificacao,
                'conteudo_modelo': conteudo_modelo,
                'criado_em': db.SERVER_TIMESTAMP
            })
            flash('Modelo de anamnese adicionado com sucesso!', 'success')
            return redirect(url_for('anamnesis_templates_bp.listar_modelos_anamnese'))
        except Exception as e:
            flash(f'Erro ao adicionar modelo de anamnese: {e}', 'danger')
            print(f"Erro add_anamnesis_template: {e}")
    return render_template('modelo_anamnese_form.html', modelo=None, action_url=url_for('anamnesis_templates_bp.adicionar_modelo_anamnese'))

@anamnesis_templates_bp.route('/modelos_anamnese/editar/<string:modelo_doc_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_modelo_anamnese(modelo_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    modelo_ref = db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').document(modelo_doc_id)
    
    if request.method == 'POST':
        identificacao = request.form['identificacao'].strip()
        conteudo_modelo = request.form['conteudo_modelo']
        
        if not identificacao:
            flash('A identificação do modelo é obrigatória.', 'danger')
            return render_template('modelo_anamnese_form.html', modelo=request.form, action_url=url_for('anamnesis_templates_bp.editar_modelo_anamnese', modelo_doc_id=modelo_doc_id))
        try:
            modelo_ref.update({
                'identificacao': identificacao,
                'conteudo_modelo': conteudo_modelo,
                'atualizado_em': db.SERVER_TIMESTAMP
            })
            flash('Modelo de anamnese atualizado com sucesso!', 'success')
            return redirect(url_for('anamnesis_templates_bp.listar_modelos_anamnese'))
        except Exception as e:
            flash(f'Erro ao atualizar modelo de anamnese: {e}', 'danger')
            print(f"Erro edit_anamnesis_template (POST): {e}")

    try:
        modelo_doc = modelo_ref.get()
        if modelo_doc.exists:
            modelo = convert_doc_to_dict(modelo_doc)
            return render_template('modelo_anamnese_form.html', modelo=modelo, action_url=url_for('anamnesis_templates_bp.editar_modelo_anamnese', modelo_doc_id=modelo_doc_id))
        else:
            flash('Modelo de anamnese não encontrado.', 'danger')
            return redirect(url_for('anamnesis_templates_bp.listar_modelos_anamnese'))
    except Exception as e:
        flash(f'Erro ao carregar modelo de anamnese para edição: {e}', 'danger')
        print(f"Erro edit_anamnesis_template (GET): {e}")
        return redirect(url_for('anamnesis_templates_bp.listar_modelos_anamnese'))

@anamnesis_templates_bp.route('/modelos_anamnese/excluir/<string:modelo_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_modelo_anamnese(modelo_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    try:
        db.collection('clinicas').document(clinica_id).collection('modelos_anamnese').document(modelo_doc_id).delete()
        flash('Modelo de anamnese excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir modelo de anamnese: {e}.', 'danger')
        print(f"Erro delete_anamnesis_template: {e}")
    return redirect(url_for('anamnesis_templates_bp.listar_modelos_anamnese'))