import os
from decouple import config
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_community.document_loaders import PyPDFLoader 

# Define a chave da API
os.environ['OPENAI_API_KEY'] = config('OPENAI_API_KEY')

if __name__ == '__main__':
    file_path = '/app/rag/data/teste.pdf'
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Arquivo PDF não encontrado: {file_path}")

    loader = PyPDFLoader(file_path)
    docs = loader.load()
    print(f"{len(docs)} páginas carregadas do PDF.")

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = text_splitter.split_documents(documents=docs)
    print(f"{len(chunks)} chunks gerados.")

    persist_directory = '/app/chroma_data'

    # Corrige erro de criação de diretório já existente
    if os.path.exists(persist_directory) and not os.path.isdir(persist_directory):
        os.remove(persist_directory)
    os.makedirs(persist_directory, exist_ok=True)

    embedding = OpenAIEmbeddings()

    vector_store = Chroma(
        embedding_function=embedding,
        persist_directory=persist_directory,
    )
    vector_store.add_documents(documents=chunks)

    print("Base vetorial criada com sucesso!")
