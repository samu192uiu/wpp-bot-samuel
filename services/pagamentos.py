# services/pagamentos.py
import os, json, importlib, hmac, hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from flask import Blueprint, request, jsonify
import mercadopago
from mercadopago.config import RequestOptions
from urllib.parse import urljoin
import requests

# Envio de WhatsApp após aprovação
from services.waha import Waha

# =========================
# Configurações básicas
# =========================
BASE_URL = os.getenv("BASE_URL", "https://example.com").rstrip("/")
MP_WEBHOOK_SECRET = os.getenv("MP_WEBHOOK_SECRET", "")  # secret gerada ao salvar o webhook no painel
MP_REQUIRE_SIGNATURE = os.getenv("MP_REQUIRE_SIGNATURE", "false").lower() in ("1", "true", "yes")

pagamentos_bp = Blueprint("pagamentos", __name__)

with open(os.path.join("config", "empresas_config.json"), "r", encoding="utf-8") as f:
    EMP_CFG = json.load(f)

# =========================
# Helpers gerais
# =========================
def _build_url(path: str) -> str:
    return urljoin(BASE_URL + "/", path.lstrip("/"))

def get_cfg(empresa: str) -> Dict[str, Any]:
    cfg = EMP_CFG.get(empresa)
    if not cfg or not cfg.get("mp_access_token"):
        raise ValueError(f"Config MP ausente para empresa '{empresa}'")
    return cfg

def get_mp_sdk(empresa: str) -> mercadopago.SDK:
    cfg = get_cfg(empresa)
    return mercadopago.SDK(cfg["mp_access_token"])

def get_agenda_mod(empresa: str):
    return importlib.import_module(f"scripts_empresas.{empresa}.agenda")

