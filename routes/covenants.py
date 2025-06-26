from flask import Blueprint, render_template, session, flash, redirect, url_for, request, current_app
from google.cloud.firestore_v1.base_query import FieldFilter
from decorators.auth_decorators import login_required, admin_required # Import decorators
from utils.firestore_utils import convert_doc_to_dict # Import utility functions

covenants_bp = Blueprint('covenants_bp', __name__)

@covenants_bp.route('/convenios')
@login_required
def listar_convenios():
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    convenios_ref = db.collection('clinicas').document(clinica_id).collection('convenios')
    convenios_lista = []
    try:
        docs = convenios_ref.order_by('nome').stream()
        for doc in docs:
            convenio = doc.to_dict()
            if convenio:
                convenio['id'] = doc.id
                convenios_lista.append(convenio)
    except Exception as e:
        flash(f'Erro ao listar convênios: {e}.', 'danger')
        print(f"Erro list_covenants: {e}")
    return render_template('convenios.html', convenios=convenios_lista)

@covenants_bp.route('/convenios/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_convenio():
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    if request.method == 'POST':
        nome = request.form['nome'].strip()
        registro_ans = request.form.get('registro_ans', '').strip()
        tipo_plano = request.form.get('tipo_plano', '').strip()

        if not nome:
            flash('O nome do convênio é obrigatório.', 'danger')
            return render_template('convenio_form.html', convenio=request.form, action_url=url_for('covenants_bp.adicionar_convenio'))
        try:
            db.collection('clinicas').document(clinica_id).collection('convenios').add({
                'nome': nome,
                'registro_ans': registro_ans if registro_ans else None,
                'tipo_plano': tipo_plano if tipo_plano else None,
                'criado_em': db.SERVER_TIMESTAMP
            })
            flash('Convênio adicionado com sucesso!', 'success')
            return redirect(url_for('covenants_bp.listar_convenios'))
        except Exception as e:
            flash(f'Erro ao adicionar convênio: {e}', 'danger')
            print(f"Erro add_covenant: {e}")
    return render_template('convenio_form.html', convenio=None, action_url=url_for('covenants_bp.adicionar_convenio'))

@covenants_bp.route('/convenios/editar/<string:convenio_doc_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_convenio(convenio_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    convenio_ref = db.collection('clinicas').document(clinica_id).collection('convenios').document(convenio_doc_id)
    
    if request.method == 'POST':
        nome = request.form['nome'].strip()
        registro_ans = request.form.get('registro_ans', '').strip()
        tipo_plano = request.form.get('tipo_plano', '').strip()

        if not nome:
            flash('O nome do convênio é obrigatório.', 'danger')
            return render_template('convenio_form.html', convenio=request.form, action_url=url_for('covenants_bp.editar_convenio', convenio_doc_id=convenio_doc_id))
        try:
            convenio_ref.update({
                'nome': nome,
                'registro_ans': registro_ans if registro_ans else None,
                'tipo_plano': tipo_plano if tipo_plano else None,
                'atualizado_em': db.SERVER_TIMESTAMP
            })
            flash('Convênio atualizado com sucesso!', 'success')
            return redirect(url_for('covenants_bp.listar_convenios'))
        except Exception as e:
            flash(f'Erro ao atualizar convênio: {e}', 'danger')
            print(f"Erro edit_covenant (POST): {e}")

    try:
        convenio_doc = convenio_ref.get()
        if convenio_doc.exists:
            convenio = convenio_doc.to_dict()
            if convenio:
                convenio['id'] = convenio_doc.id
                return render_template('convenio_form.html', convenio=convenio, action_url=url_for('covenants_bp.editar_convenio', convenio_doc_id=convenio_doc_id))
        else:
            flash('Convênio não encontrado.', 'danger')
            return redirect(url_for('covenants_bp.listar_convenios'))
    except Exception as e:
        flash(f'Erro ao carregar convênio para edição: {e}', 'danger')
        print(f"Erro edit_covenant (GET): {e}")
        return redirect(url_for('covenants_bp.listar_convenios'))

@covenants_bp.route('/convenios/excluir/<string:convenio_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_convenio(convenio_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    try:
        pacientes_ref = db.collection('clinicas').document(clinica_id).collection('pacientes')
        pacientes_com_convenio = pacientes_ref.where(filter=FieldFilter('convenio_id', '==', convenio_doc_id)).limit(1).get()
        if len(pacientes_com_convenio) > 0:
            flash('Este convênio não pode ser excluído, pois está associado a um ou mais pacientes.', 'danger')
            return redirect(url_for('covenants_bp.listar_convenios'))
            
        db.collection('clinicas').document(clinica_id).collection('convenios').document(convenio_doc_id).delete()
        flash('Convênio excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir convênio: {e}.', 'danger')
        print(f"Erro delete_covenant: {e}")
    return redirect(url_for('covenants_bp.listar_convenios'))