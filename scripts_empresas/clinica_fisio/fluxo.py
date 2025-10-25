"""Fluxo simplificado para o protótipo da clínica de fisioterapia.

O objetivo é demonstrar rapidamente como o bot atua nas quatro frentes
principais solicitadas pelo cliente:

1. Atendimento comercial
2. Agendamento / confirmação / remarcação
3. Pagamentos
4. Perguntas frequentes

A implementação mantém um estado mínimo na memória para coletar dados do
interessado durante o agendamento, mas evita integrações externas para
facilitar a apresentação inicial do protótipo.
"""
from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict

from flask import jsonify

CONFIG_EMPRESAS: Dict[str, dict] = {}
try:
    CONFIG_EMPRESAS = json.loads(
        (Path(__file__).resolve().parents[2] / "config" / "empresas_config.json").read_text(encoding="utf-8")
    )
except FileNotFoundError:
    CONFIG_EMPRESAS = {}
except json.JSONDecodeError:
    CONFIG_EMPRESAS = {}

# ---------------------------------------------------------------------------
# Mensagens fixas utilizadas no protótipo
# ---------------------------------------------------------------------------
MENU_PRINCIPAL = (
    "🤖 *Estúdio Movimenta Pilates*\n"
    "Como posso te ajudar hoje?\n\n"
    "1️⃣ Comercial – planos e diferenciais\n"
    "2️⃣ Agendar/Remarcar uma aula\n"
    "3️⃣ Pagamentos – link rápido\n"
    "4️⃣ Dúvidas frequentes\n\n"
    "Você pode digitar *menu* a qualquer momento para voltar para cá."
)

MENSAGEM_COMERCIAL = (
    "✨ *Sobre o nosso estúdio*\n"
    "• Aulas personalizadas com fisioterapeutas especializados em Pilates clínico.\n"
    "• Planos individuais, duplas e trio, com avaliações periódicas incluídas.\n"
    "• Ambiente climatizado, equipamentos modernos e vagas de estacionamento.\n\n"
    "Posso te ajudar com uma proposta ou simulação de plano?"
)

MENSAGEM_PAGAMENTO = (
    "💳 *Pagamento online*\n"
    "Para facilitar, usamos links de pagamento seguros.\n"
    "Envie *quero pagar* com o valor ou pacote desejado e encaminharemos o link em instantes.\n\n"
    "Se preferir, você pode falar direto com a recepção digitando *atendente*."
)

MENSAGEM_FAQ = (
    "❓ *Dúvidas comuns*\n"
    "• Precisamos de roupa confortável e meia antiderrapante.\n"
    "• As aulas duram 55 minutos e podem ser individuais ou em grupos reduzidos.\n"
    "• Trabalhamos com reembolso para vários convênios (via recibo).\n"
    "• Há estacionamento conveniado em frente ao estúdio.\n\n"
    "Ficou com outra dúvida? Digite e respondemos por aqui!"
)

# ---------------------------------------------------------------------------
# Estado do fluxo
# ---------------------------------------------------------------------------


@dataclass
class EstadoConversa:
    etapa: str = "menu"
    contexto: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_estado(fluxo_usuario: Dict[str, dict], chat_id: str) -> EstadoConversa:
    fluxo_usuario[chat_id] = {"etapa": "menu", "contexto": {}}
    return EstadoConversa()


def _obter_estado(fluxo_usuario: Dict[str, dict], chat_id: str) -> EstadoConversa:
    estado_bruto = fluxo_usuario.setdefault(chat_id, {"etapa": "menu", "contexto": {}})
    return EstadoConversa(
        etapa=estado_bruto.get("etapa", "menu"),
        contexto=dict(estado_bruto.get("contexto") or {}),
    )


def _salvar_estado(fluxo_usuario: Dict[str, dict], chat_id: str, estado: EstadoConversa) -> None:
    fluxo_usuario[chat_id] = {"etapa": estado.etapa, "contexto": estado.contexto}


def _normalizar(msg: str) -> str:
    return (msg or "").strip().lower()


# ---------------------------------------------------------------------------
# Processadores de cada etapa
# ---------------------------------------------------------------------------


def _handle_menu(waha, chat_id: str) -> None:
    waha.send_message(chat_id, MENU_PRINCIPAL)


def _entrar_agendamento(waha, chat_id: str, estado: EstadoConversa) -> None:
    estado.etapa = "agendamento_nome"
    estado.contexto = {}
    waha.send_message(
        chat_id,
        "Ótimo! Para reservar um horário, me conta primeiro o seu *nome completo*."
    )


