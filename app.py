import os
import json
import importlib
import traceback
import threading
import time
from datetime import datetime, date
from typing import Dict, Any, Optional, List

from flask import Flask, request, jsonify
from dotenv import load_dotenv

# WAHA client (o seu wrapper)
from services.waha import Waha

load_dotenv()

app = Flask(__name__)

# -------------------------------------------------
# Blueprints/Extensões
# -------------------------------------------------
# Mercado Pago (mantém sob try/except)
try:
    from services.pagamentos import pagamentos_bp
    app.register_blueprint(pagamentos_bp, url_prefix="/mp")
    print("[MP] Blueprint de pagamentos registrado em /mp")
except Exception as e:
    print("[MP] Pagamentos desativado ou falhou no registro:", e)

# -------------------------------------------------
# Configurações / Estado
# -------------------------------------------------
BASE_DIR = os.path.dirname(__file__)
CONFIG_DIR = os.path.join(BASE_DIR, "config")

with open(os.path.join(CONFIG_DIR, "empresas_config.json"), "r", encoding="utf-8") as f:
    config_empresas: Dict[str, Dict[str, Any]] = json.load(f)

with open(os.path.join(CONFIG_DIR, "admins_config.json"), "r", encoding="utf-8") as f:
    admins_por_empresa: Dict[str, list] = json.load(f)


def _normalize_chat_id(identifier: Optional[str]) -> str:
    """Converte identificadores do WhatsApp para o formato @c.us."""
    if identifier is None:
        return ""

    raw = str(identifier).strip()
    if not raw:
        return ""

    if raw.startswith("+"):
        raw = raw[1:]

    if raw.endswith("@s.whatsapp.net"):
        raw = raw.replace("@s.whatsapp.net", "@c.us")

    if raw.endswith("@c.us") or raw.endswith("@g.us"):
        return raw

    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits:
        return f"{digits}@c.us"

    return raw


# Um cliente WAHA por empresa
waha_clients: Dict[str, Waha] = {}
empresa_por_session: Dict[str, List[str]] = {}
empresa_por_numero_bot: Dict[str, str] = {}

for empresa, cfg in config_empresas.items():
    base_url = cfg.get("base_url")
    if base_url:
        try:
            session_cfg = cfg.get("waha_session")
            session = session_cfg.strip() if isinstance(session_cfg, str) else None
            if not session:
                session = "default"
            api_key = cfg.get("waha_api_key") or os.getenv("WAHA_API_KEY")
            waha_clients[empresa] = Waha(base_url, session=session, api_key=api_key)
            empresa_por_session.setdefault(session, []).append(empresa)
            print(
                f"[WAHA] Cliente inicializado para '{empresa}' -> {base_url} (sessão: {session})"
            )
        except Exception as e:
            print(f"[WAHA] Falha ao iniciar cliente da empresa '{empresa}': {e}")

    numeros_cfg: List[str] = []
    if isinstance(cfg.get("waha_numbers"), list):
        numeros_cfg.extend(cfg["waha_numbers"])
    elif cfg.get("waha_numbers"):
        numeros_cfg.append(cfg["waha_numbers"])

    numero_unico = cfg.get("waha_number")
    if numero_unico:
        numeros_cfg.append(numero_unico)

    for numero in numeros_cfg:
        normalizado = _normalize_chat_id(numero)
        if normalizado:
            empresa_por_numero_bot[normalizado] = empresa

# Estado de fluxo em memória, separado por empresa
fluxo_usuario: Dict[str, Dict[str, Any]] = {empresa: {} for empresa in config_empresas.keys()}

