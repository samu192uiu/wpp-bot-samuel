import os
import pandas as pd
from datetime import datetime, date, timedelta
import uuid
import json

BASE_DIR = os.path.dirname(__file__)
PLANILHA_PATH = os.path.join(BASE_DIR, 'agendamentos_empresa1.xlsx')
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')

BLOCOS_HORARIOS = [
    "08:00", "09:00", "10:00", "11:00",
    "13:00", "14:00", "15:00", "16:00",
    "17:00"
]

# =============================
# Utilidades internas
# =============================
def _now():
    return datetime.now()

def _fmt_ts(ts: datetime) -> str:
    return ts.strftime("%d/%m/%Y %H:%M:%S")

def _parse_ts(ts_str: str) -> datetime | None:
    try:
        return datetime.strptime(ts_str, "%d/%m/%Y %H:%M:%S")
    except Exception:
        return None

def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "AgendamentoID", "Nome", "Data", "Horário",
        "Serviço", "Insta", "Status", "Agendado_em", "Expira_em", "ChatID",
        "ItensJSON", "Total"
    ]
    for c in cols:
        if c not in df.columns:
            # Data como NaT, Total como 0.0, demais vazios
            if c == "Data":
                df[c] = pd.NaT
            elif c == "Total":
                df[c] = 0.0
            else:
                df[c] = ""

    # Normalizações
    if "Data" in df.columns:
        try:
            df["Data"] = pd.to_datetime(df["Data"]).dt.date
        except Exception:
            pass
    if "Horário" in df.columns:
        df["Horário"] = df["Horário"].astype(str)
    if "Status" in df.columns:
        df["Status"] = df["Status"].astype(str)
    if "Total" in df.columns:
        try:
            df["Total"] = df["Total"].astype(float)
        except Exception:
            pass
    return df[cols]

def _gen_id(prefix: str = "AG") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"

def _itens_to_label(servicos_itens: list) -> str:
    """
    Converte lista de itens ({title, quantity, unit_price}) em string legível para 'Serviço'.
    """
    if not servicos_itens:
        return ""
    partes = []
    for i in servicos_itens:
        title = (i.get("title") or "").strip()
        qty = int(i.get("quantity") or 1)
        partes.append(f"{title} x{qty}" if qty > 1 else title)
    return ", ".join([p for p in partes if p])

def _sum_itens_total(servicos_itens: list) -> float:
    return round(sum(float(i.get("unit_price", 0.0)) * int(i.get("quantity", 1)) for i in (servicos_itens or [])), 2)

# =============================
# IO de planilha
# =============================
def carregar_agendamentos() -> pd.DataFrame:
    try:
        df = pd.read_excel(PLANILHA_PATH)
        df = _ensure_columns(df)
        return df
    except FileNotFoundError:
        df = pd.DataFrame(columns=[
            "AgendamentoID", "Nome", "Data", "Horário",
            "Serviço", "Insta", "Status", "Agendado_em", "Expira_em", "ChatID",
            "ItensJSON", "Total"
        ])
        return _ensure_columns(df)

def salvar_agendamentos(df: pd.DataFrame):
    # Backup
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    backup_file = os.path.join(BACKUP_DIR, f"backup_{_now().strftime('%Y%m%d_%H%M%S')}.xlsx")
    df.to_excel(backup_file, index=False)

    # Planilha principal
    df.to_excel(PLANILHA_PATH, index=False)

# =============================
# Limpeza de reservas expiradas
# =============================
def limpar_expirados():
    df = carregar_agendamentos()
    now = _now()
    alterado = False

    if "Status" not in df.columns:
        return

    for i in df.index:
        try:
            if str(df.at[i, "Status"]).strip().lower() == "pendente":
                expira_str = str(df.at[i, "Expira_em"]) if "Expira_em" in df.columns else ""
                expira = _parse_ts(expira_str) if expira_str else None
                if expira and expira < now:
                    df.at[i, "Status"] = "Expirado"
                    alterado = True
        except Exception:
            continue

    if alterado:
        salvar_agendamentos(df)

