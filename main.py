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
SUPABASE_URL = "https://kfiwblhdbvqarwfbepan.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImtmaXdibGhkYnZxYXJ3ZmJlcGFuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDk2NzYxNTMsImV4cCI6MjA2NTI1MjE1M30.eb9v88eiOHetpVct-QOhcsDR9c99O7IcucDQ52C8APw"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# === Directories ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "templates", "static")

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

def load_session_data():
    """Load all sentences from annotation_session_1.jsonl"""
    file_path = os.path.join(BASE_DIR, "templates", "data", "annotation_session_1.jsonl")
    try:
        with open(file_path, 'r') as f:
            data = [json.loads(line) for line in f]
        return data
    except Exception as e:
        print(f"Error loading session: {e}")
        return []

def create_annotator_dataset(annotator_id, annotator_name):
    """Create a dataset for a specific annotator using all sentences from session file"""
    session_data = load_session_data()
    
    if not session_data:
        return None, "Could not load sentences from annotation_session_1.jsonl"

    # Add metadata to sentences
    annotator_dataset = []
    for i, sentence in enumerate(session_data):
        sentence_copy = sentence.copy()  # Don't modify original data
        sentence_copy['sentence_id'] = f"{annotator_id}_sent{i+1}"
        annotator_dataset.append(sentence_copy)

    # Save session data to Supabase
    try:
        supabase.table("annotator_sessions").insert({
            "annotator_id": annotator_id,
            "annotator_name": annotator_name,
            "total_sentences": len(annotator_dataset),
            "overlap_sentences": [],  # Empty since we're not using overlap logic
            "unique_sentences": annotator_dataset,  # All sentences are unique per annotator
            "dataset": annotator_dataset,
            "created_at": datetime.datetime.now().isoformat()
        }).execute()
    except Exception as e:
        return None, f"Error saving session data: {e}"

    return annotator_dataset, None

# === Start annotation session - Create dataset from session file ===
@app.get("/start_annotation")
def start_annotation(name: str = Query(..., description="Annotator's name")):
    print(f"Starting annotation for {name}")
    annotator_id = str(uuid.uuid4())

    # Create dataset for this annotator
    dataset, error = create_annotator_dataset(annotator_id, name)

    if error:
        print(f"Error creating dataset: {error}")
        return {"error": error}

    response = {
        "annotator_id": annotator_id,
        "annotator_name": name,
        "total_sentences": len(dataset),
        "message": f"Dataset created with {len(dataset)} sentences for {name}"
    }
    print(f"Annotation session started: {response}")
    return response

# === Serve sentences for specific annotator ===
@app.get("/api/sentences")
def get_sentences(annotator_id: str = Query(..., description="Annotator ID")):
    print(f"Getting sentences for annotator {annotator_id}")
    try:
        result = supabase.table("annotator_sessions").select("dataset").eq("annotator_id", annotator_id).execute()
        
        if not result.data:
            print(f"No session found for annotator {annotator_id}")
            return {"error": f"No session found for annotator {annotator_id}"}
        
        dataset = result.data[0]["dataset"]
        print(f"Retrieved {len(dataset)} sentences for annotator {annotator_id}")
        return dataset
        
    except Exception as e:
        print(f"Error getting sentences: {str(e)}")
        return {"error": f"Failed to load dataset: {str(e)}"}

# === Save individual annotation ===
@app.post("/api/save_annotation")
async def save_annotation(payload: dict):
    annotator_id = payload.get("annotator_id")
    annotator_name = payload.get("annotator_name")
    sentence = payload.get("sentence")
    label = payload.get("label")
    
    # Debug logging
    print(f"Received payload: annotator_id={annotator_id}, annotator_name={annotator_name}, sentence length={len(sentence) if sentence else 0}")
    
    if not all([annotator_id, annotator_name, sentence, label is not None]):
        missing_fields = []
        if not annotator_id: missing_fields.append("annotator_id")
        if not annotator_name: missing_fields.append("annotator_name")
        if not sentence: missing_fields.append("sentence")
        if label is None: missing_fields.append("label")
        
        error_msg = f"Missing required fields: {', '.join(missing_fields)}"
        print(f"Error: {error_msg}")
        return {"error": error_msg}
    
    try:
        # Check if annotation already exists for this annotator and sentence
        existing_result = supabase.table("annotations")\
            .select("id")\
            .eq("annotator_id", annotator_id)\
            .eq("sentence", sentence)\
            .execute()
        
        annotation_data = {
            "annotator_id": annotator_id,
            "annotator_name": annotator_name,
            "sentence": sentence,
            "label": label,
            "timestamp": datetime.datetime.now().isoformat()
        }
        
        if existing_result.data:
            # Update existing annotation
            supabase.table("annotations")\
                .update(annotation_data)\
                .eq("annotator_id", annotator_id)\
                .eq("sentence", sentence)\
                .execute()
            action = "updated"
        else:
            # Insert new annotation
            supabase.table("annotations").insert(annotation_data).execute()
            action = "created"
    
    except Exception as e:
        error_msg = f"Failed to save annotation: {str(e)}"
        print(f"Database error: {error_msg}")
        return {"error": error_msg}
    
    return {
        "status": "saved", 
        "action": action,
        "annotator_id": annotator_id, 
        "annotator_name": annotator_name
    }

# === Get annotation statistics ===
@app.get("/api/stats")
def get_annotation_stats():
    """Get statistics about annotations collected so far."""
    try:
        # Get total annotators
        annotators_result = supabase.table("annotator_sessions").select("annotator_id").execute()
        total_annotators = len(annotators_result.data)
        
        # Get total annotations
        annotations_result = supabase.table("annotations").select("id").execute()
        total_annotations = len(annotations_result.data)
        
        # Get sentences count from session file
        session_data = load_session_data()
        sentences_per_annotator = len(session_data)
        
        return {
            "total_annotators": total_annotators,
            "total_annotations": total_annotations,
            "annotations_per_annotator": total_annotations / total_annotators if total_annotators > 0 else 0,
            "sentences_per_annotator": sentences_per_annotator
        }
        
    except Exception as e:
        return {"error": f"Failed to get stats: {str(e)}"}

# === Export all annotations ===
@app.get("/api/export_annotations")
def export_annotations():
    """Export all annotations from Supabase for analysis."""
    try:
        result = supabase.table("annotations").select("*").execute()
        
        return {
            "total_annotations": len(result.data),
            "annotations": result.data
        }
        
    except Exception as e:
        return {"error": f"Failed to export annotations: {str(e)}"}

# === Get system info ===
@app.get("/api/system_info")
def get_system_info():
    """Get information about the annotation system configuration"""
    try:
        session_data = load_session_data()
        
        return {
            "total_sentences_in_dataset": len(session_data),
            "sentences_per_annotator": len(session_data),
            "source_file": "annotation_session_1.jsonl"
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
    uvicorn.run(app, host="0.0.0.0", port=8001)