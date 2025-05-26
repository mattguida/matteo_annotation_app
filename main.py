from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client

import os
import json
import random
import uuid
from pathlib import Path
import datetime

app = FastAPI()

# Supabase configuration
SUPABASE_URL = "https://btkqbbtcxbvdxtojmrhn.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJ0a3FiYnRjeGJ2ZHh0b2ptcmhuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDgxNzQ3MjksImV4cCI6MjA2Mzc1MDcyOX0.T-Ay_5X2U_9dnG4dtrarj85BadwHD5fGrlCA7Hpz2Og"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# === Directories ===x
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "templates", "static")

# === Configuration ===
SENTENCES_PER_ANNOTATOR = 10
OVERLAP_PERCENTAGE = 0.2
OVERLAP_COUNT = int(SENTENCES_PER_ANNOTATOR * OVERLAP_PERCENTAGE)  # 10 sentences
UNIQUE_COUNT = SENTENCES_PER_ANNOTATOR - OVERLAP_COUNT  # 40 sentences

# === Mount static files ===
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# === Enable CORS for frontend/backend separation ===
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_all_sentences():
    """Load all sentences from the main data file"""
    input_path = os.path.join(BASE_DIR, "templates", "data", "sample_sfu_combined.jsonl")
    try:
        with open(input_path) as f:
            sentences = [json.loads(line) for line in f]
        return sentences
    except Exception as e:
        print(f"Error loading sentences: {e}")
        return []

def get_or_create_overlap_sentences():
    """Get the fixed overlap sentences that all annotators will share"""
    try:
        # Check if overlap sentences exist in Supabase
        result = supabase.table("overlap_sentences").select("*").execute()
        
        if result.data:
            return result.data[0]["sentences"]
        
        # Create new overlap sentences if none exist
        all_sentences = load_all_sentences()
        if len(all_sentences) < OVERLAP_COUNT:
            return []
        
        overlap_sentences = random.sample(all_sentences, OVERLAP_COUNT)
        
        # Save overlap sentences to Supabase
        supabase.table("overlap_sentences").insert({
            "id": 1,  # Use fixed ID since we only need one set
            "sentences": overlap_sentences,
            "created_at": datetime.datetime.now().isoformat()
        }).execute()
        
        return overlap_sentences
        
    except Exception as e:
        print(f"Error handling overlap sentences: {e}")
        return []

def get_used_sentences():
    """Get all sentences that have already been assigned to previous annotators"""
    try:
        # Get all unique sentences from annotator sessions
        result = supabase.table("annotator_sessions").select("unique_sentences").execute()
        
        used_sentences = set()
        for session in result.data:
            for sentence in session.get("unique_sentences", []):
                sentence_key = json.dumps(sentence, sort_keys=True)
                used_sentences.add(sentence_key)
        
        return used_sentences
        
    except Exception as e:
        print(f"Error getting used sentences: {e}")
        return set()

def create_annotator_dataset(annotator_id):
    """Create a dataset for a specific annotator"""
    all_sentences = load_all_sentences()
    if len(all_sentences) < SENTENCES_PER_ANNOTATOR:
        return None, f"Not enough sentences in dataset. Need {SENTENCES_PER_ANNOTATOR}, have {len(all_sentences)}"
    
    # Get the fixed overlap sentences
    overlap_sentences = get_or_create_overlap_sentences()
    if len(overlap_sentences) != OVERLAP_COUNT:
        return None, f"Could not create overlap sentences. Expected {OVERLAP_COUNT}, got {len(overlap_sentences)}"
    
    # Get sentences that aren't part of the overlap
    non_overlap_sentences = [s for s in all_sentences if s not in overlap_sentences]
    
    # Get already used unique sentences
    used_sentences = get_used_sentences()
    
    # Find available unique sentences (not used by previous annotators)
    available_unique = []
    for sentence in non_overlap_sentences:
        sentence_key = json.dumps(sentence, sort_keys=True)
        if sentence_key not in used_sentences:
            available_unique.append(sentence)
    
    if len(available_unique) < UNIQUE_COUNT:
        return None, f"Not enough unique sentences available. Need {UNIQUE_COUNT}, have {len(available_unique)}"
    
    # Select unique sentences for this annotator
    unique_sentences = random.sample(available_unique, UNIQUE_COUNT)
    
    # Combine overlap and unique sentences
    annotator_dataset = overlap_sentences + unique_sentences
    random.shuffle(annotator_dataset)
    
    # Add metadata
    for i, sentence in enumerate(annotator_dataset):
        sentence['sentence_id'] = f"{annotator_id}_sent{i+1}"
        sentence['is_overlap'] = sentence in overlap_sentences
    
    # Save session data to Supabase
    try:
        supabase.table("annotator_sessions").insert({
            "annotator_id": annotator_id,
            "total_sentences": len(annotator_dataset),
            "overlap_sentences": overlap_sentences,
            "unique_sentences": unique_sentences,
            "dataset": annotator_dataset,
            "created_at": datetime.datetime.now().isoformat()
        }).execute()
        
    except Exception as e:
        return None, f"Error saving session data: {e}"
    
    return annotator_dataset, None