# =============================
# Consulta de disponibilidade
# =============================
def listar_blocos_disponiveis(data=None, exibir_nomes: bool=False) -> str:
    """
    Retorna uma string com a lista de horários e marcações Livre/Ocupado.
    """
    if data is None:
        data = date.today()
    limpar_expirados()
    df = carregar_agendamentos()

    ocupados = df[(df['Data'] == data) & (df['Status'].isin(["Pendente", "Confirmado"]))]  # bloqueia pendente/confirmado
    ocupados_horas = set(ocupados['Horário'].tolist())

    linhas = []
    for i, bloco in enumerate(BLOCOS_HORARIOS, 1):
        if bloco in ocupados_horas:
            label = "❌ Ocupado"
            if exibir_nomes:
                nomes = ocupados[ocupados["Horário"] == bloco]["Nome"].tolist()
                if nomes:
                    label += f" — {nomes[0]}"
        else:
            label = "✅ Livre"
        linhas.append(f"{i} - {bloco} - {label}")
    return "\n".join(linhas)

def horario_disponivel(horario_str, data=None) -> bool:
    if data is None:
        data = date.today()
    limpar_expirados()
    df = carregar_agendamentos()
    cond = (df["Data"] == data) & (df["Horário"] == horario_str) & (df["Status"].isin(["Pendente","Confirmado"]))
    return not cond.any()

# =============================
# Reserva / confirmação (para pagamentos)
# =============================
def reservar_pendente(
    agendamento_id,
    nome,
    horario_str,
    servico_label,
    data,
    insta="",
    chat_id="",
    itens=None,      # <- snapshot dos itens
    total=None,      # <- total calculado
    ttl_min=20
) -> bool:
    """
    Cria uma linha 'Pendente' (reserva) por ttl_min minutos.
    'servico_label' é string (ex.: "Corte social, Barba").
    """
    df = carregar_agendamentos()
    if not horario_disponivel(horario_str, data):
        return False

    now = _now()
    expira_em = now + timedelta(minutes=ttl_min)

    if total is None and itens:
        total = _sum_itens_total(itens)
    if total is None:
        total = 0.0

    itens_json = json.dumps(itens or [], ensure_ascii=False)

    novo = pd.DataFrame([{
        "AgendamentoID": agendamento_id,
        "Nome": (nome or "Cliente").strip().title(),
        "Data": data,
        "Horário": str(horario_str),
        "Serviço": (servico_label or "").strip(),
        "Insta": insta or "",
        "Status": "Pendente",
        "Agendado_em": _fmt_ts(now),
        "Expira_em": _fmt_ts(expira_em),
        "ChatID": chat_id or "",
        "ItensJSON": itens_json,
        "Total": float(total)
    }])
    df = pd.concat([df, novo], ignore_index=True)
    salvar_agendamentos(df)
    return True

def confirmar_pagamento(agendamento_id) -> bool:
    """
    Marca 'Pendente' -> 'Confirmado' para o agendamento_id.
    """
    df = carregar_agendamentos()
    cond = (df["AgendamentoID"] == agendamento_id) & (df["Status"] == "Pendente")
    if cond.any():
        df.loc[cond, "Status"] = "Confirmado"
        salvar_agendamentos(df)
        return True
    return False

def criar_pre_agendamento(chat_id: str, nome: str, data: date, horario: str, servicos: list, insta: str = "", ttl_min: int = 20) -> str:
    """
    Cria um pré-agendamento (Status 'Pendente') e retorna agendamento_id.
    'servicos' é a lista de itens (dicts: title, quantity, unit_price).
    """
    agendamento_id = _gen_id("AG")
    servico_label = _itens_to_label(servicos)
    total = _sum_itens_total(servicos)

    ok = reservar_pendente(
        agendamento_id=agendamento_id,
        nome=nome,
        horario_str=horario,
        servico_label=servico_label,
        data=data,
        insta=insta,
        chat_id=chat_id,
        itens=servicos,
        total=total,
        ttl_min=ttl_min
    )
    if not ok:
        raise RuntimeError("Horário ficou indisponível durante a reserva.")
    return agendamento_id

# --- Helpers para validação/leitura de snapshot ---
def _to_date(d):
    """Normaliza entrada para tipo date (YYYY-MM-DD)."""
    if isinstance(d, date):
        return d
    try:
        return pd.to_datetime(d).date()
    except Exception:
        return None

def reserva_bate(agendamento_id: str, data_ref, horario_ref: str, chat_id: str | None = None) -> bool:
    """
    Verifica se existe uma reserva Pendente com esse agendamento_id e
    se ela corresponde à mesma data/horário (e chat_id, se fornecido).
    """
    df = carregar_agendamentos()
    if df.empty:
        return False

    data_ref = _to_date(data_ref)
    horario_ref = str(horario_ref)

    cond = (df["AgendamentoID"] == agendamento_id) & (df["Status"] == "Pendente")
    if data_ref is not None:
        cond = cond & (df["Data"] == data_ref)
    if horario_ref:
        cond = cond & (df["Horário"] == horario_ref)
    if chat_id:
        cond = cond & (df["ChatID"] == chat_id)

    return bool(cond.any())

