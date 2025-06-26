from flask import Blueprint, render_template, session, flash, redirect, url_for, request, current_app
from google.cloud.firestore_v1.base_query import FieldFilter
from decorators.auth_decorators import login_required, admin_required # Import decorators

services_procedures_bp = Blueprint('services_procedures_bp', __name__)

@services_procedures_bp.route('/servicos_procedimentos')
@login_required
def listar_servicos_procedimentos():
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    servicos_procedimentos_ref = db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos')
    servicos_procedimentos_lista = []
    try:
        docs = servicos_procedimentos_ref.order_by('nome').stream()
        for doc in docs:
            servico = doc.to_dict()
            if servico:
                servico['id'] = doc.id
                servico['preco_fmt'] = "R$ {:.2f}".format(float(servico.get('preco_sugerido', 0))).replace('.', ',')
                servicos_procedimentos_lista.append(servico)
    except Exception as e:
        flash(f'Erro ao listar serviços/procedimentos: {e}.', 'danger')
        print(f"Erro list_services_procedures: {e}")
    return render_template('servicos_procedimentos.html', servicos=servicos_procedimentos_lista)

@services_procedures_bp.route('/servicos_procedimentos/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_servico_procedimento():
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    if request.method == 'POST':
        nome = request.form['nome']
        tipo = request.form['tipo']
        try:
            duracao_minutos = int(request.form['duracao_minutos'])
            preco_sugerido = float(request.form['preco'].replace(',', '.'))
            db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').add({
                'nome': nome,
                'tipo': tipo,
                'duracao_minutos': duracao_minutos,
                'preco_sugerido': preco_sugerido,
                'criado_em': db.SERVER_TIMESTAMP
            })
            flash('Serviço/Procedimento adicionado com sucesso!', 'success')
            return redirect(url_for('services_procedures_bp.listar_servicos_procedimentos'))
        except ValueError:
            flash('A duração e o preço devem ser números válidos.', 'danger')
        except Exception as e:
            flash(f'Erro ao adicionar serviço/procedimento: {e}', 'danger')
            print(f"Erro add_service_procedure: {e}")
    return render_template('servico_procedimento_form.html', servico=None, action_url=url_for('services_procedures_bp.adicionar_servico_procedimento'))

@services_procedures_bp.route('/servicos_procedimentos/editar/<string:servico_doc_id>', methods=['GET', 'POST'])
@login_required
def editar_servico_procedimento(servico_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    servico_ref = db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_doc_id)
    if request.method == 'POST':
        nome = request.form['nome']
        tipo = request.form['tipo']
        try:
            duracao_minutos = int(request.form['duracao_minutos'])
            preco_sugerido = float(request.form['preco'].replace(',', '.'))
            servico_ref.update({
                'nome': nome,
                'tipo': tipo,
                'duracao_minutos': duracao_minutos,
                'preco_sugerido': preco_sugerido,
                'atualizado_em': db.SERVER_TIMESTAMP
            })
            flash('Serviço/Procedimento atualizado com sucesso!', 'success')
            return redirect(url_for('services_procedures_bp.listar_servicos_procedimentos'))
        except ValueError:
            flash('A duração e o preço devem ser números válidos.', 'danger')
        except Exception as e:
            flash(f'Erro ao atualizar serviço/procedimento: {e}', 'danger')
            print(f"Erro edit_service_procedure (POST): {e}")
    try:
        servico_doc = servico_ref.get()
        if servico_doc.exists:
            servico = servico_doc.to_dict()
            if servico:
                servico['id'] = servico_doc.id
                servico['preco_form'] = str(servico.get('preco_sugerido', '0.00')).replace('.', ',')
                return render_template('servico_procedimento_form.html', servico=servico, action_url=url_for('services_procedures_bp.editar_servico_procedimento', servico_doc_id=servico_doc_id))
        flash('Serviço/Procedimento não encontrado.', 'danger')
        return redirect(url_for('services_procedures_bp.listar_servicos_procedimentos'))
    except Exception as e:
        flash(f'Erro ao carregar serviço/procedimento para edição: {e}', 'danger')
        print(f"Erro edit_service_procedure (GET): {e}")
        return redirect(url_for('services_procedures_bp.listar_servicos_procedimentos'))

@services_procedures_bp.route('/servicos_procedimentos/excluir/<string:servico_doc_id>', methods=['POST'])
@login_required
@admin_required
def excluir_servico_procedimento(servico_doc_id):
    db = current_app.config['DB']

    clinica_id = session['clinica_id']
    try:
        agendamentos_ref = db.collection('clinicas').document(clinica_id).collection('agendamentos')
        agendamentos_com_servico = agendamentos_ref.where(filter=FieldFilter('servico_procedimento_id', '==', servico_doc_id)).limit(1).get()
        if len(agendamentos_com_servico) > 0:
            flash('Este serviço/procedimento não pode ser excluído, pois está associado a um ou mais agendamentos.', 'danger')
            return redirect(url_for('services_procedures_bp.listar_servicos_procedimentos'))

        db.collection('clinicas').document(clinica_id).collection('servicos_procedimentos').document(servico_doc_id).delete()
        flash('Serviço/Procedimento excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir serviço/procedimento: {e}.', 'danger')
        print(f"Erro delete_service_procedure: {e}")
    return redirect(url_for('services_procedures_bp.listar_servicos_procedimentos'))