# === Start annotation session - Create dynamic dataset ===
@app.get("/start_annotation")
def start_annotation(annotator_name: str = Query(None)):
    """Start a new annotation session with optional annotator name"""
    annotator_id = str(uuid.uuid4())
    
    # Create dataset for this annotator
    dataset, error = create_annotator_dataset(annotator_id)
    
    if error:
        return {"error": error}
    
    # Prepare response data
    response_data = {
        "annotator_id": annotator_id,
        "total_sentences": len(dataset),
        "overlap_sentences": OVERLAP_COUNT,
        "unique_sentences": UNIQUE_COUNT,
        "message": f"Dataset created with {len(dataset)} sentences"
    }
    
    # Add name to response if provided
    if annotator_name:
        response_data["annotator_name"] = annotator_name
    
    # Save session data
    session_data = {
        "annotator_id": annotator_id,
        "total_sentences": len(dataset),
        "overlap_sentences": OVERLAP_COUNT,
        "unique_sentences": UNIQUE_COUNT,
        "dataset": dataset,
        "created_at": datetime.datetime.now().isoformat()
    }
    
    if annotator_name:
        session_data["annotator_name"] = annotator_name
    
    try:
        supabase.table("annotator_sessions").insert(session_data).execute()
    except Exception as e:
        return {"error": f"Error saving session data: {e}"}
    
    return response_data

# === Serve sentences for specific annotator ===
@app.get("/api/sentences")
def get_sentences(annotator_id: str = Query(..., description="Annotator ID")):
    """
    Get sentences for a specific annotator.
    """
    try:
        result = supabase.table("annotator_sessions").select("dataset").eq("annotator_id", annotator_id).execute()
        
        if not result.data:
            return {"error": f"No session found for annotator {annotator_id}"}
        
        # Return as an object with dataset field to match frontend expectations
        return {"dataset": result.data[0]["dataset"]}
        
    except Exception as e:
        return {"error": f"Failed to load dataset: {str(e)}"}

# === Save individual annotation ===@app.post("/api/save_annotation")
async def save_annotation(payload: dict):
    """Save a single annotation with annotator name"""
    annotator_id = payload.get("annotator_id")
    sentence = payload.get("sentence")
    label = payload.get("label")
    annotator_name = payload.get("annotator_name")

    if not all([annotator_id, sentence, label is not None]):
        return {"error": "Missing required fields: annotator_id, sentence, label"}

    # Create annotation record
    annotation_record = {
        "annotator_id": annotator_id,
        "annotator_name": annotator_name,
        "sentence": sentence,
        "label": label,
        "timestamp": datetime.datetime.now().isoformat()
    }

    try:
        supabase.table("annotations").insert(annotation_record).execute()
    except Exception as e:
        return {"error": f"Failed to save annotation: {str(e)}"}

    return {"status": "saved", "annotator_id": annotator_id}

# === Get annotation statistics ===
@app.get("/api/stats")
def get_annotation_stats():
    """
    Get statistics about annotations collected so far.
    """
    try:
        # Get total annotators
        annotators_result = supabase.table("annotator_sessions").select("annotator_id").execute()
        total_annotators = len(annotators_result.data)
        
        # Get total annotations
        annotations_result = supabase.table("annotations").select("id").execute()
        total_annotations = len(annotations_result.data)
        
        return {
            "total_annotators": total_annotators,
            "total_annotations": total_annotations,
            "annotations_per_annotator": total_annotations / total_annotators if total_annotators > 0 else 0,
            "sentences_per_annotator": SENTENCES_PER_ANNOTATOR,
            "overlap_percentage": OVERLAP_PERCENTAGE
        }
        
    except Exception as e:
        return {"error": f"Failed to get stats: {str(e)}"}

# === Export all annotations ===
@app.get("/api/export_annotations")
def export_annotations():
    """
    Export all annotations from Supabase for analysis.
    """
    try:
        result = supabase.table("annotations").select("*").execute()
        
        return {
            "total_annotations": len(result.data),
            "annotations": result.data
        }
        
    except Exception as e:
        return {"error": f"Failed to export annotations: {str(e)}"}

# === Reset overlap sentences (admin function) ===
@app.post("/admin/reset_overlap")
def reset_overlap_sentences():
    """Reset the overlap sentences (use carefully!)"""
    try:
        supabase.table("overlap_sentences").delete().eq("id", 1).execute()
        return {"message": "Overlap sentences reset. Next annotator will create new overlap set."}
        
    except Exception as e:
        return {"error": f"Failed to reset overlap sentences: {str(e)}"}

# === Get system info ===
@app.get("/api/system_info")
def get_system_info():
    """Get information about the annotation system configuration"""
    try:
        all_sentences = load_all_sentences()
        overlap_sentences = get_or_create_overlap_sentences()
        used_sentences = get_used_sentences()
        
        remaining_unique = len(all_sentences) - len(overlap_sentences) - len(used_sentences)
        max_additional_annotators = remaining_unique // UNIQUE_COUNT
        
        return {
            "total_sentences_in_dataset": len(all_sentences),
            "sentences_per_annotator": SENTENCES_PER_ANNOTATOR,
            "overlap_sentences": OVERLAP_COUNT,
            "unique_sentences_per_annotator": UNIQUE_COUNT,
            "overlap_percentage": OVERLAP_PERCENTAGE,
            "sentences_already_used": len(used_sentences),
            "remaining_unique_sentences": remaining_unique,
            "max_additional_annotators": max_additional_annotators
        }
        
    except Exception as e:
        return {"error": f"Failed to get system info: {str(e)}"}

# === Serve static pages ===
@app.get("/")
def instructions():
    """Serve instructions page"""
    instructions_path = os.path.join(STATIC_DIR, "instructions.html")

    if os.path.exists(instructions_path):
        return FileResponse(instructions_path, media_type='text/html')
    return {"message": "Instructions file not found."}

@app.get("/annotate")
def annotation_interface():
    """Serve annotation interface page"""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Index file not found."}

# === Health check endpoint ===
@app.get("/health")
def health_check():
    """Basic health check"""
    return {"status": "healthy", "message": "Annotation system is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)