def _continuar_agendamento(
    waha,
    chat_id: str,
    estado: EstadoConversa,
    mensagem: str,
) -> None:
    if estado.etapa == "agendamento_nome":
        estado.contexto["nome"] = mensagem.strip()
        estado.etapa = "agendamento_preferencia"
        waha.send_message(
            chat_id,
            (
                "Perfeito, {nome}! Qual dia/horário você prefere?\n"
                "Ex.: terça-feira à tarde ou 12/09 às 8h."
            ).format(nome=estado.contexto["nome"])
        )
        return

    if estado.etapa == "agendamento_preferencia":
        estado.contexto["preferencia"] = mensagem.strip()
        estado.etapa = "agendamento_observacoes"
        waha.send_message(
            chat_id,
            "Quer deixar alguma observação (lesão, objetivo, convênio)? Se não precisar, digite *não*."
        )
        return

    if estado.etapa == "agendamento_observacoes":
        observacao = mensagem.strip()
        if observacao.lower() in {"nao", "não", "nenhuma", "nada"}:
            observacao = "Sem observações adicionais."
        estado.contexto["observacoes"] = observacao

        waha.send_message(
            chat_id,
            (
                "Obrigada! Recebemos o pedido com as informações:\n"
                "• Nome: {nome}\n"
                "• Preferência: {pref}\n"
                "• Observações: {obs}\n\n"
                "Nossa equipe confirma a disponibilidade e retorna por aqui em instantes."
            ).format(
                nome=estado.contexto.get("nome", "-"),
                pref=estado.contexto.get("preferencia", "-"),
                obs=estado.contexto.get("observacoes", "Sem observações."),
            )
        )

        estado.etapa = "menu"
        estado.contexto = {}
        waha.send_message(chat_id, "Se precisar de mais algo, é só digitar *menu* para recomeçar. 😊")
        return


def _responder_pagamentos(waha, chat_id: str, empresa: str) -> None:
    mensagem = MENSAGEM_PAGAMENTO
    cfg = CONFIG_EMPRESAS.get(empresa, {})
    instrucoes = cfg.get("pagamento_instrucoes")
    if instrucoes:
        mensagem = f"{mensagem}\n\n📌 {instrucoes}"
    waha.send_message(chat_id, mensagem)


def _responder_faq(waha, chat_id: str) -> None:
    waha.send_message(chat_id, MENSAGEM_FAQ)


# ---------------------------------------------------------------------------
# Função principal chamada pelo app
# ---------------------------------------------------------------------------


def processar(chat_id: str, msg: str, empresa: str, waha, fluxo_usuario: Dict[str, dict]):
    """Roteia a mensagem para o fluxo do protótipo."""
    if not msg:
        return jsonify({"status": "ignored"}), 200

    texto = _normalizar(msg)
    estado = _obter_estado(fluxo_usuario, chat_id)

    # Comandos universais
    if texto in {"menu", "inicio", "início", "oi", "olá", "ola", "bom dia", "boa tarde", "boa noite"}:
        estado.etapa = "menu"
        estado.contexto = {}
        _salvar_estado(fluxo_usuario, chat_id, estado)
        _handle_menu(waha, chat_id)
        return jsonify({"status": "success"}), 200

    if texto in {"cancelar", "sair"}:
        _reset_estado(fluxo_usuario, chat_id)
        waha.send_message(chat_id, "Tudo bem! Quando quiser retomar, é só digitar *menu*.")
        return jsonify({"status": "success"}), 200

    if texto in {"atendente", "falar com atendente", "humano"}:
        waha.send_message(
            chat_id,
            "📞 Já vou acionar a recepção para continuar o atendimento com você."
        )
        return jsonify({"status": "success"}), 200

    # Fluxo principal
    if estado.etapa.startswith("agendamento"):
        _continuar_agendamento(waha, chat_id, estado, msg)
        _salvar_estado(fluxo_usuario, chat_id, estado)
        return jsonify({"status": "success"}), 200

    if texto in {"1", "01", "comercial"}:
        _salvar_estado(fluxo_usuario, chat_id, estado)
        waha.send_message(chat_id, MENSAGEM_COMERCIAL)
        return jsonify({"status": "success"}), 200

    if texto in {"2", "02", "agendamento", "agendar", "remarcar", "remarcação", "confirmar"}:
        _entrar_agendamento(waha, chat_id, estado)
        _salvar_estado(fluxo_usuario, chat_id, estado)
        return jsonify({"status": "success"}), 200

    if texto in {"3", "03", "pagamento", "pagamentos", "link"}:
        _responder_pagamentos(waha, chat_id, empresa)
        _salvar_estado(fluxo_usuario, chat_id, estado)
        return jsonify({"status": "success"}), 200

    if texto in {"4", "04", "duvida", "dúvida", "duvidas", "dúvidas", "faq"}:
        _responder_faq(waha, chat_id)
        _salvar_estado(fluxo_usuario, chat_id, estado)
        return jsonify({"status": "success"}), 200

    # Se nada se encaixar e estivermos no menu, apresenta novamente
    if estado.etapa == "menu":
        _salvar_estado(fluxo_usuario, chat_id, estado)
        waha.send_message(
            chat_id,
            "Não entendi muito bem. Use um dos números do menu ou digite *menu* para recomeçar."
        )
        _handle_menu(waha, chat_id)
        return jsonify({"status": "success"}), 200

    # Fallback genérico
    waha.send_message(
        chat_id,
        "Certo! Estou encaminhando para a nossa equipe finalizar esse atendimento."
    )
    return jsonify({"status": "success"}), 200
