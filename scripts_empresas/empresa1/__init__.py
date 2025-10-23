# scripts_empresas/empresa1/__init__.py
from .fluxo import processar  # exporta o fluxo do cliente

# opcional: admin (se você tiver)
def processar_admin(chat_id, msg, nome_empresa, waha):
    waha.send_message(chat_id, "🔧 Painel do admin em construção.")
    return {"status": "success"}, 200
