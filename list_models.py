import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

groq_key = os.getenv("GROQ_API_KEY")
if not groq_key:
    print("Error: GROQ_API_KEY not found in .env")
    exit(1)

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=groq_key.strip(),
)

try:
    models = client.models.list()
    print("\n--- Available models for your Groq API key ---")
    for m in sorted(models.data, key=lambda x: x.id):
        print(f"  • {m.id}")
    print("----------------------------------------------\n")
except Exception as e:
    print(f"Error fetching models from Groq: {e}")
