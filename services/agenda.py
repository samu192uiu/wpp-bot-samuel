import pandas as pd
from datetime import datetime, timedelta
import os

PLANILHA_PATH = 'agendamentos_empresa1.xlsx'
BACKUP_DIR = 'backups'

# Blocos de horário fixos
BLOCOS_HORARIOS = [
    "08:00", "09:30", "11:00", "13:00",
    "14:30", "16:00", "17:30", "19:00"
]

def carregar_agendamentos():
    try:
        df = pd.read_excel(PLANILHA_PATH)
        df['Horário'] = df['Horário'].astype(str)
        df['Data'] = pd.to_datetime(df['Data']).dt.date
        return df
    except FileNotFoundError:
        return pd.DataFrame(columns=[
            "Nome", "Data", "Horário", "Serviço", "Status", "Agendado_em"
        ])

def salvar_agendamentos(df):
    # Backup automático
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)

    backup_file = os.path.join(BACKUP_DIR, f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
    df.to_excel(backup_file, index=False)

    # Salva planilha oficial
    df.to_excel(PLANILHA_PATH, index=False)

def listar_blocos_disponiveis(data=None):
    if data is None:
        data = datetime.now().date()

    df = carregar_agendamentos()
    ocupados = df[(df['Data'] == data) & (df['Status'] == 'Pendente')]['Horário'].tolist()

    mensagem = f"📅 *Horários disponíveis para {data.strftime('%d/%m/%Y')}:*\n\n"
    for i, bloco in enumerate(BLOCOS_HORARIOS, start=1):
        status = "❌ Ocupado" if bloco in ocupados else "✅ Livre"
        mensagem += f"{i} - {bloco} - {status}\n"
    return mensagem

def horario_valido(indice_usuario):
    try:
        index = int(indice_usuario) - 1
        return BLOCOS_HORARIOS[index] if 0 <= index < len(BLOCOS_HORARIOS) else None
    except:
        return None

def horario_disponivel(horario_str, data=None):
    if data is None:
        data = datetime.now().date()

    df = carregar_agendamentos()
    filtro = (df['Data'] == data) & (df['Horário'] == horario_str) & (df['Status'] == "Pendente")
    return df[~filtro].shape[0] == df.shape[0]

def registrar_agendamento(nome, horario_str, servico, data=None):
    if data is None:
        data = datetime.now().date()

    df = carregar_agendamentos()
    novo = pd.DataFrame([{
        "Nome": nome.strip().title(),
        "Data": data,
        "Horário": horario_str,
        "Serviço": servico,
        "Status": "Pendente",
        "Agendado_em": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    }])
    df = pd.concat([df, novo], ignore_index=True)
    salvar_agendamentos(df)

def listar_agendamentos_do_dia(data=None):
    if data is None:
        data = datetime.now().date()

    df = carregar_agendamentos()
    df = df[(df["Data"] == data) & (df["Status"] == "Pendente")]
    return df.sort_values(by="Horário")

def proximo_cliente(data=None):
    if data is None:
        data = datetime.now().date()

    df = listar_agendamentos_do_dia(data)
    return df.iloc[0] if not df.empty else None

def finalizar_agendamento(data=None):
    if data is None:
        data = datetime.now().date()

    df = carregar_agendamentos()
    df = df.sort_values(by=["Data", "Horário"])

    for i in df.index:
        if df.at[i, "Data"] == data and df.at[i, "Status"] == "Pendente":
            df.at[i, "Status"] = "Finalizado"
            salvar_agendamentos(df)
            return df.at[i, "Nome"]
    return None

def cancelar_agendamento_por_nome(nome, data=None):
    if data is None:
        data = datetime.now().date()

    df = carregar_agendamentos()
    cond = (df["Nome"].str.lower() == nome.strip().lower()) & (df["Data"] == data) & (df["Status"] == "Pendente")

    if cond.any():
        df.loc[cond, "Status"] = "Cancelado"
        salvar_agendamentos(df)
        return True
    return False

def buscar_por_nome(nome):
    df = carregar_agendamentos()
    return df[df["Nome"].str.lower().str.contains(nome.strip().lower())]

def buscar_por_data(data):
    df = carregar_agendamentos()
    return df[df["Data"] == data]
