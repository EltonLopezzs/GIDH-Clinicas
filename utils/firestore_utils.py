import datetime
import pytz
import re
from google.cloud.firestore_v1 import DocumentSnapshot

SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')

def convert_doc_to_dict(doc_snapshot: DocumentSnapshot):
    """
    Converte um DocumentSnapshot do Firestore em um dicionário Python,
    formatando datas e adicionando o ID do documento.
    """
    if not doc_snapshot or not doc_snapshot.exists:
        return None

    data = doc_snapshot.to_dict()
    data['id'] = doc_snapshot.id

    def _convert_value(value):
        if isinstance(value, datetime.datetime):
            local_time = value.astimezone(SAO_PAULO_TZ)
            # Check if time is 00:00:00 for date-only formatting
            if local_time.hour == 0 and local_time.minute == 0 and local_time.second == 0:
                return local_time.strftime('%d/%m/%Y')
            return local_time.strftime('%d/%m/%Y %H:%M')
        elif isinstance(value, dict):
            return {k: _convert_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_convert_value(item) for item in value]
        return value

    return {key: _convert_value(val) for key, val in data.items()}


def parse_date_input(date_string: str) -> datetime.datetime | None:
    """
    Parse a date string from either YYYY-MM-DD or DD/MM/YYYY format
    into a timezone-aware datetime object (São Paulo timezone),
    setting time to 00:00:00.
    """
    if not date_string:
        return None
    
    parsed_date = None
    try:
        # Try YYYY-MM-DD first (common for HTML date inputs)
        parsed_date = datetime.datetime.strptime(date_string, '%Y-%m-%d').date()
    except ValueError:
        pass

    if parsed_date is None:
        try:
            # Then try DD/MM/YYYY (common for manual input)
            parsed_date = datetime.datetime.strptime(date_string, '%d/%m/%Y').date()
        except ValueError:
            pass
    
    if parsed_date:
        # Localize to São Paulo timezone at the beginning of the day
        return SAO_PAULO_TZ.localize(datetime.datetime(parsed_date.year, parsed_date.month, parsed_date.day, 0, 0, 0))
    
    return None

def slugify_filter(s: str) -> str:
    """
    Converte uma string em um slug URL-friendly.
    Remove caracteres não alfanuméricos, converte para minúsculas e substitui espaços por hífens.
    """
    s = s.lower().strip()
    s = re.sub(r'[^\w\s-]', '', s) # Remove caracteres não alfanuméricos, exceto espaços e hífens
    s = re.sub(r'[\s_-]+', '-', s)  # Substitui espaços e múltiplos hífens/underscores por um único hífen
    s = re.sub(r'^-+|-+$', '', s)  # Remove hífens do início e do fim
    return s