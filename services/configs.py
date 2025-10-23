# services/configs.py
import os, json

_EMPRESAS = None

def load_empresas():
    global _EMPRESAS
    if _EMPRESAS is None:
        with open("config/empresas_config.json", "r", encoding="utf-8") as f:
            _EMPRESAS = json.load(f)
    return _EMPRESAS

def get_empresa_config(empresa_id: str) -> dict:
    empresas = load_empresas()
    if empresa_id not in empresas:
        raise KeyError(f"Empresa não configurada: {empresa_id}")
    return empresas[empresa_id]

def get_base_url() -> str:
    base = os.getenv("BASE_URL")
    return base.rstrip("/") if base else "http://localhost:5000"
