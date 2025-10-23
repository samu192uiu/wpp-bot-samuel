from flask import jsonify
from .ai_bot import AIBot as _AIBot  # marcado como _ para evitar aviso de 'unused import'
from .agenda import (
    listar_blocos_disponiveis,
    horario_disponivel,
    BLOCOS_HORARIOS
)
from datetime import datetime, date
import re
from collections import OrderedDict

import os, json  # ADICIONE
from decimal import Decimal, ROUND_HALF_UP  # ADICIONE

CAT_PATH = os.path.join(os.path.dirname(__file__), "catalogo.json")

def _load_catalogo():
    try:
        with open(CAT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}

    by_code = {
        str(s.get("code")).strip(): s
        for s in data.get("services", [])
        if s.get("active", True) and s.get("code") is not None
    }
    return by_code

CATALOGO = _load_catalogo()

# Base interna da API (para chamadas dentro do container)
API_INTERNAL_BASE = os.getenv("API_INTERNAL_BASE", "http://api:8000").rstrip("/")

def _fmt_brl(v: float | int) -> str:
    d = Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # formata como R$ 1.234,56
    s = f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def _mk_item_from_code(code: str):
    s = CATALOGO.get(str(code))
    if not s:
        return None
    try:
        price = float(s.get("price"))
    except (TypeError, ValueError):
        return None
    return {"title": s["label"], "quantity": 1, "unit_price": price}

    
# ==========================
# Helpers de UI / Texto
# ==========================
EMOJI_TO_NUM = {
    "1️⃣": "1", "2️⃣": "2", "3️⃣": "3", "4️⃣": "4", "5️⃣": "5",
    "6️⃣": "6", "7️⃣": "7", "8️⃣": "8", "9️⃣": "9", "0️⃣": "0"
}

def _chip(n, label):
    nums = {"1":"1️⃣","2":"2️⃣","3":"3️⃣","4":"4️⃣","5":"5️⃣","6":"6️⃣","7":"7️⃣","8":"8️⃣","9":"9️⃣","0":"0️⃣"}
    return f"{nums.get(str(n), str(n))} {label}"

def _norm(txt: str) -> str:
    t = (txt or "").strip()
    for e, n in EMOJI_TO_NUM.items():
        t = t.replace(e, n)
    t = t.replace("\u200b", "").replace("\u200c", "")
    return re.sub(r"\s+", " ", t).strip()

def _caixa(titulo: str, conteudo: str) -> str:
    header = "╔════════════════════════╗"
    mid    = "╠════════════════════════╣"
    bot    = "╚════════════════════════╝"
    return f"{header}\n    {titulo}\n{mid}\n{conteudo}\n{bot}"

def _footer_comandos_inline() -> str:
    return "ℹ️ Comandos rápidos:\n   • Menu   • Voltar   • Cancelar   • Ajuda   • Atendente"

def _footer_tips_sel() -> str:
    return (
        "✨ Adicione mais serviços\n"
        "📝 Digite *pronto* para finalizar\n"
        "❌ Digite *remover* para tirar um item\n"
        "🧹 Digite *limpar* para esvaziar tudo"
    )

# ==========================
# Estado / Navegação
# ==========================
def _get(fluxo_usuario, chat_id):
    if chat_id not in fluxo_usuario:
        fluxo_usuario[chat_id] = {"etapa": "menu", "ctx": {}, "pilha": []}
    fluxo_usuario[chat_id].setdefault("ctx", {})
    fluxo_usuario[chat_id].setdefault("pilha", [])
    return fluxo_usuario[chat_id]

def _push(state, new_state):
    state["pilha"].append(state["etapa"])
    state["etapa"] = new_state

def _goto(state, new_state):
    state["etapa"] = new_state

def _back(state):
    if state["pilha"]:
        state["etapa"] = state["pilha"].pop()
        return True
    return False

def _reset(state):
    state["etapa"] = "menu"
    state["ctx"] = {}
    state["pilha"] = []
    
