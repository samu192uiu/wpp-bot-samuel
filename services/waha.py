# services/waha.py
import os
import requests
from typing import Optional

class Waha:
    """
    Cliente minimalista para WAHA.
    - send_message(chat_id, text): envia texto.
    - send_image_base64(chat_id, base64_str, filename='img.png', caption=None): envia imagem (ex.: QR em base64).
    - get_history_messages(chat_id, limit=50): histórico.
    - start_typing(chat_id) / stop_typing(chat_id): indicador de digitação.
    """

    def __init__(self, base_url: str, session: str = "default", api_key: Optional[str] = None):
        self.__api_url = (base_url or "").rstrip("/")
        self.__session = session
        self.__headers = {"Content-Type": "application/json"}

        # Se você proteger o WAHA com WAHA_API_KEY, envie o header
        self.__api_key = api_key or os.getenv("WAHA_API_KEY", "")
        if self.__api_key:
            self.__headers["WAHA-API-KEY"] = self.__api_key

        # Tentativa de health-check (não crítico)
        try:
            r = requests.get(f"{self.__api_url}/api/version", headers=self.__headers, timeout=5)
            print("[WAHA] health:", r.status_code, str(r.text)[:200])
        except Exception as e:
            print("[WAHA] Erro ao verificar health:", repr(e))

    # ----------------------
    # Envios básicos
    # ----------------------
    def send_message(self, chat_id: str, message: str):
        url = f"{self.__api_url}/api/sendText"
        payload = {"session": self.__session, "chatId": chat_id, "text": message}
        try:
            resp = requests.post(url, json=payload, headers=self.__headers, timeout=10)
            if resp.status_code >= 400:
                print("[WAHA] send_message erro:", resp.status_code, str(resp.text)[:300])
        except Exception as e:
            print("[WAHA] Erro send_message:", repr(e))

    def send_image_base64(
        self,
        chat_id: str,
        base64_str: str,
        filename: str = "image.png",
        caption: Optional[str] = None
    ):
        """
        Envia uma imagem em base64.
        - Tenta /api/sendFile com data URI (file: {name, data})
        - Se falhar, tenta /api/sendImage com campo 'base64'
        - Se ainda falhar, faz fallback para uma mensagem de texto
        Obs.: Aceita tanto string "pura" base64 quanto "data:image/png;base64,XXXX..."
        """
        if not base64_str:
            self.send_message(chat_id, "⚠️ Não foi possível anexar a imagem no momento.")
            return

        # Garante o prefixo data URI se vier só o base64 puro
        if base64_str.startswith("data:image"):
            data_uri = base64_str
            # extrai a parte base64 pura para o /sendImage
            try:
                base64_puro = base64_str.split(",", 1)[1]
            except Exception:
                base64_puro = base64_str
        else:
            data_uri = f"data:image/png;base64,{base64_str}"
            base64_puro = base64_str

        # 1) Tenta /api/sendFile (formato mais flexível nas versões recentes)
        url_file = f"{self.__api_url}/api/sendFile"
        payload_file = {
            "session": self.__session,
            "chatId": chat_id,
            "file": {
                "name": filename,
                "data": data_uri
            }
        }
        if caption:
            payload_file["caption"] = caption

        try:
            resp = requests.post(url_file, json=payload_file, headers=self.__headers, timeout=15)
            if 200 <= resp.status_code < 300:
                return
            else:
                print("[WAHA] send_image_base64 (/sendFile) falhou:", resp.status_code, str(resp.text)[:300])
        except Exception as e:
            print("[WAHA] Exceção /sendFile:", repr(e))

        # 2) Tenta /api/sendImage (algumas versões usam 'base64' diretamente)
        url_img = f"{self.__api_url}/api/sendImage"
        payload_img = {
            "session": self.__session,
            "chatId": chat_id,
            "base64": base64_puro
        }
        if caption:
            payload_img["caption"] = caption

        try:
            resp = requests.post(url_img, json=payload_img, headers=self.__headers, timeout=15)
            if 200 <= resp.status_code < 300:
                return
            else:
                print("[WAHA] send_image_base64 (/sendImage) falhou:", resp.status_code, str(resp.text)[:300])
        except Exception as e:
            print("[WAHA] Exceção /sendImage:", repr(e))

        # 3) Fallback: texto
        self.send_message(chat_id, "⚠️ Não consegui enviar a imagem agora. Tente novamente daqui a pouco.")

    # ----------------------
    # Utilidades
    # ----------------------
    def get_history_messages(self, chat_id: str, limit: int = 50):
        # Rota comum (ajuste conforme sua versão do WAHA)
        url = f"{self.__api_url}/api/{self.__session}/chats/{chat_id}/messages"
        try:
            resp = requests.get(url, params={"limit": limit, "downloadMedia": "false"},
                                headers=self.__headers, timeout=10)
            if resp.status_code >= 400:
                print("[WAHA] get_history_messages erro:", resp.status_code, str(resp.text)[:300])
                return []
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            print("[WAHA] Erro get_history_messages:", repr(e))
            return []

    def start_typing(self, chat_id: str):
        url = f"{self.__api_url}/api/startTyping"
        payload = {"session": self.__session, "chatId": chat_id}
        try:
            requests.post(url, json=payload, headers=self.__headers, timeout=5)
        except Exception as e:
            print("[WAHA] Erro start_typing:", repr(e))

    def stop_typing(self, chat_id: str):
        url = f"{self.__api_url}/api/stopTyping"
        payload = {"session": self.__session, "chatId": chat_id}
        try:
            requests.post(url, json=payload, headers=self.__headers, timeout=5)
        except Exception as e:
            print("[WAHA] Erro stop_typing:", repr(e))