def obter_snapshot(agendamento_id: str) -> dict | None:
    """
    Retorna {'itens': list, 'total': float} do pré-agendamento.
    """
    df = carregar_agendamentos()
    rows = df[df["AgendamentoID"] == agendamento_id]
    if rows.empty:
        return None
    row = rows.iloc[0]
    try:
        itens = json.loads(row.get("ItensJSON") or "[]")
    except Exception:
        itens = []
    total = float(row.get("Total") or 0.0)
    return {"itens": itens, "total": total}

# =============================
# Novos helpers (status / expiração / expira_em breve)
# =============================
def obter_por_id(agendamento_id: str) -> dict | None:
    """
    Retorna um dicionário com os campos do agendamento (ou None se não encontrado).
    """
    df = carregar_agendamentos()
    rows = df[df["AgendamentoID"] == agendamento_id]
    if rows.empty:
        return None
    row = rows.iloc[0].to_dict()
    # normaliza tipos
    if isinstance(row.get("Data"), pd.Timestamp):
        row["Data"] = row["Data"].date()
    try:
        row["Total"] = float(row.get("Total") or 0.0)
    except Exception:
        row["Total"] = 0.0
    return row

def consultar_status(agendamento_id: str) -> str | None:
    """
    Retorna o Status do agendamento (ex.: 'Pendente', 'Confirmado', 'Expirado') ou None.
    """
    df = carregar_agendamentos()
    rows = df[df["AgendamentoID"] == agendamento_id]
    if rows.empty:
        return None
    status_txt = str(rows.iloc[0]["Status"]).strip()
    return status_txt or None

def marcar_expirado(agendamento_id: str) -> bool:
    """
    Força a marcação de 'Expirado' se ainda estiver 'Pendente'.
    Retorna True se alterou, False caso contrário.
    """
    df = carregar_agendamentos()
    cond = (df["AgendamentoID"] == agendamento_id) & (df["Status"] == "Pendente")
    if cond.any():
        df.loc[cond, "Status"] = "Expirado"
        salvar_agendamentos(df)
        return True
    return False

def listar_pendentes_prestes_a_expirar(janela_min: int = 5) -> list[dict]:
    """
    Lista reservas 'Pendente' cujo Expira_em acontece nos próximos 'janela_min' minutos.
    Ideal para lembrete proativo via WhatsApp.
    """
    limpar_expirados()  # já marca os que passaram
    df = carregar_agendamentos()
    if df.empty:
        return []

    now = _now()
    limite = now + timedelta(minutes=janela_min)

    candidatos = []
    pendentes = df[df["Status"] == "Pendente"]

    for _, row in pendentes.iterrows():
        expira_str = str(row.get("Expira_em") or "")
        expira_dt = _parse_ts(expira_str) if expira_str else None
        if not expira_dt:
            continue
        if now < expira_dt <= limite:
            candidatos.append({
                "AgendamentoID": row.get("AgendamentoID"),
                "ChatID": row.get("ChatID"),
                "Nome": row.get("Nome"),
                "Data": row.get("Data"),
                "Horário": row.get("Horário"),
                "Expira_em": expira_str,
                "Serviço": row.get("Serviço"),
                "Total": float(row.get("Total") or 0.0)
            })
    return candidatos

# =============================
# Registro direto (sem pagamento)
# =============================
def registrar_agendamento(nome, horario_str, servico, data=None, insta="", chat_id=""):
    """
    Caso precise registrar sem passar por pagamento (uso administrativo).
    Já grava como 'Confirmado'.
    """
    if data is None:
        data = date.today()
    if not horario_disponivel(horario_str, data):
        return False

    df = carregar_agendamentos()
    now = _now()
    novo = pd.DataFrame([{
        "AgendamentoID": _gen_id("AG"),
        "Nome": (nome or "Cliente").strip().title(),
        "Data": data,
        "Horário": str(horario_str),
        "Serviço": (servico or "").strip().title(),
        "Insta": insta or "",
        "Status": "Confirmado",
        "Agendado_em": _fmt_ts(now),
        "Expira_em": "",
        "ChatID": chat_id or "",
        "ItensJSON": "[]",
        "Total": 0.0
    }])
    df = pd.concat([df, novo], ignore_index=True)
    salvar_agendamentos(df)
    return True