# -------------------------------------------------
# Helpers de payload / empresa / envio
# -------------------------------------------------
def _extract_message_fields(payload: dict) -> Dict[str, Any]:
    """
    Extrai campos mesmo que o WAHA mande em formatos diferentes.
    Suporta:
      - { "event":"message", "data": {...} }
      - { "event":"message", "payload": {...} }
      - { "data": {"messages": [ {...} ]} }
      - { "messages": [ {...} ] }
      - flat { "from","chatId","body","text","timestamp","id" ... }
    """
    data = (payload.get("data")
            or payload.get("payload")
            or payload
            or {})

    # Alguns eventos do WAHA chegam como lista dentro de "data"
    if isinstance(data, list) and data:
        data = data[0]

    msg_obj = None

    # 1) Objeto simples (payload "flat" do WAHA ou legacy)
    if isinstance(data, dict) and any(k in data for k in ("body", "text", "from", "chatId", "sender", "to", "fromMe", "timestamp", "t", "id", "messages", "message")):
        msg_obj = data

    # 2) Lista messages dentro de data/payload
    if isinstance(data, dict) and isinstance(data.get("messages"), list) and data["messages"]:
        msg_obj = data["messages"][0]

    # 3) Lista messages na raiz
    if msg_obj is None and isinstance(payload.get("messages"), list) and payload["messages"]:
        msg_obj = payload["messages"][0]

    msg_obj = msg_obj or {}

    text = msg_obj.get("body") or msg_obj.get("text") or ""
    if not isinstance(text, str):
        text = str(text)
    text = text.strip()
    chat_id = msg_obj.get("from") or msg_obj.get("chatId") or msg_obj.get("chat_id") or msg_obj.get("sender") or ""
    to = msg_obj.get("to") or ""
    from_me = bool(msg_obj.get("fromMe") or data.get("fromMe"))

    # Eventos recentes do WAHA usam a estrutura messages.upsert com "message" aninhado
    if not text and isinstance(msg_obj.get("message"), dict):
        message_node = msg_obj["message"]
        text = (
            message_node.get("conversation")
            or message_node.get("extendedTextMessage", {}).get("text")
            or message_node.get("ephemeralMessage", {}).get("message", {}).get("extendedTextMessage", {}).get("text")
            or message_node.get("buttonsResponseMessage", {}).get("selectedDisplayText")
            or ""
        ).strip()

        key_data = msg_obj.get("key", {}) if isinstance(msg_obj.get("key"), dict) else {}
        if not chat_id:
            chat_id = (
                key_data.get("remoteJid")
                or msg_obj.get("chatId")
                or msg_obj.get("chat_id")
                or ""
            )
        if not to:
            to = key_data.get("participant") or key_data.get("from") or ""
        if "fromMe" not in msg_obj and key_data:
            from_me = bool(key_data.get("fromMe"))

    if not chat_id and isinstance(msg_obj.get("key"), dict):
        chat_id = msg_obj["key"].get("remoteJid", "")

    chat_id = str(chat_id or "").strip()
    if chat_id.endswith("@s.whatsapp.net"):
        chat_id = chat_id.replace("@s.whatsapp.net", "@c.us")
    to = str(to or "").strip()
    from_me = bool(from_me)

    ts = (
        msg_obj.get("timestamp")
        or msg_obj.get("t")
        or msg_obj.get("messageTimestamp")
        or data.get("timestamp")
        or data.get("t")
    )
    try:
        ts = int(ts)
        if ts > 10**12:  # se vier em ms, converte para s
            ts = ts // 1000
    except Exception:
        ts = None

    msg_id = None
    mid = msg_obj.get("id") or data.get("id")
    if isinstance(mid, dict):
        msg_id = mid.get("_serialized") or mid.get("id")
    elif isinstance(mid, str):
        msg_id = mid

    # Empresa/sessão que às vezes vem no webhook
    empresa_hint = payload.get("empresa") or data.get("empresa")
    session = (
        payload.get("session")
        or data.get("session")
        or payload.get("sessionId")
        or data.get("sessionId")
        or payload.get("session_id")
        or data.get("session_id")
        or payload.get("instanceId")
        or data.get("instanceId")
        or payload.get("instance_id")
        or data.get("instance_id")
    )

    owner = None
    owner_node = None
    if isinstance(data.get("owner"), dict):
        owner_node = data.get("owner")
    elif isinstance(msg_obj.get("owner"), dict):
        owner_node = msg_obj.get("owner")

    if owner_node:
        owner = owner_node.get("id") or owner_node.get("wid") or owner_node.get("number")

    if not owner:
        possible_owner = msg_obj.get("from") if from_me else msg_obj.get("to")
        owner = possible_owner

    owner = _normalize_chat_id(owner)

    return {
        "data": data,
        "msg": text,
        "chat_id": chat_id,
        "to": to,
        "owner": owner,
        "from_me": from_me,
        "ts": ts,
        "msg_id": msg_id,
        "empresa_hint": empresa_hint,
        "session": session,
    }

