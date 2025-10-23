# scripts_empresas/empresa1/ai_bot.py
import os
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.schema import HumanMessage, AIMessage
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableSequence

# Carrega .env local (em produção via Docker, variáveis já vêm do ambiente)
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
if OPENAI_API_KEY:
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY

class AIBot:
    def __init__(self, chroma_path='/app/chroma_data'):
        self.__chat = ChatOpenAI(model="gpt-4", temperature=0)
        self.__retriever = self.__build_retriever(chroma_path)

    def __build_retriever(self, chroma_path):
        embedding = OpenAIEmbeddings(model='text-embedding-3-small')
        vector_store = Chroma(
            persist_directory=chroma_path,
            embedding_function=embedding,
        )
        return vector_store.as_retriever(search_kwargs={"k": 30})

    def __build_messages(self, history_messages, question):
        messages = []
        for message in history_messages:
            try:
                message_class = HumanMessage if message.get("fromMe") else AIMessage
                messages.append(message_class(content=message.get("body")))
            except Exception as e:
                print("Erro ao processar mensagem do histórico:", e)
        messages.append(HumanMessage(content=question))
        return messages

    def run(self, history_messages, question, intencao):
        if intencao == "internet":
            contexto_geral = '''
                Você é um atendente virtual simpático e prestativo de uma operadora de internet. Sempre começe se indentificando como "Sou o Carlos, uma inteligencia artificial feita para te ajudar."

                Utilize caracteres especiais para mensagem mais atraentes visualmentes.
                
                Use linguagem simples, com emojis e mensagens divididas por linhas para ficar fácil de ler.

                Ajude o cliente a resolver problemas como conexão lenta, falta de sinal, ou dúvidas sobre a rede.

                Evite termos técnicos! Nunca oriente reset de fábrica no roteador.

                Se necessário, oriente a verificar cabos, reiniciar o roteador e ofereça suporte com bom humor e paciência 😊
                
                Em todo final de mensagem, coloque "Desenvolvido por Alpha-Dev's INC."
                '''
        elif intencao == "financeiro":
            contexto_geral = '''
            💳 Você é um atendente virtual para assuntos financeiros, como boletos e pagamentos.
            '''
        else:
            contexto_geral = '''
            🤖 Você é um assistente virtual geral. Se não tiver certeza da resposta, peça para o cliente falar com um atendente humano.
            '''

        SYSTEM_TEMPLATE = f'''
        {contexto_geral}

        Responda com base no contexto abaixo. Use linguagem natural, objetiva e acolhedora.
        Evite termos técnicos. Responda sempre em português brasileiro.

        <context>
        {{context}}
        </context>
        '''

        try:
            docs = self.__retriever.invoke(question)
            print(f"{len(docs)} documentos relevantes encontrados.")

            from langchain.chains.combine_documents import create_stuff_documents_chain
            question_answering_prompt = ChatPromptTemplate.from_messages(
                [
                    ('system', SYSTEM_TEMPLATE),
                    MessagesPlaceholder(variable_name='messages'),
                ]
            )
            document_chain = create_stuff_documents_chain(self.__chat, question_answering_prompt)

            response = document_chain.invoke({
                "context": docs,
                "messages": self.__build_messages(history_messages, question),
            })
            return response
        except Exception as e:
            print("Erro no processamento da IA:", e)
            return "⚠️ Desculpe, houve um erro ao processar sua solicitação."
