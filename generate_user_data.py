import json
import os
import random

def create_user_datasets(input_path, output_dir, num_users=10, per_user=50, overlap_count=10):
    with open(input_path) as f:
        sentences = [json.loads(line) for line in f]

    overlap = random.sample(sentences, overlap_count)

    remaining = [s for s in sentences if s not in overlap]

    os.makedirs(output_dir, exist_ok=True)
    
    for i in range(num_users):
        unique = random.sample(remaining, per_user - overlap_count)
        user_set = overlap + unique
        random.shuffle(user_set)
        with open(os.path.join(output_dir, f"user{i+1}.json"), "w") as f:
            json.dump(user_set, f, indent=2)

create_user_datasets("/Users/guida/annotation_app/templates/data/sample_sfu_combined.jsonl", "/Users/guida/annotation_app/templates/user_data", num_users=10)