def _is_group(chat_id: str) -> bool:
    return "@g.us" in (chat_id or "")

def _resolve_empresa(payload_fields: Dict[str, Any]) -> Optional[str]:
    """
    Determina a 'empresa' da mensagem:
    1) query string ?empresa=...
    2) header X-Empresa
    3) payload['empresa'] (ou data/payload interno)
    4) session configurada no WAHA (waha_session em config)
    5) se houver UMA única empresa no config, usa ela
    6) fallback para 'empresa1'
    """
    q = request.args.get("empresa")
    if q and q in config_empresas:
        return q

    h = request.headers.get("X-Empresa")
    if h and h in config_empresas:
        return h

    hint = payload_fields.get("empresa_hint")
    if hint and hint in config_empresas:
        return hint

    session = payload_fields.get("session")
    if isinstance(session, dict):
        session = (
            session.get("name")
            or session.get("id")
            or session.get("session")
            or session.get("sessionId")
        )

    if session:
        session = str(session)
        candidatos = empresa_por_session.get(session)
        if candidatos and len(candidatos) == 1:
            return candidatos[0]

    owner = _normalize_chat_id(payload_fields.get("owner"))
    if owner and owner in empresa_por_numero_bot:
        return empresa_por_numero_bot[owner]

    to = _normalize_chat_id(payload_fields.get("to"))
    if to and to in empresa_por_numero_bot:
        return empresa_por_numero_bot[to]

    # se só tem uma empresa configurada, retorna ela
    if len(config_empresas) == 1:
        return next(iter(config_empresas.keys()))
    
    # fallback comum ao seu compose

    return "empresa1" if "empresa1" in config_empresas else None

def _get_waha_for(empresa: str) -> Optional[Waha]:
    return waha_clients.get(empresa)

def _dispatch_to_flow(empresa: str, chat_id: str, msg: str):
    """
    Carrega o módulo de fluxo da empresa e delega a mensagem.
    Tenta:
      - scripts_empresas.<empresa>  (compat)
      - <empresa>.fluxo             (seu layout atual com pacote 'empresa1')
    Faz roteamento admin/cliente conforme admins_config.
    """
    # cliente WAHA
    waha = _get_waha_for(empresa)
    if not waha:
        return jsonify({"status": "error", "message": f"WAHA não configurado para '{empresa}'"}), 500

    # importa o módulo
    modulo = None
    try:
        modulo = importlib.import_module(f"scripts_empresas.{empresa}")
    except ModuleNotFoundError:
        # tenta layout do seu 'empresa1.fluxo'
        try:
            modulo = importlib.import_module(f"{empresa}.fluxo")
        except ModuleNotFoundError:
            return jsonify({"status": "error", "message": f"Módulo de fluxo para '{empresa}' não encontrado."}), 500
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "message": f"Falha importando {empresa}.fluxo: {e}"}), 500
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Falha importando scripts_empresas.{empresa}: {e}"}), 500

    # estado da empresa
    if empresa not in fluxo_usuario:
        fluxo_usuario[empresa] = {}

    # escolhe rota admin x normal
    try:
        if chat_id in set(admins_por_empresa.get(empresa, [])):
            if hasattr(modulo, "processar_admin"):
                return modulo.processar_admin(chat_id, msg, empresa, waha)
            else:
                return jsonify({"status": "error", "message": "Função processar_admin não encontrada."}), 500

        if hasattr(modulo, "processar"):
            return modulo.processar(chat_id, msg, empresa, waha, fluxo_usuario[empresa])

        return jsonify({"status": "error", "message": "Função processar não encontrada."}), 500
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

# -------------------------------------------------
# Rotas básicas / saúde
# -------------------------------------------------
@app.get("/")
def index():
    return jsonify({
        "ok": True,
        "ts": datetime.utcnow().isoformat(),
        "empresas": list(config_empresas.keys())
    }), 200

@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200

