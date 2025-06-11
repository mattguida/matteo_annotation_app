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

# === Directories ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "templates", "static")
DATA_DIR = os.path.join(BASE_DIR, "templates", "data")

# === Configuration ===
SENTENCES_PER_ANNOTATOR = 100
SESSIONS = [1, 2, 3]
ANNOTATORS_PER_SESSION = 2

# === Mount static files ===
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# === Enable CORS ===
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_session_sentences(session_id):
    """Load sentences from a specific session file"""
    session_file = os.path.join(DATA_DIR, f"session_{session_id}.jsonl")
    try:
        with open(session_file, 'r', encoding='utf-8') as f:
            sentences = [json.loads(line) for line in f]
        return sentences
    except Exception as e:
        print(f"Error loading session {session_id}: {e}")
        return []

def get_session_assignment_info(session_id):
    """Get information about annotator assignments for a session"""
    try:
        result = supabase.table("session_assignments").select("*").eq("session_id", session_id).execute()
        return result.data
    except Exception as e:
        print(f"Error getting session assignments: {e}")
        return []

def assign_annotator_to_session(session_id, annotator_id, annotator_name, position):
    """Assign an annotator to a specific position in a session"""
    # Load all sentences for this session
    all_session_sentences = load_session_sentences(session_id)
    if not all_session_sentences:
        return None, f"Could not load sentences for session {session_id}"
    
    if len(all_session_sentences) != 200:
        return None, f"Expected 200 sentences in session {session_id}, got {len(all_session_sentences)}"
    
    # Split sentences: first 20 are overlap, remaining 180 are unique
    overlap_sentences = all_session_sentences[:20]  # These 20 are shared by both annotators
    unique_sentences = all_session_sentences[20:]   # These 180 will be split
    
    # Split unique sentences between annotators
    if position == 1:
        annotator_unique = unique_sentences[:90]  # First 90 for annotator 1
    else:
        annotator_unique = unique_sentences[90:]  # Last 90 for annotator 2
    
    # Combine overlap and unique sentences for this annotator
    annotator_dataset = overlap_sentences + annotator_unique
    random.shuffle(annotator_dataset)
    
    # Add metadata
    for i, sentence in enumerate(annotator_dataset):
        sentence['sentence_id'] = f"s{session_id}_a{position}_sent{i+1}"
        sentence['is_overlap'] = sentence in overlap_sentences
        sentence['session_id'] = session_id
        sentence['annotator_position'] = position
    
    # Save assignment to database
    try:
        supabase.table("session_assignments").insert({
            "session_id": session_id,
            "annotator_id": annotator_id,
            "annotator_name": annotator_name,
            "annotator_position": position,
            "dataset": annotator_dataset,
            "overlap_sentences": overlap_sentences,
            "unique_sentences": annotator_unique,
            "total_sentences": len(annotator_dataset),
            "created_at": datetime.datetime.now().isoformat()
        }).execute()
    except Exception as e:
        return None, f"Error saving assignment: {e}"
    
    return annotator_dataset, None

@app.get("/start_annotation")
def start_annotation(
    name: str = Query(..., description="Annotator's name"),
    session_id: int = Query(..., description="Session ID (1, 2, or 3)")
):
    """Start annotation for a specific session"""
    if session_id not in SESSIONS:
        return {"error": f"Invalid session ID. Must be one of: {SESSIONS}"}
    
    print(f"Starting annotation for {name} in session {session_id}")
    annotator_id = str(uuid.uuid4())
    
    # Check current assignments for this session
    current_assignments = get_session_assignment_info(session_id)
    assigned_positions = [a['annotator_position'] for a in current_assignments]
    
    if len(assigned_positions) >= ANNOTATORS_PER_SESSION:
        return {"error": f"Session {session_id} is full. Both annotator positions are taken."}
    
    # Determine position for this annotator
    if 1 not in assigned_positions:
        position = 1
    elif 2 not in assigned_positions:
        position = 2
    else:
        return {"error": f"Session {session_id} is full"}
    
    # Assign annotator to session
    dataset, error = assign_annotator_to_session(session_id, annotator_id, name, position)
    
    if error:
        print(f"Error assigning annotator: {error}")
        return {"error": error}
    
    response = {
        "annotator_id": annotator_id,
        "annotator_name": name,
        "session_id": session_id,
        "annotator_position": position,
        "total_sentences": len(dataset),
        "overlap_sentences": 20,
        "unique_sentences": 90,
        "message": f"Assigned {name} to session {session_id}, position {position}"
    }
    print(f"Annotation session started: {response}")
    return response