def _pref_expiration(minutes=20):
    now = datetime.utcnow()
    return {
        "expires": True,
        "expiration_date_from": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "expiration_date_to": (now + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }

def _sum_total(itens: List[Dict[str, Any]]) -> float:
    return round(sum(float(i.get("unit_price", 0.0)) * int(i.get("quantity", 1)) for i in (itens or [])), 2)

def _fmt_brl(v: float) -> str:
    try:
        return ("R$ {:,.2f}".format(float(v))).replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return f"R$ {v}"

def _debug(tag: str, data: dict | None):
    try:
        print(f"[MP:{tag}] {json.dumps(data or {}, ensure_ascii=False)[:2000]}")
    except Exception:
        print(f"[MP:{tag}] (payload não serializável)")

# =========================
# Assinatura do Webhook (opcional/soft)
# =========================
def _validar_assinatura(req) -> bool:
    """
    Validação 'soft' da assinatura (x-signature) do Mercado Pago.
    - Se MP_WEBHOOK_SECRET vazio: True (não valida).
    - Se sem header: True, a menos que MP_REQUIRE_SIGNATURE=true.
    - Tenta bases comuns (varia por produto/região).
    """
    if not MP_WEBHOOK_SECRET:
        return True

    sig_hdr = req.headers.get("x-signature") or req.headers.get("X-Signature")
    if not sig_hdr:
        return not MP_REQUIRE_SIGNATURE

    ts, v1 = None, None
    try:
        parts = dict(p.strip().split("=", 1) for p in sig_hdr.split(","))
        ts = parts.get("ts")
        v1 = parts.get("v1")
    except Exception:
        _debug("sig.parse.error", {"header": sig_hdr})
        return not MP_REQUIRE_SIGNATURE

    if not ts or not v1:
        return not MP_REQUIRE_SIGNATURE

    body_text = req.get_data(as_text=True) or ""
    path = req.path or ""
    qs = req.query_string.decode("utf-8") if req.query_string else ""

    candidates = [
        ts + body_text,
        f"{ts}{body_text}",
        f"{ts}:{body_text}",
        f"{ts}{path}{qs}",
        f"{ts}{path}",
        f"{path}{ts}",
        f"{ts}",
    ]

    for base in candidates:
        calc = hmac.new(MP_WEBHOOK_SECRET.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(calc, v1):
            return True

    _debug("sig.mismatch", {"ts": ts, "path": path, "qs": qs, "tried": len(candidates)})
    return not MP_REQUIRE_SIGNATURE

# =========================
# Utilitários de webhook
# =========================
def _extract_payment_id(args_dict: dict, payload_dict: dict) -> Optional[str]:
    # via querystring
    if (args_dict.get("topic") or "").lower() == "payment" and args_dict.get("id"):
        return str(args_dict.get("id"))

    # via payload data.id
    data_id = (payload_dict.get("data") or {}).get("id")
    if data_id:
        return str(data_id)

    # via resource .../v1/payments/<id>
    res_url = str(payload_dict.get("resource") or "")
    if "/v1/payments/" in res_url:
        return res_url.rsplit("/", 1)[-1]

    return None

def _fetch_payment_with_sdk(mp_sdk: mercadopago.SDK, pid: str) -> Optional[dict]:
    try:
        res = mp_sdk.payment().get(pid)
    except Exception as e:
        _debug("payment.get.exc", {"id": pid, "error": str(e)})
        return None
    if res.get("status") != 200:
        _debug("payment.get.bad", res)
        return None
    return res.get("response") or {}

def _process_approved_for_empresa(empresa: str, payment: dict) -> None:
    """Confirma na planilha e envia WhatsApp se approved."""
    agenda = get_agenda_mod(empresa)
    status = (payment.get("status") or "").lower()
    if status != "approved":
        return

    # External reference -> nosso snapshot/contexto
    try:
        pld = json.loads(payment.get("external_reference") or "{}")
    except Exception:
        pld = {}

    if pld.get("empresa") != empresa:
        _debug("webhook.mismatch_empresa", {"payload_empresa": pld.get("empresa"), "route": empresa})
        return

    agendamento_id = pld.get("agendamento_id")
    chat_id = pld.get("chat_id")
    total = pld.get("total") or 0.0
    servico_lbl = pld.get("servico") or "Serviço"

    # Confirma na planilha (evita duplicar mensagens)
    enviou_confirmacao = False
    if agendamento_id:
        try:
            altered = agenda.confirmar_pagamento(agendamento_id)
            enviou_confirmacao = bool(altered)
        except Exception as e:
            _debug("confirmar_pagamento.exc", {"error": str(e)})

    # Monta resumo (snapshot)
    try:
        snap = agenda.obter_snapshot(agendamento_id) if agendamento_id else None
    except Exception:
        snap = None
    itens = (snap or {}).get("itens") or []
    if not total and snap:
        total = (snap.get("total") or 0.0)

    linhas = "\n".join(
        [f"- {i.get('title')}: {_fmt_brl(i.get('unit_price', 0))}" for i in itens]
    ) if itens else None

    # Data/horário (se disponível)
    data_txt, horario_txt = pld.get("data") or "", pld.get("horario") or ""
    if hasattr(agenda, "obter_por_id") and agendamento_id:
        try:
            row = agenda.obter_por_id(agendamento_id)
            if row:
                try:
                    d = row.get("Data")
                    if hasattr(d, "strftime"):
                        data_txt = d.strftime("%d/%m/%Y")
                    else:
                        data_txt = datetime.fromisoformat(str(d)).strftime("%d/%m/%Y")
                except Exception:
                    pass
                horario_txt = str(row.get("Horário") or horario_txt)
                servico_lbl = str(row.get("Serviço") or servico_lbl)
        except Exception as e:
            _debug("obter_por_id.exc", {"error": str(e)})

    # Envia a confirmação no WhatsApp (apenas se houve transição de status)
    if enviou_confirmacao and chat_id:
        try:
            base_url = EMP_CFG[empresa].get("base_url")
            waha_session = EMP_CFG[empresa].get("waha_session", "default")
            waha = Waha(base_url, session=waha_session)

            msg = (
                "✅ *Pagamento aprovado*\n\n"
                "🎉 *Agendamento concluído!*\n"
                f"💈 {servico_lbl}\n"
                f"📅 {data_txt}  🕒 {horario_txt}\n"
            )
            if linhas:
                msg += f"\n🧾 Itens:\n{linhas}\n"
            if total:
                msg += f"Total: {_fmt_brl(total)}\n"
            msg += "\nObrigado! Sessão encerrada ✅\nSe precisar, responda aqui. Para novo agendamento, digite *menu*."

            waha.send_message(chat_id, msg)
        except Exception as e:
            _debug("whatsapp.send.exc", {"error": str(e)})

# =========================
# Checkout Pro (opcional)
# =========================
@pagamentos_bp.post("/<empresa>/create")
def mp_create_preference(empresa: str):
    mp = get_mp_sdk(empresa)
    agenda = get_agenda_mod(empresa)

    body = request.get_json(force=True) or {}
    agendamento_id = body.get("agendamento_id")
    chat_id = body.get("chat_id")
    nome     = (body.get("nome") or "Cliente").strip()
    insta    = (body.get("insta") or "").strip()
    data_iso = body.get("data")
    horario  = body.get("horario")
    itens_in = body.get("itens") or []

    if not (agendamento_id and chat_id and data_iso and horario):
        return jsonify({"error": "missing_fields"}), 400

    # Data
    try:
        dt = datetime.fromisoformat(data_iso).date()
    except Exception:
        return jsonify({"error": "invalid_date"}), 400

    # Pode estar indisponível pela própria reserva; aceita se bater com a reserva
    if not agenda.horario_disponivel(horario, dt):
        try:
            bate = agenda.reserva_bate(
                agendamento_id=agendamento_id, data_ref=dt, horario_ref=horario, chat_id=chat_id
            )
        except AttributeError:
            bate = False
        if not bate:
            return jsonify({"error": "slot_unavailable"}), 409

    # Usa snapshot, se existir
    itens = itens_in
    total = None
    try:
        snap = agenda.obter_snapshot(agendamento_id)
        if snap and (snap.get("itens") or []):
            itens = snap["itens"]
            total = float(snap.get("total") or 0.0)
    except Exception:
        pass
    total = total if total is not None else _sum_total(itens)
    servico_label = ", ".join([str(x.get("title","")).strip() for x in itens]) or "Serviço"

    pref = {
        "items": itens,
        "payer": {"name": nome},
        "auto_return": "approved",
        "back_urls": {
            "success": _build_url(f"/mp/{empresa}/return?status=success"),
            "failure": _build_url(f"/mp/{empresa}/return?status=failure"),
            "pending": _build_url(f"/mp/{empresa}/return?status=pending"),
        },
        "notification_url": _build_url(f"/mp/{empresa}/webhook"),
        "statement_descriptor": EMP_CFG[empresa].get("statement_descriptor", "BARBEARIA"),
        "external_reference": json.dumps({
            "empresa": empresa,
            "agendamento_id": agendamento_id,
            "chat_id": chat_id,
            "nome": nome,
            "insta": insta,
            "data": data_iso,
            "horario": horario,
            "servico": servico_label,
            "total": total
        }, ensure_ascii=False),
        **_pref_expiration(20),
        "metadata": {
            "empresa": empresa,
            "agendamento_id": agendamento_id
        }
    }

    # Idempotência
    idem_key = f"pref:{empresa}:{chat_id}:{data_iso}:{horario}:{total}"
    req_opts = RequestOptions()
    req_opts.custom_headers = {"x-idempotency-key": idem_key}

    _debug("pref.create.req", {"pref": pref, "idem": idem_key})
    result = mp.preference().create(pref, req_opts)
    _debug("pref.create.res", result)

    if result.get("status") not in (200, 201):
        return jsonify({"error": "mp_error", "details": result}), 502

    resp = result["response"]
    return jsonify({
        "id": resp.get("id"),
        "init_point": resp.get("init_point"),
        "sandbox_init_point": resp.get("sandbox_init_point"),
        "total": total
    }), 200

@pagamentos_bp.get("/<empresa>/return")
def mp_return(empresa: str):
    return "Pagamento processado. Você pode fechar esta janela.", 200

# =========================
# Webhook por empresa (GET/HEAD/POST)
# =========================
@pagamentos_bp.get("/<empresa>/webhook")
def mp_webhook_get_empresa(empresa: str):
    # validação do painel
    return jsonify({"status": "ok", "msg": "webhook alive", "empresa": empresa}), 200

@pagamentos_bp.route("/<empresa>/webhook", methods=["HEAD"])
def mp_webhook_head_empresa(empresa: str):
    return ("", 200)

@pagamentos_bp.post("/<empresa>/webhook")
def mp_webhook_empresa(empresa: str):
    # valida assinatura
    if not _validar_assinatura(request):
        return jsonify({"status": "invalid_signature"}), 401

    mp = get_mp_sdk(empresa)

    args = request.args or {}
    payload = request.get_json(silent=True) or {}
    _debug("webhook.in", {"empresa": empresa, "args": dict(args), "payload": payload})

    pid = _extract_payment_id(args, payload)
    if not pid:
        _debug("webhook.no_pid", {"empresa": empresa})
        return jsonify({"status": "ignored"}), 200

    payment = _fetch_payment_with_sdk(mp, pid)
    if not payment:
        return jsonify({"status": "payment_lookup_failed"}), 200

    _process_approved_for_empresa(empresa, payment)
    return jsonify({"status": "ok"}), 200

# =========================
# Webhook coringa (sem /<empresa>) — útil quando o painel está apontando para /mp/webhook
# =========================
@pagamentos_bp.get("/webhook")
def mp_webhook_get_generic():
    return jsonify({"status": "ok", "msg": "generic webhook alive"}), 200

@pagamentos_bp.route("/webhook", methods=["HEAD"])
def mp_webhook_head_generic():
    return ("", 200)

@pagamentos_bp.post("/webhook")
def mp_webhook_generic():
    # valida assinatura
    if not _validar_assinatura(request):
        return jsonify({"status": "invalid_signature"}), 401

    args = request.args or {}
    payload = request.get_json(silent=True) or {}
    _debug("webhook.generic.in", {"args": dict(args), "payload": payload})

    pid = _extract_payment_id(args, payload)
    if not pid:
        _debug("webhook.generic.no_pid", {})
        return jsonify({"status": "ignored"}), 200

    # tenta buscar o pagamento usando cada token até achar
    payment = None
    empresa_ref = None
    for emp_key, cfg in EMP_CFG.items():
        token = cfg.get("mp_access_token")
        if not token:
            continue
        sdk = mercadopago.SDK(token)
        payment = _fetch_payment_with_sdk(sdk, pid)
        if not payment:
            continue
        # achou um pagamento; tenta extrair external_reference
        try:
            pld = json.loads(payment.get("external_reference") or "{}")
        except Exception:
            pld = {}
        empresa_ref = pld.get("empresa")
        if empresa_ref and empresa_ref in EMP_CFG:
            break
        # se não achou empresa no external_reference, continua procurando
        payment = None

    if not payment or not empresa_ref:
        _debug("webhook.generic.unresolved", {"pid": pid})
        return jsonify({"status": "ignored"}), 200

    _process_approved_for_empresa(empresa_ref, payment)
    return jsonify({"status": "ok", "empresa": empresa_ref}), 200

# =========================
# Diagnóstico (opcional)
# =========================
@pagamentos_bp.get("/<empresa>/payment_methods")
def mp_list_payment_methods(empresa: str):
    try:
        token = get_cfg(empresa)["mp_access_token"]
    except Exception as e:
        return jsonify({"error": "cfg_error", "message": str(e)}), 400

    try:
        resp = requests.get(
            "https://api.mercadopago.com/v1/payment_methods",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
    except Exception as e:
        return jsonify({"error": "network_error", "message": str(e)}), 502

    if resp.status_code != 200:
        return jsonify({"error": "mp_error", "status": resp.status_code, "details": resp.text}), 502

    data = resp.json() if isinstance(resp.json(), list) else []
    methods = []
    pix_available = False
    for m in data:
        mid = (m.get("id") or "").lower()
        status = (m.get("status") or m.get("status_detail") or "").lower()
        name = m.get("name") or m.get("description") or ""
        methods.append({
            "id": mid,
            "name": name,
            "payment_type_id": m.get("payment_type_id"),
            "status": status
        })
        if mid == "pix" and status == "active":
            pix_available = True

    return jsonify({"pix_available": pix_available, "count": len(methods), "methods": methods}), 200

# =========================
# PIX DIRETO (Payments API)
# =========================
@pagamentos_bp.post("/<empresa>/pix")
def mp_create_pix(empresa: str):
    """
    Cria um pagamento PIX (copia e cola) sem exigir login/app do MP.
    Retorna o qr_code (string copia e cola) e o ticket_url (página do MP).
    """
    mp = get_mp_sdk(empresa)
    agenda = get_agenda_mod(empresa)

    body = request.get_json(force=True) or {}
    agendamento_id = body.get("agendamento_id")
    chat_id = body.get("chat_id")
    nome     = (body.get("nome") or "Cliente").strip()
    insta    = (body.get("insta") or "").strip()
    data_iso = body.get("data")
    horario  = body.get("horario")

    if not (agendamento_id and chat_id and data_iso and horario):
        return jsonify({"error": "missing_fields"}), 400

    # validando data/slot
    try:
        dt = datetime.fromisoformat(data_iso).date()
    except Exception:
        return jsonify({"error": "invalid_date"}), 400

    if not agenda.horario_disponivel(horario, dt):
        try:
            bate = agenda.reserva_bate(
                agendamento_id=agendamento_id, data_ref=dt, horario_ref=horario, chat_id=chat_id
            )
        except AttributeError:
            bate = False
        if not bate:
            return jsonify({"error": "slot_unavailable"}), 409

    # Snapshot de itens/total
    itens = []
    total = 0.0
    servico_label = "Serviço"
    try:
        snap = agenda.obter_snapshot(agendamento_id)
        if snap and (snap.get("itens") or []):
            itens = snap["itens"]
            total = float(snap.get("total") or 0.0)
            servico_label = ", ".join([str(x.get("title","")).strip() for x in itens]) or servico_label
    except Exception:
        pass
    if not itens or total <= 0:
        return jsonify({"error": "no_items"}), 400

    # PIX body
    payer_email = body.get("payer_email") or f"cliente+{agendamento_id.lower()}@example.com"
    exp_to = datetime.utcnow() + timedelta(minutes=20)
    exp_str = exp_to.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    payment_data = {
        "transaction_amount": total,
        "description": f"Agendamento {servico_label} — {data_iso} {horario}",
        "payment_method_id": "pix",
        "payer": {
            "email": payer_email,
            "first_name": nome[:60]
        },
        "external_reference": json.dumps({
            "empresa": empresa,
            "agendamento_id": agendamento_id,
            "chat_id": chat_id,
            "nome": nome,
            "insta": insta,
            "data": data_iso,
            "horario": horario,
            "servico": servico_label,
            "total": total
        }, ensure_ascii=False),
        "metadata": {
            "empresa": empresa,
            "agendamento_id": agendamento_id
        },
        "date_of_expiration": exp_str,
        "notification_url": _build_url(f"/mp/{empresa}/webhook")  # garante notificação por payment
    }

    idem_key = f"pix:{empresa}:{chat_id}:{data_iso}:{horario}:{total}"
    req_opts = RequestOptions()
    req_opts.custom_headers = {"X-Idempotency-Key": idem_key}

    _debug("pix.create.req", {"payment_data": payment_data, "idem": idem_key})
    try:
        result = mp.payment().create(payment_data, req_opts)
    except Exception as e:
        _debug("pix.create.exc", {"error": str(e)})
        return jsonify({"error": "mp_exception", "message": str(e)}), 502
    _debug("pix.create.res", result)

    status = result.get("status")
    if status not in (200, 201):
        return jsonify({"error": "mp_error", "status": status, "details": result}), 502

    resp = result.get("response", {}) or {}
    poi = resp.get("point_of_interaction", {}) or {}
    tx = poi.get("transaction_data", {}) or {}

    return jsonify({
        "payment_id": resp.get("id"),
        "status": resp.get("status"),
        "qr_code": tx.get("qr_code"),        # PIX copia e cola
        "ticket_url": tx.get("ticket_url"),  # página web com QR (sem login)
        "total": total
    }), 200
