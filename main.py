from fastapi import FastAPI, Query, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client

import os
import json
import random
import uuid
import datetime
from io import BytesIO, StringIO
from typing import Optional, List

app = FastAPI()

SUPABASE_URL = "https://btkqbbtcxbvdxtojmrhn.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJ0a3FiYnRjeGJ2ZHh0b2ptcmhuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDgxNzQ3MjksImV4cCI6MjA2Mzc1MDcyOX0.T-Ay_5X2U_9dnG4dtrarj85BadwHD5fGrlCA7Hpz2Og"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BUCKET_NAME = "annotation-db"

# === Config ===
SENTENCES_PER_ANNOTATOR = 50
OVERLAP_PERCENTAGE = 0.2
OVERLAP_COUNT = int(SENTENCES_PER_ANNOTATOR * OVERLAP_PERCENTAGE)  # 10 sentences
UNIQUE_COUNT = SENTENCES_PER_ANNOTATOR - OVERLAP_COUNT  # 40 sentences

# === Static files on local, you can keep this if you want ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "templates", "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# === Enable CORS ===
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, restrict this!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Helpers for Supabase Storage ---

def supabase_upload_json(bucket: str, path: str, data: dict):
    """Upload JSON data to Supabase Storage as UTF-8 encoded file."""
    content = json.dumps(data, indent=2).encode("utf-8")
    res = supabase.storage.from_(bucket).upload(path, BytesIO(content), {"content-type": "application/json"}, upsert=True)
    if res.get("error"):
        raise Exception(f"Supabase upload error: {res['error']}")

def supabase_download_json(bucket: str, path: str) -> Optional[dict]:
    """Download JSON file from Supabase Storage and parse it."""
    res = supabase.storage.from_(bucket).download(path)
    if res.get("error"):
        # File does not exist or other error
        return None
    content = res.get("data")
    if content is None:
        return None
    text = content.read().decode("utf-8")
    return json.loads(text)

def supabase_upload_jsonl(bucket: str, path: str, lines: List[dict], append: bool = False):
    """Upload JSONL lines to Supabase bucket.
    If append=True, download existing file, append lines, then re-upload."""
    existing_lines = []
    if append:
        existing_content = supabase_download_raw(bucket, path)
        if existing_content:
            existing_lines = existing_content.strip().split("\n")

    # Prepare new lines as JSONL strings
    new_lines = [json.dumps(line, ensure_ascii=False) for line in lines]
    all_lines = existing_lines + new_lines
    content = "\n".join(all_lines).encode("utf-8")

    res = supabase.storage.from_(bucket).upload(path, BytesIO(content), {"content-type": "application/jsonl"}, upsert=True)
    if res.get("error"):
        raise Exception(f"Supabase upload error: {res['error']}")

def supabase_download_raw(bucket: str, path: str) -> Optional[str]:
    """Download raw text file from Supabase Storage."""
    res = supabase.storage.from_(bucket).download(path)
    if res.get("error"):
        return None
    content = res.get("data")
    if content is None:
        return None
    text = content.read().decode("utf-8")
    return text

def list_files_in_bucket(bucket: str, prefix: str = "") -> List[str]:
    """List files in the bucket optionally filtered by prefix"""
    res = supabase.storage.from_(bucket).list(path=prefix)
    if res.get("error"):
        return []
    return [item["name"] for item in res.get("data", [])]

# === Load all sentences from local file (you can keep this locally if static) ===
def load_all_sentences():
    input_path = os.path.join(BASE_DIR, "templates", "data", "sample_sfu_combined.jsonl")
    try:
        with open(input_path) as f:
            sentences = [json.loads(line) for line in f]
        return sentences
    except Exception as e:
        print(f"Error loading sentences: {e}")
        return []

# === Overlap sentences stored in bucket as JSON ===
def get_or_create_overlap_sentences():
    overlap_path = "overlap_sentences.json"
    overlap_sentences = supabase_download_json(BUCKET_NAME, overlap_path)
    if overlap_sentences:
        return overlap_sentences

    # Create new overlap sentences
    all_sentences = load_all_sentences()
    if len(all_sentences) < OVERLAP_COUNT:
        return []

    overlap_sentences = random.sample(all_sentences, OVERLAP_COUNT)

    try:
        supabase_upload_json(BUCKET_NAME, overlap_path, overlap_sentences)
    except Exception as e:
        print(f"Error uploading overlap sentences: {e}")

    return overlap_sentences