# -------------------------------------------------
# Webhooks
# -------------------------------------------------
@app.post("/waha/webhook")
def waha_webhook():
    """
    Endpoint para os containers WAHA chamarem.
    No seu docker-compose: WEBHOOK_URL=http://api:8000/waha/webhook
    Você pode opcionalmente passar ?empresa=empresa1.
    """
    payload = request.get_json(silent=True, force=True) or {}
    fields = _extract_message_fields(payload)

    # Logs brutos úteis
    try:
        app.logger.info({"waha_webhook_raw": payload})
        app.logger.info({"waha_webhook_norm": {
            "from": fields["chat_id"], "text": fields["msg"],
            "fromMe": fields["from_me"], "ts": fields["ts"], "id": fields["msg_id"],
            "owner": fields["owner"], "to": fields["to"],
        }})
    except Exception:
        pass

    # Filtros básicos
    if fields["from_me"]:
        return jsonify({"status": "ignored", "reason": "fromMe"}), 200
    if not fields["chat_id"] or not fields["msg"]:
        return jsonify({"status": "ignored", "reason": "empty"}), 200
    if _is_group(fields["chat_id"]):
        return jsonify({"status": "ignored", "reason": "group"}), 200

    empresa = _resolve_empresa(fields)
    if not empresa:
        return jsonify({"status": "error", "message": "Não foi possível resolver a empresa."}), 400

    try:
        app.logger.info({
            "empresa_resolvida": empresa,
            "chat_id": fields.get("chat_id"),
            "session": fields.get("session"),
            "owner": fields.get("owner"),
            "to": fields.get("to"),
        })
    except Exception:
        pass

    return _dispatch_to_flow(empresa, fields["chat_id"], fields["msg"])

# Compatibilidade com sua rota antiga dinâmica
@app.post("/webhook/<empresa>")
def webhook_dinamico(empresa: str):
    payload = request.get_json(force=True, silent=True) or {}

    # tenta extrair de forma mais simples também
    chat_id = None
    texto = ""

    # formato legado
    if isinstance(payload.get("payload"), dict):
        p = payload["payload"]
        chat_id = p.get("from") or p.get("chatId") or p.get("chat_id")
        texto = p.get("body") or p.get("text") or p.get("message") or ""

    # *** CORREÇÃO DE INDENTAÇÃO AQUI ***
    # Estas linhas estavam indentadas errado
    chat_id = _normalize_chat_id(chat_id or payload.get("chat_id") or payload.get("chatId"))
    texto = (texto or payload.get("text") or payload.get("message") or payload.get("body") or "").strip()

    # se não conseguiu, usa o extrator robusto
    if not chat_id or not texto:
        fields = _extract_message_fields(payload)
        chat_id = fields["chat_id"]
        texto = fields["msg"]

        # filtros
        if fields["from_me"]:
            return jsonify({"status": "ignored", "reason": "fromMe"}), 200

    if not chat_id or not texto:
        return jsonify({"status": "ignored"}), 200

    if _is_group(chat_id):
        return jsonify({"status": "ignored", "reason": "group"}), 200

    if empresa not in config_empresas:
        return jsonify({"status": "error", "message": f"Empresa '{empresa}' não encontrada."}), 404

    return _dispatch_to_flow(empresa, chat_id, texto)

print("[WAHA] Webhook registrado em /waha/webhook")

# -------------------------------------------------
# Agendador (opcional) — mantido do seu código
# -------------------------------------------------
ENABLE_SCHEDULER = False
SCHEDULER_INTERVAL_SEC = int(os.getenv("SCHEDULER_INTERVAL_SEC", "60"))
PIX_REMINDER_WINDOW_MIN = int(os.getenv("PIX_REMINDER_WINDOW_MIN", "5"))

_reminded_expiring = {empresa: set() for empresa in config_empresas.keys()}
_notified_expired = {empresa: set() for empresa in config_empresas.keys()}

def _send_whatsapp(waha: Waha, chat_id: str, text: str):
    try:
        waha.send_message(chat_id, text)
    except Exception as e:
        print(f"[SCHED] Falha ao enviar mensagem WhatsApp: {e}")

def _fmt_date(d) -> str:
    if isinstance(d, date):
        return d.strftime("%d/%m/%Y")
    try:
        return datetime.fromisoformat(str(d)).strftime("%d/%m/%Y")
    except Exception:
        return str(d)