# ==========================
# Datas
# ==========================
def _parse_data(msg: str) -> date | None:
    m = re.match(r"^\s*(\d{1,2})[/-](\d{1,2})\s*$", msg)
    if not m:
        return None
    d, mth = int(m.group(1)), int(m.group(2))
    now = datetime.now()
    y = now.year
    try:
        dt = date(y, mth, d)
    except ValueError:
        return None
    if dt < now.date():
        try:
            dt = date(y + 1, mth, d)
        except ValueError:
            return None
    return dt

def _is_universal(cmd: str) -> str | None:
    key = _norm(cmd).lower()
    if key in {"menu", "início", "inicio"}:
        return "menu"
    if key == "voltar":
        return "voltar"
    if key == "cancelar":
        return "cancelar"
    if key == "ajuda":
        return "ajuda"
    if key in {"atendente", "falar com atendente", "humano"}:
        return "atendente"
    return None

# ==========================
# Catálogo de serviços
# ==========================
SERVICOS = OrderedDict([
    ("1", {"slug": "corte social", "label": "Corte social", "emoji": "💇"}),
    ("2", {"slug": "degradê", "label": "Degradê", "emoji": "🌀"}),
    ("3", {"slug": "sobrancelha", "label": "Sobrancelha", "emoji": "✨"}),
    ("4", {"slug": "barba", "label": "Barba", "emoji": "🧔"}),
])
SERVICOS_BY_NAME = {v["slug"]: k for k, v in SERVICOS.items()}
SERVICOS_BY_NAME.update({v["label"].lower(): k for k, v in SERVICOS.items()})

def _catalogo_texto():
    linhas = []
    for k, v in SERVICOS.items():
        linhas.append(f"  {_chip(k, v['label'])}  {v['emoji']}")
    return "\n".join(linhas)

def _render_carrinho(ids, indent="     "):
    if not ids:
        return f"{indent}— (vazio)"
    linhas = []
    for sid in ids:
        if sid in SERVICOS:
            linhas.append(f"{indent}{_chip(sid, SERVICOS[sid]['label'])}")
    return "\n".join(linhas)

def _parse_servicos_input(texto: str):
    raw = re.split(r"[,\s]+", _norm(texto).lower())
    found = []
    for token in raw:
        if not token:
            continue
        if token in SERVICOS:
            found.append(token)
            continue
        k = SERVICOS_BY_NAME.get(token)
        if k:
            found.append(k)
    seen, ordered = set(), []
    for x in found:
        if x not in seen:
            seen.add(x)
            ordered.append(x)
    return ordered

# ==========================
# Menu principal
# ==========================
def _send_menu(waha, chat_id):
    titulo = "💈 Barbearia do ERIK"
    conteudo = (
        f"{_chip(1, 'Agendar horário')}\n"
        f"{_chip(2, 'Ver serviços')}\n"
        f"{_chip(3, 'Ver horários disponíveis')}\n"
        f"{_chip(4, 'Falar com atendente')}"
    )
    rodape = _footer_comandos_inline()
    msg = _caixa(titulo, conteudo) + "\n\n" + rodape
    waha.send_message(chat_id, msg)

# ==========================
# Router do menu
# ==========================
MENU_ROUTER = {
    "1": "agendar", "agendar": "agendar", "agendamento": "agendar",
    "agendar horario": "agendar", "agendar horário": "agendar",
    "2": "servicos", "servicos": "servicos", "serviços": "servicos",
    "ver servicos": "servicos", "ver serviços": "servicos",
    "3": "ver_horarios", "ver horarios": "ver_horarios", "ver horários": "ver_horarios",
    "horarios": "ver_horarios", "horários": "ver_horarios",
    "4": "atendente", "atendente": "atendente", "falar com atendente": "atendente", "humano": "atendente",
}

TEXTUAL_TRIGGERS = {
    "agendar", "agendamento", "agendar horario", "agendar horário",
    "servicos", "serviços", "ver servicos", "ver serviços",
    "ver horarios", "ver horários", "horarios", "horários",
    "atendente", "falar com atendente", "humano",
}

