import os
import json
import unicodedata
from datetime import datetime
from flask import jsonify
from .agenda import listar_agendamentos_do_dia, proximo_cliente, finalizar_agendamento

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "admins_config.json")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    admins_por_empresa = json.load(f)

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return s

def processar_admin(chat_id, msg, nome_empresa, waha):
    raw = msg or ""
    norm = _norm(raw)

    if chat_id not in admins_por_empresa.get(nome_empresa, []):
        waha.send_message(chat_id, "🚫 Você não tem permissão para acessar o painel de administração.")
        return jsonify({'status': 'unauthorized'}), 403

    if norm in ("menu", "painel", "painel barbeiro"):
        return exibir_menu(chat_id, waha)
    elif norm.startswith("agendamentos"):
        return mostrar_agendamentos_do_dia(chat_id, waha)
    elif norm.startswith("proximo"):
        return mostrar_proximo_cliente(chat_id, waha)
    elif norm.startswith("finalizei"):
        return finalizar_atendimento(chat_id, waha)

    waha.send_message(chat_id, "❓ *Comando não reconhecido.*\n\n📋 Digite *menu* para ver as opções disponíveis.")
    return jsonify({'status': 'admin-comando-desconhecido'}), 200

def exibir_menu(chat_id, waha):
    waha.send_message(
        chat_id,
        "💈 *Painel do Barbeiro* 💈\n"
        "📅 agendamentos — Ver horários do dia\n"
        "➡ proximo — Ver próximo cliente\n"
        "✅ finalizei — Marcar atendimento concluído\n"
        "📖 menu — Ver este menu novamente"
    )
    return jsonify({'status': 'admin-menu'}), 200