def _scheduler_loop():
    while True:
        try:
            for empresa in list(config_empresas.keys()):
                waha = _get_waha_for(empresa)
                if not waha:
                    continue

                try:
                    agenda = importlib.import_module(f"scripts_empresas.{empresa}.agenda")
                except Exception as e:
                    print(f"[SCHED] Não consegui importar agenda de {empresa}: {e}")
                    continue

                # 1) Lembretes — prestes a expirar
                candidatos = []
                try:
                    if hasattr(agenda, "listar_pendentes_prestes_a_expirar"):
                        candidatos = agenda.listar_pendentes_prestes_a_expirar(janela_min=PIX_REMINDER_WINDOW_MIN)
                except Exception as e:
                    print(f"[SCHED] erro listar_pendentes_prestes_a_expirar({empresa}): {e}")

                now = datetime.now()
                for c in candidatos or []:
                    ag_id = str(c.get("AgendamentoID") or "")
                    if not ag_id or ag_id in _reminded_expiring[empresa]:
                        continue
                    chat_id = str(c.get("ChatID") or "")
                    if not chat_id:
                        continue

                    exp_str = str(c.get("Expira_em") or "")
                    try:
                        exp_dt = datetime.strptime(exp_str, "%d/%m/%Y %H:%M:%S")
                        mins_left = max(1, int((exp_dt - now).total_seconds() // 60))
                    except Exception:
                        mins_left = PIX_REMINDER_WINDOW_MIN

                    data_txt = _fmt_date(c.get("Data"))
                    horario_txt = str(c.get("Horário") or "")
                    servico = str(c.get("Serviço") or "seu horário")
                    msg = (
                        "⏳ *Falta pouco para sua reserva expirar!*\n\n"
                        f"💈 {servico}\n"
                        f"📅 {data_txt}  🕒 {horario_txt}\n"
                        f"⏱️ Expira em ~{mins_left} min.\n\n"
                        "Se quiser garantir, finalize o pagamento agora. "
                        "Envie *reenviar pix* para receber o código de novo."
                    )
                    _send_whatsapp(waha, chat_id, msg)
                    _reminded_expiring[empresa].add(ag_id)

                # 2) Expirados — detectar quem virou "Expirado" agora
                try:
                    df_before = agenda.carregar_agendamentos()
                    pend_before = set(
                        df_before[df_before["Status"] == "Pendente"]["AgendamentoID"].astype(str).tolist()
                    )
                except Exception as e:
                    print(f"[SCHED] erro carregar_agendamentos(before) {empresa}: {e}")
                    pend_before = set()

                try:
                    agenda.limpar_expirados()
                except Exception as e:
                    print(f"[SCHED] erro limpar_expirados {empresa}: {e}")

                try:
                    df_after = agenda.carregar_agendamentos()
                except Exception as e:
                    print(f"[SCHED] erro carregar_agendamentos(after) {empresa}: {e}")
                    df_after = None

                if df_after is not None:
                    try:
                        expired_rows = df_after[df_after["Status"] == "Expirado"]
                        for _, row in expired_rows.iterrows():
                            ag_id = str(row.get("AgendamentoID") or "")
                            if not ag_id or ag_id not in pend_before:
                                continue
                            if ag_id in _notified_expired[empresa]:
                                continue

                            chat_id = str(row.get("ChatID") or "")
                            if not chat_id:
                                continue

                            servico = str(row.get("Serviço") or "seu horário")
                            data_txt = _fmt_date(row.get("Data"))
                            horario_txt = str(row.get("Horário") or "")
                            msg = (
                                "⏰ *Sua reserva expirou por falta de pagamento.*\n\n"
                                f"💈 {servico}\n"
                                f"📅 {data_txt}  🕒 {horario_txt}\n\n"
                                "Quer tentar de novo? Digite *agendar* para escolher outro horário."
                            )
                            _send_whatsapp(waha, chat_id, msg)
                            _notified_expired[empresa].add(ag_id)
                    except Exception as e:
                        print(f"[SCHED] erro processando expirados {empresa}: {e}")

        except Exception as loop_e:
            print(f"[SCHED] erro no loop: {loop_e}")

        time.sleep(SCHEDULER_INTERVAL_SEC)

# Execução local (em produção use gunicorn: app:app)
if __name__ == "__main__":
    if ENABLE_SCHEDULER:
        t = threading.Thread(target=_scheduler_loop, name="scheduler", daemon=True)
        t.start()
        print(f"[SCHED] Agendador iniciado (intervalo {SCHEDULER_INTERVAL_SEC}s, janela {PIX_REMINDER_WINDOW_MIN}min)")
    app.run(host="0.0.0.0", port=5000, debug=True)