def _handle_menu_action(escolha: str, estado, ctx, chat_id, waha):
    if escolha == "agendar":
        _push(estado, "selecionar_servicos")
        ctx["servicos"] = []
        titulo = "✍ Selecione os serviços"
        conteudo = _catalogo_texto()
        msg = _caixa(titulo, conteudo) + "\n\n" + \
            "ℹ️ Dica:\n   envie números (ex.: 1,3) ou nomes (ex.: corte social, barba)."
        waha.send_message(chat_id, msg)
        return

    if escolha == "servicos":
        titulo = "📋 Serviços disponíveis"
        conteudo = _catalogo_texto()
        msg = _caixa(titulo, conteudo) + "\n\n" + "Para agendar, escolha 1 no menu ou digite Agendar."
        waha.send_message(chat_id, msg)
        _goto(estado, "menu")
        return

    if escolha == "ver_horarios":
        hoje = date.today()
        ctx["consulta_data"] = hoje
        _goto(estado, "ver_horarios_listar")

        horarios = listar_blocos_disponiveis(hoje, exibir_nomes=True)
        if not horarios or horarios.strip() == "":
            waha.send_message(
                chat_id,
                _caixa(
                    "📅 Consulta — Hoje",
                    f"Não encontrei horários para {hoje.strftime('%d/%m/%Y')}."
                )
            )
            waha.send_message(
                chat_id,
                _caixa(
                    "ℹ️ Como proceder",
                    "• Para *agendar*, digite: agendar\n"
                    "• Para ver outra *data*, envie: DD/MM (ex.: 15/08)\n"
                    "• Ou digite: menu"
                )
            )
            return

        titulo = f"📅 Consulta — {hoje.strftime('%d/%m/%Y')}"
        conteudo = (
            f"⏰ Disponíveis:\n{horarios}\n\n"
            "Para *agendar*, digite: agendar\n"
            "Para consultar outra *data*, envie: DD/MM (ex.: 15/08)"
        )
        waha.send_message(chat_id, _caixa(titulo, conteudo))
        return

    if escolha == "atendente":
        titulo = "👨‍💼 Atendente"
        conteudo = "Certo! Um atendente vai te chamar em instantes."
        waha.send_message(chat_id, _caixa(titulo, conteudo))
        _goto(estado, "menu")
        _send_menu(waha, chat_id)
        return

