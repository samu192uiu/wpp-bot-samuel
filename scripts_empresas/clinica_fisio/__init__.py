"""Fluxo principal do protótipo para a clínica de fisioterapia."""
from .fluxo import processar


def processar_admin(chat_id, msg, empresa, waha):
    """Resposta padrão para administradores no protótipo."""
    waha.send_message(chat_id, "🔧 Painel administrativo em desenvolvimento.")
    return {"status": "success"}, 200