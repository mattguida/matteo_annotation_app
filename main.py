from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import json
import random
import uuid
from pathlib import Path

app = FastAPI()

# === Directories ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATIC_DIR = os.path.join(BASE_DIR, "templates", "static")
USER_DATA_DIR = os.path.join(BASE_DIR, "user_data")
ANNOTATIONS_DIR = os.path.join(USER_DATA_DIR, "annotations")
SESSIONS_DIR = os.path.join(USER_DATA_DIR, "sessions")

os.makedirs(ANNOTATIONS_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)

# === Configuration ===
SENTENCES_PER_ANNOTATOR = 50
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
    input_path = "/Users/guida/annotation_app/templates/data/sample_sfu_combined.jsonl"
    try:
        with open(input_path) as f:
            sentences = [json.loads(line) for line in f]
        return sentences
    except Exception as e:
        print(f"Error loading sentences: {e}")
        return []

def get_or_create_overlap_sentences():
    """Get the fixed overlap sentences that all annotators will share"""
    overlap_file = os.path.join(USER_DATA_DIR, "overlap_sentences.json")
    
    if os.path.exists(overlap_file):
        # Load existing overlap sentences
        try:
            with open(overlap_file, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading overlap sentences: {e}")
    
    # Create new overlap sentences
    all_sentences = load_all_sentences()
    if len(all_sentences) < OVERLAP_COUNT:
        return []
    
    overlap_sentences = random.sample(all_sentences, OVERLAP_COUNT)
    
    # Save overlap sentences for consistency
    try:
        with open(overlap_file, "w") as f:
            json.dump(overlap_sentences, f, indent=2)
    except Exception as e:
        print(f"Error saving overlap sentences: {e}")
    
    return overlap_sentences

def get_used_sentences():
    """Get all sentences that have already been assigned to previous annotators"""
    used_sentences = set()
    session_files = list(Path(SESSIONS_DIR).glob("*.json"))
    
    for session_file in session_files:
        try:
            with open(session_file, "r") as f:
                session_data = json.load(f)
                for sentence in session_data.get("unique_sentences", []):
                    # Create a unique identifier for each sentence
                    sentence_key = json.dumps(sentence, sort_keys=True)
                    used_sentences.add(sentence_key)
        except Exception as e:
            print(f"Error reading session file {session_file}: {e}")
    
    return used_sentences

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
    
    # Save session data
    session_data = {
        "annotator_id": annotator_id,
        "total_sentences": len(annotator_dataset),
        "overlap_sentences": overlap_sentences,
        "unique_sentences": unique_sentences,
        "dataset": annotator_dataset
    }
    
    session_file = os.path.join(SESSIONS_DIR, f"{annotator_id}.json")
    try:
        with open(session_file, "w") as f:
            json.dump(session_data, f, indent=2)
    except Exception as e:
        return None, f"Error saving session data: {e}"
    
    return annotator_dataset, None

# === Start annotation session - Create dynamic dataset ===
@app.get("/start_annotation")
def start_annotation():
    """
    Start a new annotation session:
    - Generate unique annotator ID
    - Create dataset with fixed overlap + unique sentences
    - Return session info
    """
    annotator_id = str(uuid.uuid4())
    
    # Create dataset for this annotator
    dataset, error = create_annotator_dataset(annotator_id)
    
    if error:
        return {"error": error}
    
    return {
        "annotator_id": annotator_id,
        "total_sentences": len(dataset),
        "overlap_sentences": OVERLAP_COUNT,
        "unique_sentences": UNIQUE_COUNT,
        "message": f"Dataset created with {len(dataset)} sentences"
    }

# === Serve sentences for specific annotator ===
@app.get("/api/sentences")
def get_sentences(annotator_id: str = Query(..., description="Annotator ID")):
    """
    Get sentences for a specific annotator.
    """
    session_file = os.path.join(SESSIONS_DIR, f"{annotator_id}.json")
    if not os.path.exists(session_file):
        return {"error": f"No session found for annotator {annotator_id}"}
    
    try:
        with open(session_file, "r") as f:
            session_data = json.load(f)
        return session_data["dataset"]
    except Exception as e:
        return {"error": f"Failed to load dataset: {str(e)}"}

# === Save individual annotation ===
@app.post("/api/save_annotation")
async def save_annotation(payload: dict):
    """
    Save a single annotation.
    Expected payload:
    {
        "annotator_id": "uuid",
        "sentence": "text of sentence",
        "label": {"Economic": true, "Health_Safety": false, ...}
    }
    """
    annotator_id = payload.get("annotator_id")
    sentence = payload.get("sentence")
    label = payload.get("label")
    
    if not all([annotator_id, sentence, label is not None]):
        return {"error": "Missing required fields: annotator_id, sentence, label"}

    # Create annotation record with timestamp
    import datetime
    annotation_record = {
        "annotator_id": annotator_id,
        "sentence": sentence,
        "label": label,
        "timestamp": datetime.datetime.now().isoformat()
    }
    
    # Save to annotator-specific file
    out_path = os.path.join(ANNOTATIONS_DIR, f"{annotator_id}.jsonl")
    try:
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(annotation_record, ensure_ascii=False) + "\n")
    except Exception as e:
        return {"error": f"Failed to save annotation: {str(e)}"}

    return {"status": "saved", "annotator_id": annotator_id}

# === Get annotation statistics ===
@app.get("/api/stats")
def get_annotation_stats():
    """
    Get statistics about annotations collected so far.
    """
    annotation_files = list(Path(ANNOTATIONS_DIR).glob("*.jsonl"))
    
    total_annotators = len(annotation_files)
    total_annotations = 0
    
    for file in annotation_files:
        try:
            with open(file, "r") as f:
                lines = f.readlines()
                total_annotations += len(lines)
        except:
            continue
    
    return {
        "total_annotators": total_annotators,
        "total_annotations": total_annotations,
        "annotations_per_annotator": total_annotations / total_annotators if total_annotators > 0 else 0,
        "sentences_per_annotator": SENTENCES_PER_ANNOTATOR,
        "overlap_percentage": OVERLAP_PERCENTAGE
    }

# === Export all annotations ===
@app.get("/api/export_annotations")
def export_annotations():
    """
    Export all annotations in a single JSON file for analysis.
    """
    annotation_files = list(Path(ANNOTATIONS_DIR).glob("*.jsonl"))
    all_annotations = []
    
    for file in annotation_files:
        try:
            with open(file, "r") as f:
                for line in f:
                    annotation = json.loads(line.strip())
                    all_annotations.append(annotation)
        except Exception as e:
            print(f"Error reading {file}: {e}")
            continue
    
    return {
        "total_annotations": len(all_annotations),
        "annotations": all_annotations
    }

# === Reset overlap sentences (admin function) ===
@app.post("/admin/reset_overlap")
def reset_overlap_sentences():
    """Reset the overlap sentences (use carefully!)"""
    overlap_file = os.path.join(USER_DATA_DIR, "overlap_sentences.json")
    if os.path.exists(overlap_file):
        os.remove(overlap_file)
    return {"message": "Overlap sentences reset. Next annotator will create new overlap set."}

# === Get system info ===
@app.get("/api/system_info")
def get_system_info():
    """Get information about the annotation system configuration"""
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

# Remove the old generate_users endpoint since we're doing dynamic generation
# @app.post("/generate_users/") - REMOVED

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)