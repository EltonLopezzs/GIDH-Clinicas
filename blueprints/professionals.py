from flask import render_template, session, flash, redirect, url_for, request
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore # Importar no topo


# Importar utils
from utils import get_db, login_required, admin_required


def register_professionals_routes(app):
    @app.route('/profissionais', endpoint='listar_profissionais')
    @login_required
    def listar_profissionais():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        profissionais_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais')
        profissionais_lista = []
        try:
            docs = profissionais_ref.order_by('nome').stream()
            for doc in docs:
                profissional = doc.to_dict()
                if profissional:
                    profissional['id'] = doc.id
                    profissionais_lista.append(profissional)
        except Exception as e:
            flash(f'Erro ao listar profissionais: {e}.', 'danger')
            print(f"Erro list_professionals: {e}")
        return render_template('profissionais.html', profissionais=profissionais_lista)

    @app.route('/profissionais/novo', methods=['GET', 'POST'], endpoint='adicionar_profissional')
    @login_required
    @admin_required
    def adicionar_profissional():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        if request.method == 'POST':
            nome = request.form['nome']
            telefone = request.form.get('telefone')
            email_profissional = request.form.get('email_profissional')
            crm_ou_registro = request.form.get('crm_ou_registro')
            ativo = 'ativo' in request.form
            try:
                if telefone and not telefone.isdigit():
                    flash('O telefone deve conter apenas números.', 'warning')
                    return render_template('profissional_form.html', profissional=request.form, action_url=url_for('adicionar_profissional'))

                db_instance.collection('clinicas').document(clinica_id).collection('profissionais').add({
                    'nome': nome,
                    'telefone': telefone if telefone else None,
                    'email': email_profissional if email_profissional else None,
                    'crm_ou_registro': crm_ou_registro if crm_ou_registro else None,
                    'ativo': ativo,
                    'criado_em': firestore.SERVER_TIMESTAMP
                })
                flash('Profissional adicionado com sucesso!', 'success')
                return redirect(url_for('listar_profissionais'))
            except Exception as e:
                flash(f'Erro ao adicionar profissional: {e}', 'danger')
                print(f"Erro add_professional: {e}")
        return render_template('profissional_form.html', profissional=None, action_url=url_for('adicionar_profissional'))


    @app.route('/profissionais/editar/<string:profissional_doc_id>', methods=['GET', 'POST'], endpoint='editar_profissional')
    @login_required
    def editar_profissional(profissional_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        profissional_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id)
        
        if request.method == 'POST':
            nome = request.form['nome']
            telefone = request.form.get('telefone')
            email_profissional = request.form.get('email_profissional')
            crm_ou_registro = request.form.get('crm_ou_registro')
            ativo = 'ativo' in request.form
            try:
                if telefone and not telefone.isdigit():
                    flash('O telefone deve conter apenas números.', 'warning')
                else:
                    profissional_ref.update({
                        'nome': nome,
                        'telefone': telefone if telefone else None,
                        'email': email_profissional if email_profissional else None,
                        'crm_ou_registro': crm_ou_registro if crm_ou_registro else None,
                        'ativo': ativo,
                        'atualizado_em': firestore.SERVER_TIMESTAMP
                    })
                    flash('Profissional atualizado com sucesso!', 'success')
                    return redirect(url_for('listar_profissionais'))
            except Exception as e:
                flash(f'Erro ao atualizar profissional: {e}', 'danger')
                print(f"Erro edit_professional (POST): {e}")

        try:
            profissional_doc = profissional_ref.get()
            if profissional_doc.exists:
                profissional = profissional_doc.to_dict()
                if profissional:
                    profissional['id'] = profissional_doc.id
                    return render_template('profissional_form.html', profissional=profissional, action_url=url_for('editar_profissional', profissional_doc_id=profissional_doc_id))
            else:
                flash('Profissional não encontrado.', 'danger')
                return redirect(url_for('listar_profissionais'))
        except Exception as e:
            flash(f'Erro ao carregar profissional para edição: {e}', 'danger')
            print(f"Erro edit_professional (GET): {e}")
            return redirect(url_for('listar_profissionais'))

    @app.route('/profissionais/ativar_desativar/<string:profissional_doc_id>', methods=['POST'], endpoint='ativar_desativar_profissional')
    @login_required
    @admin_required
    def ativar_desativar_profissional(profissional_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        profissional_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id)
        try:
            profissional_doc = profissional_ref.get()
            if profissional_doc.exists:
                data = profissional_doc.to_dict()
                if data:
                    current_status = data.get('ativo', False)    
                    new_status = not current_status
                    profissional_ref.update({'ativo': new_status, 'atualizado_em': firestore.SERVER_TIMESTAMP})
                    flash(f'Profissional {"ativado" if new_status else "desativado"} com sucesso!', 'success')
            else:
                flash('Profissional não encontrado no mapeamento.', 'danger')
        except Exception as e:
            flash(f'Erro ao alterar o status do profissional: {e}', 'danger')
            print(f"Erro activate_deactivate_user: {e}")
        return redirect(url_for('listar_profissionais'))