# === Get all sentences used in sessions stored in bucket ===
def get_used_sentences():
    used_sentences = set()
    # List all session json files in the bucket
    session_files = list_files_in_bucket(BUCKET_NAME, prefix="sessions/")
    for session_file in session_files:
        if not session_file.endswith(".json"):
            continue
        session_data = supabase_download_json(BUCKET_NAME, session_file)
        if not session_data:
            continue
        for sentence in session_data.get("unique_sentences", []):
            sentence_key = json.dumps(sentence, sort_keys=True)
            used_sentences.add(sentence_key)
    return used_sentences

# === Create annotator dataset and save session to bucket ===
def create_annotator_dataset(annotator_id: str):
    all_sentences = load_all_sentences()
    if len(all_sentences) < SENTENCES_PER_ANNOTATOR:
        return None, f"Not enough sentences in dataset. Need {SENTENCES_PER_ANNOTATOR}, have {len(all_sentences)}"

    overlap_sentences = get_or_create_overlap_sentences()
    if len(overlap_sentences) != OVERLAP_COUNT:
        return None, f"Could not create overlap sentences. Expected {OVERLAP_COUNT}, got {len(overlap_sentences)}"

    non_overlap_sentences = [s for s in all_sentences if s not in overlap_sentences]

    used_sentences = get_used_sentences()

    available_unique = []
    for sentence in non_overlap_sentences:
        sentence_key = json.dumps(sentence, sort_keys=True)
        if sentence_key not in used_sentences:
            available_unique.append(sentence)

    if len(available_unique) < UNIQUE_COUNT:
        return None, f"Not enough unique sentences available. Need {UNIQUE_COUNT}, have {len(available_unique)}"

    unique_sentences = random.sample(available_unique, UNIQUE_COUNT)

    annotator_dataset = overlap_sentences + unique_sentences
    random.shuffle(annotator_dataset)

    for i, sentence in enumerate(annotator_dataset):
        sentence['sentence_id'] = f"{annotator_id}_sent{i+1}"
        sentence['is_overlap'] = sentence in overlap_sentences

    session_data = {
        "annotator_id": annotator_id,
        "total_sentences": len(annotator_dataset),
        "overlap_sentences": overlap_sentences,
        "unique_sentences": unique_sentences,
        "dataset": annotator_dataset
    }

    session_file_path = f"sessions/{annotator_id}.json"
    try:
        supabase_upload_json(BUCKET_NAME, session_file_path, session_data)
    except Exception as e:
        return None, f"Error saving session data: {e}"

    return annotator_dataset, None

# === Routes ===

@app.get("/start_annotation")
def start_annotation():
    annotator_id = str(uuid.uuid4())
    dataset, error = create_annotator_dataset(annotator_id)
    if error:
        return {"error": error}
    return {
        "annotator_id": annotator_id,
        "total_sentences": len(dataset),
        "overlap_sentences": OVERLAP_COUNT,
        "unique_sentences": UNIQUE_COUNT,
        "dataset": dataset
    }

@app.post("/save_annotation")
async def save_annotation(annotator_id: str = Query(...), annotations: List[dict] = None):
    if annotations is None:
        return {"error": "No annotations provided"}

    # Load existing annotations or create new list
    annotations_path = f"annotations/{annotator_id}.jsonl"

    try:
        existing_content = supabase_download_raw(BUCKET_NAME, annotations_path)
    except Exception as e:
        existing_content = None

    existing_lines = existing_content.strip().split("\n") if existing_content else []
    new_lines = [json.dumps(a, ensure_ascii=False) for a in annotations]
    all_lines = existing_lines + new_lines

    content = "\n".join(all_lines).encode("utf-8")

    res = supabase.storage.from_(BUCKET_NAME).upload(annotations_path, BytesIO(content), {"content-type": "application/jsonl"}, upsert=True)
    if res.get("error"):
        return {"error": f"Failed to save annotations: {res['error']}"}

    return {"message": "Annotations saved successfully"}

@app.get("/annotations/{annotator_id}")
def get_annotations(annotator_id: str):
    annotations_path = f"annotations/{annotator_id}.jsonl"
    content = supabase_download_raw(BUCKET_NAME, annotations_path)
    if not content:
        return {"error": "No annotations found"}
    return {"annotations": content}

@app.get("/overlap_sentences")
def get_overlap_sentences():
    overlap_sentences = get_or_create_overlap_sentences()
    return {"overlap_sentences": overlap_sentences}

@app.get("/")
def root():
    return {"message": "Annotation API running"}

# If you want to serve your frontend index.html, do like this:
# @app.get("/")
# async def serve_frontend():
#     return FileResponse(os.path.join(BASE_DIR, "templates", "index.html"))
