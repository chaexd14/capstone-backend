import os
import json
import numpy as np
from dotenv import load_dotenv

load_dotenv('.env')
from google import genai
from google.genai import types

client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

VERIFY_SCHEMA = {
    'type': 'object',
    'properties': {
        'verdict': {'type': 'string', 'enum': ['direct', 'partial', 'none']},
        'reason': {'type': 'string'},
    },
    'required': ['verdict', 'reason'],
}

def verify_sentence(skill_label, question, sentence):
    prompt = f"""You are verifying resume evidence.
Skill: {skill_label}
Question: {question}
Sentence: "{sentence}"

Answer with JSON only:
{{"verdict": "direct" | "partial" | "none", "reason": "<short>"}}

direct  = the candidate clearly performed or built this.
partial = related hands-on work, but not the skill itself (for example testing an API when the skill is building one).
none    = only mentions, studies, attends, or is unrelated.
Do not infer anything about the person. Judge only the sentence."""

    resp = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            seed=42,
            response_mime_type='application/json',
            response_schema=VERIFY_SCHEMA,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    return json.loads(resp.text)


def embed_texts(texts, dims=768):
    res = client.models.embed_content(
        model='gemini-embedding-001',
        contents=texts,
        config=types.EmbedContentConfig(task_type='SEMANTIC_SIMILARITY', output_dimensionality=dims),
    )
    vecs = np.array([e.values for e in res.embeddings], dtype="float32")
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)

if __name__ == "__main__":
    print("Testing Embedding...")
    queries = ["built and maintained REST APIs", "developed HTTP endpoints returning JSON"]
    sentences = [
        "Designed and maintained 12 REST APIs using FastAPI and Django",
        "Tested APIs manually with Postman",
        "Attended a seminar about containers"
    ]
    q_vecs = embed_texts(queries)
    s_vecs = embed_texts(sentences)
    sims = q_vecs @ s_vecs.T
    print("Cosine similarities:")
    for i, q in enumerate(queries):
        for j, s in enumerate(sentences):
            print(f"  Q: '{q}' vs S: '{s}' -> Sim: {sims[i, j]:.4f}")

    print("\nTesting Verification Gate:")
    v1 = verify_sentence("REST API development", "Does this sentence say the candidate built or developed APIs or web services?", sentences[0])
    print("Sentence 0 (Designed & maintained APIs):", v1)
    v2 = verify_sentence("REST API development", "Does this sentence say the candidate built or developed APIs or web services?", sentences[1])
    print("Sentence 1 (Tested APIs manually with Postman):", v2)
    v3 = verify_sentence("Docker / containerization", "Does this sentence say the candidate hands-on used or built containers?", sentences[2])
    print("Sentence 2 (Attended a seminar about containers):", v3)