@app.get("/api/sentences")
def get_sentences(annotator_id: str = Query(..., description="Annotator ID")):
    """Get sentences for a specific annotator"""
    print(f"Getting sentences for annotator {annotator_id}")
    try:
        result = supabase.table("session_assignments").select("dataset").eq("annotator_id", annotator_id).execute()
        
        if not result.data:
            print(f"No assignment found for annotator {annotator_id}")
            return {"error": f"No assignment found for annotator {annotator_id}"}
        
        dataset = result.data[0]["dataset"]
        print(f"Retrieved {len(dataset)} sentences for annotator {annotator_id}")
        return dataset
        
    except Exception as e:
        print(f"Error getting sentences: {str(e)}")
        return {"error": f"Failed to load dataset: {str(e)}"}

@app.post("/api/save_annotation")
async def save_annotation(payload: dict):
    """Save annotation"""
    annotator_id = payload.get("annotator_id")
    annotator_name = payload.get("annotator_name")
    sentence = payload.get("sentence")
    label = payload.get("label")
    
    if not all([annotator_id, annotator_name, sentence, label is not None]):
        missing_fields = []
        if not annotator_id: missing_fields.append("annotator_id")
        if not annotator_name: missing_fields.append("annotator_name")
        if not sentence: missing_fields.append("sentence")
        if label is None: missing_fields.append("label")
        
        error_msg = f"Missing required fields: {', '.join(missing_fields)}"
        return {"error": error_msg}
    
    try:
        # Get session info for this annotator
        session_result = supabase.table("session_assignments").select("session_id, annotator_position").eq("annotator_id", annotator_id).execute()
        session_info = session_result.data[0] if session_result.data else {}
        
        annotation_data = {
            "annotator_id": annotator_id,
            "annotator_name": annotator_name,
            "sentence": sentence,
            "label": label,
            "session_id": session_info.get("session_id"),
            "annotator_position": session_info.get("annotator_position"),
            "timestamp": datetime.datetime.now().isoformat()
        }
        
        # Check if annotation already exists
        existing_result = supabase.table("annotations")\
            .select("id")\
            .eq("annotator_id", annotator_id)\
            .eq("sentence", sentence)\
            .execute()
        
        if existing_result.data:
            supabase.table("annotations")\
                .update(annotation_data)\
                .eq("annotator_id", annotator_id)\
                .eq("sentence", sentence)\
                .execute()
            action = "updated"
        else:
            supabase.table("annotations").insert(annotation_data).execute()
            action = "created"
    
    except Exception as e:
        return {"error": f"Failed to save annotation: {str(e)}"}
    
    return {"status": "saved", "action": action}

@app.get("/api/session_status")
def get_session_status():
    """Get status of all sessions"""
    try:
        sessions_status = []
        for session_id in SESSIONS:
            assignments = get_session_assignment_info(session_id)
            
            # Count annotations for this session
            annotations_result = supabase.table("annotations").select("id").eq("session_id", session_id).execute()
            total_annotations = len(annotations_result.data)
            
            session_status = {
                "session_id": session_id,
                "annotators_assigned": len(assignments),
                "max_annotators": ANNOTATORS_PER_SESSION,
                "is_full": len(assignments) >= ANNOTATORS_PER_SESSION,
                "total_annotations": total_annotations,
                "expected_annotations": len(assignments) * SENTENCES_PER_ANNOTATOR,
                "annotators": [
                    {
                        "name": a["annotator_name"],
                        "position": a["annotator_position"],
                        "assigned_at": a["created_at"]
                    } for a in assignments
                ]
            }
            sessions_status.append(session_status)
        
        return {"sessions": sessions_status}
        
    except Exception as e:
        return {"error": f"Failed to get session status: {str(e)}"}

@app.get("/api/export_annotations")
def export_annotations():
    """Export all annotations"""
    try:
        result = supabase.table("annotations").select("*").execute()
        return {
            "total_annotations": len(result.data),
            "annotations": result.data
        }
    except Exception as e:
        return {"error": f"Failed to export annotations: {str(e)}"}

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
    """Serve annotation interface"""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Index file not found."}

@app.get("/health")
def health_check():
    """Health check"""
    return {"status": "healthy", "message": "Session-based annotation system running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)