# ==========================
# Fluxo principal
# ==========================
def processar(chat_id, msg, nome_empresa, waha, fluxo_usuario):
    estado = _get(fluxo_usuario, chat_id)
    ctx = estado["ctx"]
    msg_original = msg or ""
    msg_norm = _norm(msg_original)
    msg_lower = msg_norm.lower()

    # 1) Comandos UNIVERSAIS
    uni = _is_universal(msg_original)
    if uni == "menu":
        _reset(estado)
        _send_menu(waha, chat_id)
        return jsonify({"status": "success"}), 200
    if uni == "voltar":
        if _back(estado):
            waha.send_message(chat_id, _caixa("↩️ Voltar", "Voltei para a etapa anterior. Vamos continuar?"))
        else:
            waha.send_message(chat_id, _caixa("↩️ Início", "Você já está no início. Digite menu para recomeçar."))
        return jsonify({"status": "success"}), 200
    if uni == "cancelar":
        _reset(estado)
        waha.send_message(chat_id, _caixa("✅ Fluxo cancelado", "Voltei ao menu principal."))
        _send_menu(waha, chat_id)
        return jsonify({"status": "success"}), 200
    if uni == "ajuda":
        conteudo = (
            "• Use menu para voltar ao início\n"
            "• voltar para etapa anterior\n"
            "• cancelar para encerrar\n"
            "• atendente para falar com humano\n\n"
            "Ex.: “agendar amanhã às 14h”"
        )
        waha.send_message(chat_id, _caixa("🆘 Ajuda rápida", conteudo))
        return jsonify({"status": "success"}), 200
    if uni == "atendente":
        _reset(estado)
        waha.send_message(chat_id, _caixa("👩‍💼 Atendente", "Perfeito! Vou te direcionar para um atendente agora."))
        _send_menu(waha, chat_id)
        return jsonify({"status": "success"}), 200

    # 1.1) Comandos rápidos de pagamento  (<<< fora do bloco do 'atendente')
    if msg_lower in {"status", "status do pagamento", "pagamento"}:
        from . import agenda
        ag_id = ctx.get("ultimo_agendamento_id")
        if not ag_id:
            waha.send_message(chat_id, _caixa("ℹ️ Status", "Não encontrei um pagamento pendente recente. Digite *agendar* para começar."))
            return jsonify({"status": "success"}), 200

        # tenta função dedicada; se não houver, consulta direto a planilha
        status_txt = None
        if hasattr(agenda, "consultar_status"):
            try:
                status_txt = agenda.consultar_status(ag_id)
            except Exception:
                status_txt = None
        if not status_txt:
            try:
                df = agenda.carregar_agendamentos()
                row = df.loc[df["AgendamentoID"] == ag_id]
                if not row.empty:
                    status_txt = str(row.iloc[0]["Status"])
            except Exception:
                status_txt = None

        if not status_txt:
            waha.send_message(chat_id, _caixa("ℹ️ Status", f"Agendamento {ag_id}\nStatus: _indisponível agora_."))
        else:
            waha.send_message(chat_id, _caixa("ℹ️ Status do pagamento", f"Agendamento {ag_id}\nStatus: *{status_txt}*"))
        return jsonify({"status": "success"}), 200

    if msg_lower in {"reenviar pix", "reenvia pix", "pix de novo", "pagar agora"}:
        import requests
        from . import agenda
        ag_id = ctx.get("ultimo_agendamento_id")
        payload = ctx.get("ultimo_pix_payload") or {}

        if not ag_id or not payload:
            waha.send_message(chat_id, _caixa("ℹ️ PIX", "Não encontrei um pagamento pendente recente. Digite *agendar* para começar."))
            return jsonify({"status": "success"}), 200

        # checa se a reserva ainda está válida
        try:
            dt_ref = datetime.fromisoformat(payload["data"]).date()
            if not agenda.horario_disponivel(payload["horario"], dt_ref):
                # pode ser a própria reserva pendente; tenta bater pelo id
                bate = False
                if hasattr(agenda, "reserva_bate"):
                    try:
                        bate = agenda.reserva_bate(
                            agendamento_id=ag_id, data_ref=dt_ref, horario_ref=payload["horario"], chat_id=chat_id
                        )
                    except Exception:
                        bate = False
                if not bate:
                    waha.send_message(chat_id, _caixa("⏰ Reserva expirada", "Esse horário não está mais disponível. Digite *agendar* para refazer."))
                    return jsonify({"status": "success"}), 200
        except Exception:
            pass

        # chama novamente o endpoint PIX
        try:
            resp = requests.post(
                f"{API_INTERNAL_BASE}/mp/{nome_empresa}/pix",
                json={
                    "agendamento_id": ag_id,
                    "chat_id": chat_id,
                    "nome": payload.get("nome") or "Cliente",
                    "insta": payload.get("insta") or "",
                    "data": payload.get("data"),
                    "horario": payload.get("horario"),
                },
                timeout=15
            )
            if resp.status_code == 200:
                data_pix = resp.json()
                qr_code    = data_pix.get("qr_code")
                ticket_url = data_pix.get("ticket_url")

                waha.send_message(chat_id, _caixa("💳 Novo PIX", f"Enviei um novo PIX (validade ~20 min).\n\n🌐 QR em página web:\n{ticket_url or '— indisponível —'}"))
                if qr_code:
                    waha.send_message(chat_id, "🔹 *PIX Copia e Cola* (copie a mensagem abaixo):")
                    waha.send_message(chat_id, qr_code)
            else:
                waha.send_message(chat_id, _caixa("⚠️ PIX", "Não consegui gerar agora. Talvez a reserva tenha expirado. Digite *agendar* para refazer."))
        except Exception as e:
            waha.send_message(chat_id, _caixa("⚠️ PIX", f"Erro ao gerar: {e}"))

        return jsonify({"status": "success"}), 200

    # 2) HOTKEYS DO MENU
    escolha = None
    if estado["etapa"] == "menu":
        escolha = MENU_ROUTER.get(msg_lower)
    else:
        if msg_lower in TEXTUAL_TRIGGERS:
            escolha = MENU_ROUTER.get(msg_lower)
    if escolha:
        _handle_menu_action(escolha, estado, ctx, chat_id, waha)
        return jsonify({"status": "success"}), 200

    # 3) Estados
    if estado["etapa"] == "menu":
        _send_menu(waha, chat_id)
        _goto(estado, "menu")
        return jsonify({"status": "success"}), 200

    # ===== Seleção de serviços =====
    if estado["etapa"] == "selecionar_servicos":
        carrinho = ctx.get("servicos", [])

        if msg_lower in {"pronto", "finalizar", "ok"}:
            if not carrinho:
                waha.send_message(chat_id, _caixa("⚠️ Atenção", "Você ainda não selecionou nenhum serviço. Escolha ao menos 1."))
                return jsonify({"status": "success"}), 200
            _goto(estado, "solicitar_nome")
            lista = _render_carrinho(carrinho)
            titulo = "🗂 Serviços selecionados"
            conteudo = f"{lista}"
            msg = _caixa(titulo, conteudo) + "\n\n" + "🧑 Por favor, digite seu nome completo.\n(ou digite: pular)"
            waha.send_message(chat_id, msg)
            return jsonify({"status": "success"}), 200

        if msg_lower == "limpar":
            ctx["servicos"] = []
            titulo = "🧹 Seleção limpa!"
            conteudo = _catalogo_texto()
            msg = _caixa(titulo, conteudo) + "\n\n" + "Adicione serviços (ex.: 1,3) e digite pronto quando terminar."
            waha.send_message(chat_id, msg)
            return jsonify({"status": "success"}), 200

        mrem = re.match(r"^\s*remover\s+(.+)\s*$", msg_lower)
        if mrem:
            alvo = mrem.group(1).strip()
            ids = _parse_servicos_input(alvo)
            if not ids and alvo:
                for sid, v in SERVICOS.items():
                    if alvo in v["label"].lower() or alvo in v["slug"]:
                        ids = [sid]
                        break
            if not ids:
                waha.send_message(chat_id, _caixa("⚠️ Não encontrado", "Não encontrei esse serviço para remover. Tente remover 2 ou remover barba."))
                return jsonify({"status": "success"}), 200
            for sid in ids:
                if sid in carrinho:
                    carrinho.remove(sid)
            ctx["servicos"] = carrinho
            titulo = "🗑 Removido"
            conteudo = f"🗂 Agora:\n{_render_carrinho(carrinho)}"
            msg = _caixa(titulo, conteudo) + "\n\n" + _footer_tips_sel()
            waha.send_message(chat_id, msg)
            return jsonify({"status": "success"}), 200

        ids = _parse_servicos_input(msg_lower)
        if not ids:
            waha.send_message(
                chat_id,
                _caixa(
                    "⚠️ Não entendi",
                    "Envie números (ex.: 1,3) ou nomes (ex.: corte social, barba).\n"
                    "Dica: pronto para finalizar."
                )
            )
            return jsonify({"status": "success"}), 200

        for sid in ids:
            if sid not in carrinho and sid in SERVICOS:
                carrinho.append(sid)
        ctx["servicos"] = carrinho

        titulo = "✅ Adicionado!"
        conteudo = f"🗂 Seleção:\n{_render_carrinho(carrinho)}"
        msg = _caixa(titulo, conteudo) + "\n\n" + _footer_tips_sel()
        waha.send_message(chat_id, msg)
        return jsonify({"status": "success"}), 200

    # ===== Nome =====
    if estado["etapa"] == "solicitar_nome":
        nome_raw = msg_norm
        if nome_raw.lower() in {"pular", "skip"}:
            ctx["nome_cliente"] = "Cliente"
        else:
            if not re.match(r"^[A-Za-zÀ-ÿ'´`^~\- ]{2,}$", nome_raw):
                waha.send_message(chat_id, _caixa("❌ Nome inválido", "Envie seu nome completo (somente letras). Ex.: João da Silva\n(ou digite: pular)"))
                return jsonify({"status": "success"}), 200
            parts = re.sub(r"\s+", " ", nome_raw.strip()).split(" ")
            lowers = {"de","da","do","dos","das","e","di","du"}
            formatted = []
            for i, p in enumerate(parts):
                p = p.lower()
                formatted.append(p if (i != 0 and p in lowers) else p[:1].upper() + p[1:])
            ctx["nome_cliente"] = " ".join(formatted)

        _goto(estado, "solicitar_insta")
        titulo = "📷 Quer aparecer com @ na vitrine?"
        conteudo = "Envie seu @ do Instagram (ex.: @seuuser)\nOu digite: pular"
        waha.send_message(chat_id, _caixa(titulo, conteudo))
        return jsonify({"status": "success"}), 200

    # ===== Instagram (opcional) =====
    if estado["etapa"] == "solicitar_insta":
        handle = msg_norm.strip()
        insta = ""
        if handle.lower() not in {"pular", "skip", ""}:
            if not re.match(r"^@?[A-Za-z0-9._]{1,30}$", handle):
                waha.send_message(chat_id, _caixa("❌ @ inválido", "Envie no formato @usuario (letras, números, ponto e sublinhado).\nOu digite: pular"))
                return jsonify({"status": "success"}), 200
            handle = handle.lower()
            insta = handle if handle.startswith("@") else f"@{handle}"
        ctx["insta"] = insta

        _goto(estado, "solicitar_data")
        titulo = "📅 Informe a data"
        conteudo = "Digite no formato DD/MM (ex.: 12/06)."
        waha.send_message(chat_id, _caixa(titulo, conteudo))
        return jsonify({"status": "success"}), 200

    # ===== Data e horários =====
    if estado["etapa"] == "solicitar_data":
        dt = _parse_data(msg_norm.replace(" ", ""))
        if not dt:
            waha.send_message(chat_id, _caixa("❌ Data inválida", "Use DD/MM (ex.: 12/06)."))
            return jsonify({"status": "success"}), 200

        ctx["data"] = dt
        horarios = listar_blocos_disponiveis(dt, exibir_nomes=True)
        if not horarios or horarios.strip() == "":
            waha.send_message(
                chat_id,
                _caixa("😕 Sem horários", f"Não encontrei horários para {dt.strftime('%d/%m/%Y')}.\nTente outra data, ou voltar para escolher outra opção.")
            )
            return jsonify({"status": "success"}), 200

        _goto(estado, "solicitar_horario")
        titulo = f"⏰ Horários disponíveis — {dt.strftime('%d/%m/%Y')}"
        conteudo = f"{horarios}\n\n👉 Digite o número do horário desejado."
        waha.send_message(chat_id, _caixa(titulo, conteudo))
        return jsonify({"status": "success"}), 200

    if estado["etapa"] == "solicitar_horario":
        try:
            indice = int(msg_norm)
        except ValueError:
            waha.send_message(chat_id, _caixa("❌ Entrada inválida", "Digite o número do horário da lista."))
            return jsonify({"status": "success"}), 200

        if not (1 <= indice <= len(BLOCOS_HORARIOS)):
            waha.send_message(chat_id, _caixa("❌ Número inválido", "Digite um dos números exibidos."))
            return jsonify({"status": "success"}), 200

        horario_escolhido = BLOCOS_HORARIOS[indice - 1]
        data_sel = ctx.get("data")
        if not data_sel:
            _reset(estado)
            waha.send_message(chat_id, _caixa("⚠️ Ops", "Perdi o contexto da data. Vamos recomeçar pelo menu."))
            _send_menu(waha, chat_id)
            return jsonify({"status": "success"}), 200

        if not horario_disponivel(horario_escolhido, data_sel):
            horarios = listar_blocos_disponiveis(data_sel, exibir_nomes=True)
            titulo = "❌ Horário indisponível"
            conteudo = f"O horário {horario_escolhido} acabou de ser ocupado.\n\n⏰ Ainda disponíveis:\n{horarios}\n\nEscolha outro número."
            waha.send_message(chat_id, _caixa(titulo, conteudo))
            return jsonify({"status": "success"}), 200

        # Dados do cliente
        nome = ctx.get("nome_cliente", "Cliente")
        serv_ids = ctx.get("servicos", [])
        servicos_label = ", ".join([SERVICOS[s]["label"] for s in serv_ids]) if serv_ids else "Serviço"
        insta = ctx.get("insta", "")

        # Monta itens a partir do catálogo (com preços reais)
        itens = []
        for sid in serv_ids:
            item = _mk_item_from_code(sid)
            if item:
                itens.append(item)

        if not itens:
            waha.send_message(chat_id, _caixa("⚠️ Catálogo", "Não encontrei preços para os serviços selecionados. Tente novamente."))
            return jsonify({"status": "success"}), 200

        # Total calculado
        total = round(sum(i["unit_price"] * int(i.get("quantity", 1)) for i in itens), 2)

        # Cria pré-agendamento com snapshot dos itens
        from . import agenda
        agendamento_id = agenda.criar_pre_agendamento(
            chat_id=chat_id,
            nome=nome,
            data=data_sel,
            horario=horario_escolhido,
            servicos=itens,   # lista com títulos e preços
            insta=insta
        )

        # ===== Geração do PIX (Payments API) e mensagens =====
        import requests

        # guarda no contexto para comandos rápidos depois
        ctx["ultimo_agendamento_id"] = agendamento_id
        ctx["ultimo_pix_payload"] = {
            "chat_id": chat_id,
            "nome": nome,
            "insta": insta,
            "data": data_sel.isoformat(),
            "horario": horario_escolhido,
        }

        # monta itens caso ainda não exista a lista (com fallback de preço)
        serv_ids = ctx.get("servicos", [])
        itens = locals().get("itens") or []
        if not itens:
            itens = []
            for sid in serv_ids:
                if sid in SERVICOS:
                    preco = SERVICOS[sid].get("price", 35.0) if isinstance(SERVICOS[sid], dict) else 35.0
                    itens.append({
                        "title": SERVICOS[sid]["label"],
                        "quantity": 1,
                        "unit_price": float(preco)
                    })

        def _fmt_brl_local(v: float) -> str:
            return ("R$ {:,.2f}".format(float(v))).replace(",", "X").replace(".", ",").replace("X", ".")

        servicos_label = ", ".join([SERVICOS[s]["label"] for s in serv_ids if s in SERVICOS]) or "Serviço"
        linhas = "\n".join([f"- {i['title']}: {_fmt_brl(i.get('unit_price', 0))}" for i in itens]) or "—"
        total_local = round(sum(float(i.get("unit_price", 0)) * int(i.get("quantity", 1)) for i in itens), 2)


        try:
            resp = requests.post(
                f"{API_INTERNAL_BASE}/mp/{nome_empresa}/pix",
                json={
                    "agendamento_id": agendamento_id,
                    "chat_id": chat_id,
                    "nome": nome,
                    "insta": insta,
                    "data": data_sel.isoformat(),
                    "horario": horario_escolhido,
                },
                timeout=15
            )

            if resp.status_code == 200:
                data_pix = resp.json()
                qr_code    = data_pix.get("qr_code")       # PIX Copia e Cola (string longa)
                ticket_url = data_pix.get("ticket_url")    # Página web com o QR (sem login)

                # 1) Mensagem de resumo (sem o código, fica mais limpo)
                titulo = "💳 PIX para confirmar"
                conteudo = (
                    f"👤 Cliente: {nome}\n"
                    f"💈 Serviço(s): {servicos_label}\n"
                    f"📅 Data: {data_sel.strftime('%d/%m/%Y')}\n"
                    f"🕒 Horário: {horario_escolhido}\n\n"
                    f"🧾 Itens:\n{linhas}\n"
                    f"Total: {_fmt_brl_local(total_local)}\n\n"
                    f"🌐 Prefere escanear o QR?\n{ticket_url or '— indisponível —'}\n\n"
                    "Assim que o banco confirmar, eu te aviso aqui 👍\n"
                    "_(validade ~20 minutos)_"
                )
                waha.send_message(chat_id, _caixa(titulo, conteudo))

                # 2) Mensagem curta só com o “PIX Copia e Cola” para facilitar copiar
                if qr_code:
                    waha.send_message(chat_id, "🔹 *PIX Copia e Cola* (copie a mensagem abaixo):")
                    waha.send_message(chat_id, qr_code)  # <- apenas o código, isolado

            else:
                waha.send_message(chat_id, _caixa("⚠️ Erro", "Não consegui gerar o PIX agora. Tente novamente."))
        except Exception as e:
            waha.send_message(chat_id, _caixa("⚠️ Erro", f"Ocorreu um problema ao criar o pagamento: {e}"))

        _reset(estado)
        return jsonify({"status": "success"}), 200


    # ===== Ver horários (consulta sem agendar) =====
    if estado["etapa"] == "ver_horarios_data":
        dt = _parse_data(msg_norm.replace(" ", ""))
        if not dt:
            waha.send_message(chat_id, _caixa("❌ Data inválida", "Use DD/MM (ex.: 12/06)."))
            return jsonify({"status": "success"}), 200

        ctx["consulta_data"] = dt
        _goto(estado, "ver_horarios_listar")

        horarios = listar_blocos_disponiveis(dt, exibir_nomes=True)
        if not horarios or horarios.strip() == "":
            waha.send_message(
                chat_id,
                _caixa("😕 Sem horários", f"Não encontrei horários para {dt.strftime('%d/%m/%Y')}.\nEnvie outra data, voltar ou menu.")
            )
            return jsonify({"status": "success"}), 200

        titulo = f"📅 Consulta — {dt.strftime('%d/%m/%Y')}"
        conteudo = f"⏰ Disponíveis:\n{horarios}\n\nPara agendar, digite agendar.\nOu envie outra data."
        waha.send_message(chat_id, _caixa(titulo, conteudo))
        return jsonify({"status": "success"}), 200

    if estado["etapa"] == "ver_horarios_listar":
        dt_try = _parse_data(msg_norm.replace(" ", ""))
        if dt_try:
            ctx["consulta_data"] = dt_try
            horarios = listar_blocos_disponiveis(dt_try, exibir_nomes=True)
            if not horarios or horarios.strip() == "":
                waha.send_message(
                    chat_id,
                    _caixa("😕 Sem horários", f"Não encontrei horários para {dt_try.strftime('%d/%m/%Y')}.\nEnvie outra data, voltar ou menu.")
                )
                return jsonify({"status": "success"}), 200

            titulo = f"📅 Consulta — {dt_try.strftime('%d/%m/%Y')}"
            conteudo = f"⏰ Disponíveis:\n{horarios}\n\nPara agendar, digite agendar.\nOu envie outra data."
            waha.send_message(chat_id, _caixa(titulo, conteudo))
            return jsonify({"status": "success"}), 200

        if msg_lower in {"agendar", "quero agendar", "fazer agendamento"}:
            _push(estado, "selecionar_servicos")
            ctx["servicos"] = []
            titulo = "✍ Selecione os serviços"
            conteudo = _catalogo_texto()
            msg = _caixa(titulo, conteudo) + "\n\n" + \
                "ℹ️ Dica:\n   envie números (ex.: 1,3) ou nomes (ex.: corte social, barba)."
            waha.send_message(chat_id, msg)
            return jsonify({"status": "success"}), 200

        waha.send_message(
            chat_id,
            _caixa("ℹ️ Dica", "Para agendar, digite agendar.\nVocê também pode enviar outra data (DD/MM), voltar ou menu.")
        )
        return jsonify({"status": "success"}), 200

    # ===== Fallback =====
    _reset(estado)
    waha.send_message(chat_id, _caixa("⚠️ Não entendi", "Vamos recomeçar."))
    _send_menu(waha, chat_id)
    return jsonify({"status": "success"}), 200
