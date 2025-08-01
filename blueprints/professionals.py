from flask import render_template, session, flash, redirect, url_for, request
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore

from utils import get_db, login_required, admin_required, permission_required, convert_doc_to_dict

def register_professionals_routes(app):
    @app.route('/profissionais', endpoint='listar_profissionais')
    @login_required
    @permission_required('listar_profissionais')
    def listar_profissionais():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        profissionais_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais')
        cargos_ref = db_instance.collection('clinicas').document(clinica_id).collection('cargos')
        
        profissionais_lista = []
        cargos_map = {doc.id: doc.to_dict().get('nome', 'N/A') for doc in cargos_ref.stream()}
        cargos_lista = [{'id': doc.id, 'nome': doc.to_dict().get('nome')} for doc in cargos_ref.order_by('nome').stream()]

        try:
            docs = profissionais_ref.order_by('nome').stream()
            for doc in docs:
                profissional = doc.to_dict()
                if profissional:
                    profissional['id'] = doc.id
                    profissional['cargo_nome'] = cargos_map.get(profissional.get('cargo_id'), 'Sem Cargo')
                    profissionais_lista.append(profissional)
        except Exception as e:
            flash(f'Erro ao listar profissionais: {e}.', 'danger')
            print(f"Erro list_professionals: {e}")
        return render_template('profissionais.html', profissionais=profissionais_lista, cargos=cargos_lista)

    @app.route('/profissionais/novo', methods=['POST'], endpoint='adicionar_profissional')
    @login_required
    @admin_required
    def adicionar_profissional():
        db_instance = get_db()
        clinica_id = session['clinica_id']
        if request.method == 'POST':
            nome = request.form['nome']
            telefone = request.form.get('telefone')
            email_profissional = request.form.get('email')
            crm_ou_registro = request.form.get('crm')
            cargo_id = request.form.get('cargo_id') # NOVO: Captura o cargo_id
            ativo = True
            try:
                if telefone and not telefone.isdigit():
                    flash('O telefone deve conter apenas números.', 'warning')
                    return redirect(url_for('listar_profissionais'))
                
                db_instance.collection('clinicas').document(clinica_id).collection('profissionais').add({
                    'nome': nome,
                    'telefone': telefone if telefone else None,
                    'email': email_profissional if email_profissional else None,
                    'crm_ou_registro': crm_ou_registro if crm_ou_registro else None,
                    'cargo_id': cargo_id, # NOVO: Salva o cargo_id
                    'ativo': ativo,
                    'criado_em': firestore.SERVER_TIMESTAMP
                })
                flash('Profissional adicionado com sucesso!', 'success')
                return redirect(url_for('listar_profissionais'))
            except Exception as e:
                flash(f'Erro ao adicionar profissional: {e}', 'danger')
                print(f"Erro add_professional: {e}")
        return redirect(url_for('listar_profissionais'))

    @app.route('/profissionais/editar/<string:profissional_doc_id>', methods=['POST'], endpoint='editar_profissional')
    @login_required
    @admin_required
    def editar_profissional(profissional_doc_id):
        db_instance = get_db()
        clinica_id = session['clinica_id']
        profissional_ref = db_instance.collection('clinicas').document(clinica_id).collection('profissionais').document(profissional_doc_id)
        
        if request.method == 'POST':
            nome = request.form['nome']
            telefone = request.form.get('telefone')
            email_profissional = request.form.get('email')
            crm_ou_registro = request.form.get('crm')
            cargo_id = request.form.get('cargo_id') # NOVO: Captura o cargo_id
            
            try:
                if telefone and not telefone.isdigit():
                    flash('O telefone deve conter apenas números.', 'warning')
                    return redirect(url_for('listar_profissionais'))
                else:
                    profissional_ref.update({
                        'nome': nome,
                        'telefone': telefone if telefone else None,
                        'email': email_profissional if email_profissional else None,
                        'crm_ou_registro': crm_ou_registro if crm_ou_registro else None,
                        'cargo_id': cargo_id, # NOVO: Atualiza o cargo_id
                        'atualizado_em': firestore.SERVER_TIMESTAMP
                    })
                    flash('Profissional atualizado com sucesso!', 'success')
                    return redirect(url_for('listar_profissionais'))
            except Exception as e:
                flash(f'Erro ao atualizar profissional: {e}', 'danger')
                print(f"Erro edit_professional (POST): {e